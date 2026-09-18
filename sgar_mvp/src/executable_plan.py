"""Immutable contracts for the production executable Plan Compiler.

The contracts in this module form the only production boundary between the
revisioned frozen candidate pool and the Resource Runtime.  They deliberately
contain no retrieval, semantic repair, command synthesis, or execution logic.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import Field, ValidationError, field_validator, model_validator, model_serializer

from .compiler_invariants import (
    COMPILER_INVARIANT_CATALOG_PROTOCOL,
    CompilerInvariantCatalogV2,
    CompilerProposalValidationIssueV1,
    compiler_invariant_rules,
    require_compiler_proposal_invariants,
)
from .capability_cards import CapabilityOperationV2
from .model_accounting import ModelPrice, ModelPricingCatalog
from .formal_contracts import (
    ExecutableEdgeContractV2,
    ExecutionObligationV1,
    ExecutionObligationV2,
    ExecutionResourceRequirementV1,
    MaterialDescriptorV1,
    SemanticEdgeContractV2,
)
from .model_response_contracts import (
    CapabilityProbeEvidence,
    ModelResponseContractError,
    OutputFormatRequirement,
    classify_output_schema_phase,
    local_json_schema_support,
    strict_json_loads,
    system_role_requirement,
)
from .pipeline_control import (
    CandidatePoolSnapshot,
    FrozenContract,
    SubtaskRevisionRef,
    canonical_sha256,
)
from .output_realization import (
    OutputReachabilityProof,
    OutputRealizationContractV1,
)
from .controller_session import ContextBoundContract, ControllerContextBindingV1, ControllerSessionSpec
from .controller_tooling import (
    ControllerToolingError,
    validate_callable_port_partition,
)
from .resource_runtime import (
    ApplicationProfile,
    ResourceDefinition,
    ResourceEntrypoint,
    runtime_adapter_supported,
)
from .retrieval_runtime import (
    CandidateDependencyEdge,
    CandidateScoreEvidence,
    FrozenCandidatePoolResult,
    RetrievalContractProjection,
)
from .schema import DagEdgeContractV1, OperationKind
from .planner_wire import PlannerSchemaGraphWireV1, PlannerWireContractError, compile_planner_schema_graph


PLAN_COMPILER_INPUT_PROTOCOL = "sgar-plan-compiler-input-v3"
COMPILER_PLAN_DRAFT_PROTOCOL = "sgar-compiler-plan-draft-v1"
EXECUTABLE_PLAN_PROTOCOL = "sgar-executable-plan-v1"
PLAN_VALIDATION_PROTOCOL = "sgar-plan-validation-v1"
PLAN_LOWERING_PROTOCOL = "sgar-plan-lowering-v1"
PLAN_OBJECTIVE_PROTOCOL = "sgar-plan-objective-v1"
PLAN_COMPILATION_ARTIFACT_PROTOCOL = "sgar-plan-compilation-artifact-v1"
COMPILER_PLAN_PROPOSAL_PROTOCOL = "sgar-compiler-plan-proposal-v2"
COMPILER_DECISION_PROTOCOL = "sgar-compiler-decision-v5"
COMPILER_PROJECTION_AUDIT_PROTOCOL = "sgar-compiler-projection-audit-v1"
CANDIDATE_POOL_FEASIBILITY_AUDIT_PROTOCOL = "sgar-candidate-pool-feasibility-audit-v1"
RUNTIME_MATERIAL_ADAPTER_CAPABILITY_PROTOCOL = (
    "sgar-runtime-material-adapter-capability-v1"
)
MATERIAL_DELIVERY_RESOLUTION_PROTOCOL = "sgar-material-delivery-resolution-v1"

_DELIVERY_MODE_ORDER = ("inline", "artifact_handle")
_INLINE_MATERIAL_MAX_BYTES = 2_000_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:^|[\s'\"=(])(?:[a-z]:[\\/]|\\\\)")


def _require_sha256(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError(f"{field_name}_must_be_sha256_hex")
    return normalized


def _canonical_value(value: Any) -> Any:
    """Round-trip a value through the shared canonical JSON projection."""

    import json

    from .pipeline_control import canonical_json_bytes

    return json.loads(canonical_json_bytes(value).decode("utf-8"))


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
        _WINDOWS_ABSOLUTE.search(value)
        or value.startswith("file:///")
    ):
        return locator
    return None


def _ensure_host_free(value: Any, *, field_name: str) -> Any:
    locator = _host_path_locator(value)
    if locator:
        raise ValueError(f"{field_name}_contains_host_path:{locator}")
    return value


class CompilePurpose(str, Enum):
    INITIAL = "initial"
    EXECUTION_ADAPTATION = "execution_adaptation"
    FULL_GENERATION = "full_generation"


class PlanRevisionRef(FrozenContract):
    protocol: Literal[EXECUTABLE_PLAN_PROTOCOL] = EXECUTABLE_PLAN_PROTOCOL
    subtask_revision: SubtaskRevisionRef
    plan_revision: int = Field(ge=0)
    compile_purpose: CompilePurpose
    revision_sha256: str = ""

    @model_validator(mode="after")
    def _seal_revision(self) -> "PlanRevisionRef":
        if self.compile_purpose is CompilePurpose.INITIAL and self.plan_revision != 0:
            raise ValueError("initial_plan_revision_must_be_zero")
        projection = self.model_dump(mode="python", exclude={"revision_sha256"})
        expected = canonical_sha256(projection)
        if self.revision_sha256:
            supplied = _require_sha256(self.revision_sha256, field_name="revision_sha256")
            if supplied != expected:
                raise ValueError("plan_revision_sha256_mismatch")
        object.__setattr__(self, "revision_sha256", expected)
        return self


class EntrypointExecutionCard(FrozenContract):
    entrypoint_id: str = Field(min_length=1)
    input_contract: tuple[dict[str, Any], ...] = ()
    output_contract: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_contract", mode="before")
    @classmethod
    def _canonical_inputs(cls, value: Any) -> tuple[dict[str, Any], ...]:
        items = tuple(dict(item) for item in (value or ()))
        _ensure_host_free(items, field_name="entrypoint_input_contract")
        return tuple(_canonical_value(item) for item in items)

    @field_validator("output_contract", mode="before")
    @classmethod
    def _canonical_output(cls, value: Any) -> dict[str, Any]:
        output = _canonical_value(dict(value or {}))
        _ensure_host_free(output, field_name="entrypoint_output_contract")
        return output


class ApplicationProfileCard(FrozenContract):
    profile_id: str = Field(min_length=1)
    description: str = ""
    structured_preconditions: dict[str, Any] = Field(default_factory=dict)
    binding_hints: dict[str, Any] = Field(default_factory=dict)
    output_hints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _canonical_profile(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        projected = _canonical_value(dict(value))
        _ensure_host_free(projected, field_name="application_profile")
        return projected


class ModelPricingEvidence(FrozenContract):
    pricing_catalog_sha256: str
    resource_id: str
    api_model_id: str
    provider: str
    input_per_m: Decimal
    cache_per_m: Decimal | None
    output_per_m: Decimal
    pricing_unit: Literal["USD_per_million_tokens"] = "USD_per_million_tokens"

    @field_validator("pricing_catalog_sha256")
    @classmethod
    def _pricing_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="pricing_catalog_sha256")

    @classmethod
    def from_price(
        cls,
        price: ModelPrice,
        *,
        pricing_catalog_sha256: str,
    ) -> "ModelPricingEvidence":
        return cls(
            pricing_catalog_sha256=pricing_catalog_sha256,
            resource_id=price.resource_id,
            api_model_id=price.api_model_id,
            provider=price.provider,
            input_per_m=price.input_per_m,
            cache_per_m=price.cache_per_m,
            output_per_m=price.output_per_m,
            pricing_unit=price.pricing_unit,
        )


class CandidateExecutionCard(FrozenContract):
    protocol: Literal[PLAN_COMPILER_INPUT_PROTOCOL] = PLAN_COMPILER_INPUT_PROTOCOL
    resource_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    candidate_origin: str = Field(min_length=1)
    candidate_rank: int = Field(ge=1)
    semantic_score: float | None = None
    score_evidence_sha256: str
    manifest_sha256: str
    availability_status: str = "unknown"
    compatibility_status: Literal[
        "compatible", "conditional", "unknown", "incompatible"
    ] = "unknown"
    entrypoints: tuple[EntrypointExecutionCard, ...]
    application_profiles: tuple[ApplicationProfileCard, ...] = ()
    base_input_contract: tuple[dict[str, Any], ...] = ()
    base_output_contract: dict[str, Any] = Field(default_factory=dict)
    runtime_requirements: dict[str, Any] = Field(default_factory=dict)
    required_dependency_ids: tuple[str, ...] = ()
    optional_dependency_ids: tuple[str, ...] = ()
    dependency_edges: tuple[CandidateDependencyEdge, ...] = ()
    agent_base_model_candidates: tuple[str, ...] = ()
    model_pricing: ModelPricingEvidence | None = None
    capability_operations: tuple[CapabilityOperationV2, ...] = ()
    semantic_summary: str = ""
    semantic_summary_status: Literal["declared", "unknown"] = "unknown"
    semantic_limitations: tuple[str, ...] = ()
    advisory_output_description: dict[str, Any] | None = None
    card_sha256: str = ""

    @model_serializer(mode="wrap")
    def _serialize_card(self, handler):
        payload = handler(self)
        # Absent advisory data preserves the exact pre-field serialization and seal.
        if self.advisory_output_description is None:
            payload.pop("advisory_output_description", None)
        return payload

    @field_validator("score_evidence_sha256", "manifest_sha256")
    @classmethod
    def _hash_fields(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @field_validator("semantic_score")
    @classmethod
    def _finite_score(cls, value: float | None) -> float | None:
        if value is not None:
            import math

            if not math.isfinite(float(value)):
                raise ValueError("candidate_semantic_score_must_be_finite")
        return value

    @model_validator(mode="after")
    def _seal_card(self) -> "CandidateExecutionCard":
        if not self.entrypoints:
            raise ValueError("candidate_execution_card_has_no_entrypoints")
        if len({item.entrypoint_id for item in self.entrypoints}) != len(self.entrypoints):
            raise ValueError("candidate_execution_card_duplicate_entrypoint")
        if self.resource_type == "Model" and self.model_pricing is None:
            raise ValueError("model_candidate_pricing_missing")
        if self.resource_type != "Model" and self.model_pricing is not None:
            raise ValueError("non_model_candidate_has_model_pricing")
        operation_ids = [
            item.capability_operation_id for item in self.capability_operations
        ]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("candidate_capability_operation_duplicate")
        dependency_ids = tuple(edge.child_resource_id for edge in self.dependency_edges)
        if dependency_ids != self.required_dependency_ids:
            raise ValueError("candidate_required_dependency_projection_mismatch")
        projected = self.model_dump(mode="python", exclude={"card_sha256"})
        _ensure_host_free(projected, field_name="candidate_execution_card")
        expected = canonical_sha256(projected)
        if self.card_sha256:
            supplied = _require_sha256(self.card_sha256, field_name="card_sha256")
            if supplied != expected:
                raise ValueError("candidate_execution_card_sha256_mismatch")
        object.__setattr__(self, "card_sha256", expected)
        return self


class CompilerContextDescriptor(FrozenContract):
    handle_id: str | None = None
    semantic_ref: str = ""
    logical_locator: str = Field(min_length=1)
    artifact_type: str = "unknown"
    runtime_path: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    schema_hint: Any = None
    execution_contract: dict[str, Any] = Field(default_factory=dict)
    size: int | None = Field(default=None, ge=0)
    utf8_decodable: bool | None = None
    sha256: str
    provenance_source_id: str = Field(min_length=1)
    producer_task: str | None = None
    producer_step: str | None = None
    current_run: bool = False
    edge_contract_sha256s: tuple[str, ...] = ()

    @field_validator("sha256")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="context_descriptor_sha256")

    @model_validator(mode="after")
    def _host_free(self) -> "CompilerContextDescriptor":
        if not self.semantic_ref.strip():
            object.__setattr__(self, "semantic_ref", self.logical_locator)
        projected = self.model_dump(mode="python")
        _ensure_host_free(projected, field_name="compiler_context_descriptor")
        if self.runtime_path is not None and not self.runtime_path.startswith("/app/"):
            raise ValueError("compiler_context_runtime_path_outside_app")
        return self


class CompilerPublicContext(FrozenContract):
    protocol: Literal[PLAN_COMPILER_INPUT_PROTOCOL] = PLAN_COMPILER_INPUT_PROTOCOL
    descriptors: tuple[CompilerContextDescriptor, ...] = ()
    downstream_consumers: tuple[str, ...] = ()
    provenance_source_ids: tuple[str, ...] = ()
    incoming_edge_contracts: tuple[DagEdgeContractV1, ...] = ()
    incoming_semantic_edges_v2: tuple[SemanticEdgeContractV2, ...] = ()
    edge_contracts_sha256: str = ""
    context_sha256: str = ""

    @model_validator(mode="after")
    def _seal_context(self) -> "CompilerPublicContext":
        canonical_sources = [
            item.provenance_source_id for item in self.descriptors
        ]
        if len(canonical_sources) != len(set(canonical_sources)):
            raise ValueError("compiler_context_provenance_source_duplicate")
        if self.provenance_source_ids != tuple(
            sorted(set(self.provenance_source_ids))
        ):
            raise ValueError("compiler_context_source_ids_not_unique_sorted")
        descriptor_keys = [
            (item.logical_locator, item.sha256, item.provenance_source_id)
            for item in self.descriptors
        ]
        if descriptor_keys != sorted(descriptor_keys) or len(descriptor_keys) != len(
            set(descriptor_keys)
        ):
            raise ValueError("compiler_context_descriptors_not_unique_sorted")
        declared_sources = set(self.provenance_source_ids)
        if any(item.provenance_source_id not in declared_sources for item in self.descriptors):
            raise ValueError("compiler_context_descriptor_source_not_declared")
        edge_keys = tuple(
            (item.producer_id, item.consumer_id, item.input_slot)
            for item in self.incoming_edge_contracts
        )
        if edge_keys != tuple(sorted(set(edge_keys))):
            raise ValueError("compiler_context_edge_contracts_not_unique_sorted")
        semantic_edge_keys = tuple(
            (item.producer_id, item.consumer_id, item.producer_output_ref)
            for item in self.incoming_semantic_edges_v2
        )
        if semantic_edge_keys != tuple(sorted(set(semantic_edge_keys))):
            raise ValueError("compiler_context_semantic_edges_not_unique_sorted")
        edge_hashes = tuple(
            item.edge_contract_sha256 for item in self.incoming_edge_contracts
        )
        declared_edge_hashes = set(edge_hashes)
        if any(
            edge_hash not in declared_edge_hashes
            for descriptor in self.descriptors
            for edge_hash in descriptor.edge_contract_sha256s
        ):
            raise ValueError("compiler_context_descriptor_edge_not_declared")
        expected_edges_hash = canonical_sha256(
            [item.model_dump(mode="json") for item in self.incoming_edge_contracts]
        )
        if self.edge_contracts_sha256 and self.edge_contracts_sha256 != expected_edges_hash:
            raise ValueError("compiler_context_edge_contracts_sha256_mismatch")
        object.__setattr__(self, "edge_contracts_sha256", expected_edges_hash)
        projected = self.model_dump(mode="python", exclude={"context_sha256"})
        _ensure_host_free(projected, field_name="compiler_public_context")
        expected = canonical_sha256(projected)
        if self.context_sha256:
            supplied = _require_sha256(self.context_sha256, field_name="context_sha256")
            if supplied != expected:
                raise ValueError("compiler_context_sha256_mismatch")
        object.__setattr__(self, "context_sha256", expected)
        return self


class RuntimeMaterialAdapterCapabilityV1(FrozenContract):
    """Sealed evidence for an existing Runtime material adapter."""

    protocol: Literal[
        "sgar-runtime-material-adapter-capability-v1"
    ] = RUNTIME_MATERIAL_ADAPTER_CAPABILITY_PROTOCOL
    resource_type: str = Field(min_length=1)
    available_delivery_modes: tuple[Literal["inline", "artifact_handle"], ...]
    preferred_delivery_modes: tuple[Literal["inline", "artifact_handle"], ...]
    complete_material_modes: tuple[Literal["inline", "artifact_handle"], ...]
    inline_max_bytes: int = Field(default=_INLINE_MATERIAL_MAX_BYTES, ge=1)
    adapter_protocol: str = Field(min_length=1)
    evidence_sha256: str = ""

    @model_validator(mode="after")
    def _seal_adapter(self) -> "RuntimeMaterialAdapterCapabilityV1":
        available = tuple(self.available_delivery_modes)
        preferred = tuple(self.preferred_delivery_modes)
        complete = tuple(self.complete_material_modes)
        for values, field_name in (
            (available, "available_delivery_modes"),
            (preferred, "preferred_delivery_modes"),
            (complete, "complete_material_modes"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"runtime_material_adapter_{field_name}_duplicate")
            if any(item not in _DELIVERY_MODE_ORDER for item in values):
                raise ValueError(f"runtime_material_adapter_{field_name}_invalid")
        if set(preferred) != set(available):
            raise ValueError("runtime_material_adapter_preference_mismatch")
        if not set(complete).issubset(set(available)):
            raise ValueError("runtime_material_adapter_complete_mode_unsupported")
        projected = self.model_dump(mode="python", exclude={"evidence_sha256"})
        expected = canonical_sha256(projected)
        if self.evidence_sha256:
            supplied = _require_sha256(
                self.evidence_sha256,
                field_name="runtime_material_adapter_evidence_sha256",
            )
            if supplied != expected:
                raise ValueError("runtime_material_adapter_evidence_sha256_mismatch")
        object.__setattr__(self, "evidence_sha256", expected)
        return self


def build_runtime_material_adapter_capabilities(
    resource_types: Iterable[str],
) -> tuple[RuntimeMaterialAdapterCapabilityV1, ...]:
    """Declare the adapters already implemented by the sealed Runtime.

    This is an explicit framework capability statement.  It is not inferred
    from task text, resource identifiers, material names, or retrieval rank.
    """

    modes_by_type: dict[str, tuple[str, ...]] = {
        "Agent": ("inline",),
        "Model": ("inline",),
        "Skill": ("inline", "artifact_handle"),
        "Tool": ("inline", "artifact_handle"),
    }
    adapter_protocol_by_type = {
        "Agent": "sgar-resource-call-authorized-context-v1",
        "Model": "sgar-resource-call-authorized-context-v1",
        "Skill": "sgar-resource-call-artifact-handle-hydration-v1",
        "Tool": "sgar-resource-call-artifact-handle-hydration-v1",
    }
    return tuple(
        RuntimeMaterialAdapterCapabilityV1(
            resource_type=resource_type,
            available_delivery_modes=modes_by_type[resource_type],
            preferred_delivery_modes=modes_by_type[resource_type],
            complete_material_modes=modes_by_type[resource_type],
            adapter_protocol=adapter_protocol_by_type[resource_type],
        )
        for resource_type in sorted(set(str(item) for item in resource_types))
        if resource_type in modes_by_type
    )


class RuntimeCapabilities(FrozenContract):
    runtime_image_id: str = ""
    supported_runtime_kinds: tuple[str, ...] = ()
    supported_entrypoint_dispatch_kinds: tuple[str, ...] = ()
    network_policy: Literal["disabled", "declared_only"] = "declared_only"
    direct_argv_required: Literal[True] = True
    artifact_contract_support: tuple[str, ...] = ()
    material_adapter_capabilities: tuple[
        RuntimeMaterialAdapterCapabilityV1, ...
    ] = ()
    protocol_versions: dict[str, str] = Field(default_factory=dict)
    capabilities_sha256: str = ""

    @model_validator(mode="after")
    def _seal_capabilities(self) -> "RuntimeCapabilities":
        for values, field_name in (
            (self.supported_runtime_kinds, "supported_runtime_kinds"),
            (self.supported_entrypoint_dispatch_kinds, "supported_entrypoint_dispatch_kinds"),
            (self.artifact_contract_support, "artifact_contract_support"),
        ):
            if tuple(values) != tuple(sorted(set(values))):
                raise ValueError(f"{field_name}_must_be_unique_sorted")
        adapter_types = tuple(
            item.resource_type for item in self.material_adapter_capabilities
        )
        if adapter_types != tuple(sorted(set(adapter_types))):
            raise ValueError("runtime_material_adapter_types_not_unique_sorted")
        exclude_fields = {"capabilities_sha256"}
        if not self.material_adapter_capabilities:
            # Preserve the sealed identity of historical RuntimeCapabilities
            # records.  The new evidence is identity-bearing only when it is
            # explicitly present; an omitted legacy field remains omitted.
            exclude_fields.add("material_adapter_capabilities")
        projected = self.model_dump(mode="python", exclude=exclude_fields)
        _ensure_host_free(projected, field_name="runtime_capabilities")
        expected = canonical_sha256(projected)
        if self.capabilities_sha256:
            supplied = _require_sha256(
                self.capabilities_sha256,
                field_name="capabilities_sha256",
            )
            if supplied != expected:
                raise ValueError("runtime_capabilities_sha256_mismatch")
        object.__setattr__(self, "capabilities_sha256", expected)
        return self


class CompilerPolicy(FrozenContract):
    success_first: Literal[True] = True
    model_price_mode: Literal["exact_unit_price_soft"] = "exact_unit_price_soft"
    unknown_non_model_cost: Literal["unknown"] = "unknown"
    deterministic_workflow_preferred: Literal[True] = True
    minimize_generation_calls: Literal[True] = True
    agent_loop_requires_no_simpler_feasible_plan: Literal[True] = True
    retrieval_rank_is_not_a_preference: Literal[True] = True
    candidate_expansion: Literal[False] = False
    candidate_compression: Literal[False] = False
    deterministic_fallback: Literal[False] = False
    semantic_repair: bool = True
    raw_shell_execution: Literal[False] = False
    max_semantic_calls: Literal[1, 2] = 2
    max_transport_retries: Literal[2] = 2


_REQUIRED_MATERIAL_SOURCE_KIND = {
    "public_input": "public_input",
    "node_output": "node_output",
    "completed_output": "node_output",
}


def required_material_source_ids_for_obligation(
    obligation: ExecutionObligationV2,
    materials: Sequence[MaterialDescriptorV1],
) -> tuple[str, ...]:
    """Resolve the required-and-authorized material subset in declaration order."""

    materials_by_name: dict[str, list[MaterialDescriptorV1]] = {}
    for material in materials:
        materials_by_name.setdefault(material.logical_name, []).append(material)
    source_ids: list[str] = []
    for reference in obligation.authorized_inputs:
        if _REQUIRED_MATERIAL_SOURCE_KIND.get(reference.source) not in {
            "public_input",
            "node_output",
        }:
            continue
        matches = materials_by_name.get(reference.ref, [])
        if not matches:
            raise ValueError("plan_input_semantic_material_missing")
        if len(matches) != 1:
            raise ValueError("plan_input_semantic_material_ambiguous")
        source_ids.append(matches[0].source_id)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("plan_input_semantic_material_alias_duplicate")
    return tuple(source_ids)


class PlanCompilerInputEnvelope(FrozenContract):
    protocol: Literal[PLAN_COMPILER_INPUT_PROTOCOL] = PLAN_COMPILER_INPUT_PROTOCOL
    plan_revision: PlanRevisionRef
    contract_projection: RetrievalContractProjection
    retrieval_runtime_identity_sha256: str
    candidate_pool_snapshot: CandidatePoolSnapshot
    retrieval_evidence_sha256: str
    candidate_cards: tuple[CandidateExecutionCard, ...]
    dependency_edges: tuple[CandidateDependencyEdge, ...] = ()
    public_context: CompilerPublicContext
    execution_obligations: tuple[ExecutionObligationV1 | ExecutionObligationV2, ...] = ()
    execution_requirements: tuple[ExecutionResourceRequirementV1, ...] = ()
    materials: tuple[MaterialDescriptorV1, ...] = ()
    runtime_capabilities: RuntimeCapabilities
    pricing_catalog_sha256: str
    compiler_model_resource_id: str = Field(min_length=1)
    compiler_model_api_id: str = Field(min_length=1)
    compiler_policy: CompilerPolicy = Field(default_factory=CompilerPolicy)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str
    input_sha256: str = ""

    @field_validator(
        "retrieval_runtime_identity_sha256",
        "retrieval_evidence_sha256",
        "pricing_catalog_sha256",
        "prompt_sha256",
    )
    @classmethod
    def _input_hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal_input(self) -> "PlanCompilerInputEnvelope":
        contract_revision = getattr(self.contract_projection, "revision", None)
        if contract_revision != self.plan_revision.subtask_revision:
            raise ValueError("plan_input_contract_revision_mismatch")
        if self.candidate_pool_snapshot.revision != self.plan_revision.subtask_revision:
            raise ValueError("plan_input_candidate_revision_mismatch")
        candidate_ids = [item.resource_id for item in self.candidate_pool_snapshot.candidates]
        card_ids = [item.resource_id for item in self.candidate_cards]
        if card_ids != candidate_ids:
            raise ValueError("plan_input_candidate_card_order_mismatch")
        if any(
            card.resource_type != candidate.resource_type
            for card, candidate in zip(
                self.candidate_cards,
                self.candidate_pool_snapshot.candidates,
            )
        ):
            raise ValueError("plan_input_candidate_card_type_mismatch")
        if any(
            card.model_pricing is not None
            and card.model_pricing.pricing_catalog_sha256 != self.pricing_catalog_sha256
            for card in self.candidate_cards
        ):
            raise ValueError("plan_input_candidate_pricing_catalog_mismatch")
        obligation_ids = [item.obligation_id for item in self.execution_obligations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("plan_input_execution_obligation_duplicate")
        requirement_ids = [item.requirement_id for item in self.execution_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("plan_input_execution_requirement_duplicate")
        material_ids = [item.source_id for item in self.materials]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("plan_input_material_source_duplicate")
        for obligation in self.execution_obligations:
            if not isinstance(obligation, ExecutionObligationV2):
                continue
            required_material_source_ids_for_obligation(obligation, self.materials)
        descriptors_by_source: dict[str, list[CompilerContextDescriptor]] = {}
        for descriptor in self.public_context.descriptors:
            descriptors_by_source.setdefault(
                descriptor.provenance_source_id, []
            ).append(descriptor)
        for material in self.materials:
            matches = descriptors_by_source.get(material.source_id, [])
            if len(matches) != 1:
                raise ValueError("plan_input_material_descriptor_not_unique")
            descriptor = matches[0]
            if (
                not descriptor.handle_id
                or material.handle_id != descriptor.handle_id
                or material.artifact_type != descriptor.artifact_type
            ):
                raise ValueError("plan_input_material_descriptor_mismatch")
        declared_sources = set(self.public_context.provenance_source_ids)
        if any(item.source_id not in declared_sources for item in self.materials):
            raise ValueError("plan_input_material_source_not_declared")
        projected = self.model_dump(mode="python", exclude={"input_sha256"})
        _ensure_host_free(projected, field_name="plan_compiler_input")
        expected = canonical_sha256(projected)
        if self.input_sha256:
            supplied = _require_sha256(self.input_sha256, field_name="input_sha256")
            if supplied != expected:
                raise ValueError("plan_compiler_input_sha256_mismatch")
        object.__setattr__(self, "input_sha256", expected)
        return self


class ObligationFeasibilityEvidenceV1(FrozenContract):
    obligation_id: str = Field(min_length=1)
    candidate_operation_ids: tuple[str, ...] = ()
    required_material_source_ids: tuple[str, ...] = ()
    material_accessible_source_ids: tuple[str, ...] = ()
    material_delivery_evidence_sha256s: tuple[str, ...] = ()
    potentially_feasible: bool
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _delivery_evidence_sorted(self) -> "ObligationFeasibilityEvidenceV1":
        if self.material_delivery_evidence_sha256s != tuple(
            sorted(set(self.material_delivery_evidence_sha256s))
        ):
            raise ValueError("material_delivery_evidence_not_unique_sorted")
        return self


class CandidatePoolFeasibilityAuditV1(FrozenContract):
    """Conservative declaration-level feasibility, never a generated plan."""

    protocol: Literal[
        "sgar-candidate-pool-feasibility-audit-v1"
    ] = CANDIDATE_POOL_FEASIBILITY_AUDIT_PROTOCOL
    candidate_pool_sha256: str
    obligation_evidence: tuple[ObligationFeasibilityEvidenceV1, ...]
    potentially_feasible: bool
    audit_sha256: str = ""

    @model_validator(mode="after")
    def _seal(self) -> "CandidatePoolFeasibilityAuditV1":
        _require_sha256(
            self.candidate_pool_sha256,
            field_name="candidate_pool_feasibility_candidate_pool_sha256",
        )
        expected_feasible = all(
            item.potentially_feasible for item in self.obligation_evidence
        )
        if self.potentially_feasible != expected_feasible:
            raise ValueError("candidate_pool_feasibility_status_mismatch")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"audit_sha256"})
        )
        if self.audit_sha256 and self.audit_sha256 != expected:
            raise ValueError("candidate_pool_feasibility_audit_sha256_mismatch")
        object.__setattr__(self, "audit_sha256", expected)
        return self


_TEXTUAL_ARTIFACT_TYPES = frozenset(
    {
        "code",
        "csv",
        "json",
        "log",
        "markdown",
        "md",
        "messages",
        "plaintext",
        "text",
        "txt",
        "xml",
        "yaml",
        "yml",
    }
)
_STRUCTURED_ARTIFACT_TYPES = frozenset(
    {
        "csv",
        "json",
        "parquet",
        "spreadsheet",
        "structured_data",
        "table",
        "tabular_data",
        "xml",
        "xlsx",
        "yaml",
        "yml",
    }
)
_HANDLE_ARTIFACT_TYPES = frozenset(
    {"artifact", "artifact_handle", "binary", "file", "file_path"}
)


def _normalized_artifact_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "md": "markdown",
        "text/plain": "plaintext",
        "application/json": "json",
        "text/csv": "csv",
    }
    return aliases.get(normalized, normalized)


def _artifact_type_is_accepted(
    artifact_type: str,
    accepted_artifact_types: Iterable[str],
) -> bool:
    """Apply only declared exact types and sealed generic transport categories."""

    source = _normalized_artifact_type(artifact_type)
    accepted = {
        _normalized_artifact_type(item)
        for item in accepted_artifact_types
        if str(item or "").strip()
    }
    if not accepted or source in accepted:
        return True
    if accepted & _HANDLE_ARTIFACT_TYPES:
        return True
    if accepted & {"text", "plaintext", "messages", "chat_messages"}:
        if source in _TEXTUAL_ARTIFACT_TYPES:
            return True
    if "structured_data" in accepted and source in _STRUCTURED_ARTIFACT_TYPES:
        return True
    return False


def _bound_generic_capability(card: CandidateExecutionCard) -> Mapping[str, Any] | None:
    """Consume pool-projected applied authority, never a manifest assertion."""
    if (
        card.resource_type != "Model" or card.model_pricing is None
        or card.compatibility_status != "compatible"
        or card.availability_status.strip().casefold() not in {"active", "available", "ok", "ready"}
    ):
        return None
    value = card.runtime_requirements.get("sgar_structured_output_capability")
    if not isinstance(value, Mapping):
        return None
    projection = dict(value)
    supplied = projection.pop("capability_sha256", None)
    if supplied != canonical_sha256(projection):
        return None
    if (
        value.get("resource_id") != card.resource_id
        or value.get("api_model_id") != card.model_pricing.api_model_id
        or value.get("manifest_sha256") != card.manifest_sha256
        or value.get("compatibility_status") != "compatible"
    ):
        return None
    try:
        evidence = CapabilityProbeEvidence.model_validate(value.get("generic_evidence"))
    except ValidationError:
        return None
    expected_reason = {
        "native_strict_schema": "applied_ready_state_generic_strict_schema",
        "json_object_local_validator": "applied_ready_state_json_mode",
    }.get(evidence.selected_enforcement_mode)
    if expected_reason and evidence.authority_source == "operator_approval":
        expected_reason = expected_reason.replace("applied_ready_state", "operator_approved")
    if (
        evidence.resource_id != card.resource_id
        or evidence.model_id != card.model_pricing.api_model_id
        or evidence.endpoint_identity_sha256 != value.get("endpoint_identity_sha256")
        or evidence.evidence_sha256 != value.get("generic_evidence_sha256")
        or not evidence.is_applied_admission
        or expected_reason is None or evidence.reason_code != expected_reason
    ):
        return None
    return value


def _selected_schema_identity_valid(
    *, card: CandidateExecutionCard, cards: Mapping[str, CandidateExecutionCard],
    selected_backing_model_id: str | None, contract: Mapping[str, Any] | None,
    expected_schema_sha256: str | None, identity_required: bool,
) -> bool:
    if not isinstance(contract, Mapping):
        return False
    projection = dict(contract)
    if projection.pop("format_contract_sha256", None) != canonical_sha256(projection):
        return False
    if (
        not expected_schema_sha256
        or contract.get("schema_sha256") != expected_schema_sha256
        or not isinstance(contract.get("schema"), Mapping)
        or canonical_sha256(contract["schema"]) != expected_schema_sha256
    ):
        return False
    if not identity_required and "selected_model_resource_id" not in contract:
        return True  # Legacy authoritative-schema contract, checked by its existing gates.
    model = card if card.resource_type == "Model" else cards.get(str(selected_backing_model_id))
    if model is None or (
        card.resource_type == "Agent"
        and selected_backing_model_id not in card.agent_base_model_candidates
    ):
        return False
    generic = _bound_generic_capability(model)
    if generic is None:
        return False
    if any(contract.get(key) != expected for key, expected in {
        "selected_model_resource_id": model.resource_id,
        "selected_model_api_id": model.model_pricing.api_model_id,
        "endpoint_identity_sha256": generic["endpoint_identity_sha256"],
        "generic_capability_sha256": generic["capability_sha256"],
        "generic_evidence_sha256": generic["generic_evidence_sha256"],
    }.items()):
        return False
    try:
        exact = CapabilityProbeEvidence.model_validate(contract.get("selected_probe_evidence"))
        requirement = OutputFormatRequirement.from_json_schema(
            artifact_type="json", json_schema=contract["schema"],
            schema_source=contract["schema_source"],
        )
    except (ValidationError, ModelResponseContractError, KeyError):
        return False
    if (
        exact.resource_id != model.resource_id
        or exact.model_id != model.model_pricing.api_model_id
        or exact.endpoint_identity_sha256 != generic["endpoint_identity_sha256"]
        or exact.evidence_sha256 != contract.get("probe_evidence_sha256")
        or exact.schema_sha256 != expected_schema_sha256
        or exact.requirement_sha256 != requirement.requirement_sha256
        or exact.wire_schema_sha256 != requirement.wire_schema_sha256
        or contract.get("wire_schema_sha256") != requirement.wire_schema_sha256
        or contract.get("requirement_sha256") != requirement.requirement_sha256
        or not contract.get("final_producer_eligible")
        or not exact.is_applied_admission
        or exact.authority_source != generic["generic_evidence"]["authority_source"]
        or exact.message_sha256 != generic["generic_evidence"].get("message_sha256")
        or exact.response_sha256 != generic["generic_evidence"]["response_sha256"]
        or exact.selected_enforcement_mode != contract.get("selected_enforcement_mode")
    ):
        return False
    mode = exact.selected_enforcement_mode
    if mode not in contract.get("allowed_enforcement_modes", ()):
        return False
    if mode == "native_strict_schema":
        return bool(requirement.portable_wire_schema and requirement.portable_wire_schema.native_eligible)
    return mode == "json_object_local_validator" and local_json_schema_support(requirement.json_schema)[0]


def resolve_final_step_eligibility(
    card: CandidateExecutionCard,
    operation: CapabilityOperationV2,
    final_artifact_type: str,
    schema_generation_required: bool,
    runtime_capabilities: RuntimeCapabilities,
    *,
    phase: Literal["selection", "postcompile"] | None = None,
    schema_phase: str | None = None,
    candidate_cards: Mapping[str, CandidateExecutionCard] | None = None,
    selected_backing_model_id: str | None = None,
    expected_schema_sha256: str | None = None,
    selected_exact_contract: Mapping[str, Any] | None = None,
) -> bool:
    """Return the single framework truth for final-step operation eligibility."""

    resource_type = str(card.resource_type or "")
    if resource_type not in {"Model", "Agent", "Tool", "Skill", "Resource"}:
        return False
    if (
        str(card.availability_status or "").strip().casefold()
        not in {"active", "available", "ok", "ready"}
        or card.compatibility_status != "compatible"
    ):
        return False

    entrypoint_id = str(operation.entrypoint_id or "").strip()
    if not entrypoint_id:
        return False
    if sum(item.entrypoint_id == entrypoint_id for item in card.entrypoints) != 1:
        return False

    artifact_type = _normalized_artifact_type(final_artifact_type)
    produced_types = tuple(
        item for item in operation.produced_artifact_types if str(item or "").strip()
    )
    if not artifact_type or not produced_types:
        return False
    if not _artifact_type_is_accepted(artifact_type, produced_types):
        return False
    if schema_generation_required and artifact_type != "json":
        return False
    if not runtime_capabilities.artifact_contract_support or not _artifact_type_is_accepted(
        artifact_type, runtime_capabilities.artifact_contract_support
    ):
        return False

    requirements = card.runtime_requirements
    runtime_kind = requirements.get("runtime_kind") or requirements.get("runtime_type")
    if not runtime_kind:
        return False
    supported_runtime_kinds = set(runtime_capabilities.supported_runtime_kinds)
    if not supported_runtime_kinds or str(runtime_kind) not in supported_runtime_kinds:
        return False
    if not runtime_adapter_supported(resource_type, str(runtime_kind)):
        return False
    network = requirements.get("network")
    network_required = bool(
        requirements.get("network_required")
        or (isinstance(network, Mapping) and network.get("required"))
    )
    if network_required and runtime_capabilities.network_policy != "declared_only":
        return False

    if resource_type in {"Model", "Agent"} and artifact_type == "json":
        if phase == "selection" and schema_phase == "compiler_pending":
            models = (
                (card,)
                if resource_type == "Model"
                else tuple(
                    candidate_cards[model_id]
                    for model_id in card.agent_base_model_candidates
                    if candidate_cards is not None and model_id in candidate_cards
                    and (selected_backing_model_id is None or model_id == selected_backing_model_id)
                )
            )
            return any(_bound_generic_capability(model) is not None for model in models)
        if phase is not None and schema_phase == "invalid_missing":
            return False
        format_contract = requirements.get("sgar_format_contract")
        if phase == "postcompile":
            format_contract = selected_exact_contract
            if not _selected_schema_identity_valid(
                card=card, cards=candidate_cards or {},
                selected_backing_model_id=selected_backing_model_id,
                contract=format_contract, expected_schema_sha256=expected_schema_sha256,
                identity_required=schema_phase == "compiler_pending",
            ):
                return False
        if not isinstance(format_contract, Mapping):
            return False
        sealed_contract = dict(format_contract)
        supplied_hash = str(sealed_contract.pop("format_contract_sha256", ""))
        if not supplied_hash or supplied_hash != canonical_sha256(sealed_contract):
            return False
        if not bool(format_contract.get("final_producer_eligible")):
            return False
        modes = set(format_contract.get("allowed_enforcement_modes") or ())
        if not modes & {"native_strict_schema", "json_object_local_validator"}:
            return False

    return True


class MaterialDeliveryResolutionV1(FrozenContract):
    """Deterministic source-operation-port delivery evidence."""

    protocol: Literal[
        "sgar-material-delivery-resolution-v1"
    ] = MATERIAL_DELIVERY_RESOLUTION_PROTOCOL
    status: Literal["available", "unavailable", "unproven"]
    available_delivery_modes: tuple[Literal["inline", "artifact_handle"], ...]
    artifact_type_compatibility: Literal["compatible", "incompatible"]
    runtime_adapter_support: Literal["supported", "unsupported", "unproven"]
    complete_material_support: Literal["supported", "unsupported", "unproven"]
    reason_codes: tuple[str, ...]
    resource_type: str = Field(min_length=1)
    capability_operation_id: str = Field(min_length=1)
    target_port: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    material_descriptor_sha256: str
    operation_sha256: str
    runtime_capabilities_sha256: str
    evidence_sha256: str = ""

    @model_validator(mode="after")
    def _seal_resolution(self) -> "MaterialDeliveryResolutionV1":
        if self.available_delivery_modes != tuple(
            item for item in _DELIVERY_MODE_ORDER if item in self.available_delivery_modes
        ):
            raise ValueError("material_delivery_modes_not_canonical")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("material_delivery_reason_codes_not_unique_sorted")
        for value, field_name in (
            (self.material_descriptor_sha256, "material_descriptor_sha256"),
            (self.operation_sha256, "operation_sha256"),
            (self.runtime_capabilities_sha256, "runtime_capabilities_sha256"),
        ):
            _require_sha256(value, field_name=field_name)
        if self.status == "available" and not self.available_delivery_modes:
            raise ValueError("available_material_delivery_has_no_mode")
        if self.status != "available" and self.available_delivery_modes:
            raise ValueError("unavailable_material_delivery_has_mode")
        projected = self.model_dump(mode="python", exclude={"evidence_sha256"})
        expected = canonical_sha256(projected)
        if self.evidence_sha256:
            supplied = _require_sha256(
                self.evidence_sha256,
                field_name="material_delivery_evidence_sha256",
            )
            if supplied != expected:
                raise ValueError("material_delivery_evidence_sha256_mismatch")
        object.__setattr__(self, "evidence_sha256", expected)
        return self


def resolve_material_delivery(
    *,
    material: MaterialDescriptorV1,
    candidate_card: CandidateExecutionCard,
    operation: CapabilityOperationV2,
    target_port: str,
    runtime_capabilities: RuntimeCapabilities,
) -> MaterialDeliveryResolutionV1:
    """Resolve material delivery from sealed declarations without guessing."""

    reasons: set[str] = set()
    artifact_compatible = _artifact_type_is_accepted(
        material.artifact_type,
        operation.accepted_artifact_types,
    )
    declared_ports = {item.name for item in operation.input_ports}
    if target_port not in declared_ports:
        reasons.add("target_port_not_declared")
    if not artifact_compatible:
        reasons.add("artifact_type_incompatible")

    adapters = [
        item
        for item in runtime_capabilities.material_adapter_capabilities
        if item.resource_type == candidate_card.resource_type
    ]
    adapter = adapters[0] if len(adapters) == 1 else None
    if not adapters:
        reasons.add("runtime_adapter_evidence_missing")
        adapter_support: Literal["supported", "unsupported", "unproven"] = (
            "unproven"
        )
    elif len(adapters) > 1:
        reasons.add("runtime_adapter_evidence_ambiguous")
        adapter_support = "unproven"
    else:
        adapter_support = "supported"

    requested_modes: tuple[str, ...]
    if operation.material_access == "both":
        requested_modes = _DELIVERY_MODE_ORDER
    elif operation.material_access in _DELIVERY_MODE_ORDER:
        requested_modes = (operation.material_access,)
    else:
        requested_modes = ()
        reasons.add("operation_material_access_unavailable")

    available_modes: list[str] = []
    unproven_mode = False
    if adapter is not None and artifact_compatible and target_port in declared_ports:
        for mode in requested_modes:
            if mode not in adapter.available_delivery_modes:
                reasons.add(f"runtime_adapter_{mode}_unsupported")
                continue
            if mode == "inline":
                if material.utf8_decodable is None:
                    reasons.add("inline_utf8_decodability_unproven")
                    unproven_mode = True
                    continue
                if material.utf8_decodable is False:
                    reasons.add("inline_utf8_decoding_unsupported")
                    continue
                if material.original_bytes > adapter.inline_max_bytes:
                    reasons.add("inline_material_size_exceeds_limit")
                    continue
                if not material.content_sha256:
                    reasons.add("inline_complete_material_identity_missing")
                    continue
            elif not material.handle_id:
                reasons.add("artifact_handle_missing")
                continue
            available_modes.append(mode)

    canonical_modes = tuple(
        item for item in _DELIVERY_MODE_ORDER if item in available_modes
    )
    if canonical_modes:
        status: Literal["available", "unavailable", "unproven"] = "available"
        adapter_support = "supported"
        complete_support: Literal["supported", "unsupported", "unproven"] = (
            "supported"
            if adapter is not None
            and any(item in adapter.complete_material_modes for item in canonical_modes)
            else "unsupported"
        )
    elif adapter is None or unproven_mode:
        status = "unproven"
        complete_support = "unproven"
    else:
        status = "unavailable"
        if requested_modes and not set(requested_modes).intersection(
            adapter.available_delivery_modes
        ):
            adapter_support = "unsupported"
        complete_support = "unsupported"

    return MaterialDeliveryResolutionV1(
        status=status,
        available_delivery_modes=canonical_modes,
        artifact_type_compatibility=(
            "compatible" if artifact_compatible else "incompatible"
        ),
        runtime_adapter_support=adapter_support,
        complete_material_support=complete_support,
        reason_codes=tuple(sorted(reasons)),
        resource_type=candidate_card.resource_type,
        capability_operation_id=operation.capability_operation_id,
        target_port=target_port,
        source_id=material.source_id,
        material_descriptor_sha256=material.descriptor_sha256,
        operation_sha256=operation.operation_sha256,
        runtime_capabilities_sha256=runtime_capabilities.capabilities_sha256,
    )


def select_material_delivery_mode(
    *,
    resolution: MaterialDeliveryResolutionV1,
    candidate_card: CandidateExecutionCard,
    runtime_capabilities: RuntimeCapabilities,
) -> Literal["inline", "artifact_handle"]:
    """Select one already-proven delivery mode using sealed adapter priority."""

    if resolution.status != "available":
        raise ValueError("compiler_v3_material_binding_unauthorized")
    adapters = [
        item
        for item in runtime_capabilities.material_adapter_capabilities
        if item.resource_type == candidate_card.resource_type
    ]
    if len(adapters) != 1:
        raise ValueError("compiler_v3_material_binding_unauthorized")
    for mode in adapters[0].preferred_delivery_modes:
        if mode in resolution.available_delivery_modes:
            return mode
    raise ValueError("compiler_v3_material_binding_unauthorized")


def audit_candidate_pool_feasibility(
    envelope: PlanCompilerInputEnvelope,
) -> CandidatePoolFeasibilityAuditV1:
    """Check whether declarations expose at least one possible covering plan.

    This deliberately does not infer business capability from names or prose and
    does not authorize execution.  It only decides whether a Compiler claim of
    total pool insufficiency deserves one targeted semantic correction.
    """

    material_by_id = {item.source_id: item for item in envelope.materials}
    operations = tuple(
        (card, operation)
        for card in envelope.candidate_cards
        if card.availability_status not in {"unavailable", "disabled"}
        and card.compatibility_status == "compatible"
        for operation in card.capability_operations
        if operation.entrypoint_id is not None
        and operation.evidence_status != "unknown"
    )
    evidence: list[ObligationFeasibilityEvidenceV1] = []
    for obligation in envelope.execution_obligations:
        candidates: list[tuple[CandidateExecutionCard, CapabilityOperationV2]] = []
        for card, operation in operations:
            if isinstance(obligation, ExecutionObligationV1):
                if (
                    obligation.work_nature == "deterministic"
                    and operation.determinism == "nondeterministic"
                ):
                    continue
                if (
                    obligation.side_effect_policy == "none"
                    and operation.side_effects == "declared"
                ):
                    continue
            candidates.append((card, operation))
        if isinstance(obligation, ExecutionObligationV1):
            required_material_ids = tuple(
                source_id
                for source_id in obligation.required_evidence_source_ids
                if source_id in material_by_id
            )
        else:
            required_material_ids = tuple(
                required_material_source_ids_for_obligation(
                    obligation,
                    envelope.materials,
                )
            )
        accessible_material_ids: list[str] = []
        delivery_evidence_sha256s: list[str] = []
        for source_id in required_material_ids:
            material = material_by_id[source_id]
            resolutions = tuple(
                resolve_material_delivery(
                    material=material,
                    candidate_card=card,
                    operation=operation,
                    target_port=port.name,
                    runtime_capabilities=envelope.runtime_capabilities,
                )
                for card, operation in candidates
                for port in operation.input_ports
            )
            delivery_evidence_sha256s.extend(
                item.evidence_sha256 for item in resolutions
            )
            if any(item.status == "available" for item in resolutions):
                accessible_material_ids.append(source_id)
        reasons: list[str] = []
        if not candidates:
            reasons.append("no_declared_operation_with_compatible_properties")
        missing_materials = set(required_material_ids) - set(accessible_material_ids)
        if missing_materials:
            reasons.append("required_material_access_unavailable")
        if not any(
            resolve_final_step_eligibility(
                card,
                operation,
                envelope.contract_projection.artifact_type,
                bool(
                    envelope.contract_projection.json_schema is None
                    and envelope.contract_projection.artifact_type == "json"
                ),
                envelope.runtime_capabilities,
            )
            for card, operation in candidates
        ):
            reasons.append("no_final_step_eligible_operation")
        evidence.append(
            ObligationFeasibilityEvidenceV1(
                obligation_id=obligation.obligation_id,
                candidate_operation_ids=tuple(
                    sorted(
                        operation.capability_operation_id
                        for _card, operation in candidates
                    )
                ),
                required_material_source_ids=required_material_ids,
                material_accessible_source_ids=tuple(sorted(accessible_material_ids)),
                material_delivery_evidence_sha256s=tuple(
                    sorted(set(delivery_evidence_sha256s))
                ),
                potentially_feasible=not reasons,
                reason_codes=tuple(reasons),
            )
        )
    return CandidatePoolFeasibilityAuditV1(
        candidate_pool_sha256=(
            envelope.candidate_pool_snapshot.candidate_pool_sha256
        ),
        obligation_evidence=tuple(evidence),
        potentially_feasible=all(item.potentially_feasible for item in evidence),
    )


class InsufficiencyCode(str, Enum):
    CANDIDATE_POOL_INSUFFICIENT = "candidate_pool_insufficient"
    CONTRACT_NOT_ACHIEVABLE = "contract_not_achievable"
    RUNTIME_REQUIREMENTS_UNMET = "runtime_requirements_unmet"
    REQUIRED_DEPENDENCY_UNUSABLE = "required_dependency_unusable"
    REQUIRED_INPUT_MISSING = "required_input_missing"


from .artifact_semantics import require_artifact_semantics


class StepOutputContract(FrozenContract):
    content_kind: Literal["value", "json_schema_document"] = "value"
    artifact_type: str | None = None
    schema_hint: Any = None
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _canonical_contract(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        projected = _canonical_value(dict(value))
        _ensure_host_free(projected, field_name="step_output_contract")
        require_artifact_semantics(
            content_kind=projected.get("content_kind", "value"),
            artifact_type=projected.get("artifact_type"), schema=projected.get("schema_hint"),
        )
        return projected


class ResourceApplicationV1(FrozenContract):
    """Framework-completed execution application for one selected resource."""

    protocol: Literal[EXECUTABLE_PLAN_PROTOCOL] = EXECUTABLE_PLAN_PROTOCOL
    resource_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    capability_operation_id: str = Field(min_length=1)
    entrypoint_id: str = Field(min_length=1)
    resource_manifest_sha256: str
    operation_input_contract: tuple[dict[str, Any], ...] = ()
    resource_native_output_contract: dict[str, Any]
    available_semantic_output_contract: dict[str, Any] | None = None
    input_bindings: dict[str, Any] = Field(default_factory=dict)
    dependency_bindings: dict[str, Any] = Field(default_factory=dict)
    target_step_output_contract: StepOutputContract
    output_reachability_proof: OutputReachabilityProof
    output_realization_contract: OutputRealizationContractV1 | None = None
    application_sha256: str = ""

    @field_validator("resource_manifest_sha256")
    @classmethod
    def _manifest_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="resource_manifest_sha256")

    @model_validator(mode="before")
    @classmethod
    def _canonical_application(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        projected = _canonical_value(dict(value))
        _ensure_host_free(projected, field_name="resource_application")
        return projected

    @model_validator(mode="after")
    def _seal_application(self) -> "ResourceApplicationV1":
        proof = self.output_reachability_proof
        if (
            proof.resource_id != self.resource_id
            or proof.capability_operation_id != self.capability_operation_id
            or proof.entrypoint_id != self.entrypoint_id
        ):
            raise ValueError("resource_application_reachability_identity_mismatch")
        if canonical_sha256(self.target_step_output_contract.model_dump(mode="json")) != (
            proof.target_contract_sha256
        ):
            raise ValueError("resource_application_target_contract_identity_mismatch")
        source_contract = (
            self.available_semantic_output_contract
            if proof.source_view == "semantic"
            else self.resource_native_output_contract
        )
        if source_contract is None or canonical_sha256(source_contract) != (
            proof.source_contract_sha256
        ):
            raise ValueError("resource_application_source_contract_identity_mismatch")
        deterministic = proof.compatibility in {
            "exact",
            "deterministically_convertible",
        }
        if deterministic != (self.output_realization_contract is not None):
            raise ValueError("resource_application_realization_contract_missing")
        if self.output_realization_contract is not None and (
            self.output_realization_contract.contract_sha256
            != proof.output_realization_contract_sha256
        ):
            raise ValueError("resource_application_realization_identity_mismatch")
        if set(self.dependency_bindings) - set(self.input_bindings):
            raise ValueError("resource_application_dependency_binding_undeclared")
        projection = self.model_dump(mode="python", exclude={"application_sha256"})
        expected = canonical_sha256(projection)
        if self.application_sha256 and self.application_sha256 != expected:
            raise ValueError("resource_application_sha256_mismatch")
        object.__setattr__(self, "application_sha256", expected)
        return self


class StepOutputRef(FrozenContract):
    step_id: str = Field(min_length=1)
    output_key: str = Field(min_length=1)


class CompilerBindingProposalV2(FrozenContract):
    name: str = Field(min_length=1)
    source_kind: Literal[
        "literal",
        "artifact_handle",
        "resource",
        "step_output",
        "logical_path",
    ]
    literal_json: str | None = None
    source_id: str | None = None
    from_step: str | None = None
    output_key: str | None = None
    logical_path: str | None = None


class CompilerOutputContractProposalV2(FrozenContract):
    content_kind: Literal["value", "json_schema_document"] = "value"
    mode: Literal["subtask_final", "selected_resource", "custom"]
    artifact_type: str | None
    description: str | None
    schema_hint_json: str | None


class CompilerStepProposalV2(ContextBoundContract):
    step_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    capability_operation_id: str | None = None
    satisfied_obligation_ids: tuple[str, ...] = ()
    operation_kind: OperationKind
    entrypoint_id: str | None = None
    intent: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    input_bindings: tuple[CompilerBindingProposalV2, ...] = ()
    consumed_context_source_ids: tuple[str, ...] = ()
    output_key: str = Field(min_length=1)
    output_contract: CompilerOutputContractProposalV2
    advisory_profile_refs: tuple[str, ...] = ()
    agent_base_model_resource_id: str | None = None


class CompilerLiteralValueV3(FrozenContract):
    value_type: Literal["string", "integer", "number", "boolean", "string_list"]
    string_value: str | None = None
    integer_value: int | None = None
    number_value: float | None = None
    boolean_value: bool | None = None
    string_list_value: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _selected_value(self) -> "CompilerLiteralValueV3":
        values = {
            "string": self.string_value,
            "integer": self.integer_value,
            "number": self.number_value,
            "boolean": self.boolean_value,
        }
        populated = [name for name, value in values.items() if value is not None]
        if self.value_type == "string_list":
            valid = not populated
        else:
            valid = populated == [self.value_type] and not self.string_list_value
        if not valid:
            raise ValueError("compiler_v3_literal_value_mismatch")
        return self

    def python_value(self) -> Any:
        if self.value_type == "string":
            return self.string_value
        if self.value_type == "integer":
            return self.integer_value
        if self.value_type == "number":
            return self.number_value
        if self.value_type == "boolean":
            return self.boolean_value
        return list(self.string_list_value)


class CompilerInputMappingV3(FrozenContract):
    target_port: str = Field(min_length=1)
    source_kind: Literal[
        "literal", "artifact_handle", "resource", "step_output"
    ]
    source_id: str | None = None
    from_step: str | None = None
    literal_value: CompilerLiteralValueV3 | None = None

    @model_validator(mode="after")
    def _source_shape(self) -> "CompilerInputMappingV3":
        if self.source_kind == "literal":
            valid = self.literal_value is not None and self.source_id is None and self.from_step is None
        elif self.source_kind == "step_output":
            valid = self.from_step is not None and self.source_id is None and self.literal_value is None
        else:
            valid = self.source_id is not None and self.from_step is None and self.literal_value is None
        if not valid:
            raise ValueError("compiler_v3_input_mapping_shape_invalid")
        return self


class CompilerCallableToolDecisionV1(FrozenContract):
    """The Compiler selects only Tool/operation and fixed-vs-dynamic authority."""

    resource_id: str = Field(min_length=1)
    capability_operation_id: str = Field(min_length=1)
    fixed_input_mappings: tuple[CompilerInputMappingV3, ...] = ()
    dynamic_input_ports: tuple[str, ...] = ()
    capability_evidence_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _port_shape(self) -> "CompilerCallableToolDecisionV1":
        fixed_names = tuple(item.target_port for item in self.fixed_input_mappings)
        if len(fixed_names) != len(set(fixed_names)):
            raise ValueError("callable_tool_fixed_input_port_duplicate")
        if (
            len(self.dynamic_input_ports) != len(set(self.dynamic_input_ports))
            or any(not str(item).strip() for item in self.dynamic_input_ports)
        ):
            raise ValueError("callable_tool_dynamic_input_port_duplicate")
        return self


class CompilerPlanProposalV2(FrozenContract):
    is_sufficient: bool
    insufficiency_code: InsufficiencyCode | None = None
    steps: tuple[CompilerStepProposalV2, ...] = ()
    final_output: StepOutputRef | None = None
    controller_callable_tools: tuple[CompilerCallableToolDecisionV1, ...] = ()
    concise_rationale: str = Field(default="")

    @property
    def proposal_sha256(self) -> str:
        return canonical_sha256(self)


class CompilerFinalContractV4(FrozenContract):
    artifact_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    schema_graph: PlannerSchemaGraphWireV1 | None = None

    @model_validator(mode="after")
    def _schema_shape(self) -> "CompilerFinalContractV4":
        if self.artifact_type == "json" and self.schema_graph is None:
            raise ValueError("compiler_v3_json_intermediate_schema_missing")
        if self.artifact_type != "json" and self.schema_graph is not None:
            raise ValueError("compiler_v3_non_json_intermediate_has_schema")
        return self


class CompilerIntermediateContractV3(CompilerFinalContractV4):
    # Compiler-created intermediate purposes have no safe implicit default.
    content_kind: Literal["value", "json_schema_document"]


class CompilerStepDecisionV3(FrozenContract):
    step_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    resource_id: str = Field(min_length=1)
    capability_operation_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    input_mappings: tuple[CompilerInputMappingV3, ...] = ()
    satisfied_obligation_ids: tuple[str, ...] = ()
    capability_evidence_refs: tuple[str, ...] = Field(min_length=1)
    output_role: Literal["final", "selected_resource", "intermediate"]
    intermediate_contract: CompilerIntermediateContractV3 | None = None
    agent_base_model_resource_id: str | None = None
    advisory_profile_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _output_shape(self) -> "CompilerStepDecisionV3":
        if (self.output_role == "intermediate") != (self.intermediate_contract is not None):
            raise ValueError("compiler_v3_intermediate_contract_shape_invalid")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("compiler_v3_dependency_duplicate")
        return self


from .input_alignment import CompilerInputAssessment, ConstraintBasis, validate_input_alignment


class CompilerDecisionProposalV3(FrozenContract):
    protocol: Literal[COMPILER_DECISION_PROTOCOL] = COMPILER_DECISION_PROTOCOL
    input_assessment: CompilerInputAssessment
    constraint_basis: tuple[ConstraintBasis, ...]
    is_sufficient: bool
    insufficiency_code: InsufficiencyCode | None = None
    unsatisfied_obligation_ids: tuple[str, ...] = ()
    capability_gaps: tuple[str, ...] = ()
    steps: tuple[CompilerStepDecisionV3, ...] = ()
    final_step_id: str | None = None
    final_contract: CompilerFinalContractV4 | None = None
    controller_callable_tools: tuple[CompilerCallableToolDecisionV1, ...] = ()
    concise_rationale: str = Field(default="")

    @field_validator("constraint_basis", mode="before")
    @classmethod
    def _discard_retired_constraint_basis(cls, value):
        # Reserved compatibility metadata, never authorization or acceptance evidence.
        return ()

    @model_validator(mode="after")
    def _shape(self) -> "CompilerDecisionProposalV3":
        if not self.input_assessment.sufficient:
            if self.is_sufficient or self.insufficiency_code != InsufficiencyCode.REQUIRED_INPUT_MISSING or self.constraint_basis:
                raise ValueError("compiler_input_insufficiency_shape_invalid")
        elif self.insufficiency_code == InsufficiencyCode.REQUIRED_INPUT_MISSING:
            raise ValueError("compiler_input_assessment_conflicts_with_insufficiency")
        if self.is_sufficient:
            valid = (
                self.insufficiency_code is None
                and not self.unsatisfied_obligation_ids
                and not self.capability_gaps
                and bool(self.steps)
                and self.final_step_id is not None
            )
        else:
            valid = (
                self.insufficiency_code is not None
                and bool(self.unsatisfied_obligation_ids)
                and bool(self.capability_gaps)
                and not self.steps
                and self.final_step_id is None
                and self.final_contract is None
                and not self.controller_callable_tools
            )
        if not valid:
            raise ValueError("compiler_v3_sufficiency_shape_invalid")
        return self

    @property
    def decision_sha256(self) -> str:
        return canonical_sha256(self)


def planner_final_content_kind(projection: RetrievalContractProjection) -> str:
    """Read Planner authority; legacy contracts have the established value semantics."""
    semantic = projection.semantic_contract_v2
    if semantic is None:
        return "value"
    output = getattr(semantic, "output", None)
    kind = getattr(output, "content_kind", None)
    if kind not in ("value", "json_schema_document"):
        raise CompilerSchemaProjectionError(
            "compiler_authoritative_content_kind_missing", origin="Planner.output",
            responsibility="framework", contract_path="final_artifact_contract.content_kind",
            actual_type="missing" if kind is None else "invalid",
        )
    return kind


def _compile_compiler_schema_graph(graph: PlannerSchemaGraphWireV1, *,
                                   path: tuple[str | int, ...], step_id: str) -> dict[str, Any]:
    """Keep existing graph validation and attach the Compiler-owned response location."""
    try:
        return compile_planner_schema_graph(graph)
    except PlannerWireContractError as exc:
        details = exc.constraint_details
        issue_path = path
        expected = ("A valid schema graph: declared nodes, correctly linked relations and compatible constraints.",)
        observed = str(exc)
        if details:
            fields = details["observed_fields"]
            issue_path = (*path, *exc.node_path, next(iter(fields)))
            expected = (
                "Constraints apply to the node's own kind, not its child or the data described by a Schema document. "
                "For the current kind, use the listed inactive values. If the kind itself is wrong, correct it and "
                "its relations to match the intended output. Preserve task requirements; do not silently discard "
                "a required constraint or change completed checkpoints.",
                json.dumps({"inactive_values": details["inactive_values"]}, ensure_ascii=False, sort_keys=True),
            )
            observed = json.dumps(details, ensure_ascii=False, sort_keys=True)
        issue = CompilerProposalValidationIssueV1(
            invariant_id="compiler_schema_graph_invalid", path=issue_path,
            failure_layer="protocol", failure_code="compiler_schema_graph_invalid",
            authority_source="PlannerSchemaGraphWireV1/compile_planner_schema_graph",
            responsibility_stage="plan_compiler_projection", step_id=step_id,
            expected_active_fields=expected,
            observed_value=observed, observed_active_fields=(type(exc).__name__,),
        )
        raise CompilerSchemaProjectionError(
            "compiler_schema_graph_invalid", origin=issue.authority_source,
            responsibility="research", contract_path=".".join(map(str, issue_path)),
            actual_type=type(exc).__name__, issues=(issue,),
        ) from exc


def project_compiler_decision_v3(*, decision: CompilerDecisionProposalV3,
                                 envelope: PlanCompilerInputEnvelope) -> CompilerPlanProposalV2:
    """Project only model choices, with field-local correction facts on rejection."""
    try:
        return _project_compiler_decision_v3(decision=decision, envelope=envelope)
    except CompilerSchemaProjectionError:
        raise
    except ValueError as exc:
        code = str(exc)
        if not code.startswith("compiler_"):
            raise
        # Locate the decision frame that rejected a value, without persisting frames or payloads.
        frame = exc.__traceback__
        local = {}
        while frame is not None:
            if frame.tb_frame.f_code.co_name == "_project_compiler_decision_v3":
                local = frame.tb_frame.f_locals
            frame = frame.tb_next
        step = local.get("step")
        path = "steps"
        if "final_contract" in code or "final_json_contract" in code:
            path = "final_contract"
        elif "final_step" in code:
            path = "final_step_id"
        elif step is not None:
            index = next((i for i, item in enumerate(decision.steps) if item.step_id == step.step_id), None)
            if index is not None:
                path = f"steps.{index}"
                if "input" in code or "mapping" in code or "port" in code or "context_source" in code:
                    path += ".input_mappings"
                elif "output_role" in code:
                    path += ".output_role"
                elif "resource" in code:
                    path += ".resource_id"
                elif "operation" in code or "entrypoint" in code:
                    path += ".capability_operation_id"
                elif "dependenc" in code:
                    path += ".depends_on"
        issue = CompilerProposalValidationIssueV1(
            invariant_id=code, failure_code=code, failure_layer="selection", path=tuple(path.split(".")),
            expected_active_fields=("satisfy the declared invariant: " + code,),
            observed_active_fields=("declared choice violates this invariant",),
            authority_source="compiler_input", responsibility_stage="plan_compiler_projection",
        )
        raise CompilerSchemaProjectionError(code, origin="compiler_input", responsibility="research",
            contract_path=path, actual_type="invariant_violation", issues=(issue,)) from exc


def _project_compiler_decision_v3(
    *,
    decision: CompilerDecisionProposalV3,
    envelope: PlanCompilerInputEnvelope,
) -> CompilerPlanProposalV2:
    """Deterministically expand model decisions into the historical draft wire."""

    validate_input_alignment(decision, envelope)
    obligation_ids = {item.obligation_id for item in envelope.execution_obligations}
    if not decision.is_sufficient:
        unknown = set(decision.unsatisfied_obligation_ids) - obligation_ids
        if unknown:
            raise ValueError("compiler_v3_unknown_unsatisfied_obligation")
        return CompilerPlanProposalV2(
            is_sufficient=False,
            insufficiency_code=decision.insufficiency_code,
            steps=(),
            final_output=None,
            concise_rationale=decision.concise_rationale,
        )

    step_ids = [item.step_id for item in decision.steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("compiler_v3_step_id_duplicate")
    if decision.final_step_id not in set(step_ids):
        raise ValueError("compiler_v3_final_step_unknown")
    semantic_v2 = envelope.contract_projection.semantic_contract_v2
    expected_content_kind = planner_final_content_kind(envelope.contract_projection)
    if decision.final_contract is not None:
        if decision.final_contract.artifact_type != envelope.contract_projection.artifact_type:
            raise ValueError("compiler_v3_final_contract_artifact_type_mismatch")
    schema_generation_required = bool(
        semantic_v2 is not None
        and envelope.contract_projection.artifact_type == "json"
        and envelope.contract_projection.json_schema is None
    )
    if schema_generation_required and decision.final_contract is None:
        raise ValueError("compiler_v3_final_json_contract_missing")
    if not schema_generation_required and decision.final_contract is not None:
        raise ValueError("compiler_v3_final_contract_not_requested")
    covered = {
        obligation_id
        for step in decision.steps
        for obligation_id in step.satisfied_obligation_ids
    }
    if covered != obligation_ids:
        raise ValueError("compiler_v3_obligation_coverage_mismatch")
    cards = {item.resource_id: item for item in envelope.candidate_cards}
    decision_steps = {item.step_id: item for item in decision.steps}
    material_by_id = {item.source_id: item for item in envelope.materials}
    material_types = {
        source_id: item.artifact_type.strip().lower()
        for source_id, item in material_by_id.items()
    }
    authorized_artifact_sources: dict[str, CompilerContextDescriptor] = {}
    for material in envelope.materials:
        matches = [
            descriptor
            for descriptor in envelope.public_context.descriptors
            if descriptor.provenance_source_id == material.source_id
            and descriptor.handle_id == material.handle_id
        ]
        if len(matches) != 1:
            raise ValueError("compiler_v3_artifact_source_not_unique")
        authorized_artifact_sources[material.source_id] = matches[0]
    projected_steps: list[CompilerStepProposalV2] = []
    selected_operations: dict[str, CapabilityOperationV2] = {}
    for step in decision.steps:
        if (step.step_id == decision.final_step_id) != (step.output_role == "final"):
            raise ValueError("compiler_v3_final_output_role_mismatch")
        if any(dependency_id not in selected_operations for dependency_id in step.depends_on):
            raise ValueError("compiler_v3_dependency_not_prior_step")
        mapped_dependencies = {
            str(mapping.from_step)
            for mapping in step.input_mappings
            if mapping.source_kind == "step_output"
        }
        if mapped_dependencies != set(step.depends_on):
            raise ValueError("compiler_v3_dependency_input_mapping_mismatch")
        card = cards.get(step.resource_id)
        if card is None:
            raise ValueError("compiler_v3_resource_not_candidate")
        operations = {
            item.capability_operation_id: item for item in card.capability_operations
        }
        operation = operations.get(step.capability_operation_id)
        if operation is None:
            raise ValueError("compiler_v3_capability_operation_unknown")
        if operation.entrypoint_id is None:
            raise ValueError("compiler_v3_capability_entrypoint_unresolved")
        if not (
            step.capability_operation_id in step.capability_evidence_refs
            or operation.operation_sha256 in step.capability_evidence_refs
        ):
            index = next(i for i, item in enumerate(decision.steps) if item.step_id == step.step_id)
            path = ("steps", index, "capability_evidence_refs")
            issue = CompilerProposalValidationIssueV1(
                invariant_id="compiler_v3_capability_evidence_missing",
                failure_code="compiler_v3_capability_evidence_missing", failure_layer="selection",
                path=path, step_id=step.step_id,
                authority_source="compiler_input.candidate_cards.capability_operations",
                responsibility_stage="plan_compiler_projection",
                expected_active_fields=(
                    "Use the selected capability_operation_id (readable ID) or its exact operation_sha256.",
                    operation.capability_operation_id, operation.operation_sha256,
                ),
                observed_value=json.dumps(list(step.capability_evidence_refs)),
                observed_active_fields=tuple(step.capability_evidence_refs),
            )
            raise CompilerSchemaProjectionError(issue.failure_code, origin=issue.authority_source,
                responsibility="research", contract_path=".".join(map(str, path)),
                actual_type="invalid_operation_reference", issues=(issue,))
        declared_ports = {item.name: item for item in operation.input_ports}
        mappings_by_port: dict[str, list[CompilerInputMappingV3]] = {}
        for mapping in step.input_mappings:
            mappings_by_port.setdefault(mapping.target_port, []).append(mapping)
        duplicate_ports = {
            name: mappings
            for name, mappings in mappings_by_port.items()
            if len(mappings) > 1
        }
        if duplicate_ports and not (
            card.resource_type in {"Model", "Agent"}
            and all(
                mapping.source_kind == "artifact_handle"
                for mappings in duplicate_ports.values()
                for mapping in mappings
            )
        ):
            raise ValueError("compiler_v3_target_port_duplicate")
        supplied_ports = {item.target_port for item in step.input_mappings}
        required_ports = {
            name for name, port in declared_ports.items() if port.required
        }
        if required_ports - supplied_ports:
            raise ValueError("compiler_v3_required_input_port_missing")
        if declared_ports and supplied_ports - set(declared_ports):
            raise ValueError("compiler_v3_undeclared_input_port")
        try:
            operation_kind = OperationKind(operation.execution_operation_kind)
        except ValueError as exc:
            raise ValueError("compiler_v3_operation_kind_unregistered") from exc
        output_key = f"{step.step_id}_output"
        bindings: list[CompilerBindingProposalV2] = []
        consumed_context: list[str] = []
        context_bindings: list[ControllerContextBindingV1] = []
        for mapping in step.input_mappings:
            accepted_types = set(operation.accepted_artifact_types)
            source_types: set[str] = set()
            artifact_descriptor: CompilerContextDescriptor | None = None
            if mapping.source_kind == "step_output":
                producer = decision_steps[str(mapping.from_step)]
                if producer.output_role == "intermediate":
                    assert producer.intermediate_contract is not None
                    source_types.add(
                        producer.intermediate_contract.artifact_type.strip().lower()
                    )
                else:
                    source_types.update(
                        selected_operations[str(mapping.from_step)].produced_artifact_types
                    )
            elif mapping.source_kind == "artifact_handle":
                artifact_descriptor = authorized_artifact_sources.get(
                    str(mapping.source_id)
                )
                if artifact_descriptor is None:
                    raise ValueError("compiler_v3_context_source_unauthorized")
                material_type = material_types.get(str(mapping.source_id))
                if material_type:
                    source_types.add(material_type)
            elif mapping.source_kind == "resource":
                source_card = cards.get(str(mapping.source_id))
                if source_card is None:
                    raise ValueError("compiler_v3_resource_source_unauthorized")
                source_types.update(
                    artifact_type
                    for source_operation in source_card.capability_operations
                    for artifact_type in source_operation.produced_artifact_types
                )
            advisory_skill_binding = (
                mapping.source_kind == "step_output"
                and cards.get(decision_steps[str(mapping.from_step)].resource_id) is not None
                and cards[decision_steps[str(mapping.from_step)].resource_id].resource_type == "Skill"
                and card.resource_type == "Agent"
                and decision_steps[str(mapping.from_step)].resource_id in step.advisory_profile_refs
            )
            if accepted_types and mapping.source_kind != "literal" and not advisory_skill_binding:
                if not source_types:
                    raise ValueError("compiler_v3_input_artifact_type_unproven")
                if not any(
                    _artifact_type_is_accepted(source_type, accepted_types)
                    for source_type in source_types
                ):
                    raise ValueError("compiler_v3_input_artifact_type_incompatible")
            if mapping.source_kind == "artifact_handle":
                material = material_by_id.get(str(mapping.source_id))
                if material is None:
                    raise ValueError("compiler_v3_context_source_unauthorized")
                delivery_resolution = resolve_material_delivery(
                    material=material,
                    candidate_card=card,
                    operation=operation,
                    target_port=mapping.target_port,
                    runtime_capabilities=envelope.runtime_capabilities,
                )
                select_material_delivery_mode(
                    resolution=delivery_resolution,
                    candidate_card=card,
                    runtime_capabilities=envelope.runtime_capabilities,
                )
            if mapping.source_kind == "artifact_handle" and card.resource_type in {
                "Model",
                "Agent",
            }:
                assert artifact_descriptor is not None
                # The validated semantic plan retains the canonical provenance
                # identity.  Runtime-only handle resolution is performed by
                # the lowerer after this plan has passed its context and edge
                # invariants, so no raw handle is exposed to the model.
                consumed_context.append(str(mapping.source_id))
                context_bindings.append(ControllerContextBindingV1(
                    target_port=mapping.target_port,
                    source_id=str(mapping.source_id),
                    handle_id=material_by_id[str(mapping.source_id)].handle_id,
                    content_sha256=material_by_id[str(mapping.source_id)].content_sha256,
                ))
                continue
            if mapping.source_kind == "literal":
                bindings.append(
                    CompilerBindingProposalV2(
                        name=mapping.target_port,
                        source_kind="literal",
                        literal_json=json.dumps(
                            mapping.literal_value.python_value(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    )
                )
            elif mapping.source_kind == "step_output":
                producer_key = f"{mapping.from_step}_output"
                bindings.append(
                    CompilerBindingProposalV2(
                        name=mapping.target_port,
                        source_kind="step_output",
                        from_step=mapping.from_step,
                        output_key=producer_key,
                    )
                )
            else:
                projected_source_id = mapping.source_id
                if mapping.source_kind == "artifact_handle":
                    assert artifact_descriptor is not None
                    if not artifact_descriptor.handle_id:
                        raise ValueError("compiler_v3_artifact_runtime_handle_missing")
                    projected_source_id = artifact_descriptor.handle_id
                bindings.append(
                    CompilerBindingProposalV2(
                        name=mapping.target_port,
                        source_kind=mapping.source_kind,
                        source_id=projected_source_id,
                    )
                )
        if step.output_role == "final":
            if decision.final_contract is None:
                output = CompilerOutputContractProposalV2(
                    mode="subtask_final",
                    artifact_type=None,
                    description=None,
                    schema_hint_json=None,
                )
            else:
                final_schema = (
                    _compile_compiler_schema_graph(decision.final_contract.schema_graph,
                        path=("final_contract", "schema_graph"), step_id=step.step_id)
                    if decision.final_contract.schema_graph is not None
                    else None
                )
                output = CompilerOutputContractProposalV2(
                    mode="custom",
                    artifact_type=decision.final_contract.artifact_type,
                    description=decision.final_contract.description,
                    content_kind=expected_content_kind,
                    schema_hint_json=(
                        json.dumps(
                            final_schema,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                        if final_schema is not None
                        else None
                    ),
                )
        elif step.output_role == "selected_resource":
            schema_status = _manifest_output_schema_status(card, operation.entrypoint_id)
            if not schema_status["selected_resource_allowed"]:
                raise CompilerSchemaProjectionError(
                    "compiler_selected_resource_requires_explicit_schema",
                    origin="model_selection", responsibility="research",
                    contract_path=f"steps.{step.step_id}.output_role",
                    actual_type=schema_status["machine_schema_status"],
                )
            output = CompilerOutputContractProposalV2(
                mode="selected_resource",
                artifact_type=None,
                description=None,
                schema_hint_json=None,
            )
        else:
            intermediate = step.intermediate_contract
            assert intermediate is not None
            schema = (
                _compile_compiler_schema_graph(intermediate.schema_graph,
                    path=("steps", decision.steps.index(step), "intermediate_contract", "schema_graph"),
                    step_id=step.step_id)
                if intermediate.schema_graph is not None
                else None
            )
            output = CompilerOutputContractProposalV2(
                mode="custom",
                artifact_type=intermediate.artifact_type,
                description=intermediate.description,
                content_kind=intermediate.content_kind,
                schema_hint_json=(
                    json.dumps(
                        schema,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    if schema is not None
                    else None
                ),
            )
        projected_steps.append(
            CompilerStepProposalV2(
                step_id=step.step_id,
                resource_id=step.resource_id,
                capability_operation_id=step.capability_operation_id,
                satisfied_obligation_ids=step.satisfied_obligation_ids,
                operation_kind=operation_kind,
                entrypoint_id=(
                    operation.entrypoint_id if card.resource_type == "Tool" else None
                ),
                intent=step.intent,
                depends_on=step.depends_on,
                input_bindings=tuple(bindings),
                consumed_context_source_ids=tuple(dict.fromkeys(consumed_context)),
                context_bindings=tuple(sorted(context_bindings, key=lambda item: (
                    item.target_port, item.source_id, item.handle_id, item.content_sha256))),
                output_key=output_key,
                output_contract=output,
                advisory_profile_refs=step.advisory_profile_refs,
                agent_base_model_resource_id=step.agent_base_model_resource_id,
            )
        )
        selected_operations[step.step_id] = operation

    def step_closure(step_ids: Iterable[str]) -> set[str]:
        closure = set(step_ids)
        pending = list(closure)
        while pending:
            current = pending.pop()
            for dependency_id in decision_steps[current].depends_on:
                if dependency_id not in closure:
                    closure.add(dependency_id)
                    pending.append(dependency_id)
        return closure

    controller_step_id: str | None = None
    controller_callable_material_ids: set[str] = set()
    if decision.controller_callable_tools:
        controller_steps = tuple(
            item
            for item in decision.steps
            if cards[item.resource_id].resource_type in {"Model", "Agent"}
        )
        if len(controller_steps) != 1:
            raise ValueError("controller_callable_tool_requires_single_controller")
        controller_step = controller_steps[0]
        controller_step_id = controller_step.step_id
        controller_dependency_closure = step_closure(controller_step.depends_on)
        for callable_tool in decision.controller_callable_tools:
            card = cards.get(callable_tool.resource_id)
            if card is None:
                raise ValueError("compiler_v3_callable_resource_not_candidate")
            if card.resource_type != "Tool":
                raise ValueError("controller_callable_resource_not_tool")
            operation = next(
                (
                    item
                    for item in card.capability_operations
                    if item.capability_operation_id
                    == callable_tool.capability_operation_id
                ),
                None,
            )
            if operation is None:
                raise ValueError("controller_callable_operation_unknown")
            if operation.entrypoint_id is None:
                raise ValueError("controller_callable_entrypoint_unresolved")
            if not (
                operation.capability_operation_id
                in callable_tool.capability_evidence_refs
                or operation.operation_sha256
                in callable_tool.capability_evidence_refs
            ):
                raise ValueError("controller_callable_operation_evidence_missing")
            entrypoint = next(
                (
                    item
                    for item in card.entrypoints
                    if item.entrypoint_id == operation.entrypoint_id
                ),
                None,
            )
            if entrypoint is None:
                raise ValueError("controller_callable_entrypoint_unresolved")
            try:
                validate_callable_port_partition(
                    operation_input_contract=entrypoint.input_contract,
                    fixed_input_names=tuple(
                        item.target_port
                        for item in callable_tool.fixed_input_mappings
                    ),
                    dynamic_input_names=callable_tool.dynamic_input_ports,
                )
            except ControllerToolingError as exc:
                raise ValueError(exc.code) from exc
            declared_ports = {
                str(item.get("name") or ""): item
                for item in entrypoint.input_contract
            }
            for mapping in callable_tool.fixed_input_mappings:
                source_types: set[str] = set()
                if mapping.source_kind == "artifact_handle":
                    descriptor = authorized_artifact_sources.get(
                        str(mapping.source_id)
                    )
                    material = material_by_id.get(str(mapping.source_id))
                    if descriptor is None or material is None:
                        raise ValueError("compiler_v3_context_source_unauthorized")
                    source_types.add(material.artifact_type.strip().lower())
                    delivery = resolve_material_delivery(
                        material=material,
                        candidate_card=card,
                        operation=operation,
                        target_port=mapping.target_port,
                        runtime_capabilities=envelope.runtime_capabilities,
                    )
                    select_material_delivery_mode(
                        resolution=delivery,
                        candidate_card=card,
                        runtime_capabilities=envelope.runtime_capabilities,
                    )
                    controller_callable_material_ids.add(str(mapping.source_id))
                elif mapping.source_kind == "resource":
                    source_card = cards.get(str(mapping.source_id))
                    if source_card is None:
                        raise ValueError("compiler_v3_resource_source_unauthorized")
                    source_types.update(
                        artifact_type
                        for source_operation in source_card.capability_operations
                        for artifact_type in source_operation.produced_artifact_types
                    )
                elif mapping.source_kind == "step_output":
                    from_step = str(mapping.from_step or "")
                    if from_step not in controller_dependency_closure:
                        raise ValueError("callable_tool_fixed_source_unavailable")
                    producer = decision_steps.get(from_step)
                    if producer is None:
                        raise ValueError("callable_tool_fixed_source_unavailable")
                    if producer.output_role == "intermediate":
                        assert producer.intermediate_contract is not None
                        source_types.add(
                            producer.intermediate_contract.artifact_type.strip().lower()
                        )
                    else:
                        source_types.update(
                            selected_operations[from_step].produced_artifact_types
                        )
                accepted_types = set(operation.accepted_artifact_types)
                if accepted_types and mapping.source_kind != "literal":
                    if not source_types:
                        raise ValueError("compiler_v3_input_artifact_type_unproven")
                    if not any(
                        _artifact_type_is_accepted(source_type, accepted_types)
                        for source_type in source_types
                    ):
                        raise ValueError("compiler_v3_input_artifact_type_incompatible")
                if mapping.target_port not in declared_ports:
                    raise ValueError("callable_tool_fixed_port_undeclared")

    material_by_id = {item.source_id: item for item in envelope.materials}
    for obligation in envelope.execution_obligations:
        relevant_steps = [
            step
            for step in decision.steps
            if obligation.obligation_id in step.satisfied_obligation_ids
        ]
        if not relevant_steps:
            raise ValueError("compiler_v3_obligation_operation_missing")
        if isinstance(obligation, ExecutionObligationV1) and obligation.work_nature == "deterministic":
            determinations = {
                selected_operations[step.step_id].determinism
                for step in relevant_steps
            }
            if determinations == {"nondeterministic"}:
                raise ValueError("compiler_v3_deterministic_obligation_unsatisfied")
            if (
                "deterministic" not in determinations
                and "unknown" in determinations
                and obligation.verification != "required"
            ):
                raise ValueError("compiler_v3_determinism_unverified")

        if isinstance(obligation, ExecutionObligationV1):
            required_material_ids = {
                source_id
                for source_id in obligation.required_evidence_source_ids
                if source_id in material_by_id
            }
            require_complete_material_mapping = obligation.material_coverage == "complete"
        else:
            # V2 carries semantic acceptance and authorized inputs, not V1's
            # work_nature/verification policy. Operation execution character is
            # metadata: neither unknown nor a deterministic sibling proves a
            # business verification relation. Preserve the material and binding
            # obligations below without inventing an execution requirement.
            required_material_ids = {
                *required_material_source_ids_for_obligation(
                    obligation,
                    envelope.materials,
                )
            }
            require_complete_material_mapping = bool(required_material_ids)
        if require_complete_material_mapping and required_material_ids:
            # Only explicit data edges deliver materials; ordering alone does not.
            closure_ids: set[str] = set()
            def visit_material_step(step_id: str) -> None:
                if step_id in closure_ids:
                    return
                closure_ids.add(step_id)
                for mapping in decision_steps[step_id].input_mappings:
                    if mapping.source_kind == "step_output" and mapping.from_step in decision_steps:
                        visit_material_step(str(mapping.from_step))
            for material_step in relevant_steps:
                visit_material_step(material_step.step_id)
            proven_material_ids: set[str] = set()
            if controller_step_id in closure_ids:
                proven_material_ids.update(
                    required_material_ids.intersection(
                        controller_callable_material_ids
                    )
                )
            for step_id in closure_ids:
                step = decision_steps[step_id]
                operation = selected_operations[step_id]
                for mapping in step.input_mappings:
                    if (
                        mapping.source_kind != "artifact_handle"
                        or mapping.source_id not in required_material_ids
                    ):
                        continue
                    material = material_by_id[str(mapping.source_id)]
                    card = cards[step.resource_id]
                    delivery = resolve_material_delivery(
                        material=material,
                        candidate_card=card,
                        operation=operation,
                        target_port=mapping.target_port,
                        runtime_capabilities=envelope.runtime_capabilities,
                    )
                    if delivery.status == "available":
                        proven_material_ids.add(str(mapping.source_id))
            if proven_material_ids != required_material_ids:
                from .input_alignment import _reject
                _reject("compiler_v3_complete_material_mapping_unproven", ("steps",),
                        "bind all declared material sources through input_mappings: " + str(sorted(required_material_ids)),
                        "missing delivered sources: " + str(sorted(required_material_ids - proven_material_ids)))
    skill_steps = {
        step.step_id
        for step in decision.steps
        if cards[step.resource_id].resource_type == "Skill"
    }
    consumed_skill_steps = {
        str(mapping.from_step)
        for step in decision.steps
        for mapping in step.input_mappings
        if mapping.source_kind == "step_output" and mapping.from_step in skill_steps
    }
    if consumed_skill_steps != skill_steps:
        raise ValueError("compiler_v3_skill_output_not_injected")
    final_key = f"{decision.final_step_id}_output"
    return CompilerPlanProposalV2(
        is_sufficient=True,
        insufficiency_code=None,
        steps=tuple(projected_steps),
        final_output=StepOutputRef(
            step_id=str(decision.final_step_id),
            output_key=final_key,
        ),
        controller_callable_tools=decision.controller_callable_tools,
        concise_rationale=decision.concise_rationale,
    )


CompilerInvariantCatalog = CompilerInvariantCatalogV2


class CompilerProjectionAuditV1(FrozenContract):
    protocol: Literal[COMPILER_PROJECTION_AUDIT_PROTOCOL] = (
        COMPILER_PROJECTION_AUDIT_PROTOCOL
    )
    proposal_sha256: str
    projected_draft_sha256: str
    model_owned_semantic_sha256: str
    completion_fields: tuple[str, ...]
    projector_version: Literal["compiler-plan-projector-v2"] = "compiler-plan-projector-v2"
    projector_sha256: str
    semantic_round_trip: Literal[True] = True
    audit_sha256: str = ""

    @model_validator(mode="after")
    def _seal_audit(self) -> "CompilerProjectionAuditV1":
        for field_name in (
            "proposal_sha256",
            "projected_draft_sha256",
            "model_owned_semantic_sha256",
            "projector_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name=field_name)
        expected = canonical_sha256(self.model_dump(mode="python", exclude={"audit_sha256"}))
        if self.audit_sha256 and self.audit_sha256 != expected:
            raise ValueError("compiler_projection_audit_sha256_mismatch")
        object.__setattr__(self, "audit_sha256", expected)
        return self


class CompilerStepDraft(ContextBoundContract):
    step_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    capability_operation_id: str | None = None
    satisfied_obligation_ids: tuple[str, ...] = ()
    operation_kind: OperationKind
    entrypoint_id: str | None = None
    intent: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    input_bindings: dict[str, Any] = Field(default_factory=dict)
    consumed_context_source_ids: tuple[str, ...] = ()
    output_key: str = Field(min_length=1)
    expected_output_contract: StepOutputContract
    advisory_profile_refs: tuple[str, ...] = ()
    agent_base_model_resource_id: str | None = None

    @field_validator("step_id", "output_key")
    @classmethod
    def _canonical_step_identity(cls, value: str, info: Any) -> str:
        normalized = str(value or "").strip()
        if not _CANONICAL_ID.fullmatch(normalized):
            raise ValueError(f"{info.field_name}_not_canonical")
        return normalized

    @field_validator("input_bindings", mode="before")
    @classmethod
    def _canonical_bindings(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("compiler_step_input_bindings_must_be_object")
        projected = _canonical_value(dict(value))
        _ensure_host_free(projected, field_name="compiler_step_input_bindings")
        return projected

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "CompilerStepDraft":
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("compiler_step_duplicate_dependency")
        if self.step_id in self.depends_on:
            raise ValueError("compiler_step_self_dependency")
        if len(self.advisory_profile_refs) != len(set(self.advisory_profile_refs)):
            raise ValueError("compiler_step_duplicate_profile_ref")
        if len(self.consumed_context_source_ids) != len(
            set(self.consumed_context_source_ids)
        ):
            raise ValueError("compiler_step_duplicate_context_source")
        return self


class CompilerPlanDraft(FrozenContract):
    protocol: Literal[COMPILER_PLAN_DRAFT_PROTOCOL] = COMPILER_PLAN_DRAFT_PROTOCOL
    is_sufficient: bool
    insufficiency_code: InsufficiencyCode | None = None
    steps: tuple[CompilerStepDraft, ...] = ()
    final_output: StepOutputRef | None = None
    controller_callable_tools: tuple[CompilerCallableToolDecisionV1, ...] = ()
    concise_rationale: str = Field(default="")

    @model_validator(mode="after")
    def _validate_shape(self) -> "CompilerPlanDraft":
        if self.is_sufficient:
            if self.insufficiency_code is not None:
                raise ValueError("sufficient_plan_has_insufficiency_code")
            if not self.steps or self.final_output is None:
                raise ValueError("sufficient_plan_requires_steps_and_final_output")
        else:
            if self.insufficiency_code is None:
                raise ValueError("insufficient_plan_requires_code")
            if self.steps or self.final_output is not None or self.controller_callable_tools:
                raise ValueError("insufficient_plan_must_not_have_steps")
        _ensure_host_free(
            self.model_dump(mode="python"),
            field_name="compiler_plan_draft",
        )
        return self

    @property
    def draft_sha256(self) -> str:
        return canonical_sha256(self)


_COMPILER_ALLOWED_OPERATION_KINDS: dict[str, tuple[str, ...]] = {
    "Model": ("call_model", "produce_artifact", "synthesize_final"),
    "Tool": tuple(
        item.value
        for item in OperationKind
        if item
        not in {
            OperationKind.CALL_MODEL,
            OperationKind.CALL_AGENT,
            OperationKind.APPLY_CONTEXT_HINT,
            OperationKind.SYNTHESIZE_FINAL,
        }
    ),
    "Agent": ("call_agent", "produce_artifact", "synthesize_final"),
    "Skill": ("apply_context_hint",),
    "Resource": ("inspect_input",),
}
def build_compiler_invariant_catalog(
    envelope: PlanCompilerInputEnvelope,
) -> CompilerInvariantCatalog:
    cards = envelope.candidate_cards
    rules = compiler_invariant_rules()
    runtime_capabilities = envelope.runtime_capabilities.model_dump(mode="json")
    if not envelope.runtime_capabilities.material_adapter_capabilities:
        runtime_capabilities.pop("material_adapter_capabilities", None)
    return CompilerInvariantCatalog(
        candidate_resource_types={item.resource_id: item.resource_type for item in cards},
        allowed_operation_kinds=dict(_COMPILER_ALLOWED_OPERATION_KINDS),
        tool_entrypoints={
            item.resource_id: tuple(
                {
                    "entrypoint_id": entrypoint.entrypoint_id,
                    "required_binding_names": tuple(
                        str(field.get("name"))
                        for field in entrypoint.input_contract
                        if bool(field.get("required", True)) and field.get("name")
                    ),
                    "output_contract": entrypoint.output_contract,
                }
                for entrypoint in item.entrypoints
            )
            for item in cards
            if item.resource_type == "Tool"
        },
        agent_base_models={
            item.resource_id: item.agent_base_model_candidates
            for item in cards
            if item.resource_type == "Agent"
        },
        application_profiles={
            item.resource_id: tuple(profile.profile_id for profile in item.application_profiles)
            for item in cards
        },
        output_format_contracts={
            item.resource_id: item.runtime_requirements.get("sgar_format_contract")
            for item in cards
            if item.runtime_requirements.get("sgar_format_contract") is not None
        },
        resource_output_contracts={
            item.resource_id: item.base_output_contract for item in cards
        },
        resource_runtime_requirements={
            item.resource_id: item.runtime_requirements for item in cards
        },
        runtime_capabilities=runtime_capabilities,
        final_artifact_contract={
            "artifact_type": envelope.contract_projection.artifact_type,
            "description": envelope.contract_projection.expected_output,
            "schema_hint": (
                envelope.contract_projection.json_schema
                if envelope.contract_projection.semantic_contract_v2 is not None
                else OutputFormatRequirement.from_contract_projection(
                    envelope.contract_projection
                ).json_schema
            ),
            "content_kind": planner_final_content_kind(envelope.contract_projection),
            "schema_generation_required": bool(
                envelope.contract_projection.semantic_contract_v2 is not None
                and envelope.contract_projection.artifact_type == "json"
                and envelope.contract_projection.json_schema is None
            ),
        },
        capability_operations={
            item.resource_id: tuple(
                operation.model_dump(mode="json")
                for operation in item.capability_operations
            )
            for item in cards
        },
        execution_obligations=tuple(
            item.model_dump(mode="json") for item in envelope.execution_obligations
        ),
        materials=tuple(
            item.model_dump(mode="json") for item in envelope.materials
        ),
        rules=rules,
        invariant_ids=tuple(rule.invariant_id for rule in rules),
    )


def _binding_proposal_to_draft(binding: CompilerBindingProposalV2) -> Any:
    if binding.source_kind == "literal":
        return {"literal": strict_json_loads(str(binding.literal_json))}
    if binding.source_kind == "artifact_handle":
        return {"artifact_handle": binding.source_id}
    if binding.source_kind == "resource":
        return {"resource_id": binding.source_id}
    if binding.source_kind == "step_output":
        return {"from_step": binding.from_step, "output_key": binding.output_key}
    return {"path": binding.logical_path}


class CompilerSchemaProjectionError(ValueError):
    """Typed boundary failure with explicit ownership; no message-based attribution."""

    def __init__(self, code: str, *, origin: str, responsibility: Literal["research", "framework"],
                 contract_path: str, actual_type: str,
                 issues: Sequence[CompilerProposalValidationIssueV1] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.origin = origin
        self.responsibility = responsibility
        self.contract_path = contract_path
        self.actual_type = actual_type
        self.issues = tuple(issues) or (CompilerProposalValidationIssueV1(
            invariant_id=code, path=tuple(contract_path.split(".")),
            failure_layer="selection", failure_code=code,
            expected_active_fields=("satisfy_authoritative_contract",),
            observed_active_fields=(actual_type,),
            authority_source=origin, responsibility_stage="plan_compiler_projection",
        ),)


def _manifest_output_contract(card: CandidateExecutionCard, entrypoint_id: str | None) -> Mapping[str, Any]:
    if card.resource_type != "Tool":
        return card.base_output_contract
    matches = [item for item in card.entrypoints if item.entrypoint_id == entrypoint_id]
    if entrypoint_id is None and len(card.entrypoints) == 1:
        matches = list(card.entrypoints)
    if len(matches) != 1:
        raise CompilerSchemaProjectionError(
            "compiler_projection_entrypoint_ambiguous", origin="manifest_projection",
            responsibility="framework", contract_path=f"resources.{card.resource_id}.entrypoints",
            actual_type="unresolved",
        )
    return matches[0].output_contract


def _manifest_output_schema_status(card: CandidateExecutionCard, entrypoint_id: str | None) -> dict[str, Any]:
    output = _manifest_output_contract(card, entrypoint_id)
    structured = str(output.get("artifact_type", "")).strip().lower() == "json"
    status: dict[str, Any] = {
        "machine_schema_status": "not_structured" if not structured else "missing",
        "schema_source": None, "schema_sha256": None, "schema": None,
        "selected_resource_allowed": not structured,
        "requires_explicit_intermediate_schema": structured,
    }
    if not structured:
        return status
    candidates = [(key, output[key]) for key in ("json_schema", "schema", "schema_hint")
                  if isinstance(output.get(key), Mapping)]
    if any(output.get(key) is not None and not isinstance(output[key], Mapping)
           for key in ("json_schema", "schema")):
        status["machine_schema_status"] = "invalid"
        return status
    if not candidates:
        status["machine_schema_status"] = "descriptive_hint_only" if isinstance(output.get("schema_hint"), str) else "missing"
        return status
    if len({canonical_sha256(value) for _, value in candidates}) != 1:
        status["machine_schema_status"] = "conflicting"
        return status
    source, schema = candidates[0]
    try:
        requirement = OutputFormatRequirement.from_json_schema(
            artifact_type="json", json_schema=schema, schema_source="manifest_output_schema",
        )
    except ModelResponseContractError:
        status["machine_schema_status"] = "invalid"
        return status
    if not local_json_schema_support(requirement.json_schema)[0]:
        status["machine_schema_status"] = "unsupported"
        return status
    status.update(machine_schema_status="available", schema_source=source,
                  schema_sha256=requirement.schema_sha256, schema=requirement.json_schema,
                  selected_resource_allowed=True, requires_explicit_intermediate_schema=False)
    return status


def _selected_resource_output_contract(
    *,
    step: CompilerStepProposalV2,
    envelope: PlanCompilerInputEnvelope,
) -> StepOutputContract:
    cards = {item.resource_id: item for item in envelope.candidate_cards}
    card = cards.get(step.resource_id)
    if card is None:
        raise ValueError("compiler_projection_resource_missing")
    output = _manifest_output_contract(card, step.entrypoint_id)
    status = _manifest_output_schema_status(card, step.entrypoint_id)
    if not status["selected_resource_allowed"]:
        raise CompilerSchemaProjectionError(
            "compiler_manifest_machine_schema_unavailable", origin="manifest_projection",
            responsibility="framework", contract_path=f"resources.{card.resource_id}.output_contract",
            actual_type=status["machine_schema_status"],
        )
    schema_hint = status["schema"] if status["machine_schema_status"] == "available" else output.get(
        "schema_hint", output.get("json_schema", output.get("schema"))
    )
    return StepOutputContract(
        artifact_type=str(output.get("artifact_type") or "") or None,
        content_kind=output.get("content_kind", "value"),
        schema_hint=schema_hint,
        description=str(output.get("description") or "") or None,
    )


def _output_proposal_to_draft(
    *,
    output: CompilerOutputContractProposalV2,
    step: CompilerStepProposalV2,
    envelope: PlanCompilerInputEnvelope,
) -> StepOutputContract:
    if output.mode == "subtask_final":
        requirement = OutputFormatRequirement.from_contract_projection(
            envelope.contract_projection
        )
        return StepOutputContract(
            artifact_type=envelope.contract_projection.artifact_type,
            schema_hint=requirement.json_schema,
            description=envelope.contract_projection.expected_output,
            content_kind=planner_final_content_kind(envelope.contract_projection),
        )
    if output.mode == "selected_resource":
        return _selected_resource_output_contract(step=step, envelope=envelope)
    return StepOutputContract(
        artifact_type=output.artifact_type,
        schema_hint=(
            strict_json_loads(output.schema_hint_json)
            if output.schema_hint_json is not None
            else None
        ),
        description=output.description,
        content_kind=output.content_kind,
    )


def compiler_proposal_model_owned_semantics(
    proposal: CompilerPlanProposalV2,
) -> dict[str, Any]:
    return proposal.model_dump(mode="json")


def extract_projected_model_owned_semantics(
    *,
    draft: CompilerPlanDraft,
    proposal: CompilerPlanProposalV2,
    envelope: PlanCompilerInputEnvelope,
) -> dict[str, Any]:
    if len(draft.steps) != len(proposal.steps):
        raise ValueError("compiler_projection_step_count_drift")
    if draft.controller_callable_tools != proposal.controller_callable_tools:
        raise ValueError("compiler_projection_callable_tool_drift")
    projected = proposal.model_dump(mode="json")
    projected["is_sufficient"] = draft.is_sufficient
    projected["insufficiency_code"] = (
        draft.insufficiency_code.value if draft.insufficiency_code is not None else None
    )
    projected["final_output"] = (
        draft.final_output.model_dump(mode="json") if draft.final_output is not None else None
    )
    projected["concise_rationale"] = draft.concise_rationale
    for proposal_step, draft_step, row in zip(
        proposal.steps,
        draft.steps,
        projected["steps"],
    ):
        if proposal_step.entrypoint_id is not None:
            if draft_step.entrypoint_id != proposal_step.entrypoint_id:
                raise ValueError("compiler_projection_entrypoint_drift")
        if draft_step.context_bindings != proposal_step.context_bindings:
            raise ValueError("compiler_projection_context_binding_drift")
        row.update(
            {
                "step_id": draft_step.step_id,
                "resource_id": draft_step.resource_id,
                "operation_kind": draft_step.operation_kind.value,
                "intent": draft_step.intent,
                "depends_on": list(draft_step.depends_on),
                "consumed_context_source_ids": list(
                    draft_step.consumed_context_source_ids
                ),
                "output_key": draft_step.output_key,
                "advisory_profile_refs": list(draft_step.advisory_profile_refs),
                "agent_base_model_resource_id": draft_step.agent_base_model_resource_id,
            }
        )
        row["entrypoint_id"] = proposal_step.entrypoint_id
        bindings = []
        for proposal_binding in proposal_step.input_bindings:
            if proposal_binding.name not in draft_step.input_bindings:
                raise ValueError("compiler_projection_binding_missing")
            if canonical_sha256(draft_step.input_bindings[proposal_binding.name]) != (
                canonical_sha256(_binding_proposal_to_draft(proposal_binding))
            ):
                raise ValueError("compiler_projection_binding_semantic_drift")
            bindings.append(proposal_binding.model_dump(mode="json"))
        row["input_bindings"] = bindings
        expected_output = _output_proposal_to_draft(
            output=proposal_step.output_contract,
            step=proposal_step,
            envelope=envelope,
        )
        if draft_step.expected_output_contract != expected_output:
            raise ValueError("compiler_projection_output_contract_drift")
    return projected


def project_compiler_plan_proposal(
    *,
    proposal: CompilerPlanProposalV2,
    envelope: PlanCompilerInputEnvelope,
) -> tuple[CompilerPlanDraft, CompilerProjectionAuditV1]:
    require_compiler_proposal_invariants(
        proposal.model_dump(mode="json"),
        build_compiler_invariant_catalog(envelope),
    )
    final_ref = proposal.final_output
    steps: list[CompilerStepDraft] = []
    completion_fields: list[str] = ["binding_list_to_map"]
    for step in proposal.steps:
        is_final = final_ref is not None and (
            final_ref.step_id == step.step_id and final_ref.output_key == step.output_key
        )
        schema_generation_required = bool(
            envelope.contract_projection.semantic_contract_v2 is not None
            and envelope.contract_projection.artifact_type == "json"
            and envelope.contract_projection.json_schema is None
        )
        expected_final_mode = (
            "custom" if schema_generation_required else "subtask_final"
        )
        final_mode_valid = step.output_contract.mode == expected_final_mode
        if is_final and not final_mode_valid:
            raise ValueError("compiler_final_output_contract_mode_mismatch")
        if not is_final and step.output_contract.mode == "subtask_final":
            raise ValueError("compiler_final_output_contract_mode_mismatch")
        if (
            not is_final
            and step.output_contract.mode == "custom"
            and step.output_contract.artifact_type is None
        ):
            raise ValueError("compiler_intermediate_output_contract_missing")
        entrypoint_id = step.entrypoint_id
        card = next(
            (item for item in envelope.candidate_cards if item.resource_id == step.resource_id),
            None,
        )
        if card is not None and card.resource_type == "Tool" and entrypoint_id is None:
            if len(card.entrypoints) != 1:
                raise ValueError("compiler_projection_entrypoint_ambiguous")
            entrypoint_id = card.entrypoints[0].entrypoint_id
            completion_fields.append(f"steps.{step.step_id}.entrypoint_id")
        if step.output_contract.mode != "custom":
            completion_fields.append(f"steps.{step.step_id}.output_contract")
        steps.append(
            CompilerStepDraft(
                step_id=step.step_id,
                resource_id=step.resource_id,
                capability_operation_id=step.capability_operation_id,
                satisfied_obligation_ids=step.satisfied_obligation_ids,
                operation_kind=step.operation_kind,
                entrypoint_id=entrypoint_id,
                intent=step.intent,
                depends_on=step.depends_on,
                input_bindings={
                    item.name: _binding_proposal_to_draft(item)
                    for item in step.input_bindings
                },
                consumed_context_source_ids=step.consumed_context_source_ids,
                context_bindings=step.context_bindings,
                output_key=step.output_key,
                expected_output_contract=_output_proposal_to_draft(
                    output=step.output_contract,
                    step=step,
                    envelope=envelope,
                ),
                advisory_profile_refs=step.advisory_profile_refs,
                agent_base_model_resource_id=step.agent_base_model_resource_id,
            )
        )
    draft = CompilerPlanDraft(
        is_sufficient=proposal.is_sufficient,
        insufficiency_code=proposal.insufficiency_code,
        steps=tuple(steps),
        final_output=proposal.final_output,
        controller_callable_tools=proposal.controller_callable_tools,
        concise_rationale=proposal.concise_rationale,
    )
    source_semantics = compiler_proposal_model_owned_semantics(proposal)
    projected_semantics = extract_projected_model_owned_semantics(
        draft=draft,
        proposal=proposal,
        envelope=envelope,
    )
    if canonical_sha256(source_semantics) != canonical_sha256(projected_semantics):
        raise ValueError("compiler_projection_semantic_round_trip_failed")
    audit = CompilerProjectionAuditV1(
        proposal_sha256=proposal.proposal_sha256,
        projected_draft_sha256=draft.draft_sha256,
        model_owned_semantic_sha256=canonical_sha256(source_semantics),
        completion_fields=tuple(sorted(set(completion_fields))),
        projector_sha256=canonical_sha256(
            {"version": "compiler-plan-projector-v2", "target": COMPILER_PLAN_DRAFT_PROTOCOL}
        ),
    )
    return draft, audit


class ModelCostEvidence(FrozenContract):
    protocol: Literal[PLAN_OBJECTIVE_PROTOCOL] = PLAN_OBJECTIVE_PROTOCOL
    pricing_catalog_sha256: str
    model_resource_id: str
    api_model_id: str
    input_per_m: Decimal
    cache_per_m: Decimal | None
    output_per_m: Decimal
    expected_call_count: int = Field(ge=1)
    usage_estimate_status: Literal["unknown", "bounded", "exact_static"] = "unknown"
    estimated_input_tokens: int | None = Field(default=None, ge=0)
    estimated_output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_low_usd: Decimal | None = None
    estimated_cost_high_usd: Decimal | None = None
    comparable_scope: str | None = None

    @model_validator(mode="after")
    def _validate_estimate(self) -> "ModelCostEvidence":
        _require_sha256(self.pricing_catalog_sha256, field_name="pricing_catalog_sha256")
        estimates = (
            self.estimated_input_tokens,
            self.estimated_output_tokens,
            self.estimated_cost_low_usd,
            self.estimated_cost_high_usd,
        )
        if self.usage_estimate_status == "unknown" and any(item is not None for item in estimates):
            raise ValueError("unknown_usage_estimate_must_not_claim_values")
        if self.usage_estimate_status != "unknown" and (
            self.estimated_cost_low_usd is None or self.estimated_cost_high_usd is None
        ):
            raise ValueError("bounded_usage_estimate_requires_cost_bounds")
        if (
            self.estimated_cost_low_usd is not None
            and self.estimated_cost_high_usd is not None
            and self.estimated_cost_low_usd > self.estimated_cost_high_usd
        ):
            raise ValueError("model_cost_bounds_inverted")
        return self


class PlanObjectiveEvidence(FrozenContract):
    protocol: Literal[PLAN_OBJECTIVE_PROTOCOL] = PLAN_OBJECTIVE_PROTOCOL
    contract_satisfied: bool
    dependency_complete: bool
    runtime_compatible: bool
    final_output_reachable: bool
    selected_resource_count: int = Field(ge=0)
    executable_step_count: int = Field(ge=0)
    unused_selected_resources: tuple[str, ...] = ()
    disconnected_steps: tuple[str, ...] = ()
    model_cost_evidence: tuple[ModelCostEvidence, ...] = ()
    cost_comparison_status: Literal["comparable", "partially_comparable", "incomparable"]
    objective_status: Literal["valid", "valid_cost_incomparable", "invalid"]
    evidence_sha256: str = ""

    @model_validator(mode="after")
    def _seal_evidence(self) -> "PlanObjectiveEvidence":
        valid = (
            self.contract_satisfied
            and self.dependency_complete
            and self.runtime_compatible
            and self.final_output_reachable
            and not self.unused_selected_resources
            and not self.disconnected_steps
        )
        if self.objective_status == "invalid" and valid:
            raise ValueError("valid_objective_marked_invalid")
        if self.objective_status != "invalid" and not valid:
            raise ValueError("invalid_objective_marked_valid")
        if (
            self.objective_status == "valid_cost_incomparable"
            and self.cost_comparison_status == "comparable"
        ):
            raise ValueError("cost_incomparable_status_conflict")
        projected = self.model_dump(mode="python", exclude={"evidence_sha256"})
        expected = canonical_sha256(projected)
        if self.evidence_sha256:
            supplied = _require_sha256(self.evidence_sha256, field_name="evidence_sha256")
            if supplied != expected:
                raise ValueError("plan_objective_evidence_sha256_mismatch")
        object.__setattr__(self, "evidence_sha256", expected)
        return self


class ResourceUsageRecord(FrozenContract):
    resource_id: str = Field(min_length=1)
    use_as: Literal["executable_step", "agent_base_model", "runtime_dependency"]
    attached_to_steps: tuple[str, ...] = ()


class ExecutablePlanStep(ContextBoundContract):
    step_id: str
    resource_id: str
    capability_operation_id: str
    satisfied_obligation_ids: tuple[str, ...] = ()
    resource_type: str
    operation_kind: OperationKind
    entrypoint_id: str | None = None
    intent: str
    depends_on: tuple[str, ...] = ()
    input_bindings: dict[str, Any] = Field(default_factory=dict)
    consumed_context_source_ids: tuple[str, ...] = ()
    consumed_edge_contract_sha256s: tuple[str, ...] = ()
    output_key: str
    expected_output_contract: StepOutputContract
    resource_application: ResourceApplicationV1 | None = None
    controller_session_spec: ControllerSessionSpec | None = None
    advisory_profile_refs: tuple[str, ...] = ()
    agent_base_model_resource_id: str | None = None


class ExecutablePlan(FrozenContract):
    protocol: Literal[EXECUTABLE_PLAN_PROTOCOL] = EXECUTABLE_PLAN_PROTOCOL
    plan_revision: PlanRevisionRef
    contract_sha256: str
    candidate_pool_sha256: str
    retrieval_evidence_sha256: str
    compiler_input_sha256: str
    dag_edge_contract_sha256s: tuple[str, ...] = ()
    executable_edge_contracts_v2: tuple[ExecutableEdgeContractV2, ...] = ()
    is_sufficient: Literal[True] = True
    selected_resource_ids: tuple[str, ...]
    resource_usage: tuple[ResourceUsageRecord, ...]
    steps: tuple[ExecutablePlanStep, ...]
    final_output: StepOutputRef
    execution_strategy: Literal[
        "tool_only",
        "model_assisted",
        "agent_assisted",
        "generated_code",
        "mixed",
    ]
    execution_character: Literal["deterministic", "generative", "hybrid"] = "hybrid"
    objective_evidence: PlanObjectiveEvidence
    plan_sha256: str = ""

    @field_validator(
        "contract_sha256",
        "candidate_pool_sha256",
        "retrieval_evidence_sha256",
        "compiler_input_sha256",
    )
    @classmethod
    def _plan_hashes(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal_plan(self) -> "ExecutablePlan":
        executable_edge_keys = tuple(
            (
                item.producer_id,
                item.consumer_id,
                item.source_output_key,
                item.target_step_id,
                item.target_input_port,
            )
            for item in self.executable_edge_contracts_v2
        )
        if executable_edge_keys != tuple(sorted(set(executable_edge_keys))):
            raise ValueError("executable_plan_v2_edges_not_unique_sorted")
        if len(self.selected_resource_ids) != len(set(self.selected_resource_ids)):
            raise ValueError("executable_plan_selected_resources_not_unique")
        if len({item.step_id for item in self.steps}) != len(self.steps):
            raise ValueError("executable_plan_step_ids_not_unique")
        if self.dag_edge_contract_sha256s != tuple(
            sorted(set(self.dag_edge_contract_sha256s))
        ):
            raise ValueError("executable_plan_edge_contract_hashes_not_unique_sorted")
        projected = self.model_dump(mode="python", exclude={"plan_sha256"})
        _ensure_host_free(projected, field_name="executable_plan")
        expected = canonical_sha256(projected)
        if self.plan_sha256:
            supplied = _require_sha256(self.plan_sha256, field_name="plan_sha256")
            legacy_projection = self.model_dump(
                mode="python", exclude={"plan_sha256"}
            )
            for step in legacy_projection.get("steps", ()):
                if isinstance(step, dict):
                    step.pop("controller_session_spec", None)
            legacy_expected = canonical_sha256(legacy_projection)
            if supplied not in {expected, legacy_expected}:
                raise ValueError("executable_plan_sha256_mismatch")
            object.__setattr__(self, "plan_sha256", supplied)
        else:
            object.__setattr__(self, "plan_sha256", expected)
        return self


class PlanTransportAttempt(FrozenContract):
    attempt: int = Field(ge=1, le=3)
    request_sha256: str
    outcome: Literal[
        "success",
        "infrastructure_failure",
        "framework_failure",
        "budget_failure",
    ]
    failure_code: str | None = None
    response_received: bool

    @field_validator("request_sha256")
    @classmethod
    def _request_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="request_sha256")

    @model_validator(mode="after")
    def _validate_outcome(self) -> "PlanTransportAttempt":
        if self.outcome == "success":
            if self.failure_code is not None or not self.response_received:
                raise ValueError("successful_plan_transport_attempt_invalid")
        elif not self.failure_code:
            raise ValueError("failed_plan_transport_attempt_requires_code")
        return self


class PlanCompilationFailure(FrozenContract):
    responsibility: Literal["framework", "infrastructure", "research", "budget"]
    failure_stage: str = Field(min_length=1)
    failure_code: str = Field(min_length=1)
    failure_layer: Literal[
        "framework",
        "infrastructure",
        "budget",
        "protocol",
        "selection",
        "connection",
        "executability",
    ]
    exception_type: str = ""
    retryable: bool = False
    transport_attempt: int = Field(default=0, ge=0, le=3)
    request_sha256: str | None = None
    response_received: bool = False
    message_sha256: str
    secondary_audit_failures: tuple[str, ...] = ()
    output_diagnostic: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _serialize_failure(self, handler):
        payload = handler(self)
        # Keep historical failure serialization stable when no new diagnostic exists.
        if self.output_diagnostic is None:
            payload.pop("output_diagnostic", None)
        return payload

    @field_validator("message_sha256")
    @classmethod
    def _message_hash(cls, value: str) -> str:
        return _require_sha256(value, field_name="message_sha256")

    @field_validator("request_sha256")
    @classmethod
    def _optional_request_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field_name="request_sha256")

    @model_validator(mode="after")
    def _validate_failure(self) -> "PlanCompilationFailure":
        if self.transport_attempt > 0 and self.request_sha256 is None:
            raise ValueError("transport_failure_requires_request_hash")
        if self.responsibility == "infrastructure" and self.response_received:
            raise ValueError("infrastructure_failure_cannot_have_semantic_response")
        if self.responsibility != "infrastructure" and self.retryable:
            raise ValueError("non_infrastructure_plan_failure_cannot_be_retryable")
        return self


class SealedPlanCompilationArtifact(FrozenContract):
    protocol: Literal[PLAN_COMPILATION_ARTIFACT_PROTOCOL] = (
        PLAN_COMPILATION_ARTIFACT_PROTOCOL
    )
    run_id: str = Field(min_length=1)
    plan_revision: PlanRevisionRef
    status: Literal["success", "failed", "interrupted"]
    contract_sha256: str
    candidate_pool_sha256: str
    retrieval_evidence_sha256: str
    pricing_catalog_sha256: str
    prompt_sha256: str
    compiler_input_sha256: str
    compiler_model_resource_id: str
    compiler_model_api_id: str
    accounting_operation_id: str | None = None
    transport_attempts: tuple[PlanTransportAttempt, ...] = ()
    compiler_response_sha256: str | None = None
    compiler_draft_sha256: str | None = None
    executable_plan: ExecutablePlan | None = None
    validation_audit: dict[str, Any] | None = None
    lowered_plan: dict[str, Any] | None = None
    lowering_audit: dict[str, Any] | None = None
    lowered_plan_semantic_sha256: str | None = None
    lowering_sha256: str | None = None
    failure: PlanCompilationFailure | None = None
    artifact_sha256: str = ""

    @field_validator(
        "contract_sha256",
        "candidate_pool_sha256",
        "retrieval_evidence_sha256",
        "pricing_catalog_sha256",
        "prompt_sha256",
        "compiler_input_sha256",
    )
    @classmethod
    def _artifact_hash_fields(cls, value: str, info: Any) -> str:
        return _require_sha256(value, field_name=info.field_name)

    @field_validator(
        "compiler_response_sha256",
        "compiler_draft_sha256",
        "lowered_plan_semantic_sha256",
        "lowering_sha256",
    )
    @classmethod
    def _optional_artifact_hashes(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _seal_artifact(self) -> "SealedPlanCompilationArtifact":
        request_hashes = {item.request_sha256 for item in self.transport_attempts}
        if len(request_hashes) > 1:
            raise ValueError("plan_transport_request_hash_changed")
        if len(self.transport_attempts) > 3:
            raise ValueError("plan_transport_attempt_limit_exceeded")
        if self.status == "success":
            if (
                self.executable_plan is None
                or self.validation_audit is None
                or self.lowered_plan is None
                or self.lowering_audit is None
                or self.lowered_plan_semantic_sha256 is None
                or self.lowering_sha256 is None
                or self.failure is not None
                or self.compiler_response_sha256 is None
                or self.compiler_draft_sha256 is None
            ):
                raise ValueError("successful_plan_artifact_incomplete")
            if self.executable_plan.plan_sha256 != self.validation_audit.get(
                "validated_plan_sha256"
            ):
                raise ValueError("plan_artifact_validation_hash_mismatch")
            if self.executable_plan.plan_sha256 != self.lowered_plan_semantic_sha256:
                raise ValueError("plan_artifact_lowered_semantic_hash_mismatch")
            if self.lowered_plan.get("lowering_sha256") != self.lowering_sha256:
                raise ValueError("plan_artifact_lowering_hash_mismatch")
            if self.lowering_audit.get("plan_sha256") != self.executable_plan.plan_sha256:
                raise ValueError("plan_artifact_lowering_audit_mismatch")
        elif (
            self.failure is None
            or self.executable_plan is not None
            or self.lowered_plan is not None
            or self.lowering_audit is not None
        ):
            raise ValueError("failed_plan_artifact_shape_invalid")
        projected = self.model_dump(mode="python", exclude={"artifact_sha256"})
        _ensure_host_free(projected, field_name="sealed_plan_compilation_artifact")
        expected = canonical_sha256(projected)
        if self.artifact_sha256:
            supplied = _require_sha256(
                self.artifact_sha256,
                field_name="artifact_sha256",
            )
            legacy_projection = self.model_dump(
                mode="python", exclude={"artifact_sha256"}
            )
            executable = legacy_projection.get("executable_plan")
            if isinstance(executable, dict):
                for step in executable.get("steps", ()):
                    if isinstance(step, dict):
                        step.pop("controller_session_spec", None)
            lowered = legacy_projection.get("lowered_plan")
            if isinstance(lowered, dict):
                for template in lowered.get("step_templates", ()):
                    if isinstance(template, dict):
                        template.pop("controller_session_spec", None)
            legacy_expected = canonical_sha256(legacy_projection)
            if supplied not in {expected, legacy_expected}:
                raise ValueError("plan_compilation_artifact_sha256_mismatch")
            object.__setattr__(self, "artifact_sha256", supplied)
        else:
            object.__setattr__(self, "artifact_sha256", expected)
        return self


def _entrypoint_card(entrypoint: ResourceEntrypoint) -> EntrypointExecutionCard:
    return EntrypointExecutionCard(
        entrypoint_id=entrypoint.entrypoint_id,
        input_contract=entrypoint.input_contract,
        output_contract=entrypoint.output_contract,
    )


def _application_profile_card(profile: ApplicationProfile) -> ApplicationProfileCard:
    return ApplicationProfileCard(
        profile_id=profile.profile_id,
        description=profile.description,
        structured_preconditions=profile.structured_preconditions,
        binding_hints=profile.binding_hints,
        output_hints=profile.output_hints,
    )


def _model_format_contract(
    candidate_pool: FrozenCandidatePoolResult,
    *,
    resource_id: str,
) -> dict[str, Any] | None:
    contract_projection = getattr(candidate_pool, "contract_projection", None)
    if contract_projection is None:
        return None
    schema_phase = classify_output_schema_phase(contract_projection)
    if schema_phase == "compiler_pending":
        return None
    requirement = OutputFormatRequirement.from_contract_projection(
        contract_projection
    )
    if not requirement.structured:
        return None
    evidence = next(
        (
            item
            for item in getattr(candidate_pool, "capability_probe_evidence", ())
            if item.resource_id == resource_id
            and item.requirement_sha256 == requirement.requirement_sha256
        ),
        None,
    )
    decision = next(
        (
            item
            for item in candidate_pool.compatibility_decisions
            if item.resource_id == resource_id
        ),
        None,
    )
    reasons = set(decision.reason_codes if decision is not None else ())
    return build_schema_bound_candidate_format_contract(
        requirement=requirement,
        evidence=evidence,
        reason_codes=reasons,
    )


def build_schema_bound_candidate_format_contract(
    *,
    requirement: OutputFormatRequirement,
    evidence: CapabilityProbeEvidence | None,
    reason_codes: Iterable[str],
    selected_model_resource_id: str | None = None,
    selected_model_api_id: str | None = None,
    endpoint_identity_sha256: str | None = None,
    generic_capability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind exact output schema evidence to one candidate producer.

    The same contract is used for legacy retrieval-time schemas and for
    Planner V6 schemas generated after the Compiler selects a producer.
    """

    reasons = set(str(item) for item in reason_codes)
    local_validator_eligible, local_validator_reason_codes = local_json_schema_support(
        requirement.json_schema or {}
    )
    modes: list[str] = []
    if evidence is not None and evidence.is_admissible:
        selected_mode = getattr(evidence, "selected_enforcement_mode", None)
        if selected_mode:
            modes.append(selected_mode)
        else:
            modes.append("native_strict_schema")
    if (
        "manifest_json_mode_supported" in reasons
        and (evidence is None or evidence.outcome != "blocked")
        and local_validator_eligible
    ):
        modes.append("json_object_local_validator")
    if not modes:
        modes.append("intermediate_text_only")
    final_eligible = any(
        mode in {"native_strict_schema", "json_object_local_validator"}
        for mode in modes
    )
    projection = {
        "protocol": "sgar-candidate-format-contract-v1",
        "requirement_sha256": requirement.requirement_sha256,
        "schema_sha256": requirement.schema_sha256,
        "wire_schema_protocol": (
            requirement.portable_wire_schema.protocol
            if requirement.portable_wire_schema is not None
            else None
        ),
        "wire_schema_sha256": requirement.wire_schema_sha256,
        "wire_schema": (
            requirement.portable_wire_schema.wire_schema
            if requirement.portable_wire_schema is not None
            else None
        ),
        "schema": requirement.json_schema,
        "schema_source": requirement.schema_source,
        "probe_outcome": evidence.outcome if evidence is not None else "not_checked",
        "probe_reason_code": (
            evidence.reason_code
            if evidence is not None
            else "capability_probe_evidence_missing"
        ),
        "probe_evidence_sha256": (
            evidence.evidence_sha256 if evidence is not None else None
        ),
        "allowed_enforcement_modes": modes,
        "selected_enforcement_mode": (
            getattr(evidence, "selected_enforcement_mode", None)
            if evidence is not None
            else None
        ),
        "final_producer_eligible": final_eligible,
        "local_validator_eligible": local_validator_eligible,
        "local_validator_reason_codes": list(local_validator_reason_codes),
    }
    if selected_model_resource_id is not None:
        if evidence is None or generic_capability is None:
            raise ModelResponseContractError("selected_format_identity_evidence_missing")
        projection.update(
            selected_model_resource_id=selected_model_resource_id,
            selected_model_api_id=selected_model_api_id,
            endpoint_identity_sha256=endpoint_identity_sha256,
            generic_capability_sha256=generic_capability.get("capability_sha256"),
            generic_evidence_sha256=generic_capability.get("generic_evidence_sha256"),
            selected_probe_evidence=evidence.model_dump(mode="json"),
        )
    projection["format_contract_sha256"] = canonical_sha256(projection)
    return projection


