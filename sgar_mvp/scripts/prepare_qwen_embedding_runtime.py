"""Prepare and hash pinned official embedding snapshots under the configured release storage root.

The command never writes model weights into the repository and never changes
the formal retrieval index. Downloading is explicit through ``--download``;
``--snapshot-environment-only`` records the pre-change environment without
network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.direct_network import configure_direct_network
from sgar_mvp.src.release_environment import release_storage_root, require_release_storage_path
from sgar_mvp.src.embedding_runtime import load_embedding_release_config
from sgar_mvp.src.pipeline_control import canonical_json_bytes, canonical_sha256


MINIMUM_FREE_BYTES = 25 * 1024**3
OFFICIAL_EMBEDDING_REPOSITORY = "Qwen/Qwen3-Embedding-0.6B"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare pinned official SGAR embedding snapshots under SGAR_RELEASE_STORAGE_ROOT."
    )
    parser.add_argument(
        "--release-config",
        type=Path,
        default=PROJECT_ROOT / "sgar_mvp/config/embedding_release.json",
    )
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument(
        "--runtime-root", type=Path
    )
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--lock-output",
        type=Path,
        help="New versioned lock path under SGAR_RELEASE_STORAGE_ROOT; existing locks are never overwritten.",
    )
    parser.add_argument("--snapshot-environment-only", action="store_true")
    return parser.parse_args()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _command_output(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    return (completed.stdout or "") + (completed.stderr or "")


def _environment_snapshot() -> dict[str, Any]:
    conda_exe = Path(sys.prefix).parents[1] / "Scripts/conda.exe"
    try:
        import torch

        torch_state: dict[str, Any] = {
            "version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_name": (
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
            ),
        }
    except Exception as exc:
        torch_state = {"error": type(exc).__name__}
    return {
        "python": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "torch": torch_state,
        "pip_freeze": _command_output([sys.executable, "-m", "pip", "freeze"]).splitlines(),
        "conda_explicit": (
            _command_output([str(conda_exe), "list", "--explicit", "-p", sys.prefix]).splitlines()
            if conda_exe.is_file()
            else []
        ),
    }


def _snapshot_files(snapshot: Path) -> tuple[list[dict[str, Any]], str]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
        relative = path.relative_to(snapshot).as_posix()
        files.append(
            {
                "path": relative,
                "byte_size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files:
        raise RuntimeError("embedding_snapshot_empty")
    return files, canonical_sha256(files)


def _resolve_revision(snapshot: Path) -> str:
    revision = snapshot.name.lower()
    if len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
        raise RuntimeError(f"embedding_snapshot_revision_invalid:{snapshot.name}")
    return revision


def _safe_snapshot_target(snapshot: Path, relative_name: str) -> Path:
    target = (snapshot / Path(relative_name.replace("/", os.sep))).resolve()
    try:
        target.relative_to(snapshot.resolve())
    except ValueError as exc:
        raise RuntimeError(f"embedding_snapshot_path_escape:{relative_name}") from exc
    return target


def _direct_official_snapshot_download(
    *,
    repository: str,
    revision: str,
    siblings: Iterable[Any],
    hub_root: Path,
) -> Path:
    """Download exact official revision bytes when Hub HEAD metadata is incomplete.

    Some network paths strip Content-Length from small Hugging Face files. The
    Hub client refuses those files even though a revision-pinned GET is valid.
    This fallback remains restricted to the allowlisted official repos and
    verifies every available LFS size/SHA-256 before publishing a local file.
    """

    if repository != OFFICIAL_EMBEDDING_REPOSITORY:
        raise RuntimeError(f"embedding_repository_not_allowlisted:{repository}")
    owner, name = repository.split("/", 1)
    repository_root = hub_root / f"models--{owner}--{name}"
    snapshot = repository_root / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)

    import httpx

    timeout = httpx.Timeout(connect=60.0, read=120.0, write=60.0, pool=60.0)
    with httpx.Client(follow_redirects=True, timeout=timeout, trust_env=False) as client:
        for sibling in siblings:
            relative_name = str(getattr(sibling, "rfilename", "") or "")
            if not relative_name:
                raise RuntimeError("embedding_remote_filename_missing")
            target = _safe_snapshot_target(snapshot, relative_name)
            lfs = getattr(sibling, "lfs", None)
            expected_size = getattr(lfs, "size", None)
            expected_sha256 = str(getattr(lfs, "sha256", "") or "").lower()
            if target.is_file():
                actual_size = target.stat().st_size
                if expected_size is None or actual_size == int(expected_size):
                    if not expected_sha256 or _sha256_file(target) == expected_sha256:
                        continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".sgar-incomplete")
            url = (
                f"https://huggingface.co/{repository}/resolve/{revision}/"
                + quote(relative_name, safe="/")
            )
            print(f"Downloading {repository}:{relative_name}", file=sys.stderr)
            digest = hashlib.sha256()
            byte_size = temporary.stat().st_size if temporary.is_file() else 0
            if byte_size:
                with temporary.open("rb") as existing:
                    while chunk := existing.read(4 * 1024 * 1024):
                        digest.update(chunk)
            headers = {"Range": f"bytes={byte_size}-"} if byte_size else {}
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                if byte_size and response.status_code == 206:
                    content_range = str(response.headers.get("content-range") or "")
                    if not content_range.startswith(f"bytes {byte_size}-"):
                        raise RuntimeError(
                            f"embedding_download_content_range_mismatch:{relative_name}"
                        )
                    mode = "ab"
                elif byte_size and response.status_code == 200:
                    digest = hashlib.sha256()
                    byte_size = 0
                    mode = "wb"
                elif byte_size:
                    raise RuntimeError(
                        f"embedding_download_resume_status_invalid:{relative_name}:"
                        f"{response.status_code}"
                    )
                else:
                    mode = "wb"
                with temporary.open(mode) as handle:
                    for chunk in response.iter_bytes(chunk_size=4 * 1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
            if expected_size is not None and byte_size != int(expected_size):
                raise RuntimeError(
                    f"embedding_download_size_mismatch:{relative_name}:{byte_size}"
                )
            if expected_sha256 and digest.hexdigest() != expected_sha256:
                raise RuntimeError(f"embedding_download_sha256_mismatch:{relative_name}")
            os.replace(temporary, target)

    refs = repository_root / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision + "\n", encoding="utf-8")
    return snapshot.resolve()


def main() -> int:
    configure_direct_network()
    args = _arguments()
    storage_root = release_storage_root()
    cache_root = require_release_storage_path(
        args.cache_root if args.cache_root is not None else storage_root / "hf_cache",
        code="embedding_cache_root_outside_storage_root",
    )
    runtime_root = require_release_storage_path(
        args.runtime_root if args.runtime_root is not None else storage_root / "sgar_embedding_runtime",
        code="embedding_runtime_root_outside_storage_root",
    )
    free_bytes = shutil.disk_usage(cache_root.anchor).free
    if free_bytes < MINIMUM_FREE_BYTES:
        raise RuntimeError(f"embedding_disk_free_below_25_gib:{free_bytes}")
    cache_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_HUB_CACHE"] = str(cache_root / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_root / "hub")
    os.environ["SGAR_EMBEDDING_OFFLOAD_DIR"] = str(runtime_root / "offload")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

    generated_at = datetime.now(timezone.utc).isoformat()
    environment = _environment_snapshot()
    environment_path = runtime_root / (
        "environment-" + generated_at.replace(":", "").replace("+00:00", "Z") + ".json"
    )
    environment_path.write_bytes(
        canonical_json_bytes(
            {
                "protocol": "sgar-embedding-environment-snapshot-v1",
                "generated_at": generated_at,
                "free_bytes_before": free_bytes,
                "environment": environment,
            }
        )
        + b"\n"
    )
    if args.snapshot_environment_only:
        print(str(environment_path))
        return 0
    if not args.download:
        raise RuntimeError("embedding_download_requires_explicit_flag")

    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    runtime = load_embedding_release_config(args.release_config)
    if runtime.model_id != OFFICIAL_EMBEDDING_REPOSITORY:
        raise RuntimeError("official_embedding_release_config_invalid")
    repositories = [runtime.model_id]
    snapshots: list[dict[str, Any]] = []
    for repository in repositories:
        requested_revision = runtime.revision
        if requested_revision is None:
            head_info = HfApi().model_info(repository, files_metadata=False)
            requested_revision = str(head_info.sha or "").lower()
            if len(requested_revision) != 40:
                raise RuntimeError(f"embedding_remote_revision_invalid:{repository}")
        remote_info = HfApi().model_info(
            repository,
            revision=requested_revision,
            files_metadata=True,
        )
        try:
            snapshot = Path(
                snapshot_download(
                    repo_id=repository,
                    revision=requested_revision,
                    cache_dir=str(cache_root / "hub"),
                    local_files_only=False,
                    max_workers=1,
                )
            ).resolve()
        except LocalEntryNotFoundError:
            snapshot = _direct_official_snapshot_download(
                repository=repository,
                revision=requested_revision,
                siblings=remote_info.siblings or (),
                hub_root=cache_root / "hub",
            )
        revision = _resolve_revision(snapshot)
        files, files_sha256 = _snapshot_files(snapshot)
        snapshots.append(
            {
                "repo_id": repository,
                "requested_revision": requested_revision,
                "resolved_revision": revision,
                "files": files,
                "files_sha256": files_sha256,
            }
        )

    lock = {
        "protocol": "sgar-embedding-snapshot-lock-v1",
        "generated_at": generated_at,
        "release_config_sha256": hashlib.sha256(
            args.release_config.read_bytes()
        ).hexdigest(),
        "snapshots": snapshots,
        "runtime_configuration": {
            "cache_layout": "hf-cache/hub/models--owner--repo/snapshots/revision",
            "local_files_only": True,
            "offload_policy": "d-drive-only",
        },
    }
    lock["lock_sha256"] = canonical_sha256(lock)
    lock_path = (
        require_release_storage_path(args.lock_output, code="embedding_snapshot_lock_outside_storage_root")
        if args.lock_output is not None
        else runtime_root
        / (
            "embedding_snapshot_lock-"
            + generated_at.replace(":", "").replace("+00:00", "Z")
            + ".json"
        )
    )
    if lock_path.exists():
        raise RuntimeError("embedding_snapshot_lock_already_exists")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = lock_path.with_name(lock_path.name + ".tmp")
    temporary.write_bytes(canonical_json_bytes(lock) + b"\n")
    temporary.replace(lock_path)
    print(str(lock_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
