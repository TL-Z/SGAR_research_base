"""Run a complete candidate health refresh with bounded accounting evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import openai

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sgar_mvp.scripts.check_model_health import (
    READY_STATE_PROTOCOL,
    READY_STATE_SCHEMA_VERSION,
    all_capability_assignments,
    check_one,
    load_env_value,
    load_json,
    model_info,
    write_json,
)
from sgar_mvp.src.model_accounting import ModelCostPolicy, ModelPricingCatalog, RunCostLedger
from sgar_mvp.src.model_selection import candidate_health_manifests
from sgar_mvp.src.model_transport import (
    SyncModelTransportPort,
    create_model_transport_bundle,
    model_request_sha256,
    production_model_endpoint_identity,
)
from sgar_mvp.src.pipeline_control import canonical_sha256


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def serializable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return serializable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class EvidenceTransport:
    def __init__(
        self,
        *,
        base: SyncModelTransportPort,
        ledger: RunCostLedger,
        output_dir: Path,
        resource_by_api_id: dict[str, str],
    ) -> None:
        self.base = base
        self.ledger = ledger
        self.output_dir = output_dir
        self.resource_by_api_id = resource_by_api_id
        self.sequence = 0
        self.requests: list[dict[str, Any]] = []
        self.port = SyncModelTransportPort.from_sender(
            sender=self.send,
            endpoint_identity=base.endpoint_identity,
        )

    def send(self, **kwargs: Any) -> Any:
        kwargs.pop("ledger", None)
        kwargs.pop("context", None)
        model_id = str(kwargs.get("model") or "")
        resource_id = self.resource_by_api_id[model_id]
        request_sha256 = model_request_sha256(kwargs)
        self.sequence += 1
        stem = f"{self.sequence:04d}_{resource_id.replace('.', '_')}"
        request_path = self.output_dir / "requests" / f"{stem}.request.json"
        response_path = self.output_dir / "requests" / f"{stem}.response.json"
        write_json(request_path, serializable(kwargs))
        context = self.ledger.new_operation(
            stage="retrieval_format_probe",
            selected_resource_id=resource_id,
            model_resource_id=resource_id,
        )
        record = {
            "sequence": self.sequence,
            "resource_id": resource_id,
            "api_model_id": model_id,
            "request_sha256": request_sha256,
            "request_file": str(request_path.relative_to(self.output_dir)),
            "started_at": utc_now(),
        }
        started = time.perf_counter()
        try:
            response = self.base.send(
                ledger=self.ledger,
                context=context,
                **kwargs,
            )
        except Exception as exc:
            error = {
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "status_code": getattr(exc, "status_code", None),
                "code": getattr(exc, "code", None),
                "body": serializable(getattr(exc, "body", None)),
            }
            write_json(response_path, error)
            record.update({
                "outcome": "error",
                "response_file": str(response_path.relative_to(self.output_dir)),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            self.requests.append(record)
            raise
        write_json(response_path, serializable(response))
        record.update({
            "outcome": "response",
            "response_file": str(response_path.relative_to(self.output_dir)),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })
        self.requests.append(record)
        return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "Pool/resources/json/models.json")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay-sec", type=float, default=10.0)
    parser.add_argument("--freshness-ttl-sec", type=float, default=86400.0)
    parser.add_argument("--warning-usd", default="1.50")
    parser.add_argument("--cost-stop-usd", default="2.00")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_dir.exists():
        raise SystemExit("bounded_health_output_dir_already_exists")
    if args.retries != 3 or args.timeout_sec != 90 or args.max_tokens != 4096:
        raise SystemExit("bounded_health_probe_policy_changed")
    args.output_dir.mkdir(parents=True)
    api_key = load_env_value(args.api_key_env)
    base_url = args.base_url or load_env_value("LLM_BASE_URL", "OPENAI_BASE_URL")
    if not api_key or not base_url:
        raise SystemExit("bounded_health_endpoint_configuration_missing")
    catalog_payload = load_json(args.catalog)
    manifests = candidate_health_manifests(catalog_payload, root=ROOT)
    models = [model_info(manifest) for manifest in manifests]
    assignments = all_capability_assignments(models)
    normal_send_count = sum(
        1 + sum(2 if capability == "tool_result_continuation" else 1 for capability in assigned)
        for assigned in assignments.values()
    )
    pricing = ModelPricingCatalog.from_manifest_file(
        args.catalog,
        required_model_refs=[item["resource_id"] for item in models],
    )
    policy = ModelCostPolicy(
        mode="stop_after_limit",
        warning_usd=Decimal(args.warning_usd),
        limit_usd=Decimal(args.cost_stop_usd),
    )
    ledger = RunCostLedger(catalog=pricing, policy=policy, output_dir=args.output_dir / "accounting")
    bundle = create_model_transport_bundle(
        api_key=api_key,
        base_url=base_url,
        credential_environment_variable=args.api_key_env,
        timeout_seconds=args.timeout_sec,
    )
    formal_endpoint = production_model_endpoint_identity(base_url=base_url)
    evidence = EvidenceTransport(
        base=bundle.sync,
        ledger=ledger,
        output_dir=args.output_dir,
        resource_by_api_id={item["model_id"]: item["resource_id"] for item in models},
    )
    preflight = {
        "generated_at": utc_now(),
        "candidate_count": len(models),
        "candidate_resource_ids": [item["resource_id"] for item in models],
        "fable_exception": "model.claude_fable_5_1.v1",
        "normal_physical_sends": normal_send_count,
        "max_physical_sends": normal_send_count * (args.retries + 1),
        "shared_warning_usd": args.warning_usd,
        "shared_cost_stop_usd": args.cost_stop_usd,
        "probe_policy": {
            "timeout_seconds": args.timeout_sec,
            "max_tokens": args.max_tokens,
            "sdk_retries": 0,
            "infrastructure_retries": args.retries,
            "retry_delay_seconds": args.retry_delay_sec,
            "concurrency": 1,
        },
        "endpoint_identity_sha256": formal_endpoint.identity_sha256,
        "probe_transport_endpoint_identity_sha256": bundle.endpoint_identity.identity_sha256,
        "credential_environment_variable": args.api_key_env,
        "python_version": platform.python_version(),
        "openai_sdk_version": openai.__version__,
        "catalog_file_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "candidate_catalog_sha256": canonical_sha256(manifests),
    }
    write_json(args.output_dir / "preflight.json", preflight)
    results: list[dict[str, Any]] = []
    stopped_reason: str | None = None
    for item in models:
        if ledger.is_exhausted:
            stopped_reason = "cost_limit_or_unknown_usage"
            break
        result = check_one(
            item,
            api_key=api_key,
            credential_environment_variable=args.api_key_env,
            base_url=base_url,
            timeout_seconds=args.timeout_sec,
            max_tokens=args.max_tokens,
            retries=args.retries,
            retry_delay_seconds=args.retry_delay_sec,
            capability_assignments=assignments[item["resource_id"]],
            transport=evidence.port,
        )
        results.append(result)
        write_json(
            args.output_dir / "results" / f"{item['resource_id']}.json",
            result,
        )
        ledger.write_summary()
        print(f"[{result['ready_state']['status'].upper():17}] {item['resource_id']}", flush=True)
    generated_at_epoch = time.time()
    payload = {
        "schema_version": READY_STATE_SCHEMA_VERSION,
        "ready_state_protocol": READY_STATE_PROTOCOL,
        "generated_at": utc_now(),
        "generated_at_epoch": generated_at_epoch,
        "expires_at_epoch": generated_at_epoch + args.freshness_ttl_sec,
        "endpoint_identity_sha256": formal_endpoint.identity_sha256,
        "base_url": base_url,
        "catalog": str(args.catalog),
        "candidate_catalog_sha256": canonical_sha256(manifests),
        "probe_policy": preflight["probe_policy"],
        "summary": {
            "catalog_total": len(models),
            "probed": len(results),
            "ok": sum(item["status"] == "ok" for item in results),
            "unavailable": sum(item["status"] == "unavailable" for item in results),
            "blocked": sum(item["status"] == "blocked" for item in results),
            "transient_failure": sum(item["status"] == "transient_failure" for item in results),
            "text_ok": sum(bool(item["text_ok"]) for item in results),
            "ready": sum((item.get("ready_state") or {}).get("status") == "ready" for item in results),
            "not_ready": sum((item.get("ready_state") or {}).get("status") != "ready" for item in results),
            "stopped_reason": stopped_reason,
        },
        "unavailable_model_ids": sorted({
            identifier
            for item in results
            if item["status"] == "unavailable"
            for identifier in (item["resource_id"], item["model_id"])
        }),
        "models": sorted(results, key=lambda item: item["resource_id"]),
        "evidence": {
            "request_count": evidence.sequence,
            "request_index": "request_index.json",
            "accounting_summary": "accounting/cost_summary.json",
            "probe_transport_endpoint_identity_sha256": bundle.endpoint_identity.identity_sha256,
        },
    }
    payload["health_sha256"] = canonical_sha256(payload)
    write_json(args.output_dir / "request_index.json", evidence.requests)
    write_json(args.output_dir / "model_health.json", payload)
    ledger.write_summary()
    complete = len(results) == len(models) and stopped_reason is None
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
