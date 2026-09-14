"""Compile the canonical SGAR resource catalogs into generated aggregates.

Canonical inputs:

* ``Pool/resources/tools/registry/<runtime>/*.json``
* ``Pool/resources/json/models.json``
* ``Pool/resources/json/agents.json``
* ``Pool/resources/json/skills.json``
* ``Pool/resources/json/resources.json`` (an empty list is valid)

Generated outputs:

* ``Pool/resources/json/tools.json``
* ``Pool/resources/json/combine.json``

The compiler is deliberately strict.  A missing catalog, malformed manifest,
duplicate resource id, or type mismatch is an error.  In particular, the old
``_non_tool_resources.json`` migration snapshot is never a runtime fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sgar_mvp.src.capability_cards import build_capability_consistency_report
JSON_DIR = ROOT / "Pool" / "resources" / "json"
REGISTRY_DIR = ROOT / "Pool" / "resources" / "tools" / "registry"
COMBINE_PATH = JSON_DIR / "combine.json"
TOOLS_PATH = JSON_DIR / "tools.json"

CATALOGS: tuple[tuple[str, str], ...] = (
    ("models.json", "Model"),
    ("agents.json", "Agent"),
    ("skills.json", "Skill"),
    ("resources.json", "Resource"),
)


class PoolBuildError(ValueError):
    """Raised when canonical pool sources cannot be compiled safely."""


def _resource_type(manifest: dict[str, Any]) -> str:
    nested_type = manifest.get("type")
    if not isinstance(nested_type, dict):
        nested_type = {}
    return str(manifest.get("resource_type") or nested_type.get("resource_type") or "")


def _load_array(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PoolBuildError(f"Required catalog is missing: {path.relative_to(ROOT)}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PoolBuildError(f"Cannot parse {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(payload, list):
        raise PoolBuildError(f"Catalog must contain a JSON array: {path.relative_to(ROOT)}")
    invalid_positions = [index for index, item in enumerate(payload) if not isinstance(item, dict)]
    if invalid_positions:
        raise PoolBuildError(
            f"Catalog contains non-object entries at {invalid_positions[:10]}: {path.relative_to(ROOT)}"
        )
    return payload


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    expected_type: str,
    source: Path,
) -> str:
    resource_id = str(manifest.get("resource_id") or "").strip()
    if not resource_id:
        raise PoolBuildError(f"Manifest has no resource_id: {source.relative_to(ROOT)}")
    actual_type = _resource_type(manifest)
    if actual_type != expected_type:
        raise PoolBuildError(
            f"{resource_id} has type {actual_type!r}, expected {expected_type!r}: "
            f"{source.relative_to(ROOT)}"
        )
    return resource_id


def load_registry_tools() -> list[dict[str, Any]]:
    if not REGISTRY_DIR.is_dir():
        raise PoolBuildError(f"Tool registry is missing: {REGISTRY_DIR.relative_to(ROOT)}")
    paths = sorted(
        path
        for path in REGISTRY_DIR.rglob("*.json")
        if not path.name.startswith("_")
    )
    if not paths:
        raise PoolBuildError("Tool registry contains no source manifests")
    tools: list[dict[str, Any]] = []
    seen: dict[str, Path] = {}
    for path in paths:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PoolBuildError(f"Cannot parse {path.relative_to(ROOT)}: {exc}") from exc
        if not isinstance(manifest, dict):
            raise PoolBuildError(f"Tool manifest must be a JSON object: {path.relative_to(ROOT)}")
        resource_id = _validate_manifest(manifest, expected_type="Tool", source=path)
        if resource_id in seen:
            raise PoolBuildError(
                f"Duplicate resource_id {resource_id}: {seen[resource_id].relative_to(ROOT)} "
                f"and {path.relative_to(ROOT)}"
            )
        seen[resource_id] = path
        tools.append(manifest)
    return sorted(tools, key=_sort_key)


def load_non_tool_catalogs() -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    seen: dict[str, Path] = {}
    for filename, expected_type in CATALOGS:
        path = JSON_DIR / filename
        for manifest in _load_array(path):
            resource_id = _validate_manifest(
                manifest,
                expected_type=expected_type,
                source=path,
            )
            if resource_id in seen:
                raise PoolBuildError(
                    f"Duplicate resource_id {resource_id}: {seen[resource_id].relative_to(ROOT)} "
                    f"and {path.relative_to(ROOT)}"
                )
            seen[resource_id] = path
            resources.append(manifest)
    return resources


def _sort_key(manifest: dict[str, Any]) -> tuple[str, str]:
    return (_resource_type(manifest).casefold(), str(manifest["resource_id"]).casefold())


def _serialized(payload: Iterable[dict[str, Any]]) -> bytes:
    text = json.dumps(list(payload), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return text.encode("utf-8")


def _write_if_changed(path: Path, content: bytes) -> None:
    if path.is_file() and path.read_bytes() == content:
        return
    # Generated catalogs are large and are often watched by editors/indexers on
    # Windows.  Write beside the destination and atomically replace it so a
    # reader never observes a partially written JSON file; briefly retry when a
    # watcher holds a transient handle.
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        last_error: OSError | None = None
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                last_error = exc
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def compile_pool(*, write: bool = True) -> dict[str, Any]:
    tools = load_registry_tools()
    non_tools = load_non_tool_catalogs()
    all_resources = tools + non_tools
    from sgar_mvp.src.model_selection import require_registered_models
    require_registered_models(all_resources, root=ROOT, complete=True)
    ids = [str(item["resource_id"]) for item in all_resources]
    duplicate_ids = sorted(resource_id for resource_id, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        raise PoolBuildError(f"Duplicate resource ids across catalogs: {duplicate_ids[:20]}")

    tools = sorted(tools, key=_sort_key)
    combined = sorted(all_resources, key=_sort_key)
    consistency = build_capability_consistency_report(
        {str(item["resource_id"]): item for item in combined}
    )
    if consistency.sealable_resource_count != consistency.resource_count:
        failures = [
            f"{item.resource_id}:{','.join(item.issue_codes)}"
            for item in consistency.items
            if not item.sealable
        ]
        raise PoolBuildError(
            "Canonical resources are not executable and English: "
            + "; ".join(failures[:20])
        )
    tools_bytes = _serialized(tools)
    combine_bytes = _serialized(combined)
    if write:
        JSON_DIR.mkdir(parents=True, exist_ok=True)
        _write_if_changed(TOOLS_PATH, tools_bytes)
        _write_if_changed(COMBINE_PATH, combine_bytes)

    counts = Counter(_resource_type(item) for item in combined)
    return {
        "tool_count": len(tools),
        "resource_count": len(combined),
        "type_counts": dict(sorted(counts.items())),
        "tools_sha256": _sha256(tools_bytes),
        "combine_sha256": _sha256(combine_bytes),
    }


def check_generated_catalogs(report: dict[str, Any]) -> None:
    """Check actual artifacts; a dry-run compilation alone cannot detect drift."""
    for path, key in ((TOOLS_PATH, "tools_sha256"), (COMBINE_PATH, "combine_sha256")):
        if not path.is_file():
            raise PoolBuildError(f"Generated catalog is missing: {path.name}")
        if _sha256(path.read_bytes()) != report[key]:
            raise PoolBuildError(f"Generated catalog is stale: {path.name}; run build_pool.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile canonical SGAR resource catalogs")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that generated catalogs match canonical inputs without writing files.",
    )
    args = parser.parse_args()
    report = compile_pool(write=not args.check)
    if args.check:
        check_generated_catalogs(report)
    mode = "checked" if args.check else "built"
    print(f"build_pool: {mode} {report['resource_count']} resources {report['type_counts']}")
    print(f"  tools.json sha256={report['tools_sha256']}")
    print(f"  combine.json sha256={report['combine_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
