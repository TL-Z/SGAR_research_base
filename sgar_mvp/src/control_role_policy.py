"""Immutable production policy for SGAR semantic control roles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .pipeline_control import FrozenContract, canonical_sha256


CONTROL_ROLE_POLICY_PROTOCOL = "sgar-control-role-policy-v2"
CONTROL_ROLE_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "control_role_policy.json"
)
ControlRole = Literal[
    "profiler",
    "planner",
    "plan_compiler",
    "plan_adaptation",
    "evaluator",
]


class ControlRoleInvocationPolicyV1(FrozenContract):
    """Exact request policy for one production control role."""

    role: ControlRole
    resource_id: Literal["model.gpt_5_6_sol.v1"] = "model.gpt_5_6_sol.v1"
    api_model_id: Literal["gpt-5.6-sol"] = "gpt-5.6-sol"
    reasoning_effort: Literal["high", "xhigh"]
    temperature: None = None
    allow_model_failover: Literal[False] = False
    response_mode: Literal["native_strict_schema"] = "native_strict_schema"
    role_policy_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "ControlRoleInvocationPolicyV1":
        projection = self.model_dump(mode="python", exclude={"role_policy_sha256"})
        expected = canonical_sha256(projection)
        if self.role_policy_sha256 and self.role_policy_sha256 != expected:
            raise ValueError("control_role_invocation_policy_sha256_mismatch")
        object.__setattr__(self, "role_policy_sha256", expected)
        return self

    def request_fields(self) -> dict[str, str]:
        """Provider request fields authorized by this policy.

        Temperature is intentionally absent for Sol reasoning requests.
        """

        return {"reasoning_effort": self.reasoning_effort}


class ControlRolePolicyV1(FrozenContract):
    """Sol-only, no-failover policy for every production semantic control role."""

    protocol: Literal[CONTROL_ROLE_POLICY_PROTOCOL] = CONTROL_ROLE_POLICY_PROTOCOL
    roles: tuple[ControlRoleInvocationPolicyV1, ...]
    policy_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "ControlRolePolicyV1":
        names = tuple(item.role for item in self.roles)
        if names != (
            "profiler",
            "planner",
            "plan_compiler",
            "plan_adaptation",
            "evaluator",
        ):
            raise ValueError("control_role_policy_roles_invalid")
        expected_efforts = {
            "profiler": "xhigh",
            "planner": "xhigh",
            "plan_compiler": "xhigh",
            "plan_adaptation": "xhigh",
            "evaluator": "high",
        }
        if any(
            item.reasoning_effort != expected_efforts[item.role]
            for item in self.roles
        ):
            raise ValueError("control_role_policy_reasoning_effort_invalid")
        if len({item.api_model_id for item in self.roles}) != 1:
            raise ValueError("control_role_policy_model_mismatch")
        projection = self.model_dump(mode="python", exclude={"policy_sha256"})
        expected = canonical_sha256(projection)
        if self.policy_sha256 and self.policy_sha256 != expected:
            raise ValueError("control_role_policy_sha256_mismatch")
        object.__setattr__(self, "policy_sha256", expected)
        return self

    def for_role(self, role: ControlRole) -> ControlRoleInvocationPolicyV1:
        for item in self.roles:
            if item.role == role:
                return item
        raise ValueError(f"control_role_policy_role_missing:{role}")


def load_control_role_policy(
    path: Path = CONTROL_ROLE_POLICY_PATH,
) -> ControlRolePolicyV1:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return ControlRolePolicyV1.model_validate(payload)


__all__ = [
    "CONTROL_ROLE_POLICY_PATH",
    "CONTROL_ROLE_POLICY_PROTOCOL",
    "ControlRole",
    "ControlRoleInvocationPolicyV1",
    "ControlRolePolicyV1",
    "load_control_role_policy",
]
