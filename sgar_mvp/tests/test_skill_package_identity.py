from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from Pool.resources.produce.sub_scripts.skills_scan_and_generate import (
    _package_files,
    _package_fingerprint,
)
from sgar_mvp.src.resource_readiness import skill_package_hash
from sgar_mvp.src.skill_package_identity import (
    SkillPackagePathCollisionError,
    skill_package_files,
    skill_package_fingerprint,
)


def _write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class SkillPackageIdentityTests(unittest.TestCase):
    def test_component_order_and_non_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            package_root = Path(temporary_directory) / "package"
            files = {
                "SKILL.md": b"main\n",
                "helper.txt": b"helper\n",
                "LICENSE": b"license\n",
                "Mixed/Child/value.txt": b"nested\n",
                "references/api/api.md": b"api\n",
                "references/api-shield/guide.md": b"shield\n",
                ".git/ignored.txt": b"ignored\n",
                "node_modules/ignored.js": b"ignored\n",
            }
            for relative, data in files.items():
                _write(package_root, relative, data)
            (package_root / "empty" / "nested").mkdir(parents=True)
            before = _snapshot(package_root)

            expected_order = [
                "helper.txt",
                "LICENSE",
                "Mixed/Child/value.txt",
                "references/api/api.md",
                "references/api-shield/guide.md",
                "SKILL.md",
            ]
            runtime_paths = [
                path.relative_to(package_root).as_posix()
                for path in skill_package_files(package_root)
            ]
            generator_paths = [
                path.relative_to(package_root).as_posix()
                for path in _package_files(package_root)
            ]
            digest, size = skill_package_fingerprint(package_root)
            generator_digest, generator_size = _package_fingerprint(package_root)

            self.assertEqual(runtime_paths, expected_order)
            self.assertEqual(generator_paths, expected_order)
            self.assertEqual(generator_digest, digest)
            self.assertEqual(generator_size, size)
            self.assertEqual(skill_package_hash(package_root), f"sha256:{digest}")
            self.assertEqual(size, sum(len(files[path]) for path in expected_order))
            self.assertEqual(_snapshot(package_root), before)

    def test_casefold_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            package_root = Path(temporary_directory) / "package"
            _write(package_root, "References/API.md", b"one")
            _write(package_root, "references/api.MD", b"two")

            with self.assertRaises(SkillPackagePathCollisionError) as raised:
                skill_package_files(package_root)

            self.assertEqual(
                set(raised.exception.paths),
                {"References/API.md", "references/api.MD"},
            )


if __name__ == "__main__":
    unittest.main()