def _model_structured_output_capability(
    candidate_pool: FrozenCandidatePoolResult,
    *,
    resource_id: str,
    manifest_sha256: str | None = None,
    api_model_id: str | None = None,
) -> dict[str, Any] | None:
    contract_projection = getattr(candidate_pool, "contract_projection", None)
    if contract_projection is None:
        return None
    phase = classify_output_schema_phase(contract_projection)
    if phase not in {"authoritative_schema", "compiler_pending"}:
        return None
    decision = next(
        (
            item
            for item in candidate_pool.compatibility_decisions
            if item.resource_id == resource_id
        ),
        None,
    )
    reasons = set(decision.reason_codes if decision is not None else ())
    projection = {
        "protocol": "sgar-candidate-structured-output-capability-v1",
        "schema_phase": phase,
        "manifest_json_mode_status": (
            "supported"
            if "manifest_json_mode_supported" in reasons
            else "unsupported"
            if "manifest_json_mode_unsupported" in reasons
            else "unknown"
        ),
        "manifest_native_structured_output_status": (
            "supported"
            if "manifest_native_structured_output_supported" in reasons
            else "unknown"
        ),
        "compatibility_status": (
            decision.verdict if decision is not None else "unknown"
        ),
    }
    identities = [item for item in candidate_pool.resolved_model_identities
                  if item.resource_id == resource_id and item.api_model_id == api_model_id
                  and item.manifest_sha256 == manifest_sha256]
    # Exact live probes of one arbitrary schema do not authorize generic selection.
    evidence_items = [item for item in candidate_pool.capability_probe_evidence
                      if item.resource_id == resource_id and item.model_id == api_model_id
                      and item.is_applied_admission
                      and item.reason_code in {"applied_ready_state_generic_strict_schema", "applied_ready_state_json_mode",
                                               "operator_approved_generic_strict_schema", "operator_approved_json_mode"}]
    endpoints = {item.endpoint_identity_sha256 for item in evidence_items}
    if len(identities) == 1 and len(endpoints) == 1 and evidence_items:
        evidence = sorted(evidence_items, key=lambda item: item.evidence_sha256)[0]
        projection.update(
            resource_id=resource_id, api_model_id=api_model_id,
            manifest_sha256=manifest_sha256,
            endpoint_identity_sha256=evidence.endpoint_identity_sha256,
            generic_evidence_sha256=evidence.evidence_sha256,
            generic_evidence=evidence.model_dump(mode="json"),
        )
    projection["capability_sha256"] = canonical_sha256(projection)
    return projection


