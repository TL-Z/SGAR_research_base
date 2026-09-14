"""Bounded Controller Session contracts and deterministic state machine.

Stage B deliberately exposes no callable Tool surface.  This module owns the
session loop, final-contract validation, bounded validation feedback, and
host-free evidence; provider transport remains in ``ControllerTurnExecutor``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from pydantic import Field, field_validator, model_serializer, model_validator

from .controller_tooling import (
    ControllerCallableToolSpecV1,
    project_provider_tool_schema,
    require_unique_provider_tool_names,
)
from .controller_tool_runtime import (
    CONTROLLER_TOOL_RUNTIME_PROTOCOL,
    ControllerToolCallIntentV1,
    ControllerToolInvocationResultV1,
    ControllerToolRuntimeError,
    ControllerToolTerminalContext,
    prevalidate_tool_call_intents,
)
from .model_response_contracts import (
    ModelResponseContractError,
    normalize_portable_wire_instance,
    require_semantic_json_schema,
    strict_json_loads,
    validate_json_schema_instance,
)
from .pipeline_control import FrozenContract, canonical_json_bytes, canonical_sha256
from .terminal_failure import TerminalFailureEnvelope
from .controller_skills import ControllerSkillError, SkillContextBundleV1, validate_skill_snapshot


CONTROLLER_SESSION_POLICY_PROTOCOL = "sgar-controller-session-policy-v1"
CONTROLLER_SESSION_PROTOCOL = "sgar-controller-session-v1"
CONTROLLER_SESSION_V2_PROTOCOL = "sgar-controller-session-v2"
CONTROLLER_INPUT_SNAPSHOT_PROTOCOL = "sgar-controller-input-snapshot-v1"
CONTROLLER_TURN_PROTOCOL = "sgar-controller-turn-v1"
CONTROLLER_VALIDATION_FEEDBACK_PROTOCOL = "sgar-controller-validation-feedback-v1"
CONTROLLER_SESSION_RESULT_PROTOCOL = "sgar-controller-session-result-v2"
CONTROLLER_SESSION_SUMMARY_PROTOCOL = "sgar-controller-session-summary-v1"
CONTROLLER_SESSION_SUMMARY_V2_PROTOCOL = "sgar-controller-session-summary-v2"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:^|[\s'\"=(])(?:[a-z]:[\\/]|\\\\)")
_MAX_FEEDBACK_REASON_BYTES = 1024


class ControllerSessionError(ValueError):
    """A sealed session contract or deterministic state invariant failed."""


def _require_sha256(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{field_name}_must_be_sha256_hex")
    return normalized


def _host_path_locator(value: Any, locator: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _host_path_locator(item, f"{locator}.{key}")
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _host_path_locator(item, f"{locator}[{index}]")
            if found:
                return found
        return None
    if isinstance(value, str) and (
        _WINDOWS_ABSOLUTE.search(value) or value.startswith("file:///")
    ):
        return locator
    return None


def _ensure_host_free(value: Any, *, field_name: str) -> Any:
    locator = _host_path_locator(value)
    if locator:
        raise ValueError(f"{field_name}_contains_host_path:{locator}")
    return value


def _canonical_value(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


class ControllerSessionPolicyV1(FrozenContract):
    """System-owned exploration budget; never supplied by Planner/Compiler."""

    protocol: Literal[CONTROLLER_SESSION_POLICY_PROTOCOL] = (
        CONTROLLER_SESSION_POLICY_PROTOCOL
    )
    max_turns: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_semantic_repairs: int = Field(ge=0)
    max_same_call_repeats: int = Field(ge=1)
    max_same_failure_repeats: int = Field(ge=1)
    max_inline_tool_result_bytes: int = Field(ge=1)
    cost_authority: Literal["existing_run_cost_policy"] = "existing_run_cost_policy"
    policy_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionPolicyV1":
        projection = self.model_dump(mode="python", exclude={"policy_sha256"})
        expected = canonical_sha256(projection)
        if self.policy_sha256:
            supplied = _require_sha256(
                self.policy_sha256, field_name="controller_session_policy_sha256"
            )
            if supplied != expected:
                raise ValueError("controller_session_policy_sha256_mismatch")
        object.__setattr__(self, "policy_sha256", expected)
        return self


def load_controller_session_policy(
    path: str | Path | None = None,
) -> ControllerSessionPolicyV1:
    """Load the single committed policy with strict validation and sealing."""

    policy_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[1]
        / "config"
        / "controller_session_policy.json"
    )
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControllerSessionError("controller_session_policy_load_failed") from exc
    try:
        return ControllerSessionPolicyV1.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ControllerSessionError("controller_session_policy_invalid") from exc


class ControllerContextBindingV1(FrozenContract):
    """An exact material binding derived from the Compiler's authorized input."""

    target_port: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    handle_id: str = Field(min_length=1)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> "ControllerContextBindingV1":
        _ensure_host_free(self.model_dump(mode="python"), field_name="controller_context_binding")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", self.target_port):
            raise ValueError("controller_context_port_invalid")
        if not self.source_id.startswith("artifact:") or self.source_id != self.source_id.strip():
            raise ValueError("controller_context_source_identity_invalid")
        _require_sha256(self.content_sha256, field_name="controller_context_content_sha256")
        return self


