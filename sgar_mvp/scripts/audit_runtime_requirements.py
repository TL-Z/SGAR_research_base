#!/usr/bin/env python3
"""Audit resource manifests for runtime dependency metadata.

The script is read-only. It reports resources that would benefit from explicit
runtime_requirements or related dependency fields, without modifying manifests.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Iterable, List


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _as_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("resources", "items", "data"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def _resource_type(item: Dict[str, Any]) -> str:
    return str(item.get("resource_type") or item.get("type", {}).get("resource_type") or "")


def _resource_id(item: Dict[str, Any]) -> str:
    return str(item.get("resource_id") or item.get("id") or "<missing_id>")


def _env_requirements(item: Dict[str, Any]) -> List[str]:
    constraint = item.get("constraint") if isinstance(item.get("constraint"), dict) else {}
    raw = constraint.get("env_requirements") or []
    return [str(value) for value in raw] if isinstance(raw, list) else [str(raw)]


def _looks_natural_language(values: Iterable[str]) -> bool:
    markers = {"python >=", "workspace", "case_workspace", "subprocess", "json", "urllib"}
    normalized = [value.strip().lower() for value in values if str(value).strip()]
    if not normalized:
        return False
    return all(
        any(marker in value for marker in markers) or " " in value
        for value in normalized
    )


def audit(resources_dir: str) -> Dict[str, List[str]]:
    json_dir = os.path.join(resources_dir, "json")
    filenames = [
        "tools.json",
        "models.json",
        "agents.json",
        "skills.json",
        "resources.json",
        "device.json",
    ]
    report: Dict[str, List[str]] = {
        "tool_missing_runtime_requirements": [],
        "tool_natural_language_env_requirements": [],
        "agent_missing_base_model_or_dependencies": [],
        "skill_executable_missing_runtime_requirements": [],
        "resource_missing_parser_profile": [],
        "model_missing_api_capability_metadata": [],
    }
    for filename in filenames:
        path = os.path.join(json_dir, filename)
        if not os.path.isfile(path):
            continue
        for item in _as_items(_load_json(path)):
            rid = _resource_id(item)
            rtype = _resource_type(item)
            runtime_requirements = item.get("runtime_requirements")
            execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
            type_specific = item.get("type_specific") if isinstance(item.get("type_specific"), dict) else {}
            if rtype == "Tool":
                if not isinstance(runtime_requirements, dict):
                    report["tool_missing_runtime_requirements"].append(rid)
                if _looks_natural_language(_env_requirements(item)):
                    report["tool_natural_language_env_requirements"].append(rid)
            elif rtype in {"Agent", "MAS"}:
                has_base = bool(execution.get("default_base_model") or execution.get("base_model"))
                has_deps = bool(item.get("requires_tools") or item.get("requires_models") or item.get("dependencies"))
                if not has_base or not has_deps:
                    report["agent_missing_base_model_or_dependencies"].append(rid)
            elif rtype == "Skill":
                if execution.get("uri") and not isinstance(runtime_requirements, dict):
                    report["skill_executable_missing_runtime_requirements"].append(rid)
            elif rtype == "Resource":
                resource_block = type_specific.get("resource") if isinstance(type_specific.get("resource"), dict) else {}
                if not (item.get("parser_profile") or resource_block.get("parser_profile")):
                    report["resource_missing_parser_profile"].append(rid)
            elif rtype == "Model":
                model_block = type_specific.get("model") if isinstance(type_specific.get("model"), dict) else {}
                capability_keys = {
                    "temperature_supported",
                    "json_mode_supported",
                    "stream_supported",
                    "vision_supported",
                    "tool_calling_supported",
                    "max_context",
                    "context_window",
                }
                if not any(key in model_block or key in execution for key in capability_keys):
                    report["model_missing_api_capability_metadata"].append(rid)
    return {key: sorted(values) for key, values in report.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resources-dir",
        default="Pool/resources",
        help="Path to Pool/resources directory.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    args = parser.parse_args()
    report = audit(os.path.abspath(args.resources_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for key, values in report.items():
        print(f"{key}: {len(values)}")
        for rid in values[:20]:
            print(f"  - {rid}")
        if len(values) > 20:
            print(f"  ... {len(values) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

