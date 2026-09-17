"""Execution-boundary tests independent of Planner/Retrieval semantics."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sgar_mvp.src.runtime_abstraction import (
    RuntimeInjectionError,
    require_execution_substrate,
    runtime_binding,
    require_legacy_runtime,
)


class RuntimeInjectionContractTest(unittest.TestCase):
    def test_default_mode_allows_legacy_runtime_absence(self):
        self.assertIsNone(require_execution_substrate(None, mode="default"))

    def test_external_mode_requires_bound_runtime(self):
        with self.assertRaisesRegex(RuntimeInjectionError, "UNBOUND_RUNTIME_PATH"):
            require_execution_substrate(None, mode="external")

    def test_injected_runtime_requires_execute(self):
        with self.assertRaises(RuntimeInjectionError):
            require_execution_substrate(object(), mode="external")

    def test_injected_runtime_is_method_agnostic(self):
        class Runtime:
            runtime_id = "test-runtime"

            async def execute(self, *, prepared, request):
                return None

        runtime = Runtime()
        self.assertIs(require_execution_substrate(runtime, mode="external"), runtime)


class ProviderRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_host_and_docker_branches_dispatch_once(self):
        from sgar_mvp.src.executors import ExecutionResult
        from sgar_mvp.src.tool_execution_provider import PreparedToolDispatch, ToolExecutionProvider
        for profile, expected in (("host-python-stdlib", "HostPythonExecutor"), ("sgar-runtime", "DumbExecutor")):
            with self.subTest(profile=profile):
                prepared = PreparedToolDispatch(
                    subtask_description="probe", context_data="", dispatch_locator="probe",
                    command="python3", args=("probe.py",), runtime_profile=profile,
                )
                provider = ToolExecutionProvider(prepared)
                request = SimpleNamespace(
                    resource_definition=SimpleNamespace(resource_id="probe", entrypoint=lambda _: SimpleNamespace(dispatch="probe")),
                    entrypoint_id="invoke", execution_world=provider.execution_world,
                    call_id="probe-call", output_contract={"artifact_type": "plaintext"},
                    execution_context=SimpleNamespace(run_id="probe", step_id="step", attempt=1),
                )
                execution = AsyncMock(return_value=ExecutionResult(is_success=True, output_data="probe"))
                other = "DumbExecutor" if expected == "HostPythonExecutor" else "HostPythonExecutor"
                prefix = "sgar_mvp.src.tool_execution_provider."
                with patch(prefix + expected) as factory, patch(prefix + other, side_effect=AssertionError("wrong default executor")):
                    factory.return_value.execute = execution
                    result = await provider(request)
                self.assertEqual(result.presentation, "probe")
                execution.assert_awaited_once()

    async def test_host_scope_rejected_before_inventory_or_executor(self):
        from sgar_mvp.src.external_worker_runtime import JsonLinesExecutionSubstrate
        from sgar_mvp.src.tool_execution_provider import PreparedToolDispatch, ToolExecutionProvider
        runtime = JsonLinesExecutionSubstrate(input_stream=None, output_stream=None, run_id="test", trial_id="trial")
        prepared = PreparedToolDispatch(subtask_description="", context_data="", dispatch_locator="x",
                                        command="python3", args=(), execution_substrate_mode="external",
                                        sandbox_scope={"writable_root": {"host_path": "/must-not-read"}})
        with patch("sgar_mvp.src.tool_execution_provider._workspace_inventory", side_effect=AssertionError("host IO")):
            with self.assertRaisesRegex(RuntimeInjectionError, "host_scope"):
                ToolExecutionProvider(prepared, execution_substrate=runtime)

    async def test_runtime_binding_restores_default_after_failure(self):
        runtime = SimpleNamespace(execute=lambda **kwargs: None)
        with self.assertRaises(RuntimeInjectionError):
            with runtime_binding(runtime, mode="external"):
                require_legacy_runtime("test")
        require_legacy_runtime("test")

    async def test_public_pipeline_rejects_unbound_scope_before_config_or_calls(self):
        from sgar_mvp.main import run_pipeline
        class BrokenRuntime:
            runtime_id = "broken"

            async def execute(self, *, prepared, request):
                return None

            def require_pipeline_ready(self):
                raise RuntimeInjectionError(
                    "UNBOUND_RUNTIME_PATH:sealed_per_step_scope_not_mapped_to_shared_task_environment"
                )

        with patch("sgar_mvp.main.RunCostLedger", side_effect=AssertionError("entered pipeline")):
            with self.assertRaisesRegex(RuntimeInjectionError, "sealed_per_step_scope"):
                await run_pipeline(
                    {}, "probe", "/unused", "/unused/report",
                    execution_substrate=BrokenRuntime(),
                    execution_substrate_mode="external",
                )


if __name__ == "__main__":
    unittest.main()