class ContextBoundContract(FrozenContract):
    """Internal material wiring; absent bindings retain historical serialization."""

    context_bindings: tuple[ControllerContextBindingV1, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize_context(self, handler: Any) -> dict[str, Any]:
        result = handler(self)
        if not self.context_bindings:
            result.pop("context_bindings", None)
        return result

    @model_validator(mode="after")
    def _context_ports(self) -> "ContextBoundContract":
        ports = tuple(item.target_port for item in self.context_bindings)
        identities = tuple((item.target_port, item.source_id, item.handle_id, item.content_sha256)
                           for item in self.context_bindings)
        if identities != tuple(sorted(set(identities))) or len(set(
            (item.target_port, item.source_id) for item in self.context_bindings
        )) != len(identities):
            raise ValueError("controller_context_binding_duplicate_or_not_canonical")
        sources: dict[str, tuple[str, str]] = {}
        for item in self.context_bindings:
            identity = (item.handle_id, item.content_sha256)
            if item.source_id in sources and sources[item.source_id] != identity:
                raise ValueError("controller_context_source_identity_ambiguous")
            sources[item.source_id] = identity
        inputs = getattr(self, "declared_input_bindings", getattr(self, "input_bindings", {}))
        names = set(inputs) if isinstance(inputs, Mapping) else {item.name for item in inputs}
        if names.intersection(ports):
            raise ValueError("controller_context_binding_ambiguous")
        return self


class ControllerSessionSpecV1(ContextBoundContract):
    """Framework-derived plan authority for one Model or Agent controller."""

    protocol: Literal[CONTROLLER_SESSION_PROTOCOL] = CONTROLLER_SESSION_PROTOCOL
    subtask_id: str = Field(min_length=1)
    subtask_revision: int = Field(ge=0)
    controller_step_id: str = Field(min_length=1)
    controller_resource_id: str = Field(min_length=1)
    controller_resource_type: Literal["Model", "Agent"]
    backing_model_resource_id: str = Field(min_length=1)
    task_instruction: str = Field(min_length=1)
    declared_input_bindings: dict[str, Any] = Field(default_factory=dict)
    expected_output_contract: dict[str, Any]
    controller_session_policy_sha256: str
    callable_tool_scope: tuple[str, ...] = ()
    dynamic_argument_authority: Literal["none"] = "none"
    tool_result_continuation: Literal["disabled"] = "disabled"
    spec_sha256: str = ""

    @field_validator("controller_session_policy_sha256")
    @classmethod
    def _policy_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="controller_session_policy_sha256")

    @model_validator(mode="before")
    @classmethod
    def _canonicalize(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        projection = _canonical_value(dict(value))
        _ensure_host_free(projection, field_name="controller_session_spec")
        return projection

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionSpecV1":
        if self.controller_resource_type == "Model" and (
            self.backing_model_resource_id != self.controller_resource_id
        ):
            raise ValueError("model_controller_backing_model_identity_mismatch")
        if self.callable_tool_scope:
            raise ValueError("controller_stage_b_tool_scope_must_be_empty")
        projection = self.model_dump(mode="python", exclude={"spec_sha256"})
        expected = canonical_sha256(projection)
        if self.spec_sha256:
            supplied = _require_sha256(
                self.spec_sha256, field_name="controller_session_spec_sha256"
            )
            if supplied != expected:
                raise ValueError("controller_session_spec_sha256_mismatch")
        object.__setattr__(self, "spec_sha256", expected)
        return self


class ControllerSessionSpecV2(ContextBoundContract):
    """Non-empty callable scope sealed for the future Stage-C2 runtime."""

    protocol: Literal[CONTROLLER_SESSION_V2_PROTOCOL] = CONTROLLER_SESSION_V2_PROTOCOL
    subtask_id: str = Field(min_length=1)
    subtask_revision: int = Field(ge=0)
    controller_step_id: str = Field(min_length=1)
    controller_resource_id: str = Field(min_length=1)
    controller_resource_type: Literal["Model", "Agent"]
    backing_model_resource_id: str = Field(min_length=1)
    task_instruction: str = Field(min_length=1)
    declared_input_bindings: dict[str, Any] = Field(default_factory=dict)
    expected_output_contract: dict[str, Any]
    controller_session_policy_sha256: str
    callable_tool_scope: tuple[str, ...]
    callable_tools: tuple[ControllerCallableToolSpecV1, ...]
    dynamic_argument_authority: Literal["sealed_callable_tool_contracts"] = (
        "sealed_callable_tool_contracts"
    )
    tool_result_continuation: Literal["enabled"] = "enabled"
    spec_sha256: str = ""

    @field_validator("controller_session_policy_sha256")
    @classmethod
    def _policy_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="controller_session_policy_sha256")

    @model_validator(mode="before")
    @classmethod
    def _canonicalize(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        projection = _canonical_value(dict(value))
        _ensure_host_free(projection, field_name="controller_session_spec_v2")
        return projection

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionSpecV2":
        if self.controller_resource_type == "Model" and (
            self.backing_model_resource_id != self.controller_resource_id
        ):
            raise ValueError("model_controller_backing_model_identity_mismatch")
        if not self.callable_tools:
            raise ValueError("controller_session_v2_callable_scope_empty")
        if self.callable_tools != tuple(
            sorted(self.callable_tools, key=lambda item: item.callable_id)
        ):
            raise ValueError("controller_callable_tools_not_canonical")
        callable_ids = tuple(item.callable_id for item in self.callable_tools)
        if callable_ids != tuple(sorted(set(callable_ids))):
            raise ValueError("controller_callable_tool_identity_duplicate")
        if self.callable_tool_scope != callable_ids:
            raise ValueError("controller_callable_tool_scope_identity_mismatch")
        require_unique_provider_tool_names(self.callable_tools)
        projection = self.model_dump(mode="python", exclude={"spec_sha256"})
        expected = canonical_sha256(projection)
        if self.spec_sha256:
            supplied = _require_sha256(
                self.spec_sha256, field_name="controller_session_spec_sha256"
            )
            if supplied != expected:
                raise ValueError("controller_session_spec_sha256_mismatch")
        object.__setattr__(self, "spec_sha256", expected)
        return self


ControllerSessionSpec = ControllerSessionSpecV1 | ControllerSessionSpecV2


def derive_controller_session_spec(
    *,
    subtask_id: str,
    subtask_revision: int,
    controller_step_id: str,
    controller_resource_id: str,
    controller_resource_type: str,
    backing_model_resource_id: str | None,
    task_instruction: str,
    declared_input_bindings: Mapping[str, Any],
    expected_output_contract: Mapping[str, Any],
    policy: ControllerSessionPolicyV1 | None = None,
    callable_tools: Sequence[ControllerCallableToolSpecV1] = (),
    context_bindings: Sequence[ControllerContextBindingV1] = (),
) -> ControllerSessionSpec:
    active_policy = policy or load_controller_session_policy()
    if controller_resource_type not in {"Model", "Agent"}:
        raise ControllerSessionError("controller_resource_not_authorized")
    backing = (
        controller_resource_id
        if controller_resource_type == "Model"
        else str(backing_model_resource_id or "").strip()
    )
    if not backing:
        raise ControllerSessionError("controller_backing_model_not_ready")
    shared = {
        "subtask_id": subtask_id,
        "subtask_revision": subtask_revision,
        "controller_step_id": controller_step_id,
        "controller_resource_id": controller_resource_id,
        "controller_resource_type": controller_resource_type,
        "backing_model_resource_id": backing,
        "task_instruction": task_instruction,
        "declared_input_bindings": dict(declared_input_bindings),
        "context_bindings": tuple(context_bindings),
        "expected_output_contract": dict(expected_output_contract),
        "controller_session_policy_sha256": active_policy.policy_sha256,
    }
    if callable_tools:
        sealed_tools = tuple(sorted(callable_tools, key=lambda item: item.callable_id))
        require_unique_provider_tool_names(sealed_tools)
        return ControllerSessionSpecV2(
            **shared,
            callable_tool_scope=tuple(item.callable_id for item in sealed_tools),
            callable_tools=sealed_tools,
        )
    return ControllerSessionSpecV1(
        **shared,
    )


class ControllerInputDescriptorV1(FrozenContract):
    name: str = Field(min_length=1)
    binding_sha256: str
    content_sha256: str
    content_bytes: int = Field(ge=0)
    bounded_content: Any
    artifact_refs: tuple[str, ...] = ()
    logical_refs: tuple[str, ...] = ()

    @field_validator("binding_sha256", "content_sha256")
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _host_free(self) -> "ControllerInputDescriptorV1":
        _ensure_host_free(
            self.model_dump(mode="python"), field_name="controller_input_descriptor"
        )
        return self


class ControllerSessionInputSnapshotV1(FrozenContract):
    protocol: Literal[CONTROLLER_INPUT_SNAPSHOT_PROTOCOL] = (
        CONTROLLER_INPUT_SNAPSHOT_PROTOCOL
    )
    session_id: str = Field(min_length=1)
    spec_sha256: str
    resolved_input_descriptors: tuple[ControllerInputDescriptorV1, ...]
    provenance_identities: tuple[str, ...] = ()
    snapshot_sha256: str = ""

    @field_validator("spec_sha256")
    @classmethod
    def _spec_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="controller_session_spec_sha256")

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionInputSnapshotV1":
        names = tuple(item.name for item in self.resolved_input_descriptors)
        if names != tuple(sorted(set(names))):
            raise ValueError("controller_input_snapshot_names_not_unique_sorted")
        if self.provenance_identities != tuple(
            sorted(set(self.provenance_identities))
        ):
            raise ValueError("controller_input_provenance_not_unique_sorted")
        projection = self.model_dump(mode="python", exclude={"snapshot_sha256"})
        _ensure_host_free(projection, field_name="controller_input_snapshot")
        expected = canonical_sha256(projection)
        if self.snapshot_sha256:
            supplied = _require_sha256(
                self.snapshot_sha256, field_name="controller_input_snapshot_sha256"
            )
            if supplied != expected:
                raise ValueError("controller_input_snapshot_sha256_mismatch")
        object.__setattr__(self, "snapshot_sha256", expected)
        return self


