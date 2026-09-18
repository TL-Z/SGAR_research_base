"""Fixed worker entrypoint: contract probes or the guarded real SGAR pipeline."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


async def run(args, protocol_output):
    from sgar_mvp.src.external_worker_runtime import JsonLinesExecutionSubstrate
    runtime = JsonLinesExecutionSubstrate(
        input_stream=sys.stdin, output_stream=protocol_output,
        run_id=args.run_id, trial_id=args.trial_id,
        network_allowed=args.network_policy == "declared",
    )
    if args.mode == "contract":
        from sgar_mvp.src.external_runtime_contract import verify_contract
        return await verify_contract(runtime)
    if args.mode == "production-contract":
        from sgar_mvp.src.external_runtime_contract import verify_production_contract
        return await verify_production_contract(runtime, args.output_dir)
    # This check precedes config secrets, main import, model/retrieval and task IO.
    runtime.require_pipeline_ready()
    from sgar_mvp.main import load_config, run_pipeline
    config = load_config(args.config, resolve_secrets=True)
    if not config:
        raise RuntimeError("SGAR_CONFIG_MISSING")
    status = await run_pipeline(
        config, args.query, args.output_dir, args.report_path,
        execution_substrate_mode="external", execution_substrate=runtime,
        run_id=args.run_id,
        network_policy_mode=args.network_policy,
        require_final_delivery=not args.allow_missing_delivery,
        max_generation_requests=args.max_generation_requests,
        max_embedding_requests=args.max_embedding_requests,
    )
    return {"pipeline_status": str(status), "actions": runtime.records}


def main():
    parser = argparse.ArgumentParser()
    for name in ("config", "query", "output-dir", "report-path", "run-id", "trial-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--network-policy", choices=("disabled", "declared"), default="disabled")
    parser.add_argument("--allow-missing-delivery", action="store_true")
    parser.add_argument("--mode", choices=("pipeline", "contract", "production-contract"), default="pipeline")
    parser.add_argument("--max-generation-requests", type=int, default=24)
    parser.add_argument("--max-embedding-requests", type=int, default=8)
    args = parser.parse_args()
    protocol_output = sys.stdout
    # Existing SGAR components can print progress; it must not corrupt the pipe.
    sys.stdout = sys.stderr
    status, code = "completed", 0
    try:
        result = asyncio.run(run(args, protocol_output))
    except Exception as exc:
        status, code = "error", 1
        result = {"error_type": type(exc).__name__, "error": str(exc)}
    terminal = {"protocol": "sgar-external-substrate-terminal-v1", "run_id": args.run_id,
                "trial_id": args.trial_id, "status": status, "mode": args.mode,
                "result": result}
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "worker_terminal.json").write_text(json.dumps(terminal, indent=2) + "\n")
    protocol_output.write(json.dumps(terminal, ensure_ascii=True) + "\n")
    protocol_output.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
