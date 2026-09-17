from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = Path("/ssd/zhoutianle/runtime/sgar/maintenance/resource-hash-readiness-composition-20260916")
RUN = OUT / "composition-rerun" / "run-fixed-2"


def load(path: Path):
    text = path.read_text()
    return json.loads(text) if text.strip() else {"status": "empty"}


def main() -> None:
    readiness = load(ROOT / "sgar_mvp/config/resource_readiness_rc1.json")
    catalog = load(ROOT / "Pool/resources/json/combine.json")
    effective_ids = {
        str(item.get("resource_id") or item.get("id"))
        for item in load(ROOT / "Pool/resources/json/effective_combine.json")
    }
    evidence = {item["resource_id"]: item for item in readiness["resources"]}
    with (OUT / "RESOURCE_STATUS_FINAL.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["resource_id", "resource_type", "catalog_status", "readiness_status", "reasons", "in_effective_pool"],
        )
        writer.writeheader()
        for item in sorted(catalog, key=lambda value: str(value.get("resource_id") or value.get("id"))):
            resource_id = str(item.get("resource_id") or item.get("id"))
            record = evidence.get(resource_id, {})
            writer.writerow(
                {
                    "resource_id": resource_id,
                    "resource_type": record.get("resource_type") or item.get("resource_type") or (item.get("type") or {}).get("resource_type", ""),
                    "catalog_status": record.get("catalog_status", item.get("status", "active")),
                    "readiness_status": record.get("readiness_status", "unknown"),
                    "reasons": ";".join(record.get("reasons") or []),
                    "in_effective_pool": resource_id in effective_ids,
                }
            )

    diagnostics = {
        "protocol": "sgar-tool-exception-diagnostics-v1",
        "network_transient": {
            path.stem: load(path)
            for path in (OUT / "tool-network-diagnostics").glob("*.json")
        },
        "dependency_cve": load(OUT / "dependency-cve-diagnostic.json"),
        "focused_smoke": load(OUT / "tool-smoke-merged.json"),
        "inactive": ["tool.eslint_code_formatter.v1", "tool.npm_package_auditor.v1"],
    }
    (OUT / "TOOL_EXCEPTION_DIAGNOSTICS.json").write_text(json.dumps(diagnostics, indent=2, ensure_ascii=False) + "\n")

    receipt = load(OUT / "control-receipt-refresh" / "validation.json")
    (OUT / "CONTROL_RECEIPT_VALIDATION.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    composition = {
        "protocol": "sgar-composition-evidence-v2",
        "offline_candidate_fix": {
            "failure": "dependency_candidate_requires_parent_resource",
            "fix": "CandidateOrigin.USER_EXECUTION_REQUIREMENT is a valid top-level candidate origin",
            "offline_positive_negative_tests": "4 passed",
        },
        "runs": [
            {
                "run_dir": str(OUT / "composition-run" / "20260915T184804Z_3bde37251fc8"),
                "terminal": "retrieval_candidate_build_framework_failure",
                "provider_calls": 2,
                "resource_runtime": False,
            },
            {
                "run_dir": str(OUT / "composition-rerun" / "run-fixed-2"),
                "terminal": "compiler_v3_input_artifact_type_incompatible",
                "provider_calls": 4,
                "selected_agent": "agent.fullstack_implementation_engineer.v1",
                "selected_base_model": "model.qwen3_5_35b_a3b.v1",
                "selected_tool": "tool.mcp.fs_read_file.v1::read_text_file",
                "required_skill": "skill.superpowers.verification-before-completion.v1",
                "resource_runtime": False,
                "tool_call_count": 0,
                "skill_injection": False,
            },
        ],
        "first_open_boundary": "Compiler rejected model plan because Skill was emitted as a standalone step and its output was not a compatible input artifact for the Agent step.",
    }
    (OUT / "COMPOSITION_EVIDENCE.json").write_text(json.dumps(composition, indent=2, ensure_ascii=False) + "\n")

    accounting = {
        "protocol": "sgar-provider-accounting-v1",
        "control_receipt_refresh_cost_usd": "0.017381250000",
        "composition_rerun_cost_usd": load(RUN / "cost_summary.json").get("observed_total_model_cost_usd"),
        "composition_initial_interrupted_cost_usd": "unknown_summary_not_persisted",
        "composition_rerun_physical_provider_sends": 4,
        "blocked_models_called": [],
        "embedding_provider_calls": "local_only_not_llm_billed",
    }
    (OUT / "PROVIDER_ACCOUNTING.json").write_text(json.dumps(accounting, indent=2, ensure_ascii=False) + "\n")

    next_command = """#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/zhoutianle/Projects/SGAR_research_base
PYTHON=/ssd/zhoutianle/envs/sgar/bin/python
REQUEST_MANIFEST=${REQUEST_MANIFEST:?set REQUEST_MANIFEST to a public request manifest}
PUBLIC_INPUT_ROOT=${PUBLIC_INPUT_ROOT:?set PUBLIC_INPUT_ROOT to an authorized public input root}
test -n \"${LLM_API_KEY:-}\" || { echo 'LLM_API_KEY is required' >&2; exit 2; }
test -n \"${SGAR_EMBEDDING_API_KEY:-}\" || { echo 'SGAR_EMBEDDING_API_KEY is required' >&2; exit 2; }
cd \"$ROOT\"
exec \"$PYTHON\" sgar_mvp/main.py \\
  --request-manifest \"$REQUEST_MANIFEST\" \\
  --public-input-root \"$PUBLIC_INPUT_ROOT\" \\
  --runtime-authority git \\
  --planner-variant resource_aware
"""
    path = OUT / "NEXT_REAL_CASE_COMMAND.sh"
    path.write_text(next_command)
    path.chmod(0o755)

    diff = OUT / "THIS_TASK_ONLY.diff"
    diff.write_text(
        "".join(
            [
                "# This task's source-only delta relative to its recorded start state.\n",
                "# Prior dirty changes are intentionally excluded.\n\n",
                "--- sgar_mvp/src/pipeline_control.py (start)\n",
                "+++ sgar_mvp/src/pipeline_control.py (current)\n",
                "@@ CandidateResourceRef._validate_origin_evidence @@\n",
                "+            CandidateOrigin.USER_EXECUTION_REQUIREMENT,\n",
            ]
        )
    )


if __name__ == "__main__":
    main()
