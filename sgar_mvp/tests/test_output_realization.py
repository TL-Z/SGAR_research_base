import unittest

from sgar_mvp.src.output_realization import OutputRealizer, prove_output_reachability
from sgar_mvp.src.pipeline_control import canonical_sha256


class OutputRealizationTests(unittest.TestCase):
    def test_exact_text_contract_is_realizable(self) -> None:
        source_contract = {
            "artifact_type": "text",
            "description": "Read text output",
        }
        target_contract = dict(source_contract)
        proof, contract = prove_output_reachability(
            resource_id="tool.example.v1",
            capability_operation_id="tool.example.v1::read_text",
            entrypoint_id="invoke",
            resource_native_output_contract=source_contract,
            target_output_contract=target_contract,
        )

        self.assertEqual(proof.compatibility, "exact")
        self.assertIsNotNone(contract)
        content = "tool result"
        result = OutputRealizer().realize(
            resource_call_id="call-1",
            resource_id="tool.example.v1",
            operation_id="tool.example.v1::read_text",
            native_value=content,
            native_content=content,
            native_output_sha256=canonical_sha256(content),
            semantic_view_available=False,
            source_contract=source_contract,
            target_contract=target_contract,
            contract=contract,
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.realized_value, content)
        self.assertEqual(result.presentation, content)


if __name__ == "__main__":
    unittest.main()
