"""Private pipe RPC usable from SGAR's synchronous and asynchronous code."""
from __future__ import annotations

import json
import hashlib
import os
import posixpath
import select
import threading
from pathlib import Path
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
        allowed_commands = {
            "python", "python3", "bash", "sh", "test", "node", "ruby",
            "go", "cargo", "make", "gcc", "g++", "curl", "uv", "npm", "git",
        }
        command = str(prepared.command or "")
        if command not in allowed_commands and not (
            os.path.isabs(command)
            and Path(command).name in {"python", "python3"}
        ):
            raise RuntimeInjectionError("UNBOUND_RUNTIME_PATH:unregistered_executable")
        if any(not isinstance(item, str) or "\x00" in item for item in prepared.args):
            raise RuntimeInjectionError("invalid_runtime_argv")

    @staticmethod
    def task_path(path: str) -> str:
        if (not isinstance(path, str) or "\x00" in path or "\\" in path
                or posixpath.normpath(path) != path
                or path not in {"/app", "/tmp"}
                and not path.startswith(("/app/", "/tmp/"))):
            raise RuntimeInjectionError("UNBOUND_RUNTIME_PATH:task_path_outside_runtime")
        return path

    @staticmethod
    def _project_root() -> Path:
        configured = str(os.environ.get("SGAR_PROJECT_ROOT") or "").strip()
        if configured:
            root = Path(configured).resolve()
            if root.is_dir():
                return root
        return Path.cwd().resolve()

    def _dispatch_source_path(self, request: Any) -> Path:
        definition = request.resource_definition
        entrypoint = definition.entrypoint(request.entrypoint_id)
        dispatch = str(entrypoint.dispatch or "")
        if dispatch.startswith("file://"):
            dispatch = dispatch[len("file://") :]
        source = Path(dispatch)
        if not source.is_absolute():
            source = self._project_root() / source
        source = source.resolve()
        root = self._project_root()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise WorkerProtocolError("external_resource_source_outside_project") from exc
        if not source.is_file() or source.suffix != ".py":
            raise WorkerProtocolError("external_resource_source_missing")
        return source

    def _stage_resource_source(self, request: Any) -> tuple[Path, Path]:
        """Copy a selected Python wrapper and local helpers into the task image."""

        source = self._dispatch_source_path(request)
        root = self._project_root()
        relative = source.relative_to(root).as_posix()
        stage_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
        stage_dir = f"/tmp/sgar-resource-runtime/{stage_key}"
        mkdir = self.call("exec_argv", argv=["mkdir", "-p", stage_dir], timeout_sec=30)
        if not mkdir.get("ok"):
            raise WorkerProtocolError("external_resource_stage_directory_failed")
        # Wrappers import their underscore-prefixed helper from the same
        # directory.  Staging all sibling Python files is deterministic and
        # keeps imports local without exposing a host PYTHONPATH.
        files = sorted(source.parent.glob("*.py"))
        if source not in files:
            files.append(source)
        for item in files:
            target = f"{stage_dir}/{item.name}"
            result = self.call(
                "write_text",
                path=target,
                text=item.read_text(encoding="utf-8"),
                timeout_sec=30,
            )
            if not result.get("ok"):
                raise WorkerProtocolError("external_resource_stage_write_failed")
        return source, Path(stage_dir) / source.name

    def _stage_host_file(self, path: Path, stage_dir: Path) -> str:
        root = self._project_root()
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise WorkerProtocolError("external_host_path_in_resource_argv") from exc
        if not path.is_file():
            raise WorkerProtocolError("external_resource_argument_missing")
        target = f"{stage_dir.as_posix()}/{path.name}"
        result = self.call(
            "write_text",
            path=target,
            text=path.read_text(encoding="utf-8"),
            timeout_sec=30,
        )
        if not result.get("ok"):
            raise WorkerProtocolError("external_resource_argument_stage_failed")
        return target

    def _container_exec_request(self, prepared: Any, request: Any) -> tuple[list[str], dict[str, Any]]:
        """Build an exec_argv request with no host executable or script path."""

        command_name = Path(str(prepared.command)).name
        command = "python3" if command_name in {"python", "python3"} else command_name
        raw_args = [str(item) for item in prepared.args]
        extra_env = dict(prepared.extra_env or {})
        for key, value in extra_env.items():
            text = str(value)
            if os.path.isabs(text) and not text.startswith(("/app/", "/tmp/")):
                raise WorkerProtocolError("external_host_path_in_resource_env")
        # A generated script may already be staged in the task container.  We
        # still validate *every* argument; checking argv[0] alone would let a
        # later host path escape the task namespace.
        if raw_args and raw_args[0].startswith(("/app", "/tmp")):
            for raw in raw_args:
                if raw.startswith(("/app", "/tmp")):
                    self.task_path(raw)
                elif os.path.isabs(raw):
                    raise WorkerProtocolError("external_host_path_in_resource_argv")
                elif ".." in Path(raw).parts:
                    raise WorkerProtocolError("external_path_traversal_in_resource_argv")
            return [command, *raw_args], {
                "request_call_id": request.call_id,
                "logical_step_id": getattr(request, "logical_step_id", None),
                "depends_on": list(getattr(request, "depends_on", ()) or ()),
                "bindings": dict(getattr(request, "resolved_bindings", {}) or {}),
                "network_required": bool(prepared.network_required),
                "extra_env": extra_env,
                "cwd": "/app",
            }

        source, staged_source = self._stage_resource_source(request)
        args: list[str] = []
        for raw in raw_args:
            if raw == str(source) or raw == str(source.resolve()):
                args.append(str(staged_source))
            elif os.path.isabs(raw) and raw.startswith(str(source.parent) + os.sep):
                # Sibling helper/source paths are all staged under one route.
                args.append(self._stage_host_file(Path(raw), staged_source.parent))
            elif os.path.isabs(raw) and not raw.startswith(("/app/", "/tmp/")):
                args.append(self._stage_host_file(Path(raw), staged_source.parent))
            else:
                args.append(raw)
        if not args or args[0] != str(staged_source):
            # Formal resource adapters put the wrapper as argv[0].  Refuse
            # ambiguous calls rather than executing an unstaged host path.
            raise WorkerProtocolError("external_resource_entrypoint_not_staged")
        return [command, *args], {
            "request_call_id": request.call_id,
            "logical_step_id": getattr(request, "logical_step_id", None),
            "depends_on": list(getattr(request, "depends_on", ()) or ()),
            "bindings": dict(getattr(request, "resolved_bindings", {}) or {}),
            "network_required": bool(prepared.network_required),
            "extra_env": extra_env,
            "cwd": "/app",
        }

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
        card = request.resource_definition.capability_card
        selected_operation = next(
            (
                item
                for item in (card.capability_operations if card is not None else ())
                if item.capability_operation_id == request.capability_operation_id
            ),
            None,
        )
        bindings = dict(getattr(request, "resolved_bindings", {}) or {})
        operation_kind = str(
            getattr(selected_operation, "execution_operation_kind", "") or ""
        )
        if operation_kind == "write_file":
            path = next(
                (
                    bindings[name]
                    for name in (
                        "path", "file_path", "destination_path", "destination", "target_path"
                    )
                    if name in bindings
                ),
                None,
            )
            content = next(
                (
                    bindings[name]
                    for name in ("content", "text", "payload", "data")
                    if name in bindings
                ),
                None,
            )
            if not isinstance(path, str) or not isinstance(content, str):
                raise WorkerProtocolError("external_write_file_binding_invalid")
            task_path = self.task_path(path)
            written = self.call(
                "write_text",
                path=task_path,
                text=content,
                timeout_sec=min(int(prepared.timeout_sec), 30),
            )
            if written.get("ok"):
                metadata = self.call("artifact_metadata", path=task_path)
                if not metadata.get("ok"):
                    result = {
                        "ok": False,
                        "return_code": metadata.get("return_code"),
                        "stdout": "",
                        "stderr": str(
                            metadata.get("stderr")
                            or "external_artifact_metadata_unavailable"
                        ),
                        "error_type": "EXTERNAL_ARTIFACT_METADATA_UNAVAILABLE",
                    }
                else:
                    semantic_payload = {
                        "status": "success",
                        "tool": request.resource_definition.resource_id,
                        "mcp_server": "external_task_substrate",
                        "mcp_tool": "write_file",
                        "result": {"is_error": False, "content": []},
                    }
                    result = {
                        "ok": True,
                        "return_code": 0,
                        "stdout": json.dumps(semantic_payload, ensure_ascii=False),
                        "stderr": "",
                        "duration_ms": written.get("duration_ms"),
                        "stdout_truncated": False,
                        "stderr_truncated": False,
                        "direct_artifacts": [
                            {
                                "path": task_path,
                                # The process result is a JSON receipt; the side
                                # effect is a task-state file whose content type is
                                # governed by the produced-file contract, not by
                                # the receipt's target output representation.
                                "artifact_type": "file",
                                "content_sha256": metadata.get("content_sha256"),
                                "byte_size": metadata.get("byte_size"),
                            }
                        ],
                    }
            else:
                result = written
        else:
            argv, metadata = self._container_exec_request(prepared, request)
            result = self.call(
                "exec_argv", argv=argv,
                timeout_sec=prepared.timeout_sec, **metadata,
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
        for index, item in enumerate(result.get("direct_artifacts") or []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            path = self.task_path(item["path"])
            artifact_handles.append({
                "handle_id": f"{self.runtime_id}:{request.call_id}:artifact:direct:{index}",
                "kind": "tool_output",
                "producer_task": self.trial_id,
                "producer_step": request.execution_context.step_id,
                "logical_path": path,
                "tool_path": path,
                "artifact_type": str(item.get("artifact_type") or "file"),
                "content_sha256": item.get("content_sha256"),
                "byte_size": item.get("byte_size"),
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
                         "resource_execution_location": "task_container",
                         "host_resource_source_executed": False,
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
