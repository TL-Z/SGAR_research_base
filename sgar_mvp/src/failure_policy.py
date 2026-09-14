"""Structured failure ownership for fixed-pass execution.

This module intentionally does not inspect exception messages.  Ownership is
derived from explicit framework stages and structured producer metadata so new
Cases cannot change attribution through their content.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Mapping


FAILURE_TAXONOMY_PROTOCOL = "e1-failure-taxonomy-v3"
PRIMARY_FAILURE_EVIDENCE_PROTOCOL = "primary-failure-evidence-v1"


class FrameworkInvariantError(RuntimeError):
    """A framework audit failed without exposing its input material."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

_LAYER_RESPONSIBILITY = {
    "framework": "framework",
    "framework_implementation": "framework",
    "infrastructure": "infrastructure",
    "budget": "budget",
    "budget_control": "budget",
    "experimental_control": "budget",
    "plan_composition": "research",
    "resource_execution": "research",
    "task_success": "research",
    "research": "research",
    "none": "research",
    "": "research",
}

_FRAMEWORK_STAGES = {
    "public_input_serialization",
    "candidate_build",
    "payload_guard_setup",
    "compiler_trace_validation",
    "plan_analysis",
    "execution_wiring",
    "hidden_validation",
    "record_audit",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="python")
        except TypeError:
            dumped = value.model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def execution_cost(execution: Mapping[str, Any] | None) -> Mapping[str, Any]:
    result = _mapping((execution or {}).get("result"))
    return _mapping(result.get("cost_metric"))


def _int_attempt(value: Any) -> int:
    try:
        return max(0, min(3, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _primary_exception_evidence(
    exception: BaseException | None,
    *,
    failure_stage: str,
) -> Dict[str, str] | None:
    if exception is None:
        return None
    message_hash = hashlib.sha256(str(exception).encode("utf-8")).hexdigest()
    error_code = getattr(exception, "code", "")
    return {
        "schema_version": PRIMARY_FAILURE_EVIDENCE_PROTOCOL,
        "exception_type": type(exception).__name__,
        "error_code": str(error_code) if error_code is not None else "",
        "failure_stage": str(failure_stage),
        "message_sha256": message_hash,
    }


def build_failure_envelope(
    *,
    stage: str,
    exception: BaseException | None,
    compiler_invoked: bool,
    compiler_trace: Mapping[str, Any] | None,
    execution: Mapping[str, Any] | None,
    terminal_success: bool | None,
) -> Dict[str, Any]:
    trace = _mapping(compiler_trace)
    policy_call = _mapping(trace.get("policy_call"))
    cost = execution_cost(execution)
    structured = _mapping(cost.get("failure"))
    failure_layer = str(
        structured.get("responsibility")
        or structured.get("failure_layer")
        or cost.get("failure_layer")
        or ""
    ).strip().lower()

    if structured:
        responsibility = _LAYER_RESPONSIBILITY.get(failure_layer, "framework")
        failure_stage = str(
            structured.get("failure_stage")
            or structured.get("stage")  # legacy producer compatibility only
            or stage
        )
        attempt = _int_attempt(
            structured.get("transport_attempt") or structured.get("attempt_count")
        )
        request_hash = str(structured.get("request_hash") or "")
        response_received = bool(structured.get("response_received"))
        producer_retryable = bool(structured.get("retryable", False))
    elif exception is not None and stage == "plan_compiler":
        response_received = bool(policy_call.get("response_received"))
        declared = str(policy_call.get("responsibility") or "").strip().lower()
        if response_received:
            responsibility = "research"
        elif declared in {"infrastructure", "budget"}:
            responsibility = declared
        else:
            # An untyped pre-response exception is a framework attribution
            # defect, not evidence of a protocol error from the model.
            responsibility = "framework"
        failure_stage = "plan_compiler"
        attempt = _int_attempt(
            policy_call.get("transport_attempt") or policy_call.get("attempt_count")
        )
        request_hash = str(policy_call.get("request_hash") or "")
    elif exception is not None:
        responsibility = "framework" if stage in _FRAMEWORK_STAGES else _LAYER_RESPONSIBILITY.get(
            failure_layer,
            "framework",
        )
        failure_stage = stage
        attempt = 0
        request_hash = ""
        response_received = False
    else:
        responsibility = _LAYER_RESPONSIBILITY.get(failure_layer, "research")
        failure_stage = "completed" if terminal_success is True else stage
        attempt = 0
        request_hash = ""
        response_received = False

    retryable = bool(
        responsibility == "infrastructure"
        and not response_received
        and attempt < 3
        and (producer_retryable if structured else True)
    )
    primary_exception = _primary_exception_evidence(
        exception,
        failure_stage=failure_stage,
    )
    return {
        "schema_version": FAILURE_TAXONOMY_PROTOCOL,
        "present": bool(exception is not None or terminal_success is not True),
        "responsibility": responsibility,
        "failure_stage": failure_stage,
        # Retained until all existing batch readers have migrated.  New
        # producers and audits use ``failure_stage`` as the canonical field.
        "stage": failure_stage,
        "retryable": retryable,
        "transport_attempt": attempt,
        "request_hash": request_hash,
        "response_received": response_received,
        "exception_type": type(exception).__name__ if exception is not None else "",
        "failure_type": str(structured.get("failure_type") or cost.get("failure_type") or ""),
        "primary_exception": primary_exception,
    }
