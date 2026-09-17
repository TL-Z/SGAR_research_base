import hashlib
import tempfile
import unittest
from pathlib import Path

from sgar_mvp.src.executors import DumbExecutor
from sgar_mvp.src.public_inputs import inspect_internal_metadata_layout


class ExternalSandboxScopeTests(unittest.TestCase):
    def test_external_writable_accepts_project_public_input(self) -> None:
        executor = DumbExecutor()
        project = Path(executor.project_root)
        public_input = project / "maintenance_fixtures" / "v2_material.json"
        digest = hashlib.sha256(public_input.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root).resolve()
            writable = workspace / "work" / "attempt-1"
            writable.mkdir(parents=True)
            hidden_roots = [
                {
                    "host_path": str(path),
                    "runtime_path": "/app/" + path.relative_to(project).as_posix(),
                    "path_kind": "directory",
                }
                for path in inspect_internal_metadata_layout(project).hidden_roots
            ]
            scope = {
                "protocol": "sgar-sandbox-scope/v1",
                "runtime_roots": [
                    {
                        "host_path": str(project / "sgar_mvp"),
                        "runtime_path": "/app/sgar_mvp",
                        "path_kind": "directory",
                    },
                    {
                        "host_path": str(project / "Pool" / "resources" / "tools"),
                        "runtime_path": "/app/Pool/resources/tools",
                        "path_kind": "directory",
                    },
                ],
                "public_inputs": [
                    {
                        "host_path": str(public_input),
                        "runtime_path": "/app/maintenance_fixtures/v2_material.json",
                        "path_kind": "file",
                        "sha256": digest,
                    }
                ],
                "writable_root": {
                    "host_path": str(writable),
                    "runtime_path": "/app/run/work/attempt-1",
                    "path_kind": "directory",
                },
                "masked_roots": [
                    {
                        "host_path": str(workspace),
                        "runtime_path": "/app/run",
                        "path_kind": "directory",
                    }
                ],
                "hidden_roots": hidden_roots,
                "working_directory": "/app/run/work/attempt-1",
                "allow_legacy_shell": False,
            }

            normalized = executor._normalize_sandbox_scope(scope)

        self.assertEqual(
            normalized["writable_root"]["runtime_path"],
            "/app/run/work/attempt-1",
        )


if __name__ == "__main__":
    unittest.main()
