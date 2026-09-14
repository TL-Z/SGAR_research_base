"""Sealed release gate for every failure observed in production canaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from .pipeline_control import FrozenContract, canonical_json_bytes, canonical_sha256


KNOWN_CANARY_FAILURE_REGRESSION_PROTOCOL = "sgar-known-canary-failure-regression-v1"
KnownFailureStatus = Literal[
    "fixed_and_regression_passed",
    "already_fixed_and_locked",
    "still_blocking",
]


KNOWN_CANARY_FAILURE_CATALOG: tuple[tuple[str, str], ...] = (
    ("formal_configuration_missing", "formal_secret_and_config_preflight"),
    ("unhandled_framework_exception", "structured_startup_terminal_causality"),
    ("planner_read_timeout_cost_unknown", "timeout_no_resend_unknown_after_send"),
    ("planner_contract_conflict", "wire_v5_projection_and_targeted_correction"),
    (
        "planner_local_identifier_pattern_invalid",
        "lossless_ingress_and_framework_identifier_projection",
    ),
    (
        "planner_connection_state_unknown",
        "shared_transport_classification_and_unknown_after_send",
    ),
    ("planner_output_truncated", "length_only_32k_to_64k_retry"),
    ("planner_executable_unit_not_atomic", "finest_executable_dag_atomicity_audit"),
    ("retrieval_public_material_missing", "evidence_cited_portable_descriptors"),
    ("retrieval_candidate_material_mismatch", "typed_material_and_operation_filtering"),
    (
        "retrieval_v6_json_schema_precompiler_rejection",
        "staged_schema_ownership_and_selected_producer_binding",
    ),
    ("candidate_pool_insufficient", "property_feasibility_and_targeted_correction"),
    ("node_failure_primary_cause_lost", "compiler_primary_failure_preservation"),
    ("runtime_chain_not_executed", "formal_entrypoint_terminal_delivery_mock"),
    ("artifact_quality_failure", "strict_schema_grounding_and_evaluator_gate"),
)


class KnownCanaryFailureRegressionItemV1(FrozenContract):
    failure_code: str = Field(min_length=1)
    treatment_id: str = Field(min_length=1)
    status: KnownFailureStatus
    historical_run_ids: tuple[str, ...] = ()
    regression_test_ids: tuple[str, ...] = ()
    evidence_sha256s: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _evidence_shape(self) -> "KnownCanaryFailureRegressionItemV1":
        if self.status != "still_blocking" and not self.regression_test_ids:
            raise ValueError("known_failure_fixed_status_requires_regression_test")
        for value in self.evidence_sha256s:
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("known_failure_evidence_sha256_invalid")
        return self


class KnownCanaryFailureRegressionV1(FrozenContract):
    protocol: Literal[
        "sgar-known-canary-failure-regression-v1"
    ] = KNOWN_CANARY_FAILURE_REGRESSION_PROTOCOL
    framework_source_sha256: str
    items: tuple[KnownCanaryFailureRegressionItemV1, ...]
    blocking_failure_codes: tuple[str, ...] = ()
    valid: bool
    report_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "KnownCanaryFailureRegressionV1":
        catalog = dict(KNOWN_CANARY_FAILURE_CATALOG)
        item_codes = tuple(item.failure_code for item in self.items)
        if item_codes != tuple(catalog):
            raise ValueError("known_failure_catalog_order_or_coverage_mismatch")
        if any(item.treatment_id != catalog[item.failure_code] for item in self.items):
            raise ValueError("known_failure_treatment_mismatch")
        expected_blocking = tuple(
            item.failure_code for item in self.items if item.status == "still_blocking"
        )
        if self.blocking_failure_codes != expected_blocking:
            raise ValueError("known_failure_blocking_projection_mismatch")
        if self.valid != (not expected_blocking):
            raise ValueError("known_failure_validity_mismatch")
        if (
            len(self.framework_source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.framework_source_sha256)
        ):
            raise ValueError("known_failure_framework_source_sha256_invalid")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"report_sha256"})
        )
        if self.report_sha256 and self.report_sha256 != expected:
            raise ValueError("known_failure_report_sha256_mismatch")
        object.__setattr__(self, "report_sha256", expected)
        return self


def build_known_canary_failure_regression(
    *,
    framework_source_sha256: str,
    results: Mapping[str, Mapping[str, Any]],
) -> KnownCanaryFailureRegressionV1:
    unknown = set(results) - {code for code, _ in KNOWN_CANARY_FAILURE_CATALOG}
    if unknown:
        raise ValueError("known_failure_result_contains_unknown_code")
    items: list[KnownCanaryFailureRegressionItemV1] = []
    for failure_code, treatment_id in KNOWN_CANARY_FAILURE_CATALOG:
        result = dict(results.get(failure_code) or {})
        status = str(result.get("status") or "still_blocking")
        items.append(
            KnownCanaryFailureRegressionItemV1(
                failure_code=failure_code,
                treatment_id=treatment_id,
                status=status,
                historical_run_ids=tuple(result.get("historical_run_ids") or ()),
                regression_test_ids=tuple(result.get("regression_test_ids") or ()),
                evidence_sha256s=tuple(result.get("evidence_sha256s") or ()),
            )
        )
    blocking = tuple(
        item.failure_code for item in items if item.status == "still_blocking"
    )
    return KnownCanaryFailureRegressionV1(
        framework_source_sha256=framework_source_sha256,
        items=tuple(items),
        blocking_failure_codes=blocking,
        valid=not blocking,
    )


def load_known_canary_failure_regression(
    path: str | Path,
    *,
    expected_framework_source_sha256: str | None = None,
) -> KnownCanaryFailureRegressionV1:
    report = KnownCanaryFailureRegressionV1.model_validate_json(
        Path(path).read_text(encoding="utf-8-sig", errors="strict")
    )
    if (
        expected_framework_source_sha256 is not None
        and report.framework_source_sha256 != expected_framework_source_sha256
    ):
        raise ValueError("known_failure_framework_source_identity_mismatch")
    return report


def write_known_canary_failure_regression(
    path: str | Path,
    report: KnownCanaryFailureRegressionV1,
) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError("known_failure_regression_report_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(report.model_dump(mode="json")) + b"\n")


__all__ = [
    "KNOWN_CANARY_FAILURE_CATALOG",
    "KNOWN_CANARY_FAILURE_REGRESSION_PROTOCOL",
    "KnownCanaryFailureRegressionItemV1",
    "KnownCanaryFailureRegressionV1",
    "build_known_canary_failure_regression",
    "load_known_canary_failure_regression",
    "write_known_canary_failure_regression",
]
