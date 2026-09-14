"""Prepare and run sealed, result-oriented SGAR Live Golden Cases.

The runner invokes ``sgar_mvp.main.main`` with the production configuration.
It binds source/input/runtime identities, records the executed plans, and
checks the delivered final result without overriding production scheduling,
retry, recovery, resource-selection, or cost-control policy.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from sgar_mvp.src.atomic_io import temporary_sibling_path
from sgar_mvp.src.model_accounting import load_model_cost_policy
from sgar_mvp.src.model_transport import production_model_endpoint_identity
from sgar_mvp.src.pipeline_control import canonical_json_bytes, canonical_sha256
from sgar_mvp.src.planner import planner_release_prompt_identity
from sgar_mvp.src.release_environment import is_approved_release_branch
from sgar_mvp.src.release_provider_receipt import (
    resolve_activated_release_provider_probe_receipt,
)
from sgar_mvp.src.release_source_seal import load_and_verify_source_seal
from sgar_mvp.src.retrieval_policy import load_retrieval_policy
from sgar_mvp.src.retrieval_runtime import (
    build_retrieval_runtime_identity,
    resolve_model_health_state_path,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "live_golden_cases"
GOLDEN_PROTOCOL = "sgar-live-golden-cases-v2"
ADMISSION_PROTOCOL = "sgar-live-golden-admission-v2"
EXECUTION_AUTHORITY_PROTOCOL = "sgar-live-golden-execution-authority-v1"
DIRECT_RUN_PROTOCOL = "sgar-live-golden-direct-run-v1"
ORACLE_PROTOCOL = "sgar-live-golden-programmatic-oracle-v2"
STAGES = (
    "TaskInvocation",
    "Conformance",
    "Planner",
    "Profiler",
    "Retrieval",
    "Frozen Candidate Pool",
    "Compiler",
    "Validation",
    "Lowering",
    "Execution Authority",
    "Runtime",
    "Sol Evaluator",
    "Artifact Commit",
    "Programmatic Oracle",
    "Delivery",
    "terminal manifest",
)


class GoldenCaseError(RuntimeError):
    """A fail-closed Golden Case gate failure with a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    final_name: str

    @property
    def root(self) -> Path:
        return FIXTURE_ROOT / self.case_id

    @property
    def request_path(self) -> Path:
        return self.root / "request.json"


CASE_SPECS = {
    "G1": CaseSpec("G1", "inventory_tables.json"),
    "G2": CaseSpec("G2", "selected_records.json"),
    "G3": CaseSpec("G3", "metrics_summary.json"),
}


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise GoldenCaseError("golden_json_root_invalid")
    return value


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling_path(path)
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> str:
    payload = canonical_json_bytes(value) + b"\n"
    _atomic_bytes(path, payload)
    return _sha_bytes(payload)


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="strict"
    ).strip()


def _assert_clean_production_identity() -> tuple[str, str, str]:
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if not is_approved_release_branch(branch):
        raise GoldenCaseError(f"golden_branch_mismatch:{branch}")
    if _git("diff", "--name-only", "--no-renames") or _git(
        "diff", "--cached", "--name-only", "--no-renames"
    ):
        raise GoldenCaseError("golden_tracked_tree_dirty")
    return branch, head, tree


