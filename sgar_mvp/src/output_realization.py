"""Deterministic closure from Resource result views to typed node artifacts.

The primitive in this module is deliberately small.  It accepts only sealed,
schema-grounded conversions and never performs semantic inference, field-name
guessing, arbitrary code execution, or model-assisted repair.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from .model_response_contracts import (
    ModelResponseContractError,
    require_semantic_json_schema,
    validate_json_schema_instance,
)
from .pipeline_control import FrozenContract, canonical_json_bytes, canonical_sha256


OUTPUT_REALIZATION_PROTOCOL = "sgar-output-realization-v1"

RealizationKind = Literal[
    "identity",
    "manifest_payload_extract",
    "json_object_project",
    "json_object_wrap",
    "lossless_serialize",
]
SourceView = Literal["native", "semantic"]
ReachabilityCompatibility = Literal[
    "exact",
    "deterministically_convertible",
    "controller_required",
    "incompatible",
]


class OutputRealizationError(ValueError):
    """A sealed deterministic realization could not be completed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalized_artifact_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return {
        "application/json": "json",
        "text/csv": "csv",
        "text/plain": "plaintext",
        "md": "markdown",
        "binary": "file",
        "bytes": "file",
        "file_path": "file",
    }.get(normalized, normalized)


def contract_schema(contract: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("schema_hint", "json_schema", "schema"):
        value = contract.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def representation_bytes(value: Any, artifact_type: str) -> bytes:
    """Return the exact host-free byte representation used for accounting."""

    if normalized_artifact_type(artifact_type) == "json":
        if isinstance(value, str):
            return value.encode("utf-8")
        return canonical_json_bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json_bytes(value)


def representation_sha256(value: Any, artifact_type: str) -> str:
    return hashlib.sha256(representation_bytes(value, artifact_type)).hexdigest()


class OutputRealizationContractV1(FrozenContract):
    protocol: Literal[OUTPUT_REALIZATION_PROTOCOL] = OUTPUT_REALIZATION_PROTOCOL
    realization_kind: RealizationKind
    source_view: SourceView
    source_artifact_type: str = Field(min_length=1)
    target_artifact_type: str = Field(min_length=1)
    source_contract_sha256: str
    target_contract_sha256: str
    manifest_payload_path: str | None = None
    projected_properties: tuple[str, ...] = ()
    wrapper_key: str | None = None
    contract_sha256: str = ""

    @field_validator("source_contract_sha256", "target_contract_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("output_realization_contract_hash_invalid")
        return normalized

    @model_validator(mode="after")
    def _seal(self) -> "OutputRealizationContractV1":
        if self.realization_kind == "manifest_payload_extract":
            if self.source_view != "semantic" or not self.manifest_payload_path:
                raise ValueError("manifest_payload_extract_contract_invalid")
        elif self.manifest_payload_path is not None:
            raise ValueError("manifest_payload_path_kind_mismatch")
        if self.realization_kind == "json_object_project":
            if not self.projected_properties:
                raise ValueError("json_object_project_properties_missing")
        elif self.projected_properties:
            raise ValueError("projected_properties_kind_mismatch")
        if self.realization_kind == "json_object_wrap":
            if not self.wrapper_key:
                raise ValueError("json_object_wrap_key_missing")
        elif self.wrapper_key is not None:
            raise ValueError("wrapper_key_kind_mismatch")
        projection = self.model_dump(mode="python", exclude={"contract_sha256"})
        expected = canonical_sha256(projection)
        if self.contract_sha256 and self.contract_sha256 != expected:
            raise ValueError("output_realization_contract_sha256_mismatch")
        object.__setattr__(self, "contract_sha256", expected)
        return self


class OutputReachabilityProof(FrozenContract):
    protocol: Literal[OUTPUT_REALIZATION_PROTOCOL] = OUTPUT_REALIZATION_PROTOCOL
    resource_id: str = Field(min_length=1)
    capability_operation_id: str = Field(min_length=1)
    entrypoint_id: str = Field(min_length=1)
    source_view: SourceView
    source_contract_sha256: str
    target_contract_sha256: str
    compatibility: ReachabilityCompatibility
    realization_kind: RealizationKind | None = None
    output_realization_contract_sha256: str | None = None
    reason_code: str = Field(min_length=1)
    proof_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "OutputReachabilityProof":
        deterministic = self.compatibility in {
            "exact",
            "deterministically_convertible",
        }
        if deterministic != bool(
            self.realization_kind and self.output_realization_contract_sha256
        ):
            raise ValueError("output_reachability_realization_identity_invalid")
        for field_name in (
            "source_contract_sha256",
            "target_contract_sha256",
            "output_realization_contract_sha256",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            normalized = str(value).strip().lower()
            if len(normalized) != 64 or any(
                char not in "0123456789abcdef" for char in normalized
            ):
                raise ValueError("output_reachability_hash_invalid")
        projection = self.model_dump(mode="python", exclude={"proof_sha256"})
        expected = canonical_sha256(projection)
        if self.proof_sha256 and self.proof_sha256 != expected:
            raise ValueError("output_reachability_proof_sha256_mismatch")
        object.__setattr__(self, "proof_sha256", expected)
        return self


class OutputRealizationMetricsV1(FrozenContract):
    realization_count: Literal[1] = 1
    realization_kind: RealizationKind
    source_bytes: int = Field(ge=0)
    target_bytes: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    status: Literal["success", "failure"]
    model_provider_monetary_cost: Literal[0] = 0


class OutputRealizationProvenanceV1(FrozenContract):
    resource_call_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    native_output_sha256: str
    semantic_output_sha256: str | None = None
    source_view: SourceView
    source_contract_sha256: str
    output_realization_contract_sha256: str
    target_contract_sha256: str
    realized_output_sha256: str


class OutputRealizationResultV1(FrozenContract):
    protocol: Literal[OUTPUT_REALIZATION_PROTOCOL] = OUTPUT_REALIZATION_PROTOCOL
    realization_id: str = Field(min_length=64, max_length=64)
    status: Literal["success", "failure"]
    realized_value: Any = None
    presentation: str = ""
    failure_code: str | None = None
    metrics: OutputRealizationMetricsV1
    provenance: OutputRealizationProvenanceV1 | None = None
    result_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "OutputRealizationResultV1":
        if self.status == "success":
            if self.failure_code is not None or self.provenance is None:
                raise ValueError("successful_output_realization_incomplete")
        elif not self.failure_code or self.provenance is not None:
            raise ValueError("failed_output_realization_incomplete")
        projection = self.model_dump(mode="python", exclude={"result_sha256"})
        expected = canonical_sha256(projection)
        if self.result_sha256 and self.result_sha256 != expected:
            raise ValueError("output_realization_result_sha256_mismatch")
        object.__setattr__(self, "result_sha256", expected)
        return self


def _representation_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    artifact_type = normalized_artifact_type(contract.get("artifact_type"))
    projection: dict[str, Any] = {"artifact_type": artifact_type}
    schema = contract_schema(contract)
    if schema is not None:
        projection["schema_hint"] = dict(schema)
    return projection


def _validation_equivalence_view(schema: Mapping[str, Any]) -> dict[str, Any] | None:
    """A comparison-only view, never a replacement contract or general schema prover."""
    # Validate the original first: normalization must not repair an invalid/unsupported schema.
    try:
        require_semantic_json_schema(schema)
        view = json.loads(canonical_json_bytes(schema))
        def visit(node: Any) -> None:
            if isinstance(node, bool):
                return
            if not isinstance(node, dict):
                raise ValueError("non_schema_node")
            if "$schema" in node and node["$schema"] not in {
                "https://json-schema.org/draft/2020-12/schema",
                "https://json-schema.org/draft/2020-12/schema#",
            }:
                raise ValueError("unknown_dialect")
            # Identifier scopes/anchors and non-definition pointers need no new equivalence proof.
            if "$id" in node or "$anchor" in node:
                raise ValueError("scoped_reference")
            if "$ref" in node and not (
                isinstance(node["$ref"], str)
                and node["$ref"].startswith(("#/$defs/", "#/definitions/"))
                and node["$ref"].count("/") == 2
            ):
                raise ValueError("complex_reference")
            for key in ("title", "description"):
                if key in node:
                    if not isinstance(node[key], str):
                        raise ValueError("invalid_annotation")
                    del node[key]
            for key in ("minProperties", "maxProperties"):
                if key in node and (type(node[key]) is not int or node[key] < 0):
                    raise ValueError("invalid_property_count")
            props, required = node.get("properties"), node.get("required")
            simple_keys = {"type", "properties", "required", "additionalProperties",
                           "minProperties", "maxProperties", "$defs", "definitions", "$schema"}
            if (node.get("type") == "object" and isinstance(props, dict)
                and isinstance(required, list) and all(isinstance(x, str) for x in required)
                and len(required) == len(set(required)) and set(required) == set(props)
                and node.get("additionalProperties") is False and set(node) <= simple_keys):
                n = len(props)
                if "minProperties" in node and node["minProperties"] <= n:
                    del node["minProperties"]
                if "maxProperties" in node and node["maxProperties"] >= n:
                    del node["maxProperties"]
            for key in ("$defs", "definitions", "properties"):
                if key in node:
                    if not isinstance(node[key], dict):
                        raise ValueError("invalid_schema_map")
                    for child in node[key].values():
                        visit(child)
            for key in ("items", "additionalProperties"):
                if key in node:
                    visit(node[key])
            for key in ("allOf", "anyOf", "oneOf"):
                if key in node:
                    for child in node[key]:
                        visit(child)
        visit(view)
        return view
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


def _contracts_exact(
    source_contract: Mapping[str, Any],
    target_contract: Mapping[str, Any],
) -> bool:
    source = _representation_contract(source_contract)
    target = _representation_contract(target_contract)
    if not source["artifact_type"] or source["artifact_type"] != target["artifact_type"]:
        return False
    if source["artifact_type"] != "json":
        return True
    if canonical_sha256(source.get("schema_hint")) == canonical_sha256(target.get("schema_hint")):
        return True
    if not isinstance(source.get("schema_hint"), Mapping) or not isinstance(target.get("schema_hint"), Mapping):
        return False
    source_view = _validation_equivalence_view(source["schema_hint"])
    target_view = _validation_equivalence_view(target["schema_hint"])
    return (source_view is not None and target_view is not None
            and canonical_sha256(source_view) == canonical_sha256(target_view))


def _strict_object_schema(contract: Mapping[str, Any]) -> Mapping[str, Any] | None:
    schema = contract_schema(contract)
    if not isinstance(schema, Mapping):
        return None
    try:
        schema = require_semantic_json_schema(schema)
    except ModelResponseContractError:
        return None
    if (
        schema.get("type") != "object"
        or not isinstance(schema.get("properties"), Mapping)
        or schema.get("additionalProperties") is not False
    ):
        return None
    required = schema.get("required", ())
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return None
    if not all(isinstance(item, str) for item in required):
        return None
    return schema


def _projected_properties(
    source_contract: Mapping[str, Any],
    target_contract: Mapping[str, Any],
) -> tuple[str, ...] | None:
    if normalized_artifact_type(source_contract.get("artifact_type")) != "json":
        return None
    if normalized_artifact_type(target_contract.get("artifact_type")) != "json":
        return None
    source_schema = contract_schema(source_contract)
    target_schema = _strict_object_schema(target_contract)
    if not isinstance(source_schema, Mapping) or target_schema is None:
        return None
    try:
        source_schema = require_semantic_json_schema(source_schema)
    except ModelResponseContractError:
        return None
    if source_schema.get("type") != "object" or not isinstance(
        source_schema.get("properties"), Mapping
    ):
        return None
    source_properties = source_schema["properties"]
    target_properties = target_schema["properties"]
    names = tuple(str(name) for name in target_properties)
    if not names or any(name not in source_properties for name in names):
        return None
    if any(
        canonical_sha256(source_properties[name])
        != canonical_sha256(target_properties[name])
        for name in names
    ):
        return None
    source_required = set(source_schema.get("required") or ())
    target_required = set(target_schema.get("required") or ())
    if not target_required.issubset(source_required):
        return None
    if set(source_properties) == set(target_properties) and source_schema.get(
        "additionalProperties"
    ) is False:
        return None
    return names


def _wrapper_key(
    source_contract: Mapping[str, Any],
    target_contract: Mapping[str, Any],
) -> str | None:
    target_schema = _strict_object_schema(target_contract)
    if target_schema is None:
        return None
    properties = target_schema["properties"]
    required = tuple(target_schema.get("required") or ())
    if len(properties) != 1 or len(required) != 1:
        return None
    key = str(next(iter(properties)))
    if required[0] != key:
        return None
    source_schema = contract_schema(source_contract)
    if source_schema is None:
        source_type = normalized_artifact_type(source_contract.get("artifact_type"))
        if source_type in {"plaintext", "csv", "markdown", "code"}:
            source_schema = {"type": "string"}
    if not isinstance(source_schema, Mapping):
        return None
    if canonical_sha256(source_schema) != canonical_sha256(properties[key]):
        return None
    return key


def _lossless_serialization_supported(
    source_contract: Mapping[str, Any],
    target_contract: Mapping[str, Any],
) -> bool:
    source = normalized_artifact_type(source_contract.get("artifact_type"))
    target = normalized_artifact_type(target_contract.get("artifact_type"))
    return target in {"file", "plaintext"} and source in {
        "json",
        "plaintext",
        "csv",
        "markdown",
        "code",
    }


def _contract(
    *,
    kind: RealizationKind,
    source_view: SourceView,
    source_contract: Mapping[str, Any],
    target_contract: Mapping[str, Any],
    payload_path: str | None = None,
    projected_properties: tuple[str, ...] = (),
    wrapper_key: str | None = None,
) -> OutputRealizationContractV1:
    return OutputRealizationContractV1(
        realization_kind=kind,
        source_view=source_view,
        source_artifact_type=normalized_artifact_type(
            source_contract.get("artifact_type")
        ),
        target_artifact_type=normalized_artifact_type(
            target_contract.get("artifact_type")
        ),
        source_contract_sha256=canonical_sha256(dict(source_contract)),
        target_contract_sha256=canonical_sha256(dict(target_contract)),
        manifest_payload_path=payload_path,
        projected_properties=projected_properties,
        wrapper_key=wrapper_key,
    )


def prove_output_reachability(
    *,
    resource_id: str,
    capability_operation_id: str,
    entrypoint_id: str,
    resource_native_output_contract: Mapping[str, Any],
    target_output_contract: Mapping[str, Any],
) -> tuple[OutputReachabilityProof, OutputRealizationContractV1 | None]:
    """Prove exact/deterministic reachability in the frozen Stage-A order."""

    native = dict(resource_native_output_contract)
    target = dict(target_output_contract)
    semantic_raw = native.get("semantic_output")
    semantic = dict(semantic_raw) if isinstance(semantic_raw, Mapping) else None
    payload_path = (
        str(semantic.get("payload_path") or "").strip()
        if semantic is not None
        else ""
    )
    source_views: list[tuple[SourceView, dict[str, Any]]] = [("native", native)]
    if semantic is not None and payload_path:
        source_views.append(("semantic", semantic))

    def finish(
        compatibility: ReachabilityCompatibility,
        reason_code: str,
        *,
        source_view: SourceView,
        source_contract: Mapping[str, Any],
        realization: OutputRealizationContractV1 | None = None,
    ) -> tuple[OutputReachabilityProof, OutputRealizationContractV1 | None]:
        return (
            OutputReachabilityProof(
                resource_id=resource_id,
                capability_operation_id=capability_operation_id,
                entrypoint_id=entrypoint_id,
                source_view=source_view,
                source_contract_sha256=canonical_sha256(dict(source_contract)),
                target_contract_sha256=canonical_sha256(target),
                compatibility=compatibility,
                realization_kind=(
                    realization.realization_kind if realization is not None else None
                ),
                output_realization_contract_sha256=(
                    realization.contract_sha256 if realization is not None else None
                ),
                reason_code=reason_code,
            ),
            realization,
        )

    if not normalized_artifact_type(native.get("artifact_type")) or not normalized_artifact_type(
        target.get("artifact_type")
    ):
        return finish(
            "incompatible",
            "representation_contract_artifact_type_missing",
            source_view="native",
            source_contract=native,
        )
    if semantic is not None and not payload_path:
        return finish(
            "incompatible",
            "manifest_semantic_payload_path_missing",
            source_view="native",
            source_contract=native,
        )

    if _contracts_exact(native, target):
        realization = _contract(
            kind="identity",
            source_view="native",
            source_contract=native,
            target_contract=target,
        )
        return finish(
            "exact",
            "native_contract_exact",
            source_view="native",
            source_contract=native,
            realization=realization,
        )
    if semantic is not None and payload_path and _contracts_exact(semantic, target):
        realization = _contract(
            kind="manifest_payload_extract",
            source_view="semantic",
            source_contract=semantic,
            target_contract=target,
            payload_path=payload_path,
        )
        return finish(
            "exact",
            "manifest_semantic_contract_exact",
            source_view="semantic",
            source_contract=semantic,
            realization=realization,
        )

    deterministic_views = list(reversed(source_views))
    for source_view, source_contract in deterministic_views:
        projected = _projected_properties(source_contract, target)
        if projected:
            realization = _contract(
                kind="json_object_project",
                source_view=source_view,
                source_contract=source_contract,
                target_contract=target,
                projected_properties=projected,
            )
            return finish(
                "deterministically_convertible",
                "schema_grounded_object_projection",
                source_view=source_view,
                source_contract=source_contract,
                realization=realization,
            )
        wrapper_key = _wrapper_key(source_contract, target)
        if wrapper_key:
            realization = _contract(
                kind="json_object_wrap",
                source_view=source_view,
                source_contract=source_contract,
                target_contract=target,
                wrapper_key=wrapper_key,
            )
            return finish(
                "deterministically_convertible",
                "schema_grounded_single_property_wrapper",
                source_view=source_view,
                source_contract=source_contract,
                realization=realization,
            )
        if _lossless_serialization_supported(source_contract, target):
            realization = _contract(
                kind="lossless_serialize",
                source_view=source_view,
                source_contract=source_contract,
                target_contract=target,
            )
            return finish(
                "deterministically_convertible",
                "lossless_representation_serialization",
                source_view=source_view,
                source_contract=source_contract,
                realization=realization,
            )

    return finish(
        "controller_required",
        "deterministic_realization_not_provable",
        source_view=("semantic" if semantic is not None and payload_path else "native"),
        source_contract=(semantic if semantic is not None and payload_path else native),
    )


def _strict_target_value(value: Any, target_contract: Mapping[str, Any]) -> Any:
    artifact_type = normalized_artifact_type(target_contract.get("artifact_type"))
    if artifact_type == "json":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OutputRealizationError("realized_json_invalid") from exc
        schema = contract_schema(target_contract)
        if not isinstance(schema, Mapping):
            raise OutputRealizationError("target_json_schema_missing")
        try:
            canonical_schema = require_semantic_json_schema(schema)
        except ModelResponseContractError as exc:
            raise OutputRealizationError("target_json_schema_invalid") from exc
        valid, _ = validate_json_schema_instance(value, canonical_schema)
        if not valid:
            raise OutputRealizationError("realized_output_target_contract_mismatch")
        return value
    if artifact_type in {"text", "plaintext", "csv", "markdown", "code", "file"}:
        if not isinstance(value, str):
            raise OutputRealizationError("realized_textual_output_type_mismatch")
        return value
    raise OutputRealizationError("target_artifact_type_not_stage_a_supported")


class OutputRealizer:
    """Execute one sealed Stage-A realization without external effects."""

    def realize(
        self,
        *,
        resource_call_id: str,
        resource_id: str,
        operation_id: str,
        native_value: Any,
        native_content: str,
        native_output_sha256: str,
        semantic_view_available: bool,
        semantic_value: Any = None,
        semantic_content: str = "",
        semantic_output_sha256: str | None = None,
        source_contract: Mapping[str, Any],
        target_contract: Mapping[str, Any],
        contract: OutputRealizationContractV1,
    ) -> OutputRealizationResultV1:
        started_ns = time.perf_counter_ns()
        source_value = native_value
        source_content = native_content
        if contract.source_view == "semantic":
            source_value = semantic_value
            source_content = semantic_content
        source_bytes = len(
            representation_bytes(
                source_content
                if source_content or isinstance(source_value, str)
                else source_value,
                contract.source_artifact_type,
            )
        )
        realization_id = canonical_sha256(
            {
                "resource_call_id": resource_call_id,
                "resource_id": resource_id,
                "operation_id": operation_id,
                "output_realization_contract_sha256": contract.contract_sha256,
            }
        )

        def failed(code: str) -> OutputRealizationResultV1:
            return OutputRealizationResultV1(
                realization_id=realization_id,
                status="failure",
                failure_code=code,
                metrics=OutputRealizationMetricsV1(
                    realization_kind=contract.realization_kind,
                    source_bytes=source_bytes,
                    target_bytes=0,
                    latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                    status="failure",
                ),
            )

        if canonical_sha256(dict(source_contract)) != contract.source_contract_sha256:
            return failed("source_contract_identity_mismatch")
        if canonical_sha256(dict(target_contract)) != contract.target_contract_sha256:
            return failed("target_contract_identity_mismatch")
        if contract.source_view == "semantic" and not semantic_view_available:
            return failed("sealed_semantic_view_unavailable")
        try:
            kind = contract.realization_kind
            if kind in {"identity", "manifest_payload_extract"}:
                realized = source_value
                presentation = source_content
            elif kind == "json_object_project":
                if not isinstance(source_value, Mapping):
                    raise OutputRealizationError("projection_source_not_object")
                target_schema = _strict_object_schema(target_contract)
                if target_schema is None:
                    raise OutputRealizationError("projection_target_schema_invalid")
                required = tuple(target_schema.get("required") or ())
                missing = [name for name in required if name not in source_value]
                if missing:
                    raise OutputRealizationError("projection_required_property_missing")
                realized = {
                    name: source_value[name]
                    for name in contract.projected_properties
                    if name in source_value
                }
                presentation = canonical_json_bytes(realized).decode("utf-8")
            elif kind == "json_object_wrap":
                realized = {str(contract.wrapper_key): source_value}
                presentation = canonical_json_bytes(realized).decode("utf-8")
            elif kind == "lossless_serialize":
                if normalized_artifact_type(contract.source_artifact_type) == "json":
                    presentation = canonical_json_bytes(source_value).decode("utf-8")
                elif isinstance(source_value, str):
                    presentation = source_value
                else:
                    raise OutputRealizationError("lossless_serialization_source_invalid")
                realized = presentation
            else:  # pragma: no cover - Literal plus sealed model closes this branch.
                raise OutputRealizationError("realization_kind_unsupported")
            realized = _strict_target_value(realized, target_contract)
            if not presentation:
                presentation = (
                    canonical_json_bytes(realized).decode("utf-8")
                    if normalized_artifact_type(contract.target_artifact_type) == "json"
                    else str(realized)
                )
        except OutputRealizationError as exc:
            return failed(exc.code)

        target_bytes = representation_bytes(
            presentation if presentation else realized,
            contract.target_artifact_type,
        )
        realized_sha256 = hashlib.sha256(target_bytes).hexdigest()
        provenance = OutputRealizationProvenanceV1(
            resource_call_id=resource_call_id,
            resource_id=resource_id,
            operation_id=operation_id,
            native_output_sha256=native_output_sha256,
            semantic_output_sha256=semantic_output_sha256,
            source_view=contract.source_view,
            source_contract_sha256=contract.source_contract_sha256,
            output_realization_contract_sha256=contract.contract_sha256,
            target_contract_sha256=contract.target_contract_sha256,
            realized_output_sha256=realized_sha256,
        )
        return OutputRealizationResultV1(
            realization_id=realization_id,
            status="success",
            realized_value=realized,
            presentation=presentation,
            metrics=OutputRealizationMetricsV1(
                realization_kind=contract.realization_kind,
                source_bytes=source_bytes,
                target_bytes=len(target_bytes),
                latency_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
                status="success",
            ),
            provenance=provenance,
        )


__all__ = [
    "OUTPUT_REALIZATION_PROTOCOL",
    "OutputReachabilityProof",
    "OutputRealizationContractV1",
    "OutputRealizationError",
    "OutputRealizationMetricsV1",
    "OutputRealizationProvenanceV1",
    "OutputRealizationResultV1",
    "OutputRealizer",
    "contract_schema",
    "normalized_artifact_type",
    "prove_output_reachability",
    "representation_bytes",
    "representation_sha256",
]
