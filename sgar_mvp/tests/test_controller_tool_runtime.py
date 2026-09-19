import unittest

from sgar_mvp.src.controller_tool_runtime import (
    ControllerToolRuntimeError,
    _validate_dynamic_arguments,
    normalize_provider_tool_calls,
)
from sgar_mvp.src.controller_tooling import (
    ControllerCallableToolSpecV1,
    derive_provider_tool_name,
)
from sgar_mvp.src.pipeline_control import canonical_sha256


def _fixed_only_tool_spec():
    callable_id = "0" * 64
    provider_tool_name = derive_provider_tool_name(
        capability_operation_id="tool.example.v1::read_text_file",
        callable_id=callable_id,
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    wire = {
        "type": "function",
        "function": {
            "name": provider_tool_name,
            "description": "Invoke sealed fixed-input capability.",
            "parameters": parameters,
        },
    }
    return ControllerCallableToolSpecV1.model_construct(
        callable_id=callable_id,
        provider_tool_name=provider_tool_name,
        capability_operation_id="tool.example.v1::read_text_file",
        provider_description="Invoke sealed fixed-input capability.",
        provider_tool_schema_sha256=canonical_sha256(wire),
        dynamic_input_ports=(),
        dynamic_input_schema_sha256=canonical_sha256(parameters),
        fixed_input_bindings={"path": {"artifact_handle": "public_input"}},
    )


class ControllerToolRuntimeTests(unittest.TestCase):
    def test_fixed_only_callable_accepts_empty_dynamic_arguments(self) -> None:
        spec = _fixed_only_tool_spec()

        _validate_dynamic_arguments({}, spec)

    def test_fixed_only_callable_rejects_fixed_argument_override(self) -> None:
        spec = _fixed_only_tool_spec()

        with self.assertRaisesRegex(
            ControllerToolRuntimeError,
            "controller_tool_fixed_argument_override",
        ):
            _validate_dynamic_arguments({"path": "unauthorized"}, spec)

    def test_fixed_only_provider_call_normalizes_empty_arguments(self) -> None:
        spec = _fixed_only_tool_spec()

        calls = normalize_provider_tool_calls(
            session_id="session",
            turn_id="turn",
            raw_tool_calls=(
                {
                    "id": "provider-call",
                    "function": {
                        "name": spec.provider_tool_name,
                        "arguments": "{}",
                    },
                },
            ),
            callable_tools=(spec,),
        )

        self.assertEqual(calls[0].normalized_dynamic_arguments, {})
        self.assertEqual(calls[0].provider_tool_call_id, "provider-call")

    def test_semantic_provider_alias_is_canonicalized(self) -> None:
        spec = _fixed_only_tool_spec()
        calls = normalize_provider_tool_calls(
            session_id="session",
            turn_id="turn",
            raw_tool_calls=(
                {
                    "id": "provider-call",
                    "function": {
                        "name": "read_text_file",
                        "arguments": "{}",
                    },
                },
            ),
            callable_tools=(spec,),
        )
        self.assertEqual(calls[0].provider_tool_name, spec.provider_tool_name)

    def test_unsealed_operation_identifier_is_still_rejected(self) -> None:
        spec = _fixed_only_tool_spec()
        with self.assertRaisesRegex(
            ControllerToolRuntimeError,
            "controller_tool_call_not_authorized",
        ):
            normalize_provider_tool_calls(
                session_id="session",
                turn_id="turn",
                raw_tool_calls=(
                    {
                        "id": "provider-call",
                        "function": {
                            "name": "tool.example.v1::read_text_file",
                            "arguments": "{}",
                        },
                    },
                ),
                callable_tools=(spec,),
            )


if __name__ == "__main__":
    unittest.main()
