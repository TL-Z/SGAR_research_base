"""Private pipe RPC usable from SGAR's synchronous and asynchronous code."""
from __future__ import annotations

import json
import hashlib
import posixpath
import select
import threading
from typing import Any, TextIO

from .executors import ExecutionResult
from .runtime_abstraction import RuntimeInjectionError


class WorkerProtocolError(RuntimeInjectionError):
    pass


class JsonLinesExecutionSubstrate:
    def __init__(self, *, input_stream: TextIO, output_stream: TextIO,
                 run_id: str, trial_id: str, rpc_timeout: float = 60,
                 network_allowed: bool = False):
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.runtime_id = run_id
        self.trial_id = trial_id
        self.rpc_timeout = rpc_timeout
        self.network_allowed = bool(network_allowed)
        self.sequence = 0
        self._lock = threading.RLock()
        self.records: list[dict[str, Any]] = []

    def require_pipeline_ready(self) -> None:
        # The Harbor adapter supplies a persistent task root and artifact
        # metadata RPCs.  Native scope details never cross this boundary.
        return None

    def step_scope(
        self, *, step_id: str, depends_on: tuple[str, ...], attempt: int
    ) -> dict[str, Any]:
        del step_id, depends_on, attempt
        # The external task container owns the real filesystem namespace.  The
        # framework still needs a RuntimePathMap while preparing formal
        # bindings, whose internal representation requires host/runtime route
        # pairs.  Use a deterministic, non-existent virtual host root for that
        # private translation only; it is never mounted, read, or serialized
        # into the task container.  All actual I/O continues through this RPC
        # substrate using /app paths.
        virtual_host_root = (
            "/tmp/sgar-external-runtime/"
            + hashlib.sha256(self.runtime_id.encode("utf-8")).hexdigest()[:32]
        )
        runtime_path = "/app"
        return {
            "protocol": "sgar-external-task-scope/v1",
            "runtime_roots": [{
                "host_path": virtual_host_root,
                "runtime_path": "/app",
                "path_kind": "directory",
            }],
            "public_inputs": [],
            "masked_roots": [],
            "hidden_roots": [],
            "writable_root": {
                "host_path": virtual_host_root,
                "runtime_path": runtime_path,
                "path_kind": "directory",
            },
            "working_directory": runtime_path,
            "allow_legacy_shell": False,
        }

    def validate_dispatch(self, prepared: Any) -> None:
        scope = prepared.sandbox_scope or {}
        external_scope = str(scope.get("protocol") or "") == "sgar-external-task-scope/v1"
        if (prepared.sandbox_scope and not external_scope) or (
            prepared.artifact_adapter is not None and not external_scope
        ):
            raise RuntimeInjectionError("UNBOUND_RUNTIME_PATH:host_scope_or_artifact_callback")
        if prepared.network_required and not self.network_allowed:
            raise RuntimeInjectionError("TASK_NETWORK_POLICY_DENIED")
        if any(
            str(key).lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
            for key in (prepared.extra_env or {})
        ):
            raise RuntimeInjectionError("TASK_PROXY_OVERRIDE_FORBIDDEN")
        if prepared.execution_substrate_mode != "external":
            raise RuntimeInjectionError("runtime_mode_mismatch")
        if prepared.command not in {
            "python", "python3", "bash", "sh", "test", "node", "ruby",
            "go", "cargo", "make", "gcc", "g++", "curl", "uv", "npm", "git",
        }:
            raise RuntimeInjectionError("UNBOUND_RUNTIME_PATH:unregistered_executable")
        if any(not isinstance(item, str) or "\x00" in item for item in prepared.args):
            raise RuntimeInjectionError("invalid_runtime_argv")

    @staticmethod
    def task_path(path: str) -> str:
        if (not isinstance(path, str) or "\x00" in path or "\\" in path
                or posixpath.normpath(path) != path
                or not path.startswith(("/app/", "/tmp/"))):
            raise RuntimeInjectionError("UNBOUND_RUNTIME_PATH:task_path_outside_runtime")
        return path

    def call(self, operation: str, *, timeout_sec: int = 30, **arguments: Any) -> dict:
        with self._lock:
            self.sequence += 1
            action_id = f"{self.runtime_id}:action:{self.sequence:04d}"
            request = {
                "protocol": "sgar-external-substrate-action-v1", "run_id": self.runtime_id,
                "trial_id": self.trial_id, "action_id": action_id,
                "operation": operation, "timeout_sec": timeout_sec, **arguments,
            }
            wire = json.dumps(request, ensure_ascii=True) + "\n"
            if len(wire) > 1024 * 1024:
                raise WorkerProtocolError("worker_request_too_large")
            self.output_stream.write(wire)
            self.output_stream.flush()
            readable, _, _ = select.select(
                [self.input_stream], [], [], timeout_sec + self.rpc_timeout
            )
            if not readable:
                raise WorkerProtocolError("worker_response_timeout")
            line = self.input_stream.readline(1024 * 1024 + 1)
            if not line or len(line) > 1024 * 1024 or not line.endswith("\n"):
                raise WorkerProtocolError("worker_response_missing_or_oversized")
            response = json.loads(line)
            for key in ("run_id", "trial_id", "action_id"):
                if response.get(key) != request[key]:
                    raise WorkerProtocolError("worker_response_identity_mismatch")
            if response.get("protocol") != "sgar-external-substrate-result-v1":
                raise WorkerProtocolError("worker_response_protocol_invalid")
            result = response.get("result")
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise WorkerProtocolError("worker_result_invalid")
            self.records.append({"request": request, "result": result})
            return result

    async def execute(self, *, prepared: Any, request: Any) -> ExecutionResult:
        self.validate_dispatch(prepared)
        result = self.call(
            "exec_argv", argv=[prepared.command, *prepared.args],
            timeout_sec=prepared.timeout_sec, request_call_id=request.call_id,
            logical_step_id=getattr(request, "logical_step_id", None),
            depends_on=list(getattr(request, "depends_on", ()) or ()),
            bindings=dict(getattr(request, "resolved_bindings", {}) or {}),
            network_required=bool(prepared.network_required),
            extra_env=dict(prepared.extra_env or {}),
        )
        artifact_handles: list[dict[str, Any]] = []
        try:
            payload = json.loads(result.get("stdout") or "{}")
        except (TypeError, ValueError):
            payload = {}
        produced = payload.get("produced_files") if isinstance(payload, dict) else []
        if isinstance(produced, list):
            for index, path in enumerate(produced):
                if not isinstance(path, str):
                    continue
                metadata = self.call("artifact_metadata", path=self.task_path(path))
                if not metadata.get("ok"):
                    continue
                try:
                    metadata_payload = json.loads(metadata.get("stdout") or "{}")
                except (TypeError, ValueError):
                    continue
                artifact_handles.append({
                    "handle_id": f"{self.runtime_id}:{request.call_id}:artifact:{index}",
                    "kind": "tool_output",
                    "producer_task": self.trial_id,
                    "producer_step": getattr(request, "logical_step_id", None),
                    "logical_path": path,
                    "tool_path": path,
                    "artifact_type": "file",
                    "content_sha256": metadata_payload.get("content_sha256"),
                    "byte_size": metadata_payload.get("byte_size"),
                    "current_run": True,
                })
        failure = None if result["ok"] else {
            "responsibility": "infrastructure", "failure_stage": "execution",
            "failure_code": result.get("error_type") or "EXTERNAL_SUBSTRATE_ERROR",
            "retryable": False, "response_received": True,
        }
        return ExecutionResult(
            is_success=result["ok"], output_data=result.get("stdout") or "",
            error_log=None if result["ok"] else result.get("stderr") or str(failure),
            cost_metric={"runtime_protocol": "sgar-external-substrate-action-v1",
                         "return_code": result.get("return_code"),
                         "latency_ms": result.get("duration_ms"),
                         "stderr": result.get("stderr"),
                         "stdout_truncated": result.get("stdout_truncated"),
                         "stderr_truncated": result.get("stderr_truncated"),
                         "external_artifact_handles": artifact_handles,
                         **({"failure": failure} if failure else {})},
        )

    async def write_text(self, path: str, text: str) -> None:
        result = self.call("write_text", path=self.task_path(path), text=text)
        if not result["ok"]:
            raise WorkerProtocolError(result.get("error_type"))

    async def read_text(self, path: str) -> str:
        result = self.call("read_text", path=self.task_path(path))
        if not result["ok"] or result.get("stdout_truncated"):
            raise WorkerProtocolError(result.get("error_type") or "truncated_file")
        return result.get("stdout") or ""

    async def exists(self, path: str) -> bool:
        result = self.call("exists", path=self.task_path(path))
        if result["ok"]:
            return True
        if result.get("return_code") == 1 and result.get("error_type") == "TASK_COMMAND_NONZERO_EXIT":
            return False
        raise WorkerProtocolError(result.get("error_type"))
