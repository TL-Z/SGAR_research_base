"""Read-only replay gate for persisted sealed executable Plans.

The replay performs no model, Tool, Validator, retrieval, or artifact action.
It only reconstructs immutable compilation contracts and checks the identity
joins that must survive persistence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from .executable_plan import SealedPlanCompilationArtifact
from .pipeline_control import FrozenContract, canonical_sha256
from .plan_lowering import LoweredExecutionPlan


SEALED_PLAN_REPLAY_PROTOCOL = "sgar-sealed-plan-replay-v1"


class SealedPlanReplayReport(FrozenContract):
    protocol: Literal[SEALED_PLAN_REPLAY_PROTOCOL] = SEALED_PLAN_REPLAY_PROTOCOL
    artifact_count: int = Field(ge=0)
    successful_artifact_count: int = Field(ge=0)
    failed_artifact_count: int = Field(ge=0)
    interrupted_artifact_count: int = Field(ge=0)
    framework_exceptions: tuple[str, ...] = ()
    plan_hash_mutations: tuple[str, ...] = ()
    valid: bool
    report_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "SealedPlanReplayReport":
        terminal_count = (
            self.successful_artifact_count
            + self.failed_artifact_count
            + self.interrupted_artifact_count
        )
        if terminal_count > self.artifact_count:
            raise ValueError("sealed_plan_replay_count_invalid")
        expected_valid = (
            self.artifact_count > 0
            and terminal_count == self.artifact_count
            and not self.framework_exceptions
            and not self.plan_hash_mutations
        )
        if self.valid is not expected_valid:
            raise ValueError("sealed_plan_replay_validity_mismatch")
        projected = self.model_dump(mode="python", exclude={"report_sha256"})
        expected = canonical_sha256(projected)
        if self.report_sha256 and self.report_sha256 != expected:
            raise ValueError("sealed_plan_replay_hash_mismatch")
        object.__setattr__(self, "report_sha256", expected)
        return self


def _artifact_directory(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    nested = resolved / "plan_compiler"
    return nested if nested.is_dir() else resolved


def _success_identity_errors(
    artifact: SealedPlanCompilationArtifact,
    lowered: LoweredExecutionPlan,
) -> tuple[str, ...]:
    plan = artifact.executable_plan
    if plan is None:
        return ("missing_executable_plan",)
    errors: list[str] = []
    if artifact.plan_revision != plan.plan_revision or plan.plan_revision != lowered.plan_revision:
        errors.append("plan_revision_changed")
    if artifact.candidate_pool_sha256 != plan.candidate_pool_sha256:
        errors.append("artifact_candidate_pool_changed")
    if plan.candidate_pool_sha256 != lowered.candidate_pool_sha256:
        errors.append("lowered_candidate_pool_changed")
    if plan.plan_sha256 != lowered.plan_sha256:
        errors.append("lowered_plan_hash_changed")
    if plan.plan_sha256 != lowered.plan_semantic_sha256:
        errors.append("lowered_plan_semantics_changed")
    if plan.selected_resource_ids != lowered.selected_resource_ids:
        errors.append("selected_resources_changed")
    if plan.final_output != lowered.final_output:
        errors.append("final_output_changed")
    return tuple(errors)


def replay_sealed_plan_artifacts(path: str | Path) -> SealedPlanReplayReport:
    """Validate every persisted compiler artifact beneath ``path`` read-only."""

    artifact_dir = _artifact_directory(path)
    paths = (
        tuple(
            path
            for path in sorted(artifact_dir.glob("*.json"))
            if not path.name.endswith(("_semantic_attempts.json", ".sem.json"))
        )
        if artifact_dir.is_dir()
        else ()
    )
    framework_exceptions: list[str] = []
    plan_hash_mutations: list[str] = []
    counts = {"success": 0, "failed": 0, "interrupted": 0}

    if not paths:
        framework_exceptions.append("sealed_plan_artifacts_missing")

    for artifact_path in paths:
        locator = artifact_path.name
        try:
            payload: Any = json.loads(artifact_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("sealed_plan_artifact_not_object")
            artifact = SealedPlanCompilationArtifact.model_validate(payload)
        except Exception:
            framework_exceptions.append(f"artifact_invalid:{locator}")
            continue

        counts[artifact.status] += 1
        if artifact.status != "success":
            continue
        try:
            lowered = LoweredExecutionPlan.model_validate(artifact.lowered_plan)
        except Exception:
            framework_exceptions.append(f"lowered_plan_invalid:{locator}")
            continue
        for code in _success_identity_errors(artifact, lowered):
            plan_hash_mutations.append(f"{code}:{locator}")

    terminal_count = sum(counts.values())
    return SealedPlanReplayReport(
        artifact_count=len(paths),
        successful_artifact_count=counts["success"],
        failed_artifact_count=counts["failed"],
        interrupted_artifact_count=counts["interrupted"],
        framework_exceptions=tuple(framework_exceptions),
        plan_hash_mutations=tuple(plan_hash_mutations),
        valid=(
            bool(paths)
            and terminal_count == len(paths)
            and not framework_exceptions
            and not plan_hash_mutations
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay sealed SGAR Plans offline")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    report = replay_sealed_plan_artifacts(args.run_dir)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SEALED_PLAN_REPLAY_PROTOCOL",
    "SealedPlanReplayReport",
    "replay_sealed_plan_artifacts",
]