def _temporary_tool_format_contract(
    candidate_pool: FrozenCandidatePoolResult,
    *,
    resource_id: str,
) -> dict[str, Any]:
    requirement = system_role_requirement("temporary_tool")
    evidence = next(
        (
            item
            for item in getattr(candidate_pool, "capability_probe_evidence", ())
            if item.resource_id == resource_id
            and item.requirement_sha256 == requirement.requirement_sha256
        ),
        None,
    )
    eligible = evidence is not None and evidence.is_admissible
    selected_mode = (
        getattr(evidence, "selected_enforcement_mode", None)
        if evidence is not None
        else None
    )
    if eligible and not selected_mode:
        selected_mode = "native_strict_schema"
    projection = {
        "protocol": "sgar-system-role-format-contract-v1",
        "role": "temporary_tool",
        "requirement_sha256": requirement.requirement_sha256,
        "schema_sha256": requirement.schema_sha256,
        "wire_schema_protocol": (
            requirement.portable_wire_schema.protocol
            if requirement.portable_wire_schema is not None
            else None
        ),
        "wire_schema_sha256": requirement.wire_schema_sha256,
        "probe_outcome": evidence.outcome if evidence is not None else "not_checked",
        "probe_reason_code": (
            evidence.reason_code
            if evidence is not None
            else "capability_probe_evidence_missing"
        ),
        "probe_evidence_sha256": (
            evidence.evidence_sha256 if evidence is not None else None
        ),
        "allowed_enforcement_modes": (
            [selected_mode] if eligible and selected_mode else []
        ),
        "selected_enforcement_mode": selected_mode,
        "eligible": eligible,
    }
    projection["format_contract_sha256"] = canonical_sha256(projection)
    return projection