def _binding_refs(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    artifacts: set[str] = set()
    logical: set[str] = set()

    def visit(item: Any, key: str | None = None) -> None:
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, key)
        elif isinstance(item, str) and item.startswith("artifact:"):
            artifacts.add(item)
        elif isinstance(item, str) and key in {
            "path",
            "logical_path",
            "logical_locator",
            "semantic_ref",
        }:
            logical.add(item)

    visit(value)
    return tuple(sorted(artifacts)), tuple(sorted(logical))


def build_controller_input_snapshot(
    *,
    run_id: str,
    spec: ControllerSessionSpec,
    resolved_inputs: Mapping[str, Any],
    policy: ControllerSessionPolicyV1,
    provenance_identities: Sequence[str] = (),
    resolved_context: Mapping[str, str] | None = None,
) -> ControllerSessionInputSnapshotV1:
    if spec.controller_session_policy_sha256 != policy.policy_sha256:
        raise ControllerSessionError("controller_policy_identity_mismatch")
    declared_names = set(spec.declared_input_bindings)
    if set(resolved_inputs) != declared_names:
        raise ControllerSessionError("controller_input_contract_invalid")
    descriptors: list[ControllerInputDescriptorV1] = []
    context_values = dict(resolved_context or {})
    if set(context_values) != {item.source_id for item in spec.context_bindings}:
        raise ControllerSessionError("controller_context_material_missing_or_undeclared")
    port_bindings: dict[str, list[ControllerContextBindingV1]] = {}
    for binding in spec.context_bindings:
        port_bindings.setdefault(binding.target_port, []).append(binding)
    for port, bindings in sorted(port_bindings.items()):
        materials = []
        for binding in bindings:
            content = context_values[binding.source_id]
            if not isinstance(content, str):
                raise ControllerSessionError("controller_context_material_not_utf8")
            raw = content.encode("utf-8")
            if hashlib.sha256(raw).hexdigest() != binding.content_sha256:
                raise ControllerSessionError("controller_context_content_hash_mismatch")
            materials.append({"source_id": binding.source_id,
                              "source_content_sha256": binding.content_sha256, "content": content})
        material = materials[0] if len(materials) == 1 else {"materials": materials}
        binding_identity = [item.model_dump(mode="json") for item in bindings]
        if len(canonical_json_bytes(material)) > policy.max_inline_tool_result_bytes:
            raise ControllerSessionError("controller_context_material_exceeds_bound")
        descriptors.append(ControllerInputDescriptorV1(
            name=port,
            binding_sha256=canonical_sha256(binding_identity[0] if len(bindings) == 1 else binding_identity),
            content_sha256=canonical_sha256(material),
            content_bytes=len(canonical_json_bytes(material)),
            bounded_content=material,
            artifact_refs=tuple(item.source_id for item in bindings),
        ))
    for name in sorted(declared_names):
        content = _canonical_value(resolved_inputs[name])
        _ensure_host_free(content, field_name=f"controller_input:{name}")
        encoded = canonical_json_bytes(content)
        if len(encoded) > policy.max_inline_tool_result_bytes:
            raise ControllerSessionError("controller_input_contract_invalid")
        binding = spec.declared_input_bindings[name]
        artifact_refs, logical_refs = _binding_refs(binding)
        descriptors.append(
            ControllerInputDescriptorV1(
                name=name,
                binding_sha256=canonical_sha256(binding),
                content_sha256=canonical_sha256(content),
                content_bytes=len(encoded),
                bounded_content=content,
                artifact_refs=artifact_refs,
                logical_refs=logical_refs,
            )
        )
    session_id = canonical_sha256(
        {
            "protocol": CONTROLLER_SESSION_PROTOCOL,
            "run_id": str(run_id),
            "spec_sha256": spec.spec_sha256,
        }
    )
    return ControllerSessionInputSnapshotV1(
        session_id=session_id,
        spec_sha256=spec.spec_sha256,
        resolved_input_descriptors=tuple(sorted(descriptors, key=lambda item: item.name)),
        provenance_identities=tuple(sorted(set(str(item) for item in provenance_identities))),
    )


