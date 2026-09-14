"""Reviewed retrieval metadata overlays; vendored instruction bytes stay unchanged."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

OVERRIDES = Path(__file__).with_name("skill_semantic_overrides.json")


def apply_skill_semantics(manifest: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(manifest)
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    entry = overrides.get(str(result.get("resource_id")))
    if entry is None:
        return result
    if result.get("provenance", {}).get("source_hash") != entry["source_hash"]:
        raise ValueError(f"skill_semantic_source_changed:{result.get('resource_id')}")
    allowed = {"summary", "core_primitives", "domain_tags"}
    if set(entry.get("capability", {})) - allowed:
        raise ValueError("skill_semantic_override_field_forbidden")
    result["capability"].update(entry["capability"])
    if "type" in result:
        result["type"]["resource_tag"] = list(result["capability"]["domain_tags"])
    if "workflow_hint" in entry:
        result["type_specific"]["skill"]["workflow_hint"] = list(entry["workflow_hint"])
    if "recommended_roles" in entry:
        result["type_specific"]["skill"]["recommended_roles"] = list(entry["recommended_roles"])
    result["provenance"]["semantic_review"] = {
        "policy": entry.get("policy", "reviewed-skill-semantics-v1"),
        "source_hash": entry["source_hash"],
        "basis": entry["basis"],
    }
    return result
