"""Validate every catalog Skill through ingestion and runtime identity paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Pool.resources.produce.sub_scripts.skills_scan_and_generate import (
    _package_fingerprint,
)
from sgar_mvp.src.resource_readiness import resolve_file_uri, skill_package_hash
from sgar_mvp.src.skill_runtime import SkillPackageLoader


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(catalog_path: Path) -> dict[str, Any]:
    catalog_before = _file_sha256(catalog_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, list):
        raise ValueError("catalog_must_be_list")
    loader = SkillPackageLoader(PROJECT_ROOT)
    entries: list[dict[str, Any]] = []
    for manifest in catalog:
        if manifest.get("resource_type") != "Skill":
            continue
        resource_id = str(manifest.get("resource_id") or "")
        expected = str((manifest.get("provenance") or {}).get("package_hash") or "")
        entry: dict[str, Any] = {
            "resource_id": resource_id,
            "expected_package_hash": expected,
            "readiness_package_hash": None,
            "ingestion_package_hash": None,
            "runtime_package_hash": None,
            "status": "failed",
            "error": None,
        }
        try:
            entrypoint = resolve_file_uri(
                PROJECT_ROOT,
                str((manifest.get("execution") or {}).get("uri") or ""),
            )
            readiness_hash = skill_package_hash(entrypoint.parent)
            ingestion_hash, _ = _package_fingerprint(entrypoint.parent)
            loaded = loader.load(manifest, [], verify_integrity=True)
            runtime_hash = "sha256:" + loaded.verified_package_sha256
            entry.update(
                {
                    "readiness_package_hash": readiness_hash,
                    "ingestion_package_hash": "sha256:" + ingestion_hash,
                    "runtime_package_hash": runtime_hash,
                }
            )
            if len({expected, readiness_hash, "sha256:" + ingestion_hash, runtime_hash}) != 1:
                raise ValueError("skill_package_identity_disagreement")
            entry["status"] = "passed"
        except Exception as exc:
            entry["error"] = getattr(exc, "code", None) or str(exc)
        entries.append(entry)
    catalog_after = _file_sha256(catalog_path)
    passed = sum(entry["status"] == "passed" for entry in entries)
    return {
        "protocol": "sgar-skill-hash-validation-v1",
        "catalog_path": str(catalog_path),
        "catalog_sha256_before": catalog_before,
        "catalog_sha256_after": catalog_after,
        "catalog_unchanged": catalog_before == catalog_after,
        "skill_count": len(entries),
        "passed": passed,
        "failed": len(entries) - passed,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=PROJECT_ROOT / "Pool/resources/json/combine.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(args.catalog.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["failed"] == 0 and report["catalog_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
