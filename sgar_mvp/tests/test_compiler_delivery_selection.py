import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[2]


class CompilerSelectionPolicyTest(unittest.TestCase):
    def test_prompt_has_generic_cost_and_delivery_policy(self) -> None:
        from sgar_mvp.src.plan_compiler import PLAN_COMPILER_SYSTEM_PROMPT_V3

        self.assertNotIn("Astra", PLAN_COMPILER_SYSTEM_PROMPT_V3)
        self.assertNotIn("gpt_6_astra", PLAN_COMPILER_SYSTEM_PROMPT_V3)
        self.assertIn("Do not privilege any named model", PLAN_COMPILER_SYSTEM_PROMPT_V3)
        self.assertIn("physical delivery", PLAN_COMPILER_SYSTEM_PROMPT_V3)
        self.assertIn("smallest static DAG", PLAN_COMPILER_SYSTEM_PROMPT_V3)
        self.assertIn("exact model_pricing", PLAN_COMPILER_SYSTEM_PROMPT_V3)

    def test_file_writer_declares_typed_deterministic_materialization(self) -> None:
        from sgar_mvp.src.capability_cards import build_capability_card
        from sgar_mvp.src.retrieval_runtime import (
            _contract_delivery_candidate_ids,
            _manifest_covers_file_delivery,
        )

        manifest_path = (
            ROOT
            / "Pool/resources/tools/registry/mcp_server/mcp.fs_write_file.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(
            _manifest_covers_file_delivery(
                manifest,
                path="/app/result.txt",
                artifact_type="plaintext",
            )
        )
        self.assertFalse(
            _manifest_covers_file_delivery(
                manifest,
                path="/etc/result.txt",
                artifact_type="plaintext",
            )
        )
        operations = build_capability_card(manifest).capability_operations
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].execution_operation_kind, "write_file")
        self.assertEqual(operations[0].determinism, "deterministic")
        self.assertEqual(operations[0].side_effects, "declared")
        self.assertEqual(operations[0].produced_artifact_types, ["json"])
        contract = SimpleNamespace(
            produced_files=(
                SimpleNamespace(
                    path_hint="/app/result.txt",
                    artifact_type="plaintext",
                    required=True,
                ),
            )
        )
        rankings = [(SimpleNamespace(id=manifest["resource_id"]), 0.1, 0.1, None, 89)]
        self.assertEqual(
            _contract_delivery_candidate_ids(
                contract,
                rankings,
                {manifest["resource_id"]: manifest},
            ),
            (manifest["resource_id"],),
        )


class ExternalWriteAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_python_resource_is_staged_and_host_path_is_not_executed(self) -> None:
        import os

        from sgar_mvp.src.external_worker_runtime import JsonLinesExecutionSubstrate

        root = ROOT.resolve()
        os.environ["SGAR_PROJECT_ROOT"] = str(root)
        runtime = JsonLinesExecutionSubstrate(
            input_stream=None,
            output_stream=None,
            run_id="run",
            trial_id="trial",
        )
        calls = []

        def call(operation, **arguments):
            calls.append((operation, arguments))
            if operation == "exec_argv":
                return {"ok": True, "return_code": 0, "stdout": "{}", "stderr": ""}
            return {"ok": True, "return_code": 0}

        runtime.call = MagicMock(side_effect=call)
        operation = SimpleNamespace(capability_operation_id="tool::invoke")
        request = SimpleNamespace(
            resource_definition=SimpleNamespace(
                entrypoint=lambda _: SimpleNamespace(
                    dispatch="file://Pool/resources/tools/script/python_script_runner.py"
                )
            ),
            entrypoint_id="invoke",
            call_id="call",
            logical_step_id="step",
            depends_on=(),
            resolved_bindings={},
        )
        prepared = SimpleNamespace(
            command="/ssd/zhoutianle/envs/sgar/bin/python",
            args=(str(root / "Pool/resources/tools/script/python_script_runner.py"),),
            timeout_sec=30,
            network_required=False,
            extra_env={},
        )
        argv, _ = runtime._container_exec_request(prepared, request)
        assert argv[0] == "python3"
        assert argv[1].startswith("/tmp/sgar-resource-runtime/")
        assert str(root) not in " ".join(argv)
        assert any(item[0] == "write_text" for item in calls)

    async def test_write_file_operation_uses_task_container_rpc(self) -> None:
        from sgar_mvp.src.external_worker_runtime import JsonLinesExecutionSubstrate

        runtime = JsonLinesExecutionSubstrate(
            input_stream=None,
            output_stream=None,
            run_id="run",
            trial_id="trial",
        )
        calls = []

        def call(operation, **arguments):
            calls.append((operation, arguments))
            if operation == "write_text":
                return {"ok": True, "return_code": 0, "duration_ms": 2}
            if operation == "artifact_metadata":
                return {
                    "ok": True,
                    "content_sha256": "a" * 64,
                    "byte_size": 7,
                }
            raise AssertionError(f"unexpected operation: {operation}")

        runtime.call = MagicMock(side_effect=call)
        operation = SimpleNamespace(
            capability_operation_id="example.tool::write",
            execution_operation_kind="write_file",
        )
        request = SimpleNamespace(
            resource_definition=SimpleNamespace(
                resource_id="example.tool",
                capability_card=SimpleNamespace(capability_operations=(operation,)),
            ),
            capability_operation_id=operation.capability_operation_id,
            resolved_bindings={"path": "/app/result.txt", "content": "payload"},
            call_id="call",
            target_output_contract={"artifact_type": "plaintext"},
            execution_context=SimpleNamespace(step_id="write"),
        )
        prepared = SimpleNamespace(
            sandbox_scope=None,
            artifact_adapter=None,
            network_required=False,
            extra_env={},
            execution_substrate_mode="external",
            command="python",
            args=("unused.py",),
            timeout_sec=30,
        )

        result = await runtime.execute(prepared=prepared, request=request)

        self.assertTrue(result.is_success)
        self.assertEqual([item[0] for item in calls], ["write_text", "artifact_metadata"])
        payload = json.loads(result.output_data)
        self.assertEqual(payload["status"], "success")
        handles = result.cost_metric["external_artifact_handles"]
        self.assertEqual(handles[0]["tool_path"], "/app/result.txt")
        self.assertEqual(handles[0]["artifact_type"], "file")

    async def test_write_file_fails_closed_without_artifact_metadata(self) -> None:
        from sgar_mvp.src.external_worker_runtime import JsonLinesExecutionSubstrate

        runtime = JsonLinesExecutionSubstrate(
            input_stream=None,
            output_stream=None,
            run_id="run",
            trial_id="trial",
        )
        runtime.call = MagicMock(
            side_effect=(
                {"ok": True, "return_code": 0},
                {"ok": False, "return_code": 1, "stderr": "metadata failed"},
            )
        )
        operation = SimpleNamespace(
            capability_operation_id="example.tool::write",
            execution_operation_kind="write_file",
        )
        request = SimpleNamespace(
            resource_definition=SimpleNamespace(
                resource_id="example.tool",
                capability_card=SimpleNamespace(capability_operations=(operation,)),
            ),
            capability_operation_id=operation.capability_operation_id,
            resolved_bindings={"path": "/app/result.txt", "content": "payload"},
            call_id="call",
            target_output_contract={"artifact_type": "json"},
            execution_context=SimpleNamespace(step_id="write"),
        )
        prepared = SimpleNamespace(
            sandbox_scope=None,
            artifact_adapter=None,
            network_required=False,
            extra_env={},
            execution_substrate_mode="external",
            command="python",
            args=("unused.py",),
            timeout_sec=30,
        )

        result = await runtime.execute(prepared=prepared, request=request)

        self.assertFalse(result.is_success)
        self.assertEqual(
            result.cost_metric["failure"]["failure_code"],
            "EXTERNAL_ARTIFACT_METADATA_UNAVAILABLE",
        )


if __name__ == "__main__":
    unittest.main()