def _output_description_advisory(definition: ResourceDefinition) -> dict[str, Any] | None:
    """Copy declared resource prose without upgrading executable output authority."""
    text = definition.output_description
    if text is None:
        return None
    from .compiler_attempt_diagnostics import sanitize_diagnostic_value

    advisory = {
        "source_path": "constraint.output_shape",
        "scope": "resource",
        "authority": "descriptive_only",
    }
    try:
        safe, redactions = sanitize_diagnostic_value(
            text, host_roots=tuple(entry.dispatch for entry in definition.entrypoints),
        )
        _ensure_host_free(safe, field_name="output_description")
        return {
            **advisory, "status": "redacted" if redactions else "declared",
            "description": safe, "redactions": list(redactions),
        }
    except Exception:
        # Prose availability is never an execution admission condition.
        return {
            **advisory, "status": "unavailable", "description": None,
            "reason": "safe_description_unavailable",
        }


def build_candidate_execution_cards(
    candidate_pool: FrozenCandidatePoolResult,
    *,
    resource_definitions: Mapping[str, ResourceDefinition],
    pricing_catalog: ModelPricingCatalog,
) -> tuple[CandidateExecutionCard, ...]:
    """Project the exact frozen pool into immutable Compiler cards."""

    snapshot = candidate_pool.candidate_pool_snapshot
    candidate_ids = [item.resource_id for item in snapshot.candidates]
    if set(resource_definitions) != set(candidate_ids):
        raise ValueError("candidate_resource_definition_set_mismatch")

    score_by_id: dict[str, list[CandidateScoreEvidence]] = {}
    for evidence in candidate_pool.candidate_score_evidence:
        score_by_id.setdefault(evidence.resource_id, []).append(evidence)
    compatibility_by_id = {
        item.resource_id: item for item in candidate_pool.compatibility_decisions
    }
    if len(compatibility_by_id) != len(candidate_pool.compatibility_decisions):
        raise ValueError("candidate_compatibility_decisions_not_unique")
    edges_by_parent: dict[str, list[CandidateDependencyEdge]] = {}
    for edge in candidate_pool.dependency_edges:
        edges_by_parent.setdefault(edge.parent_resource_id, []).append(edge)
    optional_by_parent: dict[str, list[str]] = {}
    for hint in candidate_pool.optional_dependency_hints:
        if hint.resource_id:
            optional_by_parent.setdefault(hint.parent_resource_id, []).append(hint.resource_id)

    cards: list[CandidateExecutionCard] = []
    for candidate_rank, candidate in enumerate(snapshot.candidates, start=1):
        definition = resource_definitions[candidate.resource_id]
        if definition.resource_type == "Model" and definition.selection_scope != "candidate":
            raise ValueError("compiler_model_not_candidate")
        if definition.resource_id != candidate.resource_id:
            raise ValueError("candidate_resource_definition_id_mismatch")
        if definition.resource_type != candidate.resource_type:
            raise ValueError("candidate_resource_definition_type_mismatch")
        evidence_items = tuple(
            sorted(
                score_by_id.get(candidate.resource_id, ()),
                key=lambda item: (item.rank, item.query_role, item.parent_resource_id or ""),
            )
        )
        primary_evidence = evidence_items[0] if evidence_items else None
        dependency_edges = tuple(
            sorted(
                edges_by_parent.get(candidate.resource_id, ()),
                key=lambda edge: (
                    edge.requirement_kind,
                    edge.dependency_slot or "",
                    edge.rank,
                    edge.child_resource_id,
                ),
            )
        )
        required_ids = tuple(edge.child_resource_id for edge in dependency_edges)
        compatibility = compatibility_by_id.get(candidate.resource_id)
        pricing = None
        if candidate.resource_type == "Model":
            price = pricing_catalog.resolve(resource_id=candidate.resource_id)
            pricing = ModelPricingEvidence.from_price(
                price,
                pricing_catalog_sha256=pricing_catalog.pricing_catalog_sha256,
            )
        runtime_requirements = dict(definition.runtime_requirements)
        if candidate.resource_type == "Model":
            structured_capability = _model_structured_output_capability(
                candidate_pool,
                resource_id=candidate.resource_id,
                manifest_sha256=definition.manifest_sha256,
                api_model_id=pricing.api_model_id if pricing is not None else None,
            )
            if structured_capability is not None:
                runtime_requirements[
                    "sgar_structured_output_capability"
                ] = structured_capability
            format_contract = _model_format_contract(
                candidate_pool,
                resource_id=candidate.resource_id,
            )
            if format_contract is not None:
                runtime_requirements["sgar_format_contract"] = format_contract
            runtime_requirements["sgar_system_role_contracts"] = {
                "temporary_tool": _temporary_tool_format_contract(
                    candidate_pool,
                    resource_id=candidate.resource_id,
                )
            }
        elif candidate.resource_type == "Agent":
            base_model_ids = tuple(
                edge.child_resource_id
                for edge in dependency_edges
                if edge.requirement_kind == "agent_base_model"
            )
            if len(base_model_ids) == 1:
                format_contract = _model_format_contract(
                    candidate_pool,
                    resource_id=base_model_ids[0],
                )
                if format_contract is not None:
                    runtime_requirements["sgar_format_contract"] = format_contract
        cards.append(
            CandidateExecutionCard(
                resource_id=candidate.resource_id,
                resource_type=candidate.resource_type,
                candidate_origin=candidate.origin.value,
                candidate_rank=candidate_rank,
                semantic_score=(primary_evidence.score if primary_evidence else None),
                score_evidence_sha256=canonical_sha256(evidence_items),
                manifest_sha256=definition.manifest_sha256,
                availability_status=definition.status,
                compatibility_status=(compatibility.verdict if compatibility else "unknown"),
                entrypoints=tuple(_entrypoint_card(item) for item in definition.entrypoints),
                application_profiles=tuple(
                    _application_profile_card(item) for item in definition.application_profiles
                ),
                base_input_contract=definition.base_input_contract,
                base_output_contract=definition.base_output_contract,
                runtime_requirements=runtime_requirements,
                required_dependency_ids=required_ids,
                optional_dependency_ids=tuple(
                    sorted(set(optional_by_parent.get(candidate.resource_id, ())))
                ),
                dependency_edges=dependency_edges,
                agent_base_model_candidates=tuple(
                    edge.child_resource_id
                    for edge in dependency_edges
                    if edge.requirement_kind == "agent_base_model"
                ),
                model_pricing=pricing,
                capability_operations=(
                    tuple(definition.capability_card.capability_operations)
                    if definition.capability_card is not None
                    else ()
                ),
                advisory_output_description=_output_description_advisory(definition),
                semantic_summary=(
                    definition.capability_card.summary
                    if definition.capability_card is not None
                    else ""
                ),
                semantic_summary_status=(
                    definition.capability_card.summary_status
                    if definition.capability_card is not None
                    else "unknown"
                ),
                semantic_limitations=(
                    tuple(definition.capability_card.limitations)
                    if definition.capability_card is not None
                    else ()
                ),
            )
        )
    return tuple(cards)


