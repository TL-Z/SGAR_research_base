"""End-to-end native SGAR runner for one frozen TB2.1 task."""
from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .docker_runtime import DockerTaskRuntime, TaskRuntimeError
from .task_loader import (
    CANONICAL_BENCHMARK_ID,
    CANONICAL_BENCHMARK_VERSION,
    DEFAULT_TASK_ROOT,
    load_task,
    load_tasks,
)


def _run_dir(root: Path, task_id: str, trial_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{task_id}__{trial_id}"
    if path.exists():
        raise RuntimeError(f"run directory already exists: {path}")
    path.mkdir(parents=True)
    return path


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _worker_command(args: argparse.Namespace, run_dir: Path, trial_id: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "sgar_mvp.scripts.external_substrate_worker",
        "--config",
        str(Path(args.config).resolve()),
        "--query",
        args.query,
        "--output-dir",
        str(run_dir / "native"),
        "--report-path",
        str(run_dir / "native" / "experiment_report.md"),
        "--run-id",
        trial_id,
        "--trial-id",
        trial_id,
        "--network-policy",
        "declared" if args.network else "disabled",
        "--allow-missing-delivery",
    ]


def run_one(args: argparse.Namespace) -> dict[str, Any]:
    task = load_task(Path(args.task_root), args.task_id)
    trial_id = args.trial_id or f"{task.task_id}-{int(time.time())}"
    root = Path(args.output_root).resolve()
    run_dir = _run_dir(root, task.task_id, trial_id)
    runtime = DockerTaskRuntime(task, trial_id=trial_id, output_dir=run_dir)
    setup_started = time.monotonic()
    manifest = {
        "protocol": "sgar-tb21-trial-manifest-v1",
        "experiment_id": args.experiment_id,
        "benchmark_id": CANONICAL_BENCHMARK_ID,
        "benchmark_version": CANONICAL_BENCHMARK_VERSION,
        "task_id": task.task_id,
        "trial_id": trial_id,
        "task_hash": task.task_hash,
        "agent_timeout_sec": task.agent_timeout_sec,
        "verifier_timeout_sec": task.verifier_timeout_sec,
        "network_policy": "declared" if args.network else "disabled",
        "proxy_policy": "sgar-experiment-network-v1",
    }
    _write(run_dir / "trial_manifest.json", manifest)
    container_info: dict[str, Any] = {}
    worker: subprocess.Popen[str] | None = None
    verifier: dict[str, Any] | None = None
    method_started = time.monotonic()
    method_ended = method_started
    failure: dict[str, Any] | None = None
    try:
        container_info = runtime.start()
        manifest.update({"container": container_info, "setup_seconds": time.monotonic() - setup_started})
        _write(run_dir / "trial_manifest.json", manifest)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path.cwd()) + os.pathsep + env.get("PYTHONPATH", "")
        (run_dir / "native").mkdir(parents=True, exist_ok=True)
        worker = subprocess.Popen(
            _worker_command(args, run_dir, trial_id),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=(run_dir / "native" / "worker.stderr.log").open("w", encoding="utf-8"),
            text=True,
            bufsize=1,
            env=env,
        )
        assert worker.stdin is not None and worker.stdout is not None
        method_started = time.monotonic()
        deadline = method_started + task.agent_timeout_sec
        timed_out = False
        with (run_dir / "rpc_actions.jsonl").open("w", encoding="utf-8") as rpc_log:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                ready, _, _ = select.select([worker.stdout], [], [], remaining)
                if not ready:
                    timed_out = True
                    break
                line = worker.stdout.readline()
                if not line:
                    break
                payload = json.loads(line)
                if payload.get("protocol") == "sgar-external-substrate-action-v1":
                    result = runtime.handle_rpc(payload)
                    response = {
                        "protocol": "sgar-external-substrate-result-v1",
                        "run_id": payload.get("run_id"),
                        "trial_id": payload.get("trial_id"),
                        "action_id": payload.get("action_id"),
                        "result": result,
                    }
                    rpc_log.write(json.dumps({"request": payload, "result": result}, ensure_ascii=False) + "\n")
                    rpc_log.flush()
                    worker.stdin.write(json.dumps(response, ensure_ascii=False) + "\n")
                    worker.stdin.flush()
                    continue
                if payload.get("protocol") == "sgar-external-substrate-terminal-v1":
                    _write(run_dir / "worker_terminal.json", payload)
                    break
        method_ended = time.monotonic()
        if timed_out:
            failure = {"failure_class": "timeout", "detail": f"agent_timeout_{task.agent_timeout_sec}s"}
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=30)
        if worker.poll() is None:
            worker.stdin.close()
            worker.wait(timeout=30)
        if worker.returncode != 0:
            failure = {"failure_class": "method_failure", "detail": f"worker_exit_{worker.returncode}"}
        verifier_started = time.monotonic()
        verifier = runtime.verify()
        verifier["verifier_seconds"] = time.monotonic() - verifier_started
        _write(run_dir / "verifier.json", verifier)
        (run_dir / "verifier_stdout.log").write_text(
            str(verifier.get("stdout") or ""), encoding="utf-8"
        )
        (run_dir / "verifier_stderr.log").write_text(
            str(verifier.get("stderr") or verifier.get("detail") or ""), encoding="utf-8"
        )
        if verifier.get("status") != "ok":
            failure = {"failure_class": "verifier_failure", "detail": verifier.get("status")}
    except (TaskRuntimeError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        failure = {"failure_class": "infrastructure_failure", "detail": f"{type(exc).__name__}:{exc}"}
        method_ended = time.monotonic()
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait()
    finally:
        cleanup_started = time.monotonic()
        cleanup = runtime.cleanup()
        total = time.monotonic() - setup_started

    result = {
        **manifest,
        "method_wall_clock_seconds": method_ended - method_started,
        "e2e_wall_clock_seconds": total,
        "verifier": verifier,
        "cleanup": cleanup,
        "success": bool(verifier and verifier.get("status") == "ok" and not failure),
        "official_reward": verifier.get("reward") if verifier else None,
        "failure": failure,
        "native_run_dir": str(run_dir / "native"),
        "rpc_log_path": str(run_dir / "rpc_actions.jsonl"),
    }
    _write(run_dir / "result.json", result)
    return result


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    tasks, tree_hash = load_tasks(Path(args.task_root))
    selected = tasks if args.task_id in {None, "all"} else [
        load_task(Path(args.task_root), args.task_id)
    ]
    records = []
    for task in selected:
        runtime = DockerTaskRuntime(
            task,
            trial_id=f"preflight-{task.task_id}",
            output_dir=Path(args.output_root),
        )
        records.append(
            {
                "task_id": task.task_id,
                "image": task.image,
                "image_digest": runtime.image_digest(),
                "task_hash": task.task_hash,
            }
        )
    return {
        "protocol": "sgar-tb21-preflight-v1",
        "benchmark_id": CANONICAL_BENCHMARK_ID,
        "benchmark_version": CANONICAL_BENCHMARK_VERSION,
        "task_tree_sha256": tree_hash,
        "task_count": len(records),
        "images": records,
    }


def run_batch(args: argparse.Namespace) -> int:
    manifest = preflight(args)
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "preflight.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    results = []
    for item in manifest["images"][: args.max_tasks or None]:
        task_args = argparse.Namespace(**vars(args))
        task_args.task_id = item["task_id"]
        results.append(run_one(task_args))
    summary = {
        "protocol": "sgar-tb21-batch-result-v1",
        "task_tree_sha256": manifest["task_tree_sha256"],
        "results": results,
        "succeeded": sum(bool(item.get("success")) for item in results),
        "failed": sum(not bool(item.get("success")) for item in results),
    }
    (output_root / "batch_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one SGAR native TB2.1 task")
    parser.add_argument("--task-id", required=False)
    parser.add_argument("--task-root", default=str(DEFAULT_TASK_ROOT))
    parser.add_argument("--output-root", required=False, default="/tmp/sgar-tb21")
    parser.add_argument("--config", default="sgar_mvp/config.json")
    parser.add_argument("--query", default=None)
    parser.add_argument("--trial-id", default=None)
    parser.add_argument("--experiment-id", default="sgar-tb21")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("inventory", nargs="?", choices=("inventory",))
    args = parser.parse_args()
    if args.inventory:
        tasks, tree_hash = load_tasks(Path(args.task_root))
        print(json.dumps({"tasks": len(tasks), "task_tree_sha256": tree_hash}, indent=2))
        return 0
    if args.task_id in {None, "all"}:
        return run_batch(args)
    if not args.task_id:
        parser.error("--task-id is required for a task run")
    if args.query is None:
        args.query = load_task(Path(args.task_root), args.task_id).instruction
    result = run_one(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
