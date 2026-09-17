import unittest

from sgar_mvp.src.controller_tooling import (
    CONTROLLER_CALLABLE_TOOL_V1_PROTOCOL,
    CONTROLLER_CALLABLE_TOOL_V2_PROTOCOL,
    ControllerCallableToolSpecV1,
    ControllerToolingError,
    _callable_authority_projection,
    _provider_parameters,
    _provider_tool_schema,
    derive_provider_tool_name,
)
from sgar_mvp.src.executable_plan import CompilerCallableToolDecisionV1
from sgar_mvp.src.output_realization import prove_output_reachability
from sgar_mvp.src.pipeline_control import canonical_sha256


def _sealed_spec_payload(protocol: str) -> dict:
    resource_id = "tool.example.v1"
    operation_id = "tool.example.v1::read_text_file"
    entrypoint_id = "invoke"
    manifest_sha256 = "e" * 64
    input_contract = (
        {
            "name": "path",
            "kind": "file_path",
            "required": True,
            "cli_position": 1,
        },
    )
    fixed_bindings = {"path": {"artifact_handle": "public_input"}}
    dynamic_ports = ()
    native_contract = {
        "artifact_type": "text",
        "description": "Read text output",
    }
    proof, realization = prove_output_reachability(
        resource_id=resource_id,
        capability_operation_id=operation_id,
        entrypoint_id=entrypoint_id,
        resource_native_output_contract=native_contract,
        target_output_contract=native_contract,
    )
    assert realization is not None
    authority = _callable_authority_projection(
        resource_id=resource_id,
        capability_operation_id=operation_id,
        entrypoint_id=entrypoint_id,
        resource_manifest_sha256=manifest_sha256,
        operation_input_contract=input_contract,
        fixed_input_bindings=fixed_bindings,
        dynamic_input_ports=dynamic_ports,
        resource_native_output_contract=native_contract,
        controller_result_target_contract=native_contract,
        output_reachability_proof=proof,
        output_realization_contract=realization,
    )
    callable_id = canonical_sha256(authority)
    provider_tool_name = derive_provider_tool_name(
        capability_operation_id=operation_id,
        callable_id=callable_id,
        protocol=protocol,
    )
    provider_description = "Invoke sealed capability operation read_text_file."
    parameters = _provider_parameters(dynamic_ports)
    wire = _provider_tool_schema(
        provider_tool_name=provider_tool_name,
        provider_description=provider_description,
        parameters=parameters,
    )
    return {
        "protocol": protocol,
        "callable_id": callable_id,
        "provider_tool_name": provider_tool_name,
        "resource_id": resource_id,
        "capability_operation_id": operation_id,
        "capability_evidence_refs": (operation_id,),
        "entrypoint_id": entrypoint_id,
        "resource_manifest_sha256": manifest_sha256,
        "operation_input_contract": input_contract,
        "fixed_input_bindings": fixed_bindings,
        "dynamic_input_ports": dynamic_ports,
        "dynamic_input_schema_sha256": canonical_sha256(parameters),
        "resource_native_output_contract": native_contract,
        "controller_result_source_view": proof.source_view,
        "controller_result_target_contract": native_contract,
        "output_reachability_proof": proof,
        "output_realization_contract": realization,
        "provider_description": provider_description,
        "provider_tool_schema_sha256": canonical_sha256(wire),
    }


class ControllerToolingTests(unittest.TestCase):
    def test_v2_provider_name_is_readable_and_identity_bound(self) -> None:
        callable_id = "a" * 64

        name = derive_provider_tool_name(
            capability_operation_id="tool.mcp.fs_read_file.v1::read_text_file",
            callable_id=callable_id,
            protocol=CONTROLLER_CALLABLE_TOOL_V2_PROTOCOL,
        )

        self.assertEqual(name, f"sgar_read_text_file_{callable_id[:24]}")
        self.assertLessEqual(len(name), 64)

    def test_v1_provider_name_remains_reproducible(self) -> None:
        callable_id = "b" * 64

        name = derive_provider_tool_name(
            capability_operation_id="tool.example.v1::read_text_file",
            callable_id=callable_id,
            protocol=CONTROLLER_CALLABLE_TOOL_V1_PROTOCOL,
        )

        self.assertEqual(name, f"sgar_call_{callable_id[:24]}")

    def test_v1_and_v2_specs_validate_without_changing_authority(self) -> None:
        v1 = ControllerCallableToolSpecV1.model_validate(
            _sealed_spec_payload(CONTROLLER_CALLABLE_TOOL_V1_PROTOCOL)
        )
        v2 = ControllerCallableToolSpecV1.model_validate(
            _sealed_spec_payload(CONTROLLER_CALLABLE_TOOL_V2_PROTOCOL)
        )

        self.assertEqual(v1.callable_id, v2.callable_id)
        self.assertNotEqual(v1.provider_tool_name, v2.provider_tool_name)

    def test_v2_spec_rejects_legacy_or_tampered_provider_name(self) -> None:
        payload = _sealed_spec_payload(CONTROLLER_CALLABLE_TOOL_V2_PROTOCOL)
        payload["provider_tool_name"] = derive_provider_tool_name(
            capability_operation_id=payload["capability_operation_id"],
            callable_id=payload["callable_id"],
            protocol=CONTROLLER_CALLABLE_TOOL_V1_PROTOCOL,
        )

        with self.assertRaisesRegex(
            ValueError,
            "controller_provider_tool_name_mismatch",
        ):
            ControllerCallableToolSpecV1.model_validate(payload)

    def test_v2_name_changes_with_sealed_identity(self) -> None:
        first = derive_provider_tool_name(
            capability_operation_id="tool.example.v1::read_text_file",
            callable_id="1" * 64,
        )
        second = derive_provider_tool_name(
            capability_operation_id="tool.example.v1::read_text_file",
            callable_id="2" * 64,
        )

        self.assertNotEqual(first, second)

    def test_v2_slug_is_portable_and_bounded(self) -> None:
        name = derive_provider_tool_name(
            capability_operation_id=(
                "tool.example.v1::A Very Long/Operation.Name With Unsupported Characters"
            ),
            callable_id="c" * 64,
        )

        self.assertRegex(name, r"^[a-z0-9_]+$")
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.endswith("_" + "c" * 24))

    def test_unknown_protocol_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ControllerToolingError,
            "controller_callable_tool_protocol_unsupported",
        ):
            derive_provider_tool_name(
                capability_operation_id="tool.example.v1::read_text_file",
                callable_id="d" * 64,
                protocol="unknown",
            )

    def test_compiler_decision_cannot_emit_provider_identity(self) -> None:
        schema_text = str(CompilerCallableToolDecisionV1.model_json_schema())

        self.assertNotIn("provider_tool_name", schema_text)
        self.assertNotIn("callable_id", schema_text)


if __name__ == "__main__":
    unittest.main()