__all__ = [
    "ApplicationProfileCard",
    "CandidatePoolFeasibilityAuditV1",
    "CandidateExecutionCard",
    "CompilePurpose",
    "CompilerCallableToolDecisionV1",
    "CompilerContextDescriptor",
    "CompilerPlanDraft",
    "CompilerPolicy",
    "CompilerPublicContext",
    "CompilerStepDraft",
    "EntrypointExecutionCard",
    "ExecutablePlan",
    "ExecutablePlanStep",
    "InsufficiencyCode",
    "ModelCostEvidence",
    "ModelPricingEvidence",
    "PlanCompilerInputEnvelope",
    "ObligationFeasibilityEvidenceV1",
    "PlanCompilationFailure",
    "PlanTransportAttempt",
    "PlanObjectiveEvidence",
    "PlanRevisionRef",
    "ResourceUsageRecord",
    "ResourceApplicationV1",
    "RuntimeCapabilities",
    "RuntimeMaterialAdapterCapabilityV1",
    "SealedPlanCompilationArtifact",
    "StepOutputContract",
    "StepOutputRef",
    "build_candidate_execution_cards",
    "audit_candidate_pool_feasibility",
    "build_runtime_material_adapter_capabilities",
    "resolve_material_delivery",
    "resolve_final_step_eligibility",
    "required_material_source_ids_for_obligation",
    "select_material_delivery_mode",
]
