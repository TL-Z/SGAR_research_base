"""Merge a bounded targeted Model probe into the current candidate ready state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sgar_mvp.src.atomic_io import temporary_sibling_path
from sgar_mvp.src.model_selection import candidate_health_manifests
from sgar_mvp.src.pipeline_control import canonical_sha256


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("model_health_root_invalid")
    return value


def _validate_health(value: Mapping[str, Any], *, code: str) -> None:
    unsigned = dict(value)
    claimed = unsigned.pop("health_sha256", None)
    if claimed != canonical_sha256(unsigned):
        raise ValueError(f"{code}_hash_mismatch")
    if value.get("schema_version") != 6:
        raise ValueError(f"{code}_protocol_invalid")


def merge(base: Mapping[str, Any], targeted: Mapping[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    _validate_health(base, code="base_health")
    _validate_health(targeted, code="targeted_health")
    if base.get("endpoint_identity_sha256") != targeted.get("endpoint_identity_sha256"):
        raise ValueError("targeted_health_endpoint_mismatch")
    candidates = candidate_health_manifests(catalog, root=ROOT)
    expected = {
        str(item["resource_id"]): str((item.get("execution") or {}).get("model_id") or "")
        for item in candidates
    }
    if targeted.get("candidate_catalog_sha256") != canonical_sha256(candidates):
        raise ValueError("targeted_health_candidate_identity_mismatch")
    base_rows = {str(row.get("resource_id")): dict(row) for row in base.get("models") or ()}
    targeted_rows = {
        str(row.get("resource_id")): dict(row) for row in targeted.get("models") or ()
    }
    expected_ids = set(expected)
    base_ids = set(base_rows)
    targeted_ids = set(targeted_rows)
    added_ids = expected_ids - base_ids
    retired_ids = base_ids - expected_ids
    if not targeted_rows or targeted_ids - expected_ids:
        raise ValueError("targeted_health_population_invalid")
    if added_ids - targeted_ids:
        raise ValueError("targeted_health_added_population_unprobed")
    combined = {
        resource_id: row
        for resource_id, row in base_rows.items()
        if resource_id in expected_ids
    }
    combined.update(targeted_rows)
    if set(combined) != expected_ids:
        raise ValueError("targeted_health_population_incomplete")
    for resource_id, row in combined.items():
        if row.get("model_id") != expected[resource_id]:
            raise ValueError("targeted_health_wire_identity_mismatch")
    models = [combined[resource_id] for resource_id in sorted(combined)]
    unavailable = sorted({
        identifier
        for row in models
        if row.get("status") == "unavailable"
        for identifier in (str(row["resource_id"]), str(row["model_id"]))
    })
    ready = [row for row in models if (row.get("ready_state") or {}).get("status") == "ready"]
    result = {
        "schema_version": 6,
        "ready_state_protocol": "sgar-model-ready-state-v1",
        "generated_at": targeted["generated_at"],
        "generated_at_epoch": targeted["generated_at_epoch"],
        "expires_at_epoch": targeted["expires_at_epoch"],
        "endpoint_identity_sha256": targeted["endpoint_identity_sha256"],
        "base_url": targeted["base_url"],
        "catalog": targeted["catalog"],
        "candidate_catalog_sha256": targeted["candidate_catalog_sha256"],
        "probe_policy": {
            "mode": "targeted_merge",
            "targeted_probe_policy": targeted.get("probe_policy"),
            "preserved_evidence_policy": "same endpoint and unchanged resource/api identity",
        },
        "summary": {
            "catalog_total": len(expected),
            "probed": len(targeted_rows),
            "preserved": len(models) - len(targeted_rows),
            "ok": sum(row.get("status") == "ok" for row in models),
            "unavailable": sum(row.get("status") == "unavailable" for row in models),
            "blocked": sum(row.get("status") == "blocked" for row in models),
            "transient_failure": sum(row.get("status") == "transient_failure" for row in models),
            "text_ok": sum(bool(row.get("text_ok")) for row in models),
            "ready": len(ready),
            "not_ready": len(models) - len(ready),
            "by_ready_status": {
                status: sum((row.get("ready_state") or {}).get("status") == status for row in models)
                for status in ("ready", "blocked", "unavailable", "transient_failure")
            },
        },
        "unavailable_model_ids": unavailable,
        "models": models,
        "revision_provenance": {
            "operation": "targeted_candidate_population_merge",
            "parent_health_sha256": base["health_sha256"],
            "targeted_health_sha256": targeted["health_sha256"],
            "target_resource_ids": sorted(targeted_rows),
            "added_resource_ids": sorted(added_ids),
            "retired_resource_ids": sorted(retired_ids),
            "preserved_resource_ids": sorted(expected_ids - targeted_ids),
        },
    }
    result["health_sha256"] = canonical_sha256(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--targeted", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "Pool/resources/json/models.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-base-sha256", required=True)
    args = parser.parse_args()
    base = _read(args.base)
    if base.get("health_sha256") != args.expected_base_sha256:
        raise ValueError("base_health_compare_and_swap_mismatch")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    if not isinstance(catalog, list):
        raise ValueError("model_catalog_invalid")
    result = merge(base, _read(args.targeted), catalog)
    temporary = temporary_sibling_path(args.output)
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"health_sha256": result["health_sha256"], "summary": result["summary"]}, indent=2))


if __name__ == "__main__":
    main()
