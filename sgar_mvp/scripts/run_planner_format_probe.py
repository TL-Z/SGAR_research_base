#!/usr/bin/env python3
"""Run exactly one production-shape control-role FORMAT probe and persist raw evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from sgar_mvp.scripts.check_model_health import load_env_value
from sgar_mvp.src.direct_network import direct_sync_http_client
from sgar_mvp.src.model_accounting import (
    ModelPricingCatalog,
    calculate_actual_model_cost_usd,
    normalize_token_usage,
)
from sgar_mvp.src.model_response_contracts import (
    build_exact_schema_probe_request,
    system_role_requirement,
    validate_structured_response_content,
)
from sgar_mvp.src.model_transport import ProviderEndpointIdentity, SyncModelTransportPort
from sgar_mvp.src.pipeline_control import canonical_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ID = "model.gpt_5_6_sol.v1"
MODEL_ID = "gpt-5.6-sol"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--role", choices=("planner", "plan_compiler"), default="planner")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    api_key = load_env_value("LLM_API_KEY", "OPENAI_API_KEY")
    config = json.loads((PROJECT_ROOT / "sgar_mvp/config.json").read_text(encoding="utf-8"))
    base_url = load_env_value("LLM_BASE_URL", "OPENAI_BASE_URL") or config["llm_settings"]["base_url"]
    requirement = system_role_requirement(args.role)
    request = build_exact_schema_probe_request(
        model_id=MODEL_ID,
        requirement=requirement,
        mode="native_strict_schema",
    )
    request.pop("temperature", None)
    request["max_tokens"] = 8192
    _write(output_dir / "request.json", request)
    endpoint = ProviderEndpointIdentity.create(
        provider="openai_compatible",
        base_url=base_url,
        credential_environment_variable="LLM_API_KEY",
        timeout_seconds=args.timeout_sec,
        max_retries=0,
    )
    client = OpenAI(
        http_client=direct_sync_http_client(),
        api_key=api_key,
        base_url=base_url,
        timeout=args.timeout_sec,
        max_retries=0,
    )
    transport = SyncModelTransportPort.from_sdk_client(client=client, endpoint_identity=endpoint)
    started = time.perf_counter()
    response = transport.send(**request)
    raw = str(response.choices[0].message.content or "")
    (output_dir / "response.txt").write_text(raw, encoding="utf-8")
    usage = normalize_token_usage(getattr(response, "usage", None))
    price = ModelPricingCatalog.from_manifest_file(
        PROJECT_ROOT / "Pool/resources/json/models.json"
    ).resolve(resource_id=RESOURCE_ID, api_model_id=MODEL_ID)
    cost = calculate_actual_model_cost_usd(usage, price)
    try:
        validate_structured_response_content(
            raw,
            requirement=requirement,
            mode="native_strict_schema",
        )
        status = "passed"
        error = None
    except Exception as exc:
        status = "failed"
        error = type(exc).__name__ + ":" + str(exc)
    report = {
        "protocol": "sgar-control-role-format-probe-v1",
        "role": args.role,
        "resource_id": RESOURCE_ID,
        "api_model_id": MODEL_ID,
        "status": status,
        "error": error,
        "physical_sends": 1,
        "sdk_retries": 0,
        "infrastructure_retries": 0,
        "timeout_seconds": args.timeout_sec,
        "request_sha256": canonical_sha256(request),
        "requirement_sha256": requirement.requirement_sha256,
        "wire_schema_sha256": requirement.wire_schema_sha256,
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "usage": usage.model_dump(mode="json"),
        "observed_cost_usd": format(cost, ".12f"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "endpoint_identity_sha256": endpoint.identity_sha256,
    }
    _write(output_dir / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