class ControllerTurnResultV1(FrozenContract):
    protocol: Literal[CONTROLLER_TURN_PROTOCOL] = CONTROLLER_TURN_PROTOCOL
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    turn_index: int = Field(ge=1)
    status: Literal["success", "failure"]
    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None
    transport_audit: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    model_accounting_reference: dict[str, Any] | None = None
    request_sha256: str
    response_sha256: str
    candidate_sha256: str
    latency_ms: float = Field(ge=0)
    failure_code: str | None = None
    terminal_failure: TerminalFailureEnvelope | None = None

    @field_validator("request_sha256", "response_sha256", "candidate_sha256")
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_result(self) -> "ControllerTurnResultV1":
        if self.status == "failure" and not self.failure_code:
            raise ValueError("controller_turn_failure_code_missing")
        if self.status == "success" and (self.failure_code or self.terminal_failure):
            raise ValueError("controller_turn_success_has_failure_code")
        _ensure_host_free(
            self.model_dump(mode="python"), field_name="controller_turn_result"
        )
        return self


class ValidationFeedbackV1(FrozenContract):
    protocol: Literal[CONTROLLER_VALIDATION_FEEDBACK_PROTOCOL] = (
        CONTROLLER_VALIDATION_FEEDBACK_PROTOCOL
    )
    target_contract_sha256: str
    error_path: str = Field(min_length=1)
    error_category: str = Field(min_length=1)
    bounded_reason: str = Field(min_length=1, max_length=1024)
    previous_candidate_sha256: str
    feedback_sha256: str = ""

    @field_validator("target_contract_sha256", "previous_candidate_sha256")
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal(self) -> "ValidationFeedbackV1":
        projection = self.model_dump(mode="python", exclude={"feedback_sha256"})
        _ensure_host_free(projection, field_name="controller_validation_feedback")
        expected = canonical_sha256(projection)
        if self.feedback_sha256:
            supplied = _require_sha256(
                self.feedback_sha256, field_name="validation_feedback_sha256"
            )
            if supplied != expected:
                raise ValueError("validation_feedback_sha256_mismatch")
        object.__setattr__(self, "feedback_sha256", expected)
        return self


class ControllerSessionSummaryV1(FrozenContract):
    protocol: Literal[CONTROLLER_SESSION_SUMMARY_PROTOCOL] = (
        CONTROLLER_SESSION_SUMMARY_PROTOCOL
    )
    session_id: str = Field(min_length=1)
    actual_turns: int = Field(ge=0)
    semantic_repairs: int = Field(ge=0)
    terminal_status: Literal["success", "failure"]
    terminal_failure_code: str | None = None
    elapsed_ms: float = Field(ge=0)
    controller_resource_id: str = Field(min_length=1)
    backing_model_resource_id: str = Field(min_length=1)
    input_snapshot_sha256: str
    policy_sha256: str
    model_accounting_references: tuple[dict[str, Any], ...] = ()
    summary_sha256: str = ""

    @field_validator("input_snapshot_sha256", "policy_sha256")
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionSummaryV1":
        if self.terminal_status == "success" and self.terminal_failure_code:
            raise ValueError("successful_controller_summary_has_failure_code")
        if self.terminal_status == "failure" and not self.terminal_failure_code:
            raise ValueError("failed_controller_summary_missing_failure_code")
        projection = self.model_dump(mode="python", exclude={"summary_sha256"})
        _ensure_host_free(projection, field_name="controller_session_summary")
        expected = canonical_sha256(projection)
        if self.summary_sha256 and self.summary_sha256 != expected:
            raise ValueError("controller_session_summary_sha256_mismatch")
        object.__setattr__(self, "summary_sha256", expected)
        return self


class ControllerSessionSummaryV2(FrozenContract):
    """Additive C2 evidence without changing the sealed Stage-B V1 summary."""

    protocol: Literal[CONTROLLER_SESSION_SUMMARY_V2_PROTOCOL] = (
        CONTROLLER_SESSION_SUMMARY_V2_PROTOCOL
    )
    runtime_protocol: Literal[CONTROLLER_TOOL_RUNTIME_PROTOCOL] = (
        CONTROLLER_TOOL_RUNTIME_PROTOCOL
    )
    session_id: str = Field(min_length=1)
    actual_turns: int = Field(ge=0)
    semantic_repairs: int = Field(ge=0)
    terminal_status: Literal["success", "failure"]
    terminal_failure_code: str | None = None
    elapsed_ms: float = Field(ge=0)
    controller_resource_id: str = Field(min_length=1)
    backing_model_resource_id: str = Field(min_length=1)
    input_snapshot_sha256: str
    policy_sha256: str
    model_accounting_references: tuple[dict[str, Any], ...] = ()
    tool_call_count: int = Field(ge=0)
    tool_success_count: int = Field(ge=0)
    tool_failure_count: int = Field(ge=0)
    tool_observation_bytes: int = Field(ge=0)
    resource_call_ids: tuple[str, ...] = ()
    controller_tool_provenance_ids: tuple[str, ...] = ()
    tool_observation_sha256s: tuple[str, ...] = ()
    terminal_failure: ControllerToolTerminalContext | None = None
    failed_tool_attempts: tuple[ControllerToolTerminalContext, ...] = ()
    requested_tool_action_count: int = Field(default=0, ge=0)
    accepted_tool_action_count: int = Field(default=0, ge=0)
    evidence_incomplete: bool = False
    secondary_audit_failures: tuple[str, ...] = ()
    summary_sha256: str = ""

    @field_validator("input_snapshot_sha256", "policy_sha256")
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionSummaryV2":
        if self.terminal_status == "success" and self.terminal_failure_code:
            raise ValueError("successful_controller_summary_has_failure_code")
        if self.terminal_status == "failure" and not self.terminal_failure_code:
            raise ValueError("failed_controller_summary_missing_failure_code")
        if self.tool_success_count + self.tool_failure_count != self.tool_call_count:
            raise ValueError("controller_tool_summary_count_mismatch")
        if not (
            len(self.resource_call_ids)
            == len(self.controller_tool_provenance_ids)
            == len(self.tool_observation_sha256s)
            <= self.tool_call_count
        ):
            raise ValueError("controller_tool_summary_reference_count_mismatch")
        projection = self.model_dump(mode="python", exclude={"summary_sha256"})
        _ensure_host_free(projection, field_name="controller_session_summary_v2")
        expected = canonical_sha256(projection)
        if self.summary_sha256 and self.summary_sha256 != expected:
            raise ValueError("controller_session_summary_sha256_mismatch")
        object.__setattr__(self, "summary_sha256", expected)
        return self


