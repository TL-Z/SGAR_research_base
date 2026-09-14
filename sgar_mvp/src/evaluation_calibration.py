"""Offline, label-only calibration metrics for the contract-first evaluator.

This module never loads held-out cases or builds evaluator payloads.  Callers
must first produce model decisions, then provide only public artifact type,
human/Gold label, verdict and review metadata for aggregate comparison.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal, Sequence

from pydantic import Field, model_validator

from .pipeline_control import FrozenContract, canonical_sha256


EVALUATION_CALIBRATION_PROTOCOL = "sgar-evaluation-calibration-v1"


class EvaluationCalibrationRecord(FrozenContract):
    artifact_type: Literal["code", "json", "csv", "markdown", "plaintext"]
    reference_label: Literal["pass", "fail"]
    initial_verdict: Literal["pass", "fail", "inconclusive", "invalid"]
    final_verdict: Literal["pass", "fail", "inconclusive", "invalid"]
    review_triggered: bool
    deterministic_contract_valid: bool = True
    initial_tokens: int = Field(default=0, ge=0)
    review_tokens: int = Field(default=0, ge=0)


class ArtifactTypeAgreement(FrozenContract):
    artifact_type: str
    count: int = Field(ge=0)
    agreement: float = Field(ge=0.0, le=1.0)


class EvaluationCalibrationReport(FrozenContract):
    protocol: Literal[EVALUATION_CALIBRATION_PROTOCOL] = EVALUATION_CALIBRATION_PROTOCOL
    sample_count: int = Field(ge=0)
    pass_fail_macro_f1: float = Field(ge=0.0, le=1.0)
    false_pass_rate: float = Field(ge=0.0, le=1.0)
    false_fail_rate: float = Field(ge=0.0, le=1.0)
    initial_inconclusive_rate: float = Field(ge=0.0, le=1.0)
    review_resolution_rate: float = Field(ge=0.0, le=1.0)
    final_inconclusive_rate: float = Field(ge=0.0, le=1.0)
    deterministic_invalid_false_pass_count: int = Field(ge=0)
    per_artifact_type_agreement: tuple[ArtifactTypeAgreement, ...]
    initial_token_count: int = Field(ge=0)
    review_token_count: int = Field(ge=0)
    activation_ready: bool
    gate_failures: tuple[str, ...]
    report_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "EvaluationCalibrationReport":
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"report_sha256"})
        )
        if self.report_sha256 and self.report_sha256 != expected:
            raise ValueError("evaluation_calibration_sha256_mismatch")
        object.__setattr__(self, "report_sha256", expected)
        return self


def _f1(records: Sequence[EvaluationCalibrationRecord], label: str) -> float:
    true_positive = sum(
        item.reference_label == label and item.final_verdict == label for item in records
    )
    false_positive = sum(
        item.reference_label != label and item.final_verdict == label for item in records
    )
    false_negative = sum(
        item.reference_label == label and item.final_verdict != label for item in records
    )
    denominator = 2 * true_positive + false_positive + false_negative
    return (2 * true_positive / denominator) if denominator else 0.0


def build_calibration_report(
    records: Sequence[EvaluationCalibrationRecord],
) -> EvaluationCalibrationReport:
    frozen = tuple(records)
    total = len(frozen)
    macro_f1 = (_f1(frozen, "pass") + _f1(frozen, "fail")) / 2 if total else 0.0
    reference_fail = sum(item.reference_label == "fail" for item in frozen)
    reference_pass = sum(item.reference_label == "pass" for item in frozen)
    false_pass = sum(
        item.reference_label == "fail" and item.final_verdict == "pass" for item in frozen
    )
    false_fail = sum(
        item.reference_label == "pass" and item.final_verdict == "fail" for item in frozen
    )
    initial_uncertain = [
        item for item in frozen if item.initial_verdict in {"inconclusive", "invalid"}
    ]
    resolved = sum(
        item.review_triggered and item.final_verdict in {"pass", "fail"}
        for item in initial_uncertain
    )
    deterministic_false_pass = sum(
        not item.deterministic_contract_valid and item.final_verdict == "pass"
        for item in frozen
    )
    by_type: dict[str, list[EvaluationCalibrationRecord]] = defaultdict(list)
    for item in frozen:
        by_type[item.artifact_type].append(item)
    agreements = tuple(
        ArtifactTypeAgreement(
            artifact_type=artifact_type,
            count=len(items),
            agreement=(
                sum(item.final_verdict == item.reference_label for item in items) / len(items)
            ),
        )
        for artifact_type, items in sorted(by_type.items())
    )
    final_uncertain = sum(
        item.final_verdict in {"inconclusive", "invalid"} for item in frozen
    )
    review_resolution = resolved / len(initial_uncertain) if initial_uncertain else 1.0
    final_inconclusive_rate = final_uncertain / total if total else 1.0
    gates: list[str] = []
    if deterministic_false_pass:
        gates.append("deterministic_invalid_false_pass")
    if macro_f1 < 0.90:
        gates.append("pass_fail_macro_f1_below_0_90")
    if review_resolution < 0.90:
        gates.append("review_resolution_rate_below_0_90")
    if final_inconclusive_rate > 0.05:
        gates.append("final_inconclusive_rate_above_0_05")
    return EvaluationCalibrationReport(
        sample_count=total,
        pass_fail_macro_f1=macro_f1,
        false_pass_rate=false_pass / reference_fail if reference_fail else 0.0,
        false_fail_rate=false_fail / reference_pass if reference_pass else 0.0,
        initial_inconclusive_rate=len(initial_uncertain) / total if total else 1.0,
        review_resolution_rate=review_resolution,
        final_inconclusive_rate=final_inconclusive_rate,
        deterministic_invalid_false_pass_count=deterministic_false_pass,
        per_artifact_type_agreement=agreements,
        initial_token_count=sum(item.initial_tokens for item in frozen),
        review_token_count=sum(item.review_tokens for item in frozen),
        activation_ready=not gates,
        gate_failures=tuple(gates),
    )


__all__ = [
    "EVALUATION_CALIBRATION_PROTOCOL",
    "ArtifactTypeAgreement",
    "EvaluationCalibrationRecord",
    "EvaluationCalibrationReport",
    "build_calibration_report",
]
