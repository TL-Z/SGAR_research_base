import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sgar_mvp.main import _runtime_policy_path
from sgar_mvp.scripts.run_release_provider_probes import release_probe_specs
from sgar_mvp.src.control_role_policy import ControlRolePolicyV1, load_control_role_policy
from sgar_mvp.src.evaluation_contracts import EvaluatorPolicy


EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "experiments"
    / "gemini-3.8-flash"
)


def _gemini_policy_payload() -> dict:
    return json.loads((EXPERIMENT_DIR / "control_role_policy.json").read_text())


def test_legacy_control_policy_remains_sol_only() -> None:
    payload = _gemini_policy_payload()
    payload["protocol"] = "sgar-control-role-policy-v2"
    with pytest.raises(ValidationError, match="control_role_policy_v2_identity_invalid"):
        ControlRolePolicyV1.model_validate(payload)


def test_gemini_policy_builds_exact_high_reasoning_requests() -> None:
    policy = load_control_role_policy(EXPERIMENT_DIR / "control_role_policy.json")
    specs = release_probe_specs(policy)
    assert tuple(item["role"] for item in specs) == tuple(item.role for item in policy.roles)
    assert all(item["request"]["model"] == "gemini-3.8-flash" for item in specs)
    assert all(item["request"]["reasoning_effort"] == "high" for item in specs)
    assert all("temperature" not in item["request"] for item in specs)


def test_reasoning_and_temperature_are_mutually_exclusive() -> None:
    payload = _gemini_policy_payload()
    payload["roles"][0]["temperature"] = 0.0
    with pytest.raises(ValidationError, match="control_role_reasoning_and_temperature_conflict"):
        ControlRolePolicyV1.model_validate(payload)


def test_legacy_evaluator_policy_remains_sol_only() -> None:
    payload = json.loads((EXPERIMENT_DIR / "evaluator_policy.json").read_text())
    payload["protocol"] = "sgar-evaluator-policy-v2"
    with pytest.raises(ValidationError, match="evaluator_policy_v2_identity_invalid"):
        EvaluatorPolicy.model_validate(payload)


def test_runtime_policy_path_must_stay_inside_project() -> None:
    config = {
        "runtime_settings": {
            "control_role_policy_path": str(EXPERIMENT_DIR / "control_role_policy.json")
        }
    }
    assert _runtime_policy_path(
        config,
        "control_role_policy_path",
        "sgar_mvp/config/control_role_policy.json",
    ) == (EXPERIMENT_DIR / "control_role_policy.json").resolve()
    with pytest.raises(RuntimeError, match="runtime_control_role_policy_path_invalid"):
        _runtime_policy_path(
            {"runtime_settings": {"control_role_policy_path": "/tmp/not-allowed.json"}},
            "control_role_policy_path",
            "sgar_mvp/config/control_role_policy.json",
        )
