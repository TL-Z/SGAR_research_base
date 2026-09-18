#!/usr/bin/env python3
"""Inventory, pull, and verify the frozen Terminal-Bench 2.1 images."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_TASK_ROOT = Path(
    "/ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1"
)
DEFAULT_STATE_DIR = Path(
    "/ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight"
)
DEFAULT_NET_WRAPPER = Path("/usr/local/bin/sgar-net-run")
EXPECTED_TASK_COUNT = 89
PRINT_LOCK = threading.Lock()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def image_filename(image: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", image)


def run(
    command: list[str],
    *,
    timeout: int | None = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
        check=False,
    )


def require_program(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required program not found: {name}")


def load_inventory(task_root: Path, expected_count: int) -> list[dict[str, str]]:
    if not task_root.is_dir():
        raise RuntimeError(f"task root does not exist: {task_root}")

    rows: list[dict[str, str]] = []
    for task_file in sorted(task_root.glob("*/task.toml")):
        with task_file.open("rb") as handle:
            config = tomllib.load(handle)
        image = str(config.get("environment", {}).get("docker_image", "")).strip()
        if not image:
            raise RuntimeError(f"missing [environment].docker_image: {task_file}")
        if image.endswith("-vfready") or "-vfready:" in image:
            raise RuntimeError(f"debug vfready image is not allowed: {image}")
        rows.append(
            {
                "task_id": task_file.parent.name,
                "image": image,
                "task_toml": str(task_file.resolve()),
            }
        )

    unique_images = {row["image"] for row in rows}
    if len(rows) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} tasks, found {len(rows)} under {task_root}"
        )
    if len(unique_images) != len(rows):
        raise RuntimeError(
            f"expected one unique image per task, found {len(unique_images)} images "
            f"for {len(rows)} tasks"
        )
    return rows


def write_inventory(
    rows: list[dict[str, str]], task_root: Path, state_dir: Path
) -> None:
    generated_at = utc_now()
    write_json(
        state_dir / "inventory.json",
        {
            "protocol": "tb21-image-inventory-v1",
            "generated_at": generated_at,
            "task_root": str(task_root.resolve()),
            "task_count": len(rows),
            "unique_image_count": len({row["image"] for row in rows}),
            "tasks": rows,
        },
    )
    lines = ["task_id\timage\ttask_toml"]
    lines.extend(
        f'{row["task_id"]}\t{row["image"]}\t{row["task_toml"]}' for row in rows
    )
    atomic_write(state_dir / "images.tsv", "\n".join(lines) + "\n")
    atomic_write(
        state_dir / "images.txt",
        "\n".join(row["image"] for row in rows) + "\n",
    )


def inspect_image(image: str, timeout: int) -> dict[str, Any]:
    result = run(["docker", "image", "inspect", image], timeout=timeout)
    if result.returncode != 0:
        return {
            "image": image,
            "present": False,
            "inspect_error": (result.stderr or result.stdout).strip(),
        }
    try:
        inspected = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        return {
            "image": image,
            "present": False,
            "inspect_error": f"invalid docker inspect response: {exc}",
        }
    config = inspected.get("Config") or {}
    return {
        "image": image,
        "present": True,
        "image_id": inspected.get("Id"),
        "repo_digests": inspected.get("RepoDigests") or [],
        "os": inspected.get("Os"),
        "architecture": inspected.get("Architecture"),
        "size_bytes": inspected.get("Size"),
        "created": inspected.get("Created"),
        "labels": config.get("Labels") or {},
    }


def inspect_all(
    rows: list[dict[str, str]], *, jobs: int, timeout: int
) -> list[dict[str, Any]]:
    task_by_image = {row["image"]: row["task_id"] for row in rows}
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(inspect_image, row["image"], timeout): row["image"]
            for row in rows
        }
        for future in concurrent.futures.as_completed(futures):
            image = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # Defensive: preserve a status row.
                result = {
                    "image": image,
                    "present": False,
                    "inspect_error": f"{type(exc).__name__}: {exc}",
                }
            result["task_id"] = task_by_image[image]
            results[image] = result
    return [results[row["image"]] for row in rows]


def write_status(results: list[dict[str, Any]], state_dir: Path) -> dict[str, Any]:
    present = [row for row in results if row["present"]]
    missing = [row for row in results if not row["present"]]
    wrong_platform = [
        row
        for row in present
        if row.get("os") != "linux" or row.get("architecture") != "amd64"
    ]
    digest_missing = [row for row in present if not row.get("repo_digests")]
    summary = {
        "protocol": "tb21-image-status-v1",
        "generated_at": utc_now(),
        "expected": len(results),
        "present": len(present),
        "missing": len(missing),
        "wrong_platform": len(wrong_platform),
        "digest_missing": len(digest_missing),
        "total_size_bytes": sum(int(row.get("size_bytes") or 0) for row in present),
        "images": results,
    }
    write_json(state_dir / "status.json", summary)
    atomic_write(
        state_dir / "present_images.txt",
        "".join(f'{row["image"]}\n' for row in present),
    )
    atomic_write(
        state_dir / "missing_images.txt",
        "".join(f'{row["image"]}\n' for row in missing),
    )
    atomic_write(
        state_dir / "wrong_platform_images.txt",
        "".join(f'{row["image"]}\n' for row in wrong_platform),
    )
    return summary


def print_status(summary: dict[str, Any]) -> None:
    gib = summary["total_size_bytes"] / (1024**3)
    print(
        "TB2.1 images: "
        f'present={summary["present"]}/{summary["expected"]} '
        f'missing={summary["missing"]} '
        f'wrong_platform={summary["wrong_platform"]} '
        f'digest_missing={summary["digest_missing"]} '
        f"virtual_size={gib:.2f} GiB"
    )


def pull_one(
    image: str,
    *,
    state_dir: Path,
    net_wrapper: Path,
    attempts: int,
    skopeo_retries: int,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    log_path = state_dir / "logs" / f"{image_filename(image)}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path = state_dir / "archives" / f"{image_filename(image)}.tar"
    partial_archive_path = archive_path.with_suffix(".tar.partial")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    download_command = [
        str(net_wrapper),
        "skopeo",
        "copy",
        "--retry-times",
        str(skopeo_retries),
        "--override-os",
        "linux",
        "--override-arch",
        "amd64",
        f"docker://docker.io/{image}",
        f"docker-archive:{partial_archive_path}:{image}",
    ]
    load_command = ["docker", "load", "--input", str(archive_path)]
    if dry_run:
        return {
            "image": image,
            "status": "dry_run",
            "attempts": 0,
            "download_command": download_command,
            "load_command": load_command,
            "log_path": str(log_path),
            "archive_path": str(archive_path),
        }

    started_at = utc_now()
    last_error = ""
    for attempt in range(1, attempts + 1):
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"\n[{utc_now()}] attempt={attempt}/{attempts} image={image}\n"
            )
            log.flush()
            if not archive_path.is_file():
                partial_archive_path.unlink(missing_ok=True)
                log.write(f"[{utc_now()}] phase=download archive={archive_path}\n")
                log.flush()
                try:
                    download_result = run(
                        download_command,
                        timeout=timeout,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    download_return_code = download_result.returncode
                except subprocess.TimeoutExpired:
                    download_return_code = 124
                    log.write(f"[{utc_now()}] download_timeout={timeout}\n")
                if download_return_code != 0:
                    last_error = f"download_exit_code={download_return_code}"
                    partial_archive_path.unlink(missing_ok=True)
                    if attempt < attempts:
                        time.sleep(min(30, 5 * attempt))
                    continue
                if not partial_archive_path.is_file():
                    last_error = "download_succeeded_but_archive_missing"
                    if attempt < attempts:
                        time.sleep(min(30, 5 * attempt))
                    continue
                partial_archive_path.replace(archive_path)
            else:
                log.write(
                    f"[{utc_now()}] phase=download skipped=reuse_archive "
                    f"archive={archive_path}\n"
                )

            log.write(f"[{utc_now()}] phase=load archive={archive_path}\n")
            log.flush()
            try:
                load_result = run(
                    load_command,
                    timeout=timeout,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                load_return_code = load_result.returncode
            except subprocess.TimeoutExpired:
                load_return_code = 124
                log.write(f"[{utc_now()}] load_timeout={timeout}\n")
            last_error = f"load_exit_code={load_return_code}"

        inspected = inspect_image(image, timeout=60)
        if load_return_code == 0 and inspected["present"]:
            archive_path.unlink(missing_ok=True)
            return {
                "image": image,
                "status": "pulled",
                "attempts": attempt,
                "started_at": started_at,
                "finished_at": utc_now(),
                "log_path": str(log_path),
                "image_id": inspected.get("image_id"),
                "repo_digests": inspected.get("repo_digests") or [],
                "size_bytes": inspected.get("size_bytes"),
                "archive_removed": True,
            }
        if attempt < attempts:
            time.sleep(min(30, 5 * attempt))

    return {
        "image": image,
        "status": "failed",
        "attempts": attempts,
        "started_at": started_at,
        "finished_at": utc_now(),
        "error": last_error,
        "log_path": str(log_path),
        "archive_path": str(archive_path),
        "archive_retained": archive_path.is_file(),
    }


def append_journal(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def acquire_pull_lock(state_dir: Path) -> Any:
    lock_path = state_dir / "pull.lock"
    lock = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError(
            f"another pull process holds {lock_path}; do not start duplicate workers"
        ) from exc
    lock.write(f"pid={os.getpid()} started_at={utc_now()}\n")
    lock.flush()
    return lock


def command_inventory(args: argparse.Namespace) -> int:
    rows = load_inventory(args.task_root, args.expected_count)
    write_inventory(rows, args.task_root, args.state_dir)
    print(
        f"Inventory written: tasks={len(rows)} unique_images={len(rows)} "
        f"state_dir={args.state_dir}"
    )
    return 0


def refresh_status(args: argparse.Namespace) -> tuple[list[dict[str, str]], dict[str, Any]]:
    require_program("docker")
    rows = load_inventory(args.task_root, args.expected_count)
    write_inventory(rows, args.task_root, args.state_dir)
    results = inspect_all(rows, jobs=args.inspect_jobs, timeout=args.inspect_timeout)
    summary = write_status(results, args.state_dir)
    print_status(summary)
    return rows, summary


def command_status(args: argparse.Namespace) -> int:
    _, summary = refresh_status(args)
    return 0 if summary["wrong_platform"] == 0 else 2


def command_pull(args: argparse.Namespace) -> int:
    require_program("docker")
    require_program("skopeo")
    if not args.net_wrapper.is_file() or not os.access(args.net_wrapper, os.X_OK):
        raise RuntimeError(f"network wrapper is not executable: {args.net_wrapper}")

    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock = acquire_pull_lock(args.state_dir)
    try:
        _, summary = refresh_status(args)
        missing = [row["image"] for row in summary["images"] if not row["present"]]
        if args.limit is not None:
            missing = missing[: args.limit]
        if not missing:
            print("No missing images to pull.")
            return 0

        print(
            f"Pull queue: images={len(missing)} jobs={args.jobs} "
            f"attempts={args.attempts} dry_run={args.dry_run}"
        )
        journal = args.state_dir / "pull_results.jsonl"
        failed = 0
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    pull_one,
                    image,
                    state_dir=args.state_dir,
                    net_wrapper=args.net_wrapper,
                    attempts=args.attempts,
                    skopeo_retries=args.skopeo_retries,
                    timeout=args.pull_timeout,
                    dry_run=args.dry_run,
                ): image
                for image in missing
            }
            for future in concurrent.futures.as_completed(futures):
                image = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "image": image,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "finished_at": utc_now(),
                    }
                append_journal(journal, result)
                completed += 1
                if result["status"] == "failed":
                    failed += 1
                with PRINT_LOCK:
                    print(
                        f'[{completed}/{len(missing)}] {result["status"]}: {image}',
                        flush=True,
                    )

        if args.dry_run:
            print("Dry run complete; no image layers were downloaded.")
            return 0
        _, final_summary = refresh_status(args)
        if failed:
            print(f"Pull finished with {failed} failed image(s). Re-run to resume.")
            return 3
        return 0 if final_summary["missing"] == 0 else 4
    finally:
        lock.close()


def command_verify(args: argparse.Namespace) -> int:
    _, summary = refresh_status(args)
    report = {
        "protocol": "tb21-image-verification-v1",
        "verified_at": utc_now(),
        "passed": summary["missing"] == 0 and summary["wrong_platform"] == 0,
        "expected": summary["expected"],
        "present": summary["present"],
        "missing": summary["missing"],
        "wrong_platform": summary["wrong_platform"],
        "digest_missing": summary["digest_missing"],
        "status_path": str(args.state_dir / "status.json"),
    }
    write_json(args.state_dir / "verification.json", report)
    if report["passed"]:
        print("PASS: all frozen TB2.1 images are available as linux/amd64.")
        if report["digest_missing"]:
            print(
                "WARNING: some local images have no RepoDigest; image IDs are still "
                "recorded in status.json."
            )
        return 0
    print(
        f'FAIL: missing={report["missing"]} '
        f'wrong_platform={report["wrong_platform"]}'
    )
    return 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the frozen Terminal-Bench 2.1 Docker image cache."
    )
    parser.add_argument("command", choices=("inventory", "status", "pull", "verify"))
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_TASK_COUNT)
    parser.add_argument("--inspect-jobs", type=int, default=8)
    parser.add_argument("--inspect-timeout", type=int, default=60)
    parser.add_argument("--net-wrapper", type=Path, default=DEFAULT_NET_WRAPPER)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--skopeo-retries", type=int, default=5)
    parser.add_argument("--pull-timeout", type=int, default=7200)
    parser.add_argument(
        "--limit", type=int, help="pull only the first N currently missing images"
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "expected_count",
        "inspect_jobs",
        "inspect_timeout",
        "jobs",
        "attempts",
        "skopeo_retries",
        "pull_timeout",
    ):
        if getattr(args, name) <= 0:
            raise RuntimeError(f"--{name.replace('_', '-')} must be positive")
    if args.limit is not None and args.limit <= 0:
        raise RuntimeError("--limit must be positive")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        args.state_dir.mkdir(parents=True, exist_ok=True)
        commands = {
            "inventory": command_inventory,
            "status": command_status,
            "pull": command_pull,
            "verify": command_verify,
        }
        return commands[args.command](args)
    except KeyboardInterrupt:
        print("Interrupted. Re-run the same command to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