class ControllerSessionResultV1(FrozenContract):
    protocol: Literal[CONTROLLER_SESSION_RESULT_PROTOCOL] = (
        CONTROLLER_SESSION_RESULT_PROTOCOL
    )
    status: Literal["success", "failure"]
    output_data: str = ""
    failure_code: str | None = None
    terminal_failure: TerminalFailureEnvelope | None = None
    controller_session_spec_sha256: str
    controller_policy_sha256: str
    input_snapshot_sha256: str
    turns: tuple[ControllerTurnResultV1, ...] = ()
    validation_feedback: tuple[ValidationFeedbackV1, ...] = ()
    summary: ControllerSessionSummaryV1 | ControllerSessionSummaryV2
    result_sha256: str = ""

    @field_validator(
        "controller_session_spec_sha256",
        "controller_policy_sha256",
        "input_snapshot_sha256",
    )
    @classmethod
    def _hash(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal(self) -> "ControllerSessionResultV1":
        if self.status == "success":
            if self.failure_code or self.terminal_failure or not self.turns:
                raise ValueError("successful_controller_session_result_invalid")
        elif not self.failure_code:
            raise ValueError("failed_controller_session_result_missing_code")
        projection = self.model_dump(mode="python", exclude={"result_sha256"})
        _ensure_host_free(projection, field_name="controller_session_result")
        expected = canonical_sha256(projection)
        if self.result_sha256 and self.result_sha256 != expected:
            raise ValueError("controller_session_result_sha256_mismatch")
        object.__setattr__(self, "result_sha256", expected)
        return self


class ControllerTurnExecutorPort(Protocol):
    async def execute_turn(self, **kwargs: Any) -> ControllerTurnResultV1: ...


class ControllerToolRuntimePort(Protocol):
    async def execute_tool_call(
        self,
        *,
        intent: ControllerToolCallIntentV1,
        max_inline_result_bytes: int,
    ) -> ControllerToolInvocationResultV1: ...


def _validation_feedback(
    *, target_contract: Mapping[str, Any], candidate_sha256: str, reason: str
) -> ValidationFeedbackV1:
    bounded = str(reason or "$:invalid")
    bounded = bounded.encode("utf-8", errors="replace")[:_MAX_FEEDBACK_REASON_BYTES].decode(
        "utf-8", errors="ignore"
    )
    parts = bounded.split(":")
    error_path = parts[0] if parts and parts[0] else "$"
    error_category = parts[1] if len(parts) > 1 and parts[1] else "invalid"
    return ValidationFeedbackV1(
        target_contract_sha256=canonical_sha256(target_contract),
        error_path=error_path,
        error_category=error_category,
        bounded_reason=bounded,
        previous_candidate_sha256=candidate_sha256,
    )


from .artifact_semantics import validate_schema_document


def _validate_final_candidate(
    content: str,
    contract: Mapping[str, Any],
    *,
    format_enforcement: Mapping[str, Any] | None,
) -> tuple[bool, str, str | None]:
    artifact_type = str(contract.get("artifact_type") or "plaintext").strip().lower()
    if not content or not content.strip():
        return False, content, "$:empty"
    if artifact_type != "json":
        return True, content, None
    schema_value = contract.get("schema_hint")
    if not isinstance(schema_value, Mapping):
        return False, content, "$:schema_missing"
    try:
        schema = require_semantic_json_schema(schema_value)
        parsed = strict_json_loads(content)
    except (ModelResponseContractError, ValueError, json.JSONDecodeError):
        return False, content, "$:json_parse"
    normalized = parsed
    if (
        isinstance(format_enforcement, Mapping)
        and format_enforcement.get("selected_enforcement_mode")
        == "native_strict_schema"
    ):
        normalized = normalize_portable_wire_instance(parsed, schema)
    valid, reason = validate_json_schema_instance(normalized, schema)
    if not valid:
        return False, content, reason or "$:schema"
    if contract.get("content_kind", "value") == "json_schema_document" and not validate_schema_document(normalized):
        return False, content, "$:invalid_schema_document"
    if normalized != parsed:
        return True, canonical_json_bytes(normalized).decode("utf-8"), None
    return True, content, None


class ControllerSessionRunner:
    """Run one sealed Controller Session with bounded contract-only repair."""

    def __init__(
        self,
        *,
        turn_executor: ControllerTurnExecutorPort,
        policy: ControllerSessionPolicyV1 | None = None,
        cost_ledger: Any = None,
        execution_ledger: Any = None,
        tool_runtime: ControllerToolRuntimePort | None = None,
    ) -> None:
        self.turn_executor = turn_executor
        self.policy = policy or load_controller_session_policy()
        self.cost_ledger = cost_ledger
        self.execution_ledger = execution_ledger
        self.tool_runtime = tool_runtime

    async def run(
        self,
        *,
        run_id: str,
        spec: ControllerSessionSpec,
        resolved_inputs: Mapping[str, Any],
        provenance_identities: Sequence[str] = (),
        format_enforcement: Mapping[str, Any] | None = None,
        controller_context: Mapping[str, Any] | None = None,
        skill_bundle: SkillContextBundleV1 | None = None,
        resolved_context: Mapping[str, str] | None = None,
    ) -> ControllerSessionResultV1:
        started = time.perf_counter()
        tool_enabled = isinstance(spec, ControllerSessionSpecV2)
        if tool_enabled and self.tool_runtime is None:
            raise ControllerSessionError("controller_tool_runtime_continuation_not_enabled")
        if spec.controller_session_policy_sha256 != self.policy.policy_sha256:
            raise ControllerSessionError("controller_policy_identity_mismatch")
        snapshot = build_controller_input_snapshot(
            run_id=run_id,
            spec=spec,
            resolved_inputs=resolved_inputs,
            policy=self.policy,
            provenance_identities=provenance_identities,
            resolved_context=resolved_context,
        )
        ledger = self.execution_ledger
        skill_metadata = {}
        if skill_bundle is not None:
            try:
                validate_skill_snapshot(skill_bundle, spec, snapshot)
                skill_metadata = skill_bundle.metadata()
            except (ControllerSkillError, ValueError) as exc:
                raise ControllerSessionError(getattr(exc, "code", "controller_skill_bundle_invalid")) from exc
        if ledger is not None:
            ledger.record_controller_session_started(
                session_id=snapshot.session_id,
                controller_session_spec_sha256=spec.spec_sha256,
                controller_policy_sha256=self.policy.policy_sha256,
                input_snapshot_sha256=snapshot.snapshot_sha256,
                controller_resource_id=spec.controller_resource_id,
                backing_model_resource_id=spec.backing_model_resource_id,
                **skill_metadata,
            )

        turns: list[ControllerTurnResultV1] = []
        feedback_items: list[ValidationFeedbackV1] = []
        conversation_messages: list[dict[str, Any]] = []
        accounting_references: list[dict[str, Any]] = []
        failure_counts: dict[str, int] = {}
        tool_fingerprint_counts: dict[str, int] = {}
        tool_results: list[ControllerToolInvocationResultV1] = []
        failed_tool_attempts: list[ControllerToolTerminalContext] = []
        requested_tool_action_count = 0
        accepted_tool_action_count = 0
        tool_call_attempt_count = 0
        tool_success_count = 0
        tool_failure_count = 0

        def finalize(
            *, status: Literal["success", "failure"], output: str = "", failure: str | None = None,
            terminal: ControllerToolTerminalContext | None = None,
            responsibility: str = "framework",
            failure_envelope: TerminalFailureEnvelope | None = None,
        ) -> ControllerSessionResultV1:
            if failure_envelope is not None:
                failure = failure_envelope.failure_code
            if tool_enabled and status == "failure" and terminal is None:
                terminal = ControllerToolTerminalContext(
                    failure_stage=failure_envelope.failure_stage if failure_envelope else "controller_session", failure_code=str(failure),
                    responsibility=responsibility, session_id=snapshot.session_id,
                )
            if status == "failure" and failure_envelope is None:
                failure_envelope = TerminalFailureEnvelope.create(
                    responsibility=terminal.responsibility if terminal else responsibility,
                    failure_stage=terminal.failure_stage if terminal else "controller_session",
                    failure_code=terminal.failure_code if terminal else str(failure),
                    run_id=run_id, subtask_id=spec.subtask_id,
                    subtask_revision=spec.subtask_revision,
                    response_received=bool(turns and turns[-1].transport_audit.get("response_received")),
                    resource_call_id=terminal.resource_call_id if terminal else None,
                )
            elapsed_ms = (time.perf_counter() - started) * 1000
            summary_fields = {
                "session_id": snapshot.session_id,
                "actual_turns": len(turns),
                "semantic_repairs": len(feedback_items),
                "terminal_status": status,
                "terminal_failure_code": failure,
                "elapsed_ms": elapsed_ms,
                "controller_resource_id": spec.controller_resource_id,
                "backing_model_resource_id": spec.backing_model_resource_id,
                "input_snapshot_sha256": snapshot.snapshot_sha256,
                "policy_sha256": self.policy.policy_sha256,
                "model_accounting_references": tuple(accounting_references),
            }
            if tool_enabled:
                observations = tuple(item.observation for item in tool_results)
                summary: ControllerSessionSummaryV1 | ControllerSessionSummaryV2 = (
                    ControllerSessionSummaryV2(
                        **summary_fields,
                        terminal_failure=terminal,
                        failed_tool_attempts=tuple(failed_tool_attempts),
                        requested_tool_action_count=requested_tool_action_count,
                        accepted_tool_action_count=accepted_tool_action_count,
                        evidence_incomplete=any(item.evidence_incomplete for item in failed_tool_attempts),
                        tool_call_count=tool_call_attempt_count,
                        tool_success_count=tool_success_count,
                        tool_failure_count=tool_failure_count,
                        tool_observation_bytes=sum(
                            item.metrics.observation_bytes for item in tool_results
                        ),
                        resource_call_ids=tuple(
                            item.resource_call_id for item in observations
                        ),
                        controller_tool_provenance_ids=tuple(
                            item.provenance_sha256 for item in observations
                        ),
                        tool_observation_sha256s=tuple(
                            item.observation_sha256 for item in observations
                        ),
                    )
                )
            else:
                summary = ControllerSessionSummaryV1(**summary_fields)
            result = ControllerSessionResultV1(
                status=status,
                output_data=output,
                failure_code=failure,
                terminal_failure=failure_envelope,
                controller_session_spec_sha256=spec.spec_sha256,
                controller_policy_sha256=self.policy.policy_sha256,
                input_snapshot_sha256=snapshot.snapshot_sha256,
                turns=tuple(turns),
                validation_feedback=tuple(feedback_items),
                summary=summary,
            )
            try:
                if ledger is not None:
                    if status == "success":
                        ledger.record_controller_session_finished(
                            session_id=snapshot.session_id,
                            controller_session_spec_sha256=spec.spec_sha256,
                            controller_policy_sha256=self.policy.policy_sha256,
                            input_snapshot_sha256=snapshot.snapshot_sha256,
                            controller_resource_id=spec.controller_resource_id,
                            backing_model_resource_id=spec.backing_model_resource_id,
                            terminal_status="success",
                            actual_turns=len(turns),
                            semantic_repairs=len(feedback_items),
                            tool_call_count=tool_call_attempt_count,
                            runtime_protocol=(
                                CONTROLLER_TOOL_RUNTIME_PROTOCOL if tool_enabled else None
                            ),
                            elapsed_ms=elapsed_ms,
                            result_sha256=result.result_sha256,
                        )
                    else:
                        ledger.record_controller_session_failed(
                            session_id=snapshot.session_id,
                            controller_session_spec_sha256=spec.spec_sha256,
                            controller_policy_sha256=self.policy.policy_sha256,
                            input_snapshot_sha256=snapshot.snapshot_sha256,
                            controller_resource_id=spec.controller_resource_id,
                            backing_model_resource_id=spec.backing_model_resource_id,
                            terminal_status="failure",
                            actual_turns=len(turns),
                            semantic_repairs=len(feedback_items),
                            tool_call_count=tool_call_attempt_count,
                            runtime_protocol=(
                                CONTROLLER_TOOL_RUNTIME_PROTOCOL if tool_enabled else None
                            ),
                            elapsed_ms=elapsed_ms,
                            failure_code=str(failure),
                            result_sha256=result.result_sha256,
                        )
            except Exception:
                if not tool_enabled:
                    raise
                # The terminal write was attempted once. Re-seal only the returned
                # projection; never retry a possibly already persisted event.
                terminal = terminal or ControllerToolTerminalContext(
                    failure_stage="controller_session_accounting",
                    failure_code="controller_session_terminal_persistence_failed",
                    responsibility="framework", session_id=snapshot.session_id,
                    evidence_incomplete=True,
                )
                summary_data = summary.model_dump(mode="python", exclude={"summary_sha256"})
                summary_data.update(
                    terminal_status="failure", terminal_failure_code=terminal.failure_code,
                    terminal_failure=terminal, evidence_incomplete=True,
                    secondary_audit_failures=("controller_session_terminal_persistence_failed",),
                )
                result_data = result.model_dump(mode="python", exclude={"result_sha256"})
                result_data.update(status="failure", output_data="", failure_code=terminal.failure_code,
                                   summary=ControllerSessionSummaryV2(**summary_data),
                                   terminal_failure=TerminalFailureEnvelope.create(
                                       responsibility=terminal.responsibility,
                                       failure_stage=terminal.failure_stage,
                                       failure_code=terminal.failure_code,
                                       run_id=run_id, subtask_id=spec.subtask_id,
                                       subtask_revision=spec.subtask_revision,
                                       primary_failure_sha256=failure_envelope.failure_sha256 if failure_envelope and failure_envelope.failure_code != terminal.failure_code else None))
                result = ControllerSessionResultV1(**result_data)
            return result

        for turn_index in range(1, self.policy.max_turns + 1):
            if self.cost_ledger is not None:
                exhausted = getattr(self.cost_ledger, "is_exhausted", False)
                is_exhausted = (
                    exhausted() if callable(exhausted) else bool(exhausted)
                )
                if is_exhausted:
                    return finalize(status="failure", failure="controller_cost_limit", responsibility="budget")
            turn_id = canonical_sha256(
                {"session_id": snapshot.session_id, "turn_index": turn_index}
            )
            if ledger is not None:
                ledger.record_controller_turn_started(
                    session_id=snapshot.session_id,
                    turn_id=turn_id,
                    turn_index=turn_index,
                    controller_session_spec_sha256=spec.spec_sha256,
                    controller_policy_sha256=self.policy.policy_sha256,
                    input_snapshot_sha256=snapshot.snapshot_sha256,
                    **skill_metadata,
                )
            turn = await self.turn_executor.execute_turn(
                spec=spec,
                snapshot=snapshot,
                turn_id=turn_id,
                turn_index=turn_index,
                conversation_messages=tuple(conversation_messages),
                provider_tools=(
                    tuple(project_provider_tool_schema(item) for item in spec.callable_tools)
                    if tool_enabled
                    else ()
                ),
                format_enforcement=(
                    None
                    if tool_enabled
                    else dict(format_enforcement)
                    if format_enforcement is not None
                    else None
                ),
                controller_context=dict(controller_context or {}),
                **({"skill_bundle": skill_bundle} if skill_bundle is not None else {}),
            )
            if turn.session_id != snapshot.session_id or turn.turn_id != turn_id:
                return finalize(
                    status="failure", failure="controller_session_identity_mismatch"
                )
            turns.append(turn)
            if turn.model_accounting_reference is not None:
                accounting_references.append(dict(turn.model_accounting_reference))
            if ledger is not None:
                ledger.record_controller_turn_finished(
                    session_id=snapshot.session_id,
                    turn_id=turn.turn_id,
                    turn_index=turn.turn_index,
                    status=turn.status,
                    controller_session_spec_sha256=spec.spec_sha256,
                    controller_policy_sha256=self.policy.policy_sha256,
                    input_snapshot_sha256=snapshot.snapshot_sha256,
                    model_accounting_reference=turn.model_accounting_reference,
                    request_sha256=turn.request_sha256,
                    response_sha256=turn.response_sha256,
                    candidate_sha256=turn.candidate_sha256,
                    latency_ms=turn.latency_ms,
                    failure_code=turn.failure_code,
                    **skill_metadata,
                )
            if turn.status == "failure":
                if turn.terminal_failure is not None:
                    return finalize(status="failure", failure=turn.failure_code,
                        responsibility=turn.terminal_failure.responsibility,
                        failure_envelope=turn.terminal_failure)
                failure_code = str(turn.failure_code or "controller_transport_failure")
                if failure_code.startswith("controller_skill_"):
                    return finalize(status="failure", failure=failure_code, responsibility="framework")
                if failure_code in {
                    "controller_backing_model_not_ready",
                    "controller_cost_limit",
                    "controller_provider_truncation",
                }:
                    return finalize(status="failure", failure=failure_code,
                                    responsibility="budget" if failure_code == "controller_cost_limit" else "framework")
                if tool_enabled and failure_code.startswith(
                    ("controller_tool_", "controller_provider_tool_")
                ):
                    return finalize(status="failure", failure=failure_code,
                                    responsibility="budget" if failure_code == "controller_cost_limit" else "framework")
                return finalize(status="failure", failure="controller_transport_failure")
            if turn.tool_calls:
                requested_tool_action_count += len(turn.tool_calls)
                if not tool_enabled:
                    return finalize(
                        status="failure", failure="CONTROLLER_TOOL_CALL_NOT_AUTHORIZED"
                    )
                assert isinstance(spec, ControllerSessionSpecV2)
                assert self.tool_runtime is not None
                try:
                    intents = prevalidate_tool_call_intents(
                        intents=turn.tool_calls,
                        callable_tools=spec.callable_tools,
                        session_id=snapshot.session_id,
                        turn_id=turn_id,
                        current_tool_call_count=tool_call_attempt_count,
                        prior_fingerprint_counts=tool_fingerprint_counts,
                        max_tool_calls=self.policy.max_tool_calls,
                        max_same_call_repeats=self.policy.max_same_call_repeats,
                    )
                except (ControllerToolRuntimeError, TypeError, ValueError) as exc:
                    failure_code = getattr(
                        exc, "code", "controller_tool_call_intent_invalid"
                    )
                    return finalize(
                        status="failure", failure=str(failure_code),
                        terminal=ControllerToolTerminalContext(
                            failure_stage=getattr(exc, "failure_stage", "controller_tool_prevalidation"),
                            failure_code=str(failure_code),
                            responsibility=getattr(exc, "responsibility", "framework"),
                            session_id=snapshot.session_id, turn_id=turn_id,
                        ),
                    )
                accepted_tool_action_count += len(intents)
                for intent in intents:
                    tool_fingerprint_counts[intent.call_fingerprint_sha256] = (
                        tool_fingerprint_counts.get(
                            intent.call_fingerprint_sha256, 0
                        )
                        + 1
                    )
                    if ledger is not None:
                        ledger.record_controller_tool_call_validated(
                            session_id=snapshot.session_id,
                            turn_id=turn_id,
                            provider_tool_call_id=intent.provider_tool_call_id,
                            callable_id=intent.callable_id,
                            intent_sha256=intent.intent_sha256,
                            dynamic_arguments_sha256=(
                                intent.dynamic_arguments_sha256
                            ),
                            runtime_protocol=CONTROLLER_TOOL_RUNTIME_PROTOCOL,
                        )
                action_message = {
                    "role": "assistant",
                    "content": turn.content,
                    "tool_calls": [
                        {
                            "id": intent.provider_tool_call_id,
                            "type": "function",
                            "function": {
                                "name": intent.provider_tool_name,
                                "arguments": canonical_json_bytes(
                                    intent.normalized_dynamic_arguments
                                ).decode("utf-8"),
                            },
                        }
                        for intent in intents
                    ],
                }
                observations: list[dict[str, Any]] = []
                for intent in intents:
                    tool_call_attempt_count += 1
                    invocation = None
                    audit_references = None
                    terminal_error = None
                    cancellation = None
                    try:
                        received = await self.tool_runtime.execute_tool_call(
                            intent=intent,
                            max_inline_result_bytes=self.policy.max_inline_tool_result_bytes,
                        )
                        if not isinstance(received, ControllerToolInvocationResultV1):
                            raise ControllerToolRuntimeError("controller_tool_invocation_result_invalid")
                        audit_references = received.audit_references_for(intent)
                        invocation = received
                        # Consumption belongs to the same boundary as Gateway dispatch.
                        public_content = canonical_json_bytes(
                            invocation.observation.public_projection()
                        ).decode("utf-8")
                    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as exc:
                        cancellation = exc
                        terminal_error = ControllerToolRuntimeError(
                            "controller_tool_cancelled",
                            failure_stage=("controller_tool_observation_consumption" if audit_references
                                           else "controller_tool_runtime"),
                            terminal_context=getattr(exc, "terminal_context", None),
                        )
                    except Exception as exc:
                        terminal_error = exc if isinstance(exc, ControllerToolRuntimeError) else (
                            ControllerToolRuntimeError(
                                "controller_tool_observation_consumption_failed",
                                failure_stage="controller_tool_observation_consumption",
                            )
                        )
                    if terminal_error is not None:
                        tool_failure_count += 1
                        invocation = invocation or terminal_error.invocation_result
                        if isinstance(invocation, ControllerToolInvocationResultV1):
                            tool_results.append(invocation)
                        else:
                            invocation = None
                        terminal = terminal_error.terminal_context
                        if audit_references is not None:
                            terminal = audit_references.terminal_context(terminal_error)
                        if terminal is None:
                            obs = invocation.observation if invocation else None
                            terminal = ControllerToolTerminalContext(
                                failure_stage=terminal_error.failure_stage,
                                failure_code=terminal_error.code,
                                responsibility=terminal_error.responsibility,
                                session_id=snapshot.session_id, turn_id=turn_id,
                                provider_tool_call_id=intent.provider_tool_call_id,
                                callable_id=intent.callable_id,
                                resource_call_id=obs.resource_call_id if obs else None,
                                resource_dispatched=obs is not None,
                                resource_outcome="result_received" if obs else "unknown",
                                observation_sha256=obs.observation_sha256 if obs else None,
                                provenance_sha256=obs.provenance_sha256 if obs else None,
                                realized_output_sha256=obs.realized_output_sha256 if obs else None,
                                evidence_incomplete=True,
                            )
                        failed_tool_attempts.append(terminal)
                        terminal_result = finalize(
                            status="failure", failure=terminal.failure_code, terminal=terminal,
                        )
                        if cancellation is not None:
                            cancellation.terminal_context = terminal
                            cancellation.session_result = terminal_result
                            raise cancellation
                        return terminal_result
                    assert invocation is not None
                    tool_results.append(invocation)
                    if invocation.observation.status == "success":
                        tool_success_count += 1
                    else:
                        tool_failure_count += 1
                    observations.append({
                        "role": "tool", "tool_call_id": intent.provider_tool_call_id,
                        "content": public_content,
                    })
                conversation_messages.append(action_message)
                conversation_messages.extend(observations)
                if turn_index >= self.policy.max_turns:
                    return finalize(
                        status="failure", failure="controller_turn_limit", responsibility="budget"
                    )
                continue
            valid, output, reason = _validate_final_candidate(
                turn.content,
                spec.expected_output_contract,
                format_enforcement=None if tool_enabled else format_enforcement,
            )
            if valid:
                return finalize(status="success", output=output)

            fingerprint = canonical_sha256(
                {
                    "reason": reason,
                    "candidate_sha256": turn.candidate_sha256,
                }
            )
            failure_counts[fingerprint] = failure_counts.get(fingerprint, 0) + 1
            if failure_counts[fingerprint] >= self.policy.max_same_failure_repeats:
                return finalize(status="failure", failure="controller_no_progress", responsibility="research")
            if len(feedback_items) >= self.policy.max_semantic_repairs:
                return finalize(status="failure", failure="controller_turn_limit", responsibility="budget")
            if turn_index >= self.policy.max_turns:
                return finalize(status="failure", failure="controller_turn_limit", responsibility="budget")
            feedback = _validation_feedback(
                target_contract=spec.expected_output_contract,
                candidate_sha256=turn.candidate_sha256,
                reason=str(reason or "$:invalid"),
            )
            feedback_items.append(feedback)
            if ledger is not None:
                ledger.record_controller_validation_feedback(
                    session_id=snapshot.session_id,
                    turn_id=turn.turn_id,
                    controller_session_spec_sha256=spec.spec_sha256,
                    controller_policy_sha256=self.policy.policy_sha256,
                    input_snapshot_sha256=snapshot.snapshot_sha256,
                    feedback_sha256=feedback.feedback_sha256,
                    target_contract_sha256=feedback.target_contract_sha256,
                    previous_candidate_sha256=feedback.previous_candidate_sha256,
                )
            conversation_messages.extend(
                (
                    {"role": "assistant", "content": turn.content},
                    {
                        "role": "user",
                        "content": json.dumps(
                            feedback.model_dump(mode="json"),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                )
            )

        return finalize(status="failure", failure="controller_turn_limit", responsibility="budget")


__all__ = [
    "CONTROLLER_SESSION_POLICY_PROTOCOL",
    "CONTROLLER_SESSION_PROTOCOL",
    "CONTROLLER_SESSION_V2_PROTOCOL",
    "CONTROLLER_SESSION_SUMMARY_V2_PROTOCOL",
    "ControllerInputDescriptorV1",
    "ControllerSessionError",
    "ControllerSessionInputSnapshotV1",
    "ControllerSessionPolicyV1",
    "ControllerSessionResultV1",
    "ControllerSessionRunner",
    "ControllerSessionSpec",
    "ControllerSessionSpecV1",
    "ControllerSessionSpecV2",
    "ControllerSessionSummaryV1",
    "ControllerSessionSummaryV2",
    "ControllerToolRuntimePort",
    "ControllerTurnResultV1",
    "ValidationFeedbackV1",
    "build_controller_input_snapshot",
    "derive_controller_session_spec",
    "load_controller_session_policy",
]
