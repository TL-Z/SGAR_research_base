#!/usr/bin/env python3
"""Build the RC1 readiness report and effective resource catalogs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SGAR_ROOT = PROJECT_ROOT / "sgar_mvp"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.resource_readiness import RC1ReadinessBuilder, markdown_report
from sgar_mvp.src.capability_cards import build_capability_consistency_report


JSON_DIR = PROJECT_ROOT / "Pool" / "resources" / "json"
DEFAULT_REPORT = SGAR_ROOT / "config" / "resource_readiness_rc1.json"
DEFAULT_TOOL_SMOKE = SGAR_ROOT / "execution_outputs" / "tool_smoke" / "tool_smoke_latest.json"
DEFAULT_MODEL_HEALTH = SGAR_ROOT / "runtime_state" / "model_ready_state.json"
DEFAULT_AGENT_SMOKE = SGAR_ROOT / "execution_outputs" / "agent_smoke" / "agent_smoke_latest.json"
DEFAULT_RUNTIME_LOCK = SGAR_ROOT / "config" / "rc1_runtime_lock.json"
DEFAULT_MARKDOWN = PROJECT_ROOT / "docs" / "verification_report" / "rc1_resource_pool_report.md"


def load_optional(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Evidence must be a JSON object: {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=JSON_DIR / "combine.json")
    parser.add_argument("--tool-smoke", type=Path, default=DEFAULT_TOOL_SMOKE)
    parser.add_argument("--model-health", type=Path, default=DEFAULT_MODEL_HEALTH)
    parser.add_argument("--agent-smoke", type=Path, default=DEFAULT_AGENT_SMOKE)
    parser.add_argument("--runtime-lock", type=Path, default=DEFAULT_RUNTIME_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--fail-if-not-final", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    if not isinstance(catalog, list):
        raise ValueError("Catalog must be an array")
    from sgar_mvp.src.model_selection import require_registered_models
    require_registered_models(catalog, root=PROJECT_ROOT, complete=True)
    consistency = build_capability_consistency_report(
        {str(item["resource_id"]): item for item in catalog}
    )
    if consistency.sealable_resource_count != consistency.resource_count:
        raise ValueError("resource_catalog_execution_or_language_gate_failed")
    from sgar_mvp.src.model_selection import validated_candidate_ready_ids, require_admitted_candidate_models
    model_health = load_optional(args.model_health)
    validated_candidate_ready_ids(model_health, root=PROJECT_ROOT)
    builder = RC1ReadinessBuilder(
        PROJECT_ROOT,
        catalog,
        tool_smoke=load_optional(args.tool_smoke),
        model_health=model_health,
        agent_smoke=load_optional(args.agent_smoke),
        runtime_lock=load_optional(args.runtime_lock),
    )
    report, effective = builder.build()
    require_admitted_candidate_models(effective, model_health, root=PROJECT_ROOT)
    report["evidence"]["model_health_sha256"] = model_health["health_sha256"]
    report["evidence"]["model_health_endpoint_identity_sha256"] = model_health["endpoint_identity_sha256"]
    write_json(args.output, report)
    write_json(JSON_DIR / "effective_combine.json", effective)
    write_json(
        JSON_DIR / "effective_tools.json",
        [item for item in effective if item.get("resource_type") == "Tool"],
    )
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.fail_if_not_final:
        transient = any(
            item["readiness_status"] == "transient_failure" for item in report["resources"]
        )
        missing_evidence = any(
            reason.endswith("not_run") or reason in {"runtime_lock_missing", "real_smoke_not_run"}
            for item in report["resources"]
            for reason in item.get("reasons", [])
        )
        if transient or missing_evidence:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
