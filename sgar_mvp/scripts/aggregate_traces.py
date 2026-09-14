"""
S-GAR Trace Aggregator (standalone, stdlib-only)
================================================
Parse one or more run directories (each containing trace.jsonl and/or
pipeline.log) and produce compatibility-focused executability metrics for
A/B comparison between the frozen baseline and optimized versions.

Design goals:
  * No dependency on src/ so the SAME script runs against any checkout.
  * Separate INFRA / MODEL-QUALITY / EVALUATOR-NOISE failures from the
    COMPATIBILITY (routing / artifact-flow) failures we actually study.
  * Surface the edge/attempt-level signal (retries, insufficient,
    low-advantage) that end-to-end status hides via repair/replan loops.

Usage:
    python scripts/aggregate_traces.py execution_outputs/case_sql_api_6
    python scripts/aggregate_traces.py execution_outputs/*        # glob
    python scripts/aggregate_traces.py --root execution_outputs   # all subdirs
    python scripts/aggregate_traces.py execution_outputs/* --json out.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────
# Failure taxonomy → 4 categories
# ─────────────────────────────────────────────
# COMPATIBILITY = routing / artifact-flow incompatibility (what we study & can fix)
# INFRA         = provider/runtime/quota/budget (must be excluded from the gap)
# MODEL_QUALITY = generation content quality (not a routing problem)
# EVAL_NOISE    = evaluator failed to judge (measurement artifact, exclude from denominators)

CATEGORY_RULES: List[Tuple[str, str]] = [
    # INFRA
    (r"insufficient_quota|429|provider_connection|provider_stream|model_unavailable|"
     r"runtime_warmup|runtime_image_pull|runtime_profile|dependency_install|"
     r"environment_preflight|budget", "infra"),
    # EVALUATOR NOISE
    (r"evaluator_inconclusive|evaluator_noise", "eval_noise"),
    # COMPATIBILITY (routing / artifact-flow / binding / dependency)
    (r"tool_missing_required_input|binding_invalid|operation_misuse|"
     r"artifact_lineage_mismatch|resource_dependency_missing|"
     r"artifact_dependency_missing|dependency_not_used|dependency_grounding|"
     r"binding_ambiguous|binding_missing|tool_path_mapping|runner_cwd|"
     r"tool_semantic_misuse|tool_semantic_failure|contract_produced_file_missing|"
     r"contract_code_extraction|side_artifact|validation_target|bundle_insufficient|"
     r"policy_hallucinated_resource|policy_invalid_plan|overlay_target|unsafe_execution_path",
     "compatibility"),
    # MODEL QUALITY
    (r"missing_required_content|factual_mismatch|placeholder_content|format_invalid|"
     r"contract_violation|completeness|actionability", "model_quality"),
]


def categorize(failure_type: Optional[str]) -> Optional[str]:
    if not failure_type:
        return None
    ft = str(failure_type).lower().strip()
    if ft in ("none", ""):
        return None
    for pattern, category in CATEGORY_RULES:
        if re.search(pattern, ft):
            return category
    return "uncategorized"


# ─────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────

def load_jsonl(path: str) -> List[dict]:
    events: List[dict] = []
    if not os.path.isfile(path):
        return events
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    return events


# Router-attempt signals live most reliably in pipeline.log text.
_RE_INSUFFICIENT = re.compile(r"\[Router\] Bundle insufficient for (\S+) attempt (\d+)")
_RE_LOW_ADV = re.compile(r"\[Router\] Bundle low advantage for (\S+).*?([\d.]+) < ([\d.]+)")
_RE_PASSED = re.compile(r"\[Router\] Bundle passed for (\S+) attempt (\d+)")
_RE_QUOTA = re.compile(r"insufficient_quota|Error code: 429", re.IGNORECASE)
_RE_FINAL_OK = re.compile(r"S-GAR MVP - Complete \(success", re.IGNORECASE)
_RE_FINAL_WARN = re.compile(r"success_with_warnings", re.IGNORECASE)
_RE_FINAL_FAIL = re.compile(r"S-GAR MVP - Structured Failure", re.IGNORECASE)


def parse_log(path: str) -> dict:
    out = {
        "insufficient": [],   # (subtask, attempt)
        "low_advantage": [],  # (subtask, score, threshold)
        "passed": [],         # (subtask, attempt)
        "quota_hit": False,
        "final_status": None,  # complete_success | success_with_warnings | structured_failure | None
    }
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for m in _RE_INSUFFICIENT.finditer(line):
                out["insufficient"].append((m.group(1), int(m.group(2))))
            for m in _RE_LOW_ADV.finditer(line):
                out["low_advantage"].append((m.group(1), float(m.group(2)), float(m.group(3))))
            for m in _RE_PASSED.finditer(line):
                out["passed"].append((m.group(1), int(m.group(2))))
            if _RE_QUOTA.search(line):
                out["quota_hit"] = True
            if _RE_FINAL_OK.search(line):
                out["final_status"] = "complete_success"
            elif _RE_FINAL_WARN.search(line):
                out["final_status"] = "success_with_warnings"
            elif _RE_FINAL_FAIL.search(line):
                out["final_status"] = "structured_failure"
    return out


def analyze_run(run_dir: str) -> dict:
    trace = load_jsonl(os.path.join(run_dir, "trace.jsonl"))
    log = parse_log(os.path.join(run_dir, "pipeline.log"))

    exec_events = [e for e in trace if e.get("event_type") == "execution_trace"]
    eval_events = [e for e in trace if e.get("event_type") == "evaluation_trace"]
    plan_events = [e for e in trace if e.get("event_type") == "application_plan_trace"]
    planner_events = [e for e in trace if e.get("event_type") == "planner_trace"]

    subtasks = set()
    for e in trace:
        sid = e.get("subtask_id")
        if sid:
            subtasks.add(sid)

    # Per-subtask: max attempt index = retries needed
    attempts_by_subtask: Dict[str, int] = defaultdict(int)
    for e in exec_events + plan_events:
        sid, ai = e.get("subtask_id"), e.get("attempt_index")
        if sid and isinstance(ai, int):
            attempts_by_subtask[sid] = max(attempts_by_subtask[sid], ai)

    # Execution outcomes
    exec_fail = Counter()
    exec_success = 0
    exec_total = 0
    for e in exec_events:
        exec_total += 1
        if e.get("is_success"):
            exec_success += 1
        cat = categorize(e.get("failure_type"))
        if cat:
            exec_fail[cat] += 1

    # Evaluation outcomes (final verdict per subtask = last eval event for it)
    last_eval: Dict[str, dict] = {}
    for e in eval_events:
        sid = e.get("subtask_id")
        if sid:
            last_eval[sid] = e.get("result", {}) or {}
    eval_fail = Counter()
    eval_pass = 0
    for sid, res in last_eval.items():
        if res.get("passed"):
            eval_pass += 1
        else:
            cat = categorize(res.get("failure_type"))
            if cat:
                eval_fail[cat] += 1

    # Pre-repair signal: every failing eval/exec across ALL attempts, including
    # the intermediate failures that repair/replan later masks in the final
    # verdict. This is where artifact-flow incompatibility actually surfaces.
    pre_repair_fail = Counter()
    for e in eval_events:
        res = e.get("result", {}) or {}
        if not res.get("passed"):
            cat = categorize(res.get("failure_type"))
            if cat:
                pre_repair_fail[cat] += 1
    for e in exec_events:
        if not e.get("is_success"):
            cat = categorize(e.get("failure_type"))
            if cat:
                pre_repair_fail[cat] += 1

    # Artifact-flow edges live at TWO levels:
    #  (a) inter-node: subtask depends_on subtask (the primary plan DAG)
    #  (b) intra-node: step->step where a step input consumes another step's output_key
    inter_edges = 0
    for e in planner_events:
        for st in (e.get("subtasks") or []):
            inter_edges += len(st.get("depends_on") or [])
    intra_edges = 0
    for e in plan_events:
        steps = (e.get("plan") or {}).get("steps") or []
        output_keys = {s.get("output_key") for s in steps if s.get("output_key")}
        for s in steps:
            for v in (s.get("input_bindings") or {}).values():
                # bindings may be bare ("schema_json") or namespaced ("step_1.schema_json")
                if isinstance(v, str) and any(k and k in v for k in output_keys):
                    intra_edges += 1
    edges = inter_edges + intra_edges

    # End-to-end status: prefer log label; else infer from final evals
    final_status = log["final_status"]
    if final_status is None and last_eval:
        final_status = (
            "complete_success"
            if all(r.get("passed") for r in last_eval.values())
            else "structured_failure"
        )

    total_router_attempts = len(log["passed"]) + len(log["insufficient"]) + len(log["low_advantage"])

    return {
        "run": os.path.basename(run_dir.rstrip("/\\")),
        "path": run_dir,
        "has_trace": bool(trace),
        "quota_contaminated": log["quota_hit"],
        "n_subtasks": len(subtasks),
        "n_planner_replans": len(planner_events),
        "final_status": final_status,
        "edges": edges,
        "inter_node_edges": inter_edges,
        "intra_node_edges": intra_edges,
        # retry / gap signals
        "router_insufficient": len(log["insufficient"]),
        "router_low_advantage": len(log["low_advantage"]),
        "router_passed": len(log["passed"]),
        "router_attempts_total": total_router_attempts,
        "retries_by_subtask": dict(attempts_by_subtask),
        "nodes_needing_retry": sum(1 for v in attempts_by_subtask.values() if v > 1),
        # execution / eval
        "exec_total": exec_total,
        "exec_success": exec_success,
        "eval_pass": eval_pass,
        "eval_judged": len(last_eval),
        "failures": dict(exec_fail + eval_fail),
        "pre_repair_failures": dict(pre_repair_fail),
    }


def aggregate(runs: List[dict]) -> dict:
    clean = [r for r in runs if not r["quota_contaminated"] and r["has_trace"]]
    contaminated = [r for r in runs if r["quota_contaminated"]]
    no_trace = [r for r in runs if not r["has_trace"]]

    cat_totals = Counter()
    pre_repair_totals = Counter()
    for r in clean:
        for cat, n in r["failures"].items():
            cat_totals[cat] += n
        for cat, n in r["pre_repair_failures"].items():
            pre_repair_totals[cat] += n

    status_counts = Counter(r["final_status"] for r in clean)
    total_edges = sum(r["edges"] for r in clean)
    total_nodes = sum(r["n_subtasks"] for r in clean)
    nodes_retry = sum(r["nodes_needing_retry"] for r in clean)
    insufficient = sum(r["router_insufficient"] for r in clean)
    low_adv = sum(r["router_low_advantage"] for r in clean)
    router_attempts = sum(r["router_attempts_total"] for r in clean)

    return {
        "n_runs_total": len(runs),
        "n_runs_clean": len(clean),
        "n_runs_quota_contaminated": len(contaminated),
        "n_runs_no_trace": len(no_trace),
        "clean_run_names": [r["run"] for r in clean],
        "contaminated_run_names": [r["run"] for r in contaminated],
        "end_to_end_status": dict(status_counts),
        "total_nodes": total_nodes,
        "nodes_needing_retry": nodes_retry,
        "node_retry_rate": round(nodes_retry / total_nodes, 3) if total_nodes else None,
        "total_plan_edges": total_edges,
        "router_bundle_insufficient": insufficient,
        "router_bundle_low_advantage": low_adv,
        "router_attempts_total": router_attempts,
        # NOTE: these two are fundamentally different signals and must NOT be summed.
        #   insufficient  = policy judged the bundle CANNOT do the job (capability/compat)
        #   low_advantage = cost/latency efficiency below threshold (economic, still executes)
        "router_insufficient_rate": (
            round(insufficient / router_attempts, 3) if router_attempts else None
        ),
        "router_low_advantage_rate": (
            round(low_adv / router_attempts, 3) if router_attempts else None
        ),
        "failure_categories_final": dict(cat_totals),
        "failure_categories_pre_repair": dict(pre_repair_totals),
    }


# ─────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────

def print_report(runs: List[dict], agg: dict) -> None:
    print("=" * 68)
    print("S-GAR TRACE AGGREGATION — compatibility / executability read")
    print("=" * 68)

    print("\n[PER-RUN]")
    hdr = f"{'run':<20}{'trace':<6}{'quota':<6}{'nodes':<6}{'retry':<6}{'insuf':<6}{'lowA':<6}{'status':<20}"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(runs, key=lambda x: x["run"]):
        print(
            f"{r['run']:<20}"
            f"{'Y' if r['has_trace'] else '-':<6}"
            f"{'HIT' if r['quota_contaminated'] else '-':<6}"
            f"{r['n_subtasks']:<6}"
            f"{r['nodes_needing_retry']:<6}"
            f"{r['router_insufficient']:<6}"
            f"{r['router_low_advantage']:<6}"
            f"{str(r['final_status']):<20}"
        )

    print("\n[AGGREGATE — clean runs only]")
    print(f"  runs: {agg['n_runs_clean']} clean / "
          f"{agg['n_runs_quota_contaminated']} quota-contaminated / "
          f"{agg['n_runs_no_trace']} no-trace  (of {agg['n_runs_total']} total)")
    if agg["clean_run_names"]:
        print(f"  clean: {agg['clean_run_names']}")
    if agg["contaminated_run_names"]:
        print(f"  EXCLUDED (quota): {agg['contaminated_run_names']}")

    print("\n  End-to-end status:", agg["end_to_end_status"] or "(none determinable)")
    print(f"  Nodes: {agg['total_nodes']} | needing retry: {agg['nodes_needing_retry']} "
          f"| retry rate: {agg['node_retry_rate']}")
    print(f"  Plan edges (u->v consumed): {agg['total_plan_edges']}")
    print(f"  Router attempts: {agg['router_attempts_total']} "
          f"| insufficient: {agg['router_bundle_insufficient']} "
          f"| low-advantage: {agg['router_bundle_low_advantage']}")
    print(f"  >>> insufficient rate (CAPABILITY/COMPAT signal): "
          f"{agg['router_insufficient_rate']}")
    print(f"      low-advantage rate (economic only, still executes): "
          f"{agg['router_low_advantage_rate']}")

    order = ("compatibility", "model_quality", "infra", "eval_noise", "uncategorized")
    print("\n  Failure categories — FINAL verdict (post repair/replan):")
    final = agg["failure_categories_final"]
    if not final:
        print("    (none — everything either passed or was repaired away)")
    for cat in order:
        if cat in final:
            print(f"    {cat:<16} {final[cat]}")

    print("\n  Failure categories — PRE-REPAIR (all attempts; the real gap signal):")
    pre = agg["failure_categories_pre_repair"]
    if not pre:
        print("    (none categorized)")
    for cat in order:
        if cat in pre:
            marker = "  <<< COMPATIBILITY / artifact-flow" if cat == "compatibility" else ""
            print(f"    {cat:<16} {pre[cat]}{marker}")

    print("\n[CAVEAT] End-to-end status hides edge failures: repair/replan loops")
    print("         mask them. The router first-attempt fail rate and node retry")
    print("         rate are the real Relevance-Executability Gap signal.")
    print("=" * 68)


def resolve_dirs(args: argparse.Namespace) -> List[str]:
    dirs: List[str] = []
    if args.root:
        for name in sorted(os.listdir(args.root)):
            p = os.path.join(args.root, name)
            if os.path.isdir(p):
                dirs.append(p)
    for pat in args.paths:
        for p in glob.glob(pat):
            if os.path.isdir(p):
                dirs.append(p)
    # de-dup, keep only dirs that have at least a trace or log
    seen = set()
    result = []
    for d in dirs:
        d = os.path.normpath(d)
        if d in seen:
            continue
        seen.add(d)
        if os.path.isfile(os.path.join(d, "trace.jsonl")) or os.path.isfile(
            os.path.join(d, "pipeline.log")
        ):
            result.append(d)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate S-GAR run traces into compatibility metrics.")
    ap.add_argument("paths", nargs="*", help="Run directories or globs (each with trace.jsonl/pipeline.log).")
    ap.add_argument("--root", help="Parent dir; every immediate subdir is treated as a run.")
    ap.add_argument("--json", dest="json_out", help="Write full machine-readable results to this path.")
    args = ap.parse_args()

    try:  # avoid gbk/cp936 mojibake for unicode chars on Windows consoles
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    dirs = resolve_dirs(args)
    if not dirs:
        print("No run directories found (need trace.jsonl or pipeline.log).", file=sys.stderr)
        sys.exit(1)

    runs = [analyze_run(d) for d in dirs]
    agg = aggregate(runs)
    print_report(runs, agg)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"runs": runs, "aggregate": agg}, f, ensure_ascii=False, indent=2)
        print(f"\nWrote machine-readable results to {args.json_out}")


if __name__ == "__main__":
    main()
