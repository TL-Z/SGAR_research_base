import unittest

from sgar_mvp.src.pipeline_control import CandidateOrigin, CandidateResourceRef


class CandidateOriginTests(unittest.TestCase):
    def test_user_execution_requirement_is_top_level(self):
        ref = CandidateResourceRef(
            resource_id="agent.example.v1",
            resource_type="Agent",
            origin=CandidateOrigin.USER_EXECUTION_REQUIREMENT,
        )
        self.assertIsNone(ref.required_by_resource_id)

    def test_dependency_origin_requires_parent(self):
        with self.assertRaises(ValueError):
            CandidateResourceRef(
                resource_id="tool.example.v1",
                resource_type="Tool",
                origin=CandidateOrigin.EXPLICIT_DEPENDENCY,
            )


if __name__ == "__main__":
    unittest.main()