def _sealed(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    unsigned = dict(value)
    unsigned.pop(field_name, None)
    return {**unsigned, field_name: canonical_sha256(unsigned)}


@contextmanager
def _activated_seal_environment(path: Path):
    key = "SGAR_ACTIVATED_SYSTEM_SEAL_PATH"
    previous = os.environ.get(key)
    os.environ[key] = str(path.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _production_identity(
    *, activated: Mapping[str, Any], probe_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    policy = load_retrieval_policy()
    probe_records = [
        {
            "role": item["role"],
            "model_resource_id": item["model_resource_id"],
            "api_model_id": item["api_model_id"],
            "reasoning_effort": item["reasoning_effort"],
            "request_policy_sha256": item["request_policy_sha256"],
            "prompt_sha256": item["prompt_sha256"],
            "schema_sha256": item["schema_sha256"],
        }
        for item in probe_receipt.get("records") or ()
        if isinstance(item, Mapping)
    ]
    projection = {
        "planner_prompt_identity": planner_release_prompt_identity(),
        "retrieval_policy_sha256": policy.sha256(),
        "retrieval_policy_version": policy.policy_version,
        "retrieval_strategy": policy.active_strategy,
        "embedding_model": policy.embedding_model,
        "embedding_runtime_identity_sha256": policy.embedding_runtime_identity_sha256,
        "index_sha256": dict(policy.index_sha256),
        "index_meta_sha256": policy.index_meta_sha256,
        "index_build_manifest_sha256": policy.index_build_manifest_sha256,
        "resource_pool_sha256": policy.effective_pool_sha256,
        "source_collection_sha256": activated.get("source_collection_sha256"),
        "control_role_probe_result_sha256": probe_receipt.get("result_sha256"),
        "control_role_identities": probe_records,
    }
    return _sealed(projection, "production_identity_sha256")


def _case_input_files(spec: CaseSpec) -> tuple[Path, ...]:
    request = _read_json(spec.request_path)
    paths = [spec.request_path]
    for item in request.get("inputs") or ():
        if not isinstance(item, Mapping):
            raise GoldenCaseError("golden_request_input_invalid")
        paths.append((spec.root / str(item.get("path") or "")).resolve())
    if any(not path.is_file() for path in paths):
        raise GoldenCaseError("golden_fixture_missing")
    return tuple(paths)


def _load_effective_resource_rows() -> tuple[dict[str, Any], ...]:
    path = ROOT / "Pool" / "resources" / "json" / "effective_combine.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = raw if isinstance(raw, list) else [raw]
    return tuple(row for row in rows if isinstance(row, dict))


def _execution_authority(
    *,
    activated: Mapping[str, Any],
    provider_endpoint_identity_sha256: str,
) -> dict[str, Any]:
    retrieval_identity = build_retrieval_runtime_identity(
        project_root=ROOT,
        provider_endpoint_identity_sha256=provider_endpoint_identity_sha256,
        require_release_sealed=True,
    )
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in _load_effective_resource_rows():
        resource_id = str(row.get("resource_id") or "").strip()
        if not resource_id:
            raise GoldenCaseError("golden_execution_authority_resource_identity_missing")
        if resource_id in rows_by_id:
            raise GoldenCaseError("golden_execution_authority_resource_identity_duplicate")
        rows_by_id[resource_id] = row

    eligible_ids = sorted(retrieval_identity.eligible_resource_ids)
    if not eligible_ids:
        raise GoldenCaseError("golden_execution_authority_empty")
    resources: list[dict[str, str]] = []
    for resource_id in eligible_ids:
        row = rows_by_id.get(resource_id)
        if row is None or row.get("status") != "active":
            raise GoldenCaseError("golden_execution_authority_manifest_missing")
        resources.append(
            {
                "resource_id": resource_id,
                "resource_type": str(row.get("resource_type") or ""),
                "manifest_sha256": canonical_sha256(row),
            }
        )

    return _sealed(
        {
            "protocol": EXECUTION_AUTHORITY_PROTOCOL,
            "activated_system_seal_sha256": activated.get("seal_sha256"),
            "release_source_seal_sha256": activated.get(
                "parent_release_source_seal_sha256"
            ),
            "endpoint_identity_sha256": provider_endpoint_identity_sha256,
            "retrieval_runtime_identity_sha256": retrieval_identity.identity_sha256,
            "pool_sha256": retrieval_identity.pool_sha256,
            "index_sha256": retrieval_identity.index_sha256,
            "retrieval_policy_sha256": retrieval_identity.policy_sha256,
            "availability_sha256": retrieval_identity.availability_sha256,
            "model_health_state_file_sha256": _sha_file(
                resolve_model_health_state_path(ROOT)
            ),
            "eligible_resource_count": len(resources),
            "resources": resources,
        },
        "execution_authority_sha256",
    )


def _production_policy_identity() -> dict[str, Any]:
    config_path = ROOT / "sgar_mvp" / "config.json"
    cost_policy_path = ROOT / "sgar_mvp" / "config" / "model_cost_policy.json"
    config = _read_json(config_path)
    settings = dict(config.get("llm_settings") or {})
    cost_policy = load_model_cost_policy(
        cost_policy_path,
        local_cost_control=settings.get("cost_control"),
    )
    return _sealed(
        {
            "config_file_sha256": _sha_file(config_path),
            "resolved_cost_policy": cost_policy.snapshot(),
            "resolved_retry_policy": {
                "max_retries": int(settings.get("max_retries", 3)),
                "max_planner_replans": max(
                    0, int(settings.get("max_planner_replans", 2))
                ),
                "max_eval_retries": max(0, int(settings.get("max_eval_retries", 0))),
                "execution_max_retries": max(
                    1, int(settings.get("execution_max_retries", 3))
                ),
                "enable_control_model_failover": bool(
                    settings.get("enable_control_model_failover", False)
                ),
                "allow_plan_recovery": bool(settings.get("allow_plan_recovery", False)),
                "max_same_bundle_repair_attempts": max(
                    0, int(settings.get("max_same_bundle_repair_attempts", 0))
                ),
                "capability_probe_enforcement_policy": str(
                    settings.get(
                        "capability_probe_enforcement_policy", "adaptive_fallback"
                    )
                ),
            },
        },
        "production_policy_identity_sha256",
    )


def prepare_case(
    *,
    case_id: str,
    output_root: Path,
    activated_seal_path: Path,
    batch_id: str | None = None,
) -> Path:
    spec = CASE_SPECS[case_id]
    branch, head, tree = _assert_clean_production_identity()
    activated = load_and_verify_source_seal(
        activated_seal_path,
        project_root=ROOT,
        allowed_stages=("activated",),
    )
    config = _read_json(ROOT / "sgar_mvp" / "config.json")
    base_url = str((config.get("llm_settings") or {}).get("base_url") or "")
    endpoint = production_model_endpoint_identity(base_url=base_url)
    with _activated_seal_environment(activated_seal_path):
        probe_path, probe_receipt = resolve_activated_release_provider_probe_receipt(
            ROOT, expected_endpoint_identity_sha256=endpoint.identity_sha256
        )
    probe_file_sha = _sha_file(probe_path)
    if activated.get("git_head") != head:
        raise GoldenCaseError("golden_activated_release_head_mismatch")
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_batch = batch_id or (
        f"{case_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{head[:7]}"
    )
    case_dir = output_root / resolved_batch
    case_dir.mkdir(parents=False, exist_ok=False)
    execution_authority = _execution_authority(
        activated=activated,
        provider_endpoint_identity_sha256=endpoint.identity_sha256,
    )
    production_policy = _production_policy_identity()
    fixture_files = _case_input_files(spec)
    fixture_hashes = {
        path.relative_to(ROOT).as_posix(): _sha_file(path) for path in fixture_files
    }
    runner_sha = _sha_file(Path(__file__).resolve())
    oracle_policy_sha = canonical_sha256(
        {
            "protocol": ORACLE_PROTOCOL,
            "case_id": case_id,
            "implementation_sha256": runner_sha,
        }
    )
    production_identity = _production_identity(
        activated=activated,
        probe_receipt=probe_receipt,
    )
    identity = _sealed(
        {
            "protocol": GOLDEN_PROTOCOL,
            "case_id": case_id,
            "branch": branch,
            "git_head": head,
            "git_tree": tree,
            "activated_system_seal_sha256": activated["seal_sha256"],
            "release_provider_probe_receipt_file_sha256": probe_file_sha,
            "fixture_files_sha256": fixture_hashes,
            "runner_sha256": runner_sha,
            "execution_authority_sha256": execution_authority[
                "execution_authority_sha256"
            ],
            "oracle_policy_sha256": oracle_policy_sha,
            "production_identity": production_identity,
            "production_policy": production_policy,
        },
        "case_identity_sha256",
    )
    admission_unsigned = {
        "protocol": ADMISSION_PROTOCOL,
        "case_id": case_id,
        "case_identity_sha256": identity["case_identity_sha256"],
        "branch": branch,
        "git_head": head,
        "git_tree": tree,
        "activated_system_seal_sha256": activated["seal_sha256"],
        "release_source_seal_sha256": activated.get(
            "parent_release_source_seal_sha256"
        ),
        "request_manifest_sha256": fixture_hashes[
            spec.request_path.relative_to(ROOT).as_posix()
        ],
        "fixture_files_sha256": fixture_hashes,
        "runner_sha256": runner_sha,
        "execution_authority_sha256": execution_authority[
            "execution_authority_sha256"
        ],
        "retrieval_runtime_identity_sha256": execution_authority[
            "retrieval_runtime_identity_sha256"
        ],
        "availability_sha256": execution_authority["availability_sha256"],
        "model_health_state_file_sha256": execution_authority[
            "model_health_state_file_sha256"
        ],
        "oracle_policy_sha256": oracle_policy_sha,
        "production_identity_sha256": production_identity[
            "production_identity_sha256"
        ],
        "endpoint_identity_sha256": endpoint.identity_sha256,
        "production_config_file_sha256": production_policy["config_file_sha256"],
        "resolved_cost_policy_sha256": production_policy[
            "resolved_cost_policy"
        ]["policy_sha256"],
        "production_policy_identity_sha256": production_policy[
            "production_policy_identity_sha256"
        ],
    }
    admission = _sealed(admission_unsigned, "admission_sha256")
    authorization_text = (
        f"授权运行 SGAR Live Golden Case {case_id}，admission_sha256 "
        f"{admission['admission_sha256']}；资源选择及 retry、correction、replan、"
        "failover、recovery 仅遵循该 Admission 封印的正式生产配置；不设置 Case "
        "级资源 allowlist、Provider-send ceiling 或独立成本阈值；模型费用由正式"
        "全局 stop_after_limit 策略控制。"
    )
    admission["authorization_text"] = authorization_text
    admission["authorization_sha256"] = hashlib.sha256(
        authorization_text.encode("utf-8")
    ).hexdigest()
    admission = _sealed(admission, "admission_record_sha256")
    _atomic_json(case_dir / "00_case_identity.json", identity)
    _atomic_json(case_dir / "01_provider_admission.json", admission)
    _atomic_json(case_dir / "02_execution_authority.json", execution_authority)
    _atomic_json(
        case_dir / "03_prepare_evidence.json",
        {
            "status": "identity_verified",
            "provider_calls": 0,
            "external_network_requests": 0,
            "model_cost_usd": "0",
            "docker_runs": 0,
            "execution_authority_sha256": execution_authority[
                "execution_authority_sha256"
            ],
            "eligible_resource_count": execution_authority[
                "eligible_resource_count"
            ],
            "production_policy_identity_sha256": production_policy[
                "production_policy_identity_sha256"
            ],
        },
    )
    print(authorization_text)
    print(f"authorization_sha256={admission['authorization_sha256']}")
    print(f"admission_path={case_dir / '01_provider_admission.json'}")
    return case_dir


def _verify_admission(
    *, case_id: str, admission_path: Path, authorization_sha256: str
) -> tuple[dict[str, Any], Path]:
    admission = _read_json(admission_path)
    record_sha = str(admission.get("admission_record_sha256") or "")
    unsigned_record = dict(admission)
    unsigned_record.pop("admission_record_sha256", None)
    if record_sha != canonical_sha256(unsigned_record):
        raise GoldenCaseError("golden_admission_record_hash_invalid")
    admission_sha = str(admission.get("admission_sha256") or "")
    unsigned = {
        key: value
        for key, value in admission.items()
        if key not in {"admission_sha256", "authorization_text", "authorization_sha256", "admission_record_sha256"}
    }
    if admission_sha != canonical_sha256(unsigned):
        raise GoldenCaseError("golden_admission_hash_invalid")
    if (
        admission.get("protocol") != ADMISSION_PROTOCOL
        or admission.get("case_id") != case_id
        or admission.get("authorization_sha256") != authorization_sha256
    ):
        raise GoldenCaseError("golden_authorization_invalid")
    case_dir = admission_path.resolve().parent
    identity = _read_json(case_dir / "00_case_identity.json")
    identity_sha = str(identity.get("case_identity_sha256") or "")
    unsigned_identity = dict(identity)
    unsigned_identity.pop("case_identity_sha256", None)
    if identity_sha != canonical_sha256(unsigned_identity):
        raise GoldenCaseError("golden_case_identity_hash_invalid")
    branch, head, tree = _assert_clean_production_identity()
    if (
        branch != admission.get("branch")
        or head != admission.get("git_head")
        or tree != admission.get("git_tree")
    ):
        raise GoldenCaseError("golden_admission_source_identity_drift")
    if identity.get("case_identity_sha256") != admission.get("case_identity_sha256"):
        raise GoldenCaseError("golden_case_identity_drift")
    spec = CASE_SPECS[case_id]
    for path in _case_input_files(spec):
        relative = path.relative_to(ROOT).as_posix()
        if _sha_file(path) != (admission.get("fixture_files_sha256") or {}).get(relative):
            raise GoldenCaseError("golden_fixture_identity_drift")
    if _sha_file(Path(__file__).resolve()) != admission.get("runner_sha256"):
        raise GoldenCaseError("golden_runner_identity_drift")
    activated_raw = str(os.environ.get("SGAR_ACTIVATED_SYSTEM_SEAL_PATH") or "").strip()
    if not activated_raw:
        raise GoldenCaseError("golden_activated_system_seal_missing")
    activated_path = Path(activated_raw).resolve()
    activated = load_and_verify_source_seal(
        activated_path,
        project_root=ROOT,
        allowed_stages=("activated",),
    )
    if activated.get("seal_sha256") != admission.get("activated_system_seal_sha256"):
        raise GoldenCaseError("golden_activated_system_seal_identity_drift")
    if activated.get("parent_release_source_seal_sha256") != admission.get(
        "release_source_seal_sha256"
    ):
        raise GoldenCaseError("golden_release_source_seal_identity_drift")
    config = _read_json(ROOT / "sgar_mvp" / "config.json")
    endpoint = production_model_endpoint_identity(
        base_url=str((config.get("llm_settings") or {}).get("base_url") or "")
    )
    with _activated_seal_environment(activated_path):
        _, probe_receipt = resolve_activated_release_provider_probe_receipt(
            ROOT,
            expected_endpoint_identity_sha256=endpoint.identity_sha256,
        )
    current_production_identity = _production_identity(
        activated=activated,
        probe_receipt=probe_receipt,
    )
    if current_production_identity.get("production_identity_sha256") != admission.get(
        "production_identity_sha256"
    ):
        raise GoldenCaseError("golden_production_identity_drift")
    authority_path = case_dir / "02_execution_authority.json"
    authority = _read_json(authority_path)
    authority_sha = str(authority.get("execution_authority_sha256") or "")
    unsigned_authority = dict(authority)
    unsigned_authority.pop("execution_authority_sha256", None)
    if authority_sha != canonical_sha256(unsigned_authority):
        raise GoldenCaseError("golden_execution_authority_hash_invalid")
    current_authority = _execution_authority(
        activated=activated,
        provider_endpoint_identity_sha256=endpoint.identity_sha256,
    )
    if (
        authority_sha != admission.get("execution_authority_sha256")
        or current_authority != authority
        or authority.get("retrieval_runtime_identity_sha256")
        != admission.get("retrieval_runtime_identity_sha256")
        or authority.get("availability_sha256")
        != admission.get("availability_sha256")
        or authority.get("model_health_state_file_sha256")
        != admission.get("model_health_state_file_sha256")
    ):
        raise GoldenCaseError("golden_execution_authority_identity_drift")
    production_policy = _production_policy_identity()
    if (
        production_policy.get("production_policy_identity_sha256")
        != admission.get("production_policy_identity_sha256")
        or production_policy.get("config_file_sha256")
        != admission.get("production_config_file_sha256")
        or (production_policy.get("resolved_cost_policy") or {}).get("policy_sha256")
        != admission.get("resolved_cost_policy_sha256")
    ):
        raise GoldenCaseError("golden_production_policy_identity_drift")
    return admission, case_dir


@dataclass
class GoldenRunState:
    case_id: str
    case_dir: Path
    admission: Mapping[str, Any] | None = None
    source_identity: Mapping[str, Any] | None = None
    runtime_authority: str = "release"
    task_list: list[dict[str, Any]] = field(default_factory=list)
    plan_records: list[dict[str, Any]] = field(default_factory=list)
    first_failure_stage: str | None = None
    first_failure_code: str | None = None
    oracle_passed: bool = False
    delivery_called: bool = False
    delivery_context: Any | None = None
    delivery_task_list: tuple[Mapping[str, Any], ...] = ()
    delivery_output_dir: str | None = None

    def fail(self, stage: str, code: str) -> None:
        if self.first_failure_stage is None:
            self.first_failure_stage = stage
            self.first_failure_code = code


def _authoritative_final_schema(case_id: str) -> dict[str, Any] | None:
    payload = _read_json(CASE_SPECS[case_id].request_path)
    matches = [
        item.get("schema")
        for item in payload.get("public_context_descriptors") or ()
        if isinstance(item, Mapping) and item.get("kind") == "authoritative_json_schema"
    ]
    if not matches:
        return None
    if len(matches) != 1 or not isinstance(matches[0], Mapping):
        raise GoldenCaseError("golden_authoritative_schema_identity_invalid")
    return dict(matches[0])


def _record_observed_plan(
    state: GoldenRunState,
    artifact: Any,
    candidate_resources: Sequence[Any],
) -> dict[str, Any]:
    try:
        plan = getattr(artifact, "executable_plan", None)
        if plan is None:
            record = {"status": "plan_not_available_to_observer"}
            state.plan_records.append(record)
            return record
        candidate_ids = {
            str(getattr(item, "resource_id", "") or "")
            for item in candidate_resources
        }
        steps = [step.model_dump(mode="json") for step in plan.steps]
        record = {
            "status": "observed",
            "subtask_id": plan.plan_revision.subtask_revision.subtask_id,
            "plan_sha256": plan.plan_sha256,
            "steps": steps,
            "selected_resource_ids": list(plan.selected_resource_ids),
            "candidate_resource_ids": sorted(candidate_ids),
        }
    except Exception as exc:
        # Golden diagnostics must never become an execution gate.  Preserve only
        # the exception class so observation also cannot leak runtime content.
        record = {
            "status": "observation_failed",
            "error_type": type(exc).__name__,
        }
        state.plan_records.append(record)
        return record
    state.plan_records.append(record)
    return record


def _evaluate_after_framework(state: GoldenRunState) -> None:
    """Apply the Case oracle only after the production entrypoint has returned."""

    if (
        not state.delivery_called
        or state.delivery_context is None
        or not state.delivery_task_list
        or state.delivery_output_dir is None
    ):
        return
    try:
        evaluate_committed_final(
            state=state,
            context=state.delivery_context,
            task_list=state.delivery_task_list,
            output_dir=state.delivery_output_dir,
        )
    except GoldenCaseError as exc:
        state.fail("Programmatic Oracle", exc.code)
    except Exception:
        state.fail("Programmatic Oracle", "golden_programmatic_evaluator_error")


def evaluate_committed_final(
    *, state: GoldenRunState, context: Any, task_list: Sequence[Mapping[str, Any]], output_dir: str
) -> dict[str, Any]:
    if not task_list:
        raise GoldenCaseError("golden_final_task_missing")
    task_id = str(task_list[-1].get("id") or "")
    committed = context.committed_manifest_for(task_id)
    if committed is None or context.artifact_store is None:
        raise GoldenCaseError("golden_committed_final_missing")
    delivered = Path(output_dir) / CASE_SPECS[state.case_id].final_name
    if not delivered.is_file():
        raise GoldenCaseError("golden_delivered_final_missing")
    if _sha_file(delivered) != committed.content_sha256:
        raise GoldenCaseError("golden_delivered_final_hash_mismatch")
    try:
        payload = json.loads(delivered.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GoldenCaseError("golden_programmatic_evaluator_invalid_json") from exc
    errors: list[str] = []
    if state.case_id == "G1":
        expected = {
            "status": "success",
            "table_count": 1,
            "tables": [
                {
                    "headers": [["item", "count"]],
                    "rows": [["alpha", "2"], ["beta", "3"]],
                }
            ],
        }
        if payload != expected:
            errors.append("inventory_tables_mismatch")
    elif state.case_id == "G2":
        expected = {
            "status": "success",
            "kept_columns": ["name", "score"],
            "row_count": 2,
            "csv": "name,score\nAda,91\nLin,84\n",
        }
        normalized_payload = dict(payload)
        normalized_payload["csv"] = str(payload.get("csv") or "").replace(
            "\r\n", "\n"
        )
        if normalized_payload != expected:
            errors.append("selected_records_mismatch")
    else:
        expected = {
            "row_count": 2,
            "total": 5,
            "items": [
                {"metric": "alpha", "value": 2},
                {"metric": "beta", "value": 3},
            ],
        }
        if payload != expected:
            errors.append("metrics_summary_mismatch")
    result = _sealed(
        {
            "protocol": ORACLE_PROTOCOL,
            "case_id": state.case_id,
            "status": "passed" if not errors else "failed",
            "errors": errors,
            "committed_manifest_sha256": committed.committed_manifest_sha256,
            "content_sha256": committed.content_sha256,
            "artifact_payload_sha256": canonical_sha256(payload),
        },
        "oracle_result_sha256",
    )
    _atomic_json(state.case_dir / "06_programmatic_evaluator.json", result)
    if errors:
        state.fail("Programmatic Oracle", "golden_programmatic_evaluator_failed")
        raise GoldenCaseError("golden_programmatic_evaluator_failed")
    state.oracle_passed = True
    return result


@contextmanager
def _golden_guards(state: GoldenRunState):
    """Attach fail-open observers; production calls and return values stay authoritative."""

    main_module = importlib.import_module("sgar_mvp.main")
    orchestrator_module = importlib.import_module("sgar_mvp.src.orchestrator")
    original_run_pipeline = orchestrator_module.DAGOrchestrator.run_pipeline
    original_execute = orchestrator_module.DAGOrchestrator.execute_sealed_plan
    original_delivery = main_module.extract_deliverables

    async def guarded_run_pipeline(instance: Any, task_list: list[dict[str, Any]], routing: dict[str, Any]):
        try:
            state.task_list = [dict(item) for item in task_list]
        except Exception:
            # Observation failure cannot alter production scheduling.
            pass
        return await original_run_pipeline(instance, task_list, routing)

    async def guarded_execute(instance: Any, subtask: Any, artifact: Any, candidate_resources: Any, resource_index: Any, *args: Any, **kwargs: Any):
        _record_observed_plan(state, artifact, candidate_resources)
        return await original_execute(
            instance,
            subtask,
            artifact,
            candidate_resources,
            resource_index,
            *args,
            **kwargs,
        )

    def guarded_delivery(context: Any, task_list: list[dict[str, Any]], output_dir: str = "execution_artifacts", **kwargs: Any):
        outcome = original_delivery(context, task_list, output_dir=output_dir, **kwargs)
        state.delivery_called = outcome is not None
        if state.delivery_called:
            try:
                state.delivery_context = context
                state.delivery_task_list = tuple(dict(item) for item in task_list)
                state.delivery_output_dir = output_dir
            except Exception:
                # The production Delivery has already completed.  Mark only the
                # post-run Golden assessment as unavailable.
                state.fail(
                    "Programmatic Oracle", "golden_delivery_observation_failed"
                )
        return outcome

    orchestrator_module.DAGOrchestrator.run_pipeline = guarded_run_pipeline
    orchestrator_module.DAGOrchestrator.execute_sealed_plan = guarded_execute
    main_module.extract_deliverables = guarded_delivery
    try:
        yield main_module
    finally:
        orchestrator_module.DAGOrchestrator.run_pipeline = original_run_pipeline
        orchestrator_module.DAGOrchestrator.execute_sealed_plan = original_execute
        main_module.extract_deliverables = original_delivery


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _model_stage_completed(events: Sequence[Mapping[str, Any]], stage: str) -> bool:
    return any(
        item.get("event_type") == "model_call_finished"
        and item.get("stage") == stage
        and item.get("completion_status") == "completed"
        and item.get("response_received") is True
        and not item.get("error_code")
        for item in events
    )


def _evaluation_completed(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        item.get("event_type")
        in {"evaluation_finished", "evaluation_review_finished"}
        and item.get("status") == "pass"
        for item in events
    )


def _planner_attempt_valid(attempts: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        bool(item.get("canonical_contract_sha256"))
        and (item.get("planner_atomicity_audit") or {}).get("valid") is True
        and (item.get("planner_contract_audit") or {}).get("semantic_round_trip")
        is True
        and (item.get("planner_wire_projection") or {}).get("semantic_round_trip")
        is True
        for item in attempts
    )


def _golden_stage_for_primary_failure(failure_stage: str) -> str | None:
    normalized = failure_stage.strip().lower()
    if normalized == "framework_conformance":
        return "Conformance"
    if normalized in {"retrieval", "candidate_pool_preparation"}:
        return "Retrieval"
    if normalized.startswith("plan_compiler_lower"):
        return "Lowering"
    if normalized.startswith("plan_compiler_valid"):
        return "Validation"
    if normalized.startswith("plan_compiler") or normalized == "plan_compilation_persistence":
        return "Compiler"
    if normalized == "resource_execution" or normalized.startswith("runtime"):
        return "Runtime"
    if normalized.startswith("evaluation"):
        return "Sol Evaluator"
    if normalized == "artifact_publication":
        return "Artifact Commit"
    if normalized == "delivery":
        return "Delivery"
    return None


def _finalize_evidence(
    *, state: GoldenRunState, run_dir: Path, exit_code: int
) -> dict[str, Any]:
    run_manifest = _read_json(run_dir / "run_manifest.json") if (run_dir / "run_manifest.json").is_file() else {}
    primary_failure = run_manifest.get("primary_failure") or {}
    if state.first_failure_stage is None and isinstance(primary_failure, Mapping):
        failure_stage = _golden_stage_for_primary_failure(
            str(primary_failure.get("failure_stage") or "")
        )
        failure_code = str(primary_failure.get("failure_code") or "")
        if failure_stage and failure_code:
            state.fail(failure_stage, failure_code)
    model_events = _read_events(run_dir / "model_calls.jsonl")
    started_model_events = [
        item for item in model_events if item.get("event_type") == "model_call_started"
    ]
    semantic_send_counts: dict[str, int] = {}
    for item in started_model_events:
        stage = str(item.get("stage") or "unknown")
        semantic_send_counts[stage] = semantic_send_counts.get(stage, 0) + 1
    retry_attempt_count = sum(
        1 for item in started_model_events if int(item.get("provider_attempt") or 1) > 1
    )
    blocked_events = [
        item for item in model_events if item.get("event_type") == "model_call_blocked"
    ]
    production_cost_path = run_dir / "cost_summary.json"
    production_cost = (
        _read_json(production_cost_path) if production_cost_path.is_file() else {}
    )
    planner_attempts = _read_events(run_dir / "planner_attempts.jsonl")
    execution_events = _read_events(run_dir / "execution_events.jsonl")
    evaluation_events = _read_events(run_dir / "evaluation" / "evaluation_events.jsonl")
    delivery_exists = (run_dir / "delivery_manifest.json").is_file()
    statuses = {stage: "not_reached_due_to_first_failure" for stage in STAGES}
    reached = ["TaskInvocation", "Conformance"]
    planner_passed = bool(state.task_list) or (
        _model_stage_completed(model_events, "planner_decompose")
        and _planner_attempt_valid(planner_attempts)
    )
    profiler_passed = bool(state.task_list) or _model_stage_completed(
        model_events, "retrieval_hyde"
    )
    if planner_passed:
        reached.append("Planner")
    if profiler_passed:
        reached.append("Profiler")
    if state.task_list:
        reached.extend(["Retrieval", "Frozen Candidate Pool"])
    if state.plan_records:
        reached.extend(["Compiler", "Validation", "Lowering", "Execution Authority", "Runtime"])
    evaluator_passed = _evaluation_completed(evaluation_events)
    if evaluator_passed:
        reached.append("Sol Evaluator")
    if state.oracle_passed:
        reached.extend(["Artifact Commit", "Programmatic Oracle"])
    if delivery_exists and state.delivery_called:
        reached.append("Delivery")
    if run_manifest:
        reached.append("terminal manifest")
    for stage in STAGES:
        if stage in reached:
            statuses[stage] = "passed"
        if state.first_failure_stage == stage:
            statuses[stage] = "failed"
            break
    if run_manifest:
        statuses["terminal manifest"] = "passed"
    stage_matrix = {
        "protocol": "sgar-live-golden-stage-matrix-v1",
        "case_id": state.case_id,
        "first_failure_stage": state.first_failure_stage,
        "first_failure_code": state.first_failure_code,
        "stages": [{"stage": stage, "status": statuses[stage]} for stage in STAGES],
    }
    _atomic_json(state.case_dir / "04_stage_matrix.json", stage_matrix)
    execution_trace = _sealed(
        {
            "protocol": "sgar-live-golden-execution-trace-v1",
            "case_id": state.case_id,
            "status": "recorded" if state.plan_records else "not_reached",
            "planner_node_count": len(state.task_list),
            "plans": state.plan_records,
        },
        "execution_trace_sha256",
    )
    _atomic_json(state.case_dir / "05_execution_trace.json", execution_trace)
    cost_summary = _sealed(
        {
            "protocol": "sgar-live-golden-cost-latency-v1",
            "case_id": state.case_id,
            "semantic_send_counts": semantic_send_counts,
            "provider_send_count": len(started_model_events),
            "retry_attempt_count": retry_attempt_count,
            "blocked_call_count": len(blocked_events),
            "model_event_count": len(model_events),
            "execution_event_count": len(execution_events),
            "evaluation_event_count": len(evaluation_events),
            "observed_total_model_cost_usd": production_cost.get(
                "observed_total_model_cost_usd"
            ),
            "resolved_cost_policy": production_cost.get("resolved_cost_policy"),
            "production_cost_summary_file_sha256": (
                _sha_file(production_cost_path) if production_cost else None
            ),
            "run_ledger_hashes": run_manifest.get("ledger_hashes"),
            "unmatched_calls": run_manifest.get("unmatched_calls"),
        },
        "summary_sha256",
    )
    _atomic_json(state.case_dir / "07_cost_latency_summary.json", cost_summary)
    passed = (
        exit_code == 0
        and evaluator_passed
        and state.oracle_passed
        and state.delivery_called
        and delivery_exists
        and run_manifest.get("status") == "succeeded"
        and not any((run_manifest.get("unmatched_calls") or {}).values())
    )
    admission = state.admission or {}
    source_identity = state.source_identity or admission
    terminal = _sealed(
        {
            "protocol": "sgar-live-golden-terminal-summary-v1",
            "case_id": state.case_id,
            "status": "passed_final_delivery" if passed else "failed",
            "runtime_authority": state.runtime_authority,
            "source_authority": (
                "git_worktree"
                if state.runtime_authority == "git"
                else "release_attestation"
            ),
            "release_attestation": (
                "not_requested" if state.runtime_authority == "git" else "verified"
            ),
            "git_head": source_identity.get("git_head"),
            "git_tree": source_identity.get("git_tree"),
            "tracked_dirty": source_identity.get("tracked_dirty"),
            "runtime_classification": source_identity.get("runtime_classification"),
            "activated_system_seal_sha256": admission.get(
                "activated_system_seal_sha256"
            ),
            "main_exit_code": exit_code,
            "first_failure_stage": state.first_failure_stage,
            "first_failure_code": state.first_failure_code,
            "delivery_manifest_sha256": (
                _sha_file(run_dir / "delivery_manifest.json") if delivery_exists else None
            ),
            "run_manifest_sha256": (
                _sha_file(run_dir / "run_manifest.json") if run_manifest else None
            ),
            "provider_send_count": len(started_model_events),
            "retry_attempt_count": retry_attempt_count,
            "observed_total_model_cost_usd": production_cost.get(
                "observed_total_model_cost_usd"
            ),
        },
        "terminal_summary_sha256",
    )
    _atomic_json(state.case_dir / "08_terminal_summary.json", terminal)
    return terminal


def run_case(
    *, case_id: str, admission_path: Path, authorization_sha256: str
) -> int:
    admission, case_dir = _verify_admission(
        case_id=case_id,
        admission_path=admission_path,
        authorization_sha256=authorization_sha256,
    )
    if (case_dir / "08_terminal_summary.json").exists():
        raise GoldenCaseError("golden_case_already_terminal")
    state = GoldenRunState(
        case_id=case_id,
        case_dir=case_dir,
        admission=admission,
        source_identity=admission,
        runtime_authority="release",
    )
    config_path = ROOT / "sgar_mvp" / "config.json"
    run_dir = case_dir / "formal_run"
    spec = CASE_SPECS[case_id]
    previous_argv = list(sys.argv)
    exit_code = 1
    with _golden_guards(state) as main_module:
        try:
            sys.argv = [
                "sgar_mvp.main",
                "--request-manifest",
                str(spec.request_path),
                "--public-input-root",
                str(spec.root),
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
                "--runtime-authority",
                "release",
            ]
            exit_code = int(main_module.main() or 0)
        except GoldenCaseError as exc:
            state.fail(state.first_failure_stage or "Runtime", exc.code)
            exit_code = 1
        finally:
            sys.argv = previous_argv
    _evaluate_after_framework(state)
    terminal = _finalize_evidence(
        state=state, run_dir=run_dir, exit_code=exit_code
    )
    print(json.dumps(terminal, ensure_ascii=False, sort_keys=True))
    return 0 if terminal["status"] == "passed_final_delivery" else 1


def direct_case(
    *,
    case_id: str,
    output_root: Path,
    authorize_configured_provider_disclosure: bool,
    batch_id: str | None = None,
) -> int:
    """Run one result-oriented Case from the current Git checkout."""

    if not authorize_configured_provider_disclosure:
        raise GoldenCaseError(
            "golden_configured_provider_disclosure_authorization_required"
        )
    main_module = importlib.import_module("sgar_mvp.main")
    git_identity = main_module._git_checkout_runtime_identity(ROOT)
    config_path = ROOT / "sgar_mvp" / "config.json"
    config = _read_json(config_path)
    endpoint = production_model_endpoint_identity(
        base_url=str((config.get("llm_settings") or {}).get("base_url") or "")
    )
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    resolved_batch = batch_id or (
        f"{case_id.lower()}-direct-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"{str(git_identity.get('git_head') or 'unknown')[:7]}"
    )
    case_dir = output_root / resolved_batch
    case_dir.mkdir(parents=False, exist_ok=False)
    spec = CASE_SPECS[case_id]
    fixture_hashes = {
        path.relative_to(ROOT).as_posix(): _sha_file(path)
        for path in _case_input_files(spec)
    }
    direct_context = {
        "protocol": DIRECT_RUN_PROTOCOL,
        "case_id": case_id,
        "runtime_authority": "git",
        **dict(git_identity),
        "endpoint_identity_sha256": endpoint.identity_sha256,
        "fixture_files_sha256": fixture_hashes,
        "configured_provider_disclosure_authorized": True,
    }
    _atomic_json(case_dir / "00_direct_run_context.json", direct_context)

    state = GoldenRunState(
        case_id=case_id,
        case_dir=case_dir,
        source_identity=git_identity,
        runtime_authority="git",
    )
    run_dir = case_dir / "direct_run"
    previous_argv = list(sys.argv)
    exit_code = 1
    with _golden_guards(state) as guarded_main_module:
        try:
            sys.argv = [
                "sgar_mvp.main",
                "--request-manifest",
                str(spec.request_path),
                "--public-input-root",
                str(spec.root),
                "--config",
                str(config_path),
                "--run-dir",
                str(run_dir),
                "--runtime-authority",
                "git",
            ]
            exit_code = int(guarded_main_module.main() or 0)
        except GoldenCaseError as exc:
            state.fail(state.first_failure_stage or "Runtime", exc.code)
            exit_code = 1
        finally:
            sys.argv = previous_argv
    _evaluate_after_framework(state)
    terminal = _finalize_evidence(
        state=state,
        run_dir=run_dir,
        exit_code=exit_code,
    )
    print(json.dumps(terminal, ensure_ascii=False, sort_keys=True))
    return 0 if terminal["status"] == "passed_final_delivery" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run release-audited or Git-authoritative SGAR Golden Cases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--case", choices=tuple(CASE_SPECS), required=True)
    prepare.add_argument("--output-root", type=Path, default=ROOT / "runs" / "live_golden_cases")
    prepare.add_argument("--activated-seal", type=Path, required=True)
    prepare.add_argument("--batch-id")
    run = subparsers.add_parser("run")
    run.add_argument("--case", choices=tuple(CASE_SPECS), required=True)
    run.add_argument("--admission", type=Path, required=True)
    run.add_argument("--authorization-sha256", required=True)
    direct = subparsers.add_parser("direct")
    direct.add_argument("--case", choices=tuple(CASE_SPECS), required=True)
    direct.add_argument(
        "--output-root", type=Path, default=ROOT / "runs" / "golden-direct"
    )
    direct.add_argument("--batch-id")
    direct.add_argument(
        "--authorize-configured-provider-disclosure", action="store_true"
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        prepare_case(
            case_id=args.case,
            output_root=args.output_root,
            activated_seal_path=args.activated_seal,
            batch_id=args.batch_id,
        )
        return 0
    if args.command == "direct":
        return direct_case(
            case_id=args.case,
            output_root=args.output_root,
            authorize_configured_provider_disclosure=(
                args.authorize_configured_provider_disclosure
            ),
            batch_id=args.batch_id,
        )
    return run_case(
        case_id=args.case,
        admission_path=args.admission,
        authorization_sha256=args.authorization_sha256,
    )


if __name__ == "__main__":
    raise SystemExit(main())
