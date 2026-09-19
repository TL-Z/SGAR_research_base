"""Persistent official Terminal-Bench task container and verifier runtime."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .task_loader import TerminalBenchTask


class TaskRuntimeError(RuntimeError):
    pass


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def experiment_proxy() -> dict[str, str]:
    """Resolve container proxy values without exposing credentials in records."""
    values = _parse_env_file(Path("/etc/sgar/experiment-network.env"))
    host_proxy = os.environ.get("SGAR_DOCKER_PROXY") or values.get("SGAR_DOCKER_PROXY")
    if not host_proxy:
        relay_port = values.get("SGAR_DOCKER_RELAY_PORT", "7894")
        try:
            gateway = subprocess.run(
                ["docker", "network", "inspect", "bridge", "-f", "{{(index .IPAM.Config 0).Gateway}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskRuntimeError("docker_bridge_gateway_unavailable") from exc
        host_proxy = f"http://{gateway}:{relay_port}"
    no_proxy = os.environ.get("SGAR_NO_PROXY") or values.get(
        "SGAR_NO_PROXY", "localhost,127.0.0.1,::1"
    )
    return {
        "HTTP_PROXY": host_proxy,
        "HTTPS_PROXY": host_proxy,
        "http_proxy": host_proxy,
        "https_proxy": host_proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def _valid_task_path(path: str) -> str:
    text = str(path or "")
    normalized = posixpath.normpath(text)
    if (
        not text
        or "\x00" in text
        or "\\" in text
        or not text.startswith("/")
        or normalized != text
        or normalized == "/"
        or (normalized not in {"/app", "/tmp"}
            and not normalized.startswith(("/app/", "/tmp/")))
    ):
        raise TaskRuntimeError("task_container_path_invalid")
    return normalized


class DockerTaskRuntime:
    """One persistent task-facing Docker container per TB trial."""

    def __init__(self, task: TerminalBenchTask, *, trial_id: str, output_dir: Path):
        self.task = task
        self.trial_id = trial_id
        self.output_dir = output_dir
        self.container_id: str | None = None
        self.container_name = "sgar-tb21-" + hashlib.sha256(
            f"{task.task_id}:{trial_id}".encode()
        ).hexdigest()[:20]
        self.proxy = experiment_proxy()
        self.working_dir: str | None = None
        self.rpc_records: list[dict[str, Any]] = []

    def _run(self, args: list[str], *, timeout: float, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def image_digest(self) -> str:
        result = self._run(
            ["docker", "image", "inspect", self.task.image, "--format", "{{.Id}}"],
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise TaskRuntimeError(f"task_image_missing:{self.task.image}")
        return result.stdout.strip()

    def start(self) -> dict[str, Any]:
        image_digest = self.image_digest()
        args = [
            "docker", "run", "--detach", "--rm", "--name", self.container_name,
            "--label", "sgar.tb21=true",
            "--label", f"sgar.tb21.trial_id={self.trial_id}",
            "--cpus", str(self.task.cpus),
            "--memory", f"{self.task.memory_mb}m",
        ]
        if self.task.gpus:
            args.extend(["--gpus", str(self.task.gpus)])
        if not self.task.allow_internet:
            args.extend(["--network", "none"])
        # Pass names, not values, on argv; values come from the subprocess env.
        for name in self.proxy:
            args.extend(["--env", name])
        args.extend([self.task.image, "sh", "-c", "sleep infinity"])
        env = os.environ.copy()
        env.update(self.proxy)
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            raise TaskRuntimeError(f"task_container_start_failed:{result.stderr[-1000:]}")
        self.container_id = result.stdout.strip()
        inspect = self._run(
            ["docker", "inspect", self.container_id, "--format", "{{json .Config}}"],
            timeout=30,
        )
        # SGAR's logical task namespace is stable across benchmark images;
        # never inherit an image-specific WORKDIR for compiler-generated paths.
        self.working_dir = "/app"
        self.exec_argv(
            ["mkdir", "-p", "/app", "/tmp/sgar-resource-runtime", "/logs/verifier"],
            timeout=30,
            cwd="/tmp",
        )
        return {
            "container_id_sha256": hashlib.sha256(self.container_id.encode()).hexdigest(),
            "container_name": self.container_name,
            "image": self.task.image,
            "image_digest": image_digest,
            "working_dir": self.working_dir,
            "network_enabled": self.task.allow_internet,
        }

    def _require_started(self) -> str:
        if not self.container_id:
            raise TaskRuntimeError("task_container_not_started")
        return self.container_id

    def _proxy_exports(self) -> str:
        return "; ".join(
            f"export {key}={shlex.quote(value)}" for key, value in self.proxy.items()
        )

    def exec_argv(
        self,
        argv: list[str],
        *,
        timeout: float = 120,
        extra_env: Mapping[str, str] | None = None,
        cwd: str = "/app",
    ) -> dict[str, Any]:
        container = self._require_started()
        cwd = _valid_task_path(cwd)
        if not argv or any("\x00" in str(item) for item in argv):
            raise TaskRuntimeError("task_exec_argv_invalid")
        extra_env = dict(extra_env or {})
        if any(str(key).lower() in {"http_proxy", "https_proxy", "no_proxy", "all_proxy"} for key in extra_env):
            raise TaskRuntimeError("task_proxy_override_forbidden")
        env_prefix = self._proxy_exports()
        for key, value in extra_env.items():
            if not str(key).replace("_", "a").isalnum() or "=" in str(key):
                raise TaskRuntimeError("task_extra_env_name_invalid")
            env_prefix += f"; export {key}={shlex.quote(str(value))}"
        shell = f"{env_prefix}; exec {shlex.join([str(item) for item in argv])}"
        started = time.monotonic()
        process = subprocess.run(
            ["docker", "exec", "-w", cwd, container, "sh", "-c", shell],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": process.returncode == 0,
            "return_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "duration_ms": duration_ms,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    def write_text(self, path: str, text: str) -> dict[str, Any]:
        path = _valid_task_path(path)
        container = self._require_started()
        parent = posixpath.dirname(path) or "/app"
        mkdir = self.exec_argv(["mkdir", "-p", parent], timeout=30, cwd="/app")
        if not mkdir.get("ok"):
            return {"ok": False, "return_code": mkdir.get("return_code"),
                    "stderr": mkdir.get("stderr") or "task_parent_create_failed"}
        process = subprocess.run(
            ["docker", "exec", "-i", "-w", "/app", container, "sh", "-c", f"cat > {shlex.quote(path)}"],
            input=text,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {"ok": process.returncode == 0, "return_code": process.returncode, "stderr": process.stderr}

    def read_text(self, path: str) -> dict[str, Any]:
        path = _valid_task_path(path)
        result = self.exec_argv(["cat", path], timeout=30)
        result["stdout_truncated"] = len(result.get("stdout") or "") > 1024 * 1024
        if result["stdout_truncated"]:
            result["stdout"] = result["stdout"][: 1024 * 1024]
        return result

    def exists(self, path: str) -> dict[str, Any]:
        path = _valid_task_path(path)
        return self.exec_argv(["test", "-e", path], timeout=30)

    def artifact_metadata(self, path: str) -> dict[str, Any]:
        path = _valid_task_path(path)
        result = self.exec_argv(
            ["sh", "-c", f"test -f {shlex.quote(path)} && sha256sum {shlex.quote(path)} && wc -c < {shlex.quote(path)}"],
            timeout=30,
        )
        if not result["ok"]:
            return result
        lines = [line.strip() for line in (result.get("stdout") or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return {"ok": False, "error_type": "artifact_metadata_malformed"}
        digest = lines[-2].split()[0]
        try:
            size = int(lines[-1])
        except ValueError:
            return {"ok": False, "error_type": "artifact_metadata_size_invalid"}
        return {"ok": True, "content_sha256": digest, "byte_size": size, "stdout": result.get("stdout", "")}

    def verify(self) -> dict[str, Any]:
        container = self._require_started()
        tests_dir = self.task.tests_dir
        mkdir = self.exec_argv(["mkdir", "-p", "/tests"], timeout=30)
        if not mkdir.get("ok"):
            return {"status": "verifier_failure", "detail": "cannot_create_tests_dir"}
        cp1 = subprocess.run(["docker", "cp", f"{tests_dir}/.", f"{container}:/tests/"], capture_output=True, text=True, timeout=60, check=False)
        cp2 = subprocess.run(["docker", "cp", str(self.task.test_script), f"{container}:/tmp/test.sh"], capture_output=True, text=True, timeout=30, check=False)
        if cp1.returncode != 0 or cp2.returncode != 0:
            return {"status": "verifier_failure", "detail": (cp1.stderr or cp2.stderr)[-1000:]}
        started = time.monotonic()
        env_prefix = self._proxy_exports()
        shell = f"{env_prefix}; exec bash /tmp/test.sh"
        try:
            process = subprocess.run(
                ["docker", "exec", "-w", self.working_dir or "/app", container, "sh", "-c", shell],
                capture_output=True,
                text=True,
                timeout=self.task.verifier_timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "verifier_timeout",
                "duration_seconds": time.monotonic() - started,
                "stdout": str(exc.stdout or ""),
                "stderr": str(exc.stderr or ""),
            }
        reward = self.exec_argv(["cat", "/logs/verifier/reward.txt"], timeout=15)
        reward_value: float | None = None
        if reward["ok"]:
            try:
                reward_value = float((reward.get("stdout") or "").strip())
            except ValueError:
                pass
        status = "ok" if process.returncode == 0 and reward_value is not None else "verifier_failure"
        return {
            "status": status,
            "reward": reward_value,
            "exit_code": process.returncode,
            "duration_seconds": time.monotonic() - started,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }

    def cleanup(self) -> dict[str, Any]:
        if not self.container_id:
            return {"cleaned": True, "container_id": None}
        result = subprocess.run(["docker", "rm", "-f", self.container_id], capture_output=True, text=True, timeout=30, check=False)
        cleaned = result.returncode == 0 or "No such container" in (result.stderr or "")
        return {"cleaned": cleaned, "container_id_sha256": hashlib.sha256(self.container_id.encode()).hexdigest()}

    def handle_rpc(self, request: Mapping[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation") or "")
        timeout = float(request.get("timeout_sec") or 60)
        if operation == "exec_argv":
            result = self.exec_argv(
                list(request.get("argv") or []), timeout=timeout,
                extra_env=request.get("extra_env") or {},
                cwd=str(request.get("cwd") or "/app"),
            )
        elif operation == "write_text":
            result = self.write_text(str(request.get("path") or ""), str(request.get("text") or ""))
        elif operation == "read_text":
            result = self.read_text(str(request.get("path") or ""))
        elif operation == "exists":
            result = self.exists(str(request.get("path") or ""))
        elif operation == "artifact_metadata":
            result = self.artifact_metadata(str(request.get("path") or ""))
        else:
            result = {"ok": False, "error_type": "task_runtime_operation_unknown"}
        self.rpc_records.append({"request": dict(request), "result": result})
        return result
