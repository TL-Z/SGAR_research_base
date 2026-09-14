"""Offline cost admission for the five formal Sol release probes."""

from __future__ import annotations

import argparse
import json
import math
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.model_accounting import (
    ModelPricingCatalog,
    calculate_model_cost_from_token_counts,
)
from sgar_mvp.src.pipeline_control import canonical_json_bytes, canonical_sha256
from sgar_mvp.src.release_environment import require_release_storage_path
from sgar_mvp.src.release_source_seal import (
    load_and_verify_source_seal,
    source_seal_reference,
)
from sgar_mvp.scripts.run_release_provider_probes import release_probe_specs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-source-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-limit-usd", type=Decimal, required=True)
    args = parser.parse_args()
    output = require_release_storage_path(
        args.output, code="release_probe_admission_output_outside_storage_root"
    )
    seal = load_and_verify_source_seal(
        args.release_source_seal,
        project_root=PROJECT_ROOT,
        allowed_stages=("release",),
    )
    catalog = ModelPricingCatalog.from_manifest_file(
        PROJECT_ROOT / "Pool/resources/json/combine.json",
        required_model_refs=("gpt-5.6-sol",),
    )
    price = catalog.resolve(model_ref="gpt-5.6-sol")
    probes: list[dict[str, object]] = []
    total = Decimal("0")
    for spec in release_probe_specs():
        role = str(spec["role"])
        request = dict(spec["request"])
        request_bytes = canonical_json_bytes(request)
        input_reserve = max(1, math.ceil(len(request_bytes) / 2))
        output_reserve = int(request["max_tokens"])
        maximum = calculate_model_cost_from_token_counts(
            input_tokens=input_reserve,
            cached_input_tokens=0,
            output_tokens=output_reserve,
            input_per_m=price.input_per_m,
            cache_per_m=price.cache_per_m,
            output_per_m=price.output_per_m,
        )
        total += maximum
        probes.append(
            {
                "role": role,
                "model_resource_id": price.resource_id,
                "api_model_id": price.api_model_id,
                "reasoning_effort": request["reasoning_effort"],
                "temperature": None,
                "request_sha256": spec["request_sha256"],
                "input_token_reserve": input_reserve,
                "output_token_reserve": output_reserve,
                "maximum_cost_usd": str(maximum),
            }
        )
    payload: dict[str, object] = {
        "protocol": "sgar-release-probe-cost-admission-v2",
        "network_requests_authorized": False,
        "provider_request_count": 5,
        "normal_provider_request_count": 5,
        "legal_automatic_retry_request_count": 0,
        "disaster_upper_bound_provider_request_count": 5,
        "release_source_seal": source_seal_reference(seal),
        "pricing_catalog_sha256": catalog.pricing_catalog_sha256,
        "local_price": price.model_dump(mode="json"),
        "probes": probes,
        "normal_reserved_total_cost_usd": str(total),
        "all_legal_retries_disaster_upper_bound_cost_usd": str(total),
        "maximum_total_cost_usd": str(total),
        "retry_policy": (
            "The five release probes are exactly-once admission checks. SDK and script "
            "automatic retries are disabled; any failed probe stops promotion."
        ),
        "cost_limit_usd": str(args.cost_limit_usd),
        "admitted": total <= args.cost_limit_usd,
    }
    payload["admission_sha256"] = canonical_sha256(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(payload) + b"\n")
    print(output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
