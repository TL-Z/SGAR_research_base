#!/usr/bin/env python3
"""Run the bounded production native-strict repeat for two model identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.scripts.check_model_health import (  # noqa: E402
    generic_strict_schema_requirement,
    load_env_value,
    validate_probe_json_content,
)
from sgar_mvp.src.model_accounting import (  # noqa: E402
    ModelPricingCatalog,
    calculate_actual_model_cost_usd,
    normalize_token_usage,
)
from sgar_mvp.src.model_response_contracts import (  # noqa: E402
    build_exact_schema_probe_request,
)
from sgar_mvp.src.model_transport import (  # noqa: E402
    ProviderEndpointIdentity,
    SyncModelTransportPort,
)
from sgar_mvp.src.pipeline_control import canonical_sha256  # noqa: E402
from sgar_mvp.src.direct_network import direct_sync_http_client  # noqa: E402


TARGETS = (
    ("model.claude_opus_5.v1", "claude-opus-5"),
    ("model.qwen3_coder_next.v1", "qwen3-coder-next"),
)
EXPECTED_REQUEST_HASHES = {
    "claude-opus-5": "5e516c1dc2fa013d7eade4559c65e64f09f2e1814ca63f79606141c80dea0954",
    "qwen3-coder-next": "37b5aeff6dff11363cd38de3bd520faebb761b04e56e5d6310fce5f25dcde920",
}


def _request(model_id: str) -> dict[str, Any]:
    payload = build_exact_schema_probe_request(
        model_id=model_id,
        requirement=generic_strict_schema_requirement(),
        mode="native_strict_schema",
    )
    payload.pop("temperature", None)
    payload["max_tokens"] = 4096
    return payload


def _raw_response(response: Any) -> str:
    return str(response.choices[0].message.content or "")


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    args = parser.parse_args()
    if args.repeats != 3:
        raise SystemExit("This bounded task requires exactly three repeats per model")
    api_key = load_env_value("LLM_API_KEY", "OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("LLM_API_KEY is not configured")
    config = json.loads((PROJECT_ROOT / "sgar_mvp/config.json").read_text(encoding="utf-8"))
    base_url = args.base_url or load_env_value("LLM_BASE_URL", "OPENAI_BASE_URL") or config["llm_settings"]["base_url"]
    output = args.output_dir.resolve()
    requests_dir = output / "requests"
    responses_dir = output / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    requests: dict[str, dict[str, Any]] = {}
    for _, model_id in TARGETS:
        payload = _request(model_id)
        digest = canonical_sha256(payload)
        expected = EXPECTED_REQUEST_HASHES[model_id]
        if digest != expected:
            raise SystemExit(f"request_hash_mismatch:{model_id}:{digest}:{expected}")
        requests[model_id] = payload
        _json_dump(requests_dir / f"{model_id}.request.json", payload)

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
    pricing = ModelPricingCatalog.from_manifest_file(PROJECT_ROOT / "Pool/resources/json/models.json")
    results: list[dict[str, Any]] = []
    for resource_id, model_id in TARGETS:
        for repeat in range(1, 4):
            payload = requests[model_id]
            started = time.perf_counter()
            request_hash = canonical_sha256(payload)
            base = {
                "resource_id": resource_id,
                "model_id": model_id,
                "repeat": repeat,
                "request_sha256": request_hash,
                "endpoint_identity_sha256": endpoint.identity_sha256,
                "request_file": str((requests_dir / f"{model_id}.request.json").name),
            }
            try:
                response = transport.send(**payload)
                raw = _raw_response(response)
                response_path = responses_dir / f"{model_id}-{repeat}.response.txt"
                response_path.write_text(raw, encoding="utf-8")
                usage = normalize_token_usage(getattr(response, "usage", None))
                price = pricing.resolve(resource_id=resource_id, api_model_id=model_id)
                cost = calculate_actual_model_cost_usd(usage, price)
                try:
                    value = validate_probe_json_content(
                        raw,
                        requirement=generic_strict_schema_requirement(),
                        mode="native_strict_schema",
                    )
                    status = "passed"
                    error = None
                except Exception as exc:  # strict contract failure is evidence
                    value = None
                    status = "failed"
                    error = type(exc).__name__ + ":" + str(exc)
                result = {
                    **base,
                    "status": status,
                    "raw_content": raw,
                    "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "value": value,
                    "error": error,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    "usage": usage.model_dump(mode="json"),
                    "observed_cost_usd": format(cost, ".12f"),
                    "response_file": response_path.name,
                }
            except Exception as exc:
                result = {
                    **base,
                    "status": "failed",
                    "raw_content": None,
                    "raw_response_sha256": None,
                    "value": None,
                    "error": type(exc).__name__ + ":" + str(exc),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    "usage": None,
                    "observed_cost_usd": None,
                    "response_file": None,
                }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))
    total = sum((Decimal(item["observed_cost_usd"]) for item in results if item["observed_cost_usd"]), Decimal("0"))
    report = {
        "schema_version": "sgar-targeted-native-strict-repeat-v1",
        "scope": "native_strict_schema_only",
        "base_url": base_url,
        "endpoint_identity_sha256": endpoint.identity_sha256,
        "timeout_seconds": args.timeout_sec,
        "max_tokens": 4096,
        "sdk_retries": 0,
        "infrastructure_retries": 0,
        "repeats_per_model": 3,
        "targets": [{"resource_id": rid, "model_id": mid} for rid, mid in TARGETS],
        "results": results,
        "summary": {
            model_id: {
                "passed": sum(item["status"] == "passed" for item in results if item["model_id"] == model_id),
                "failed": sum(item["status"] == "failed" for item in results if item["model_id"] == model_id),
            }
            for _, model_id in TARGETS
        },
        "physical_sends": len(results),
        "observed_total_model_cost_usd": format(total, ".12f"),
        "request_hashes": {model_id: canonical_sha256(payload) for model_id, payload in requests.items()},
        "raw_response_files": sorted(path.name for path in responses_dir.glob("*.txt")),
    }
    _json_dump(output / "report.json", report)
    return 0 if all(item["status"] == "passed" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
