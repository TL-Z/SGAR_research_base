"""Strict production Plan Compiler over one revisioned frozen candidate pool."""

from __future__ import annotations

from . import terminal_progress

import asyncio
import hashlib
import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import ValidationError

from .atomic_io import bounded_path_component, temporary_sibling_path
from . import compiler_attempt_diagnostics
from .compiler_invariants import (
    CompilerProposalCorrectionViewV1,
    CompilerProposalInvariantError,
    CompilerProposalNormalizationAuditV1,
    CompilerProposalValidationIssueV1,
    build_compiler_safe_issue_audits,
    build_compiler_proposal_correction_view,
    adaptation_model_invariant_projection,
    compiler_constraint_prompt_projection,
    compiler_invariant_validator_projection,
    initial_compiler_model_invariant_projection,
    invariant_id_for_failure_code,
    normalize_compiler_proposal_payload,
    project_initial_compiler_correction_v3,
    require_plan_adaptation_invariants,
    require_compiler_proposal_invariants,
)
from .control_role_policy import (
    ControlRoleInvocationPolicyV1,
    ControlRolePolicyV1,
)
from .executable_plan import (
    COMPILER_DECISION_PROTOCOL,
    COMPILER_PLAN_DRAFT_PROTOCOL,
    PLAN_COMPILATION_ARTIFACT_PROTOCOL,
    PLAN_COMPILER_INPUT_PROTOCOL,
    CandidateExecutionCard,
    CompilerDecisionProposalV3,
    CompilerSchemaProjectionError,
    CompilerInvariantCatalog,
    CompilerPlanProposalV2,
    CompilerProjectionAuditV1,
    CompilerContextDescriptor,
    CompilerPlanDraft,
    CompilerPolicy,
    CompilerPublicContext,
    PlanCompilationFailure,
    PlanCompilerInputEnvelope,
    PlanRevisionRef,
    PlanTransportAttempt,
    RuntimeCapabilities,
    SealedPlanCompilationArtifact,
    audit_candidate_pool_feasibility,
    build_schema_bound_candidate_format_contract,
    build_candidate_execution_cards,
    build_compiler_invariant_catalog,
    project_compiler_plan_proposal,
    project_compiler_decision_v3,
    resolve_final_step_eligibility,
    resolve_material_delivery,
    _ensure_host_free,
    _manifest_output_schema_status,
    _manifest_output_contract,
    _bound_generic_capability,
    _selected_schema_identity_valid,
)
from .model_accounting import (
    AccountingPersistenceError,
    BudgetControlError,
    ModelCallContext,
    ModelPricingCatalog,
    PricingCatalogError,
    RunCostLedger,
)
from .formal_contracts import (
    MaterialDescriptorV1,
    SemanticEdgeContractV2,
    compile_execution_obligation_v2,
    compile_execution_obligations,
)
from .model_response_contracts import (
    ExactCapabilityProbeService,
    classify_output_schema_phase,
    ModelResponseContractError,
    OutputFormatRequirement,
    StructuredResponseModeInput,
    build_structured_role_contract,
    normalize_structured_response_content,
    normalize_structured_response_mode,
    structured_response_format,
    structured_role_prompt_projection,
    system_role_requirement,
    system_role_schema,
    validate_structured_response_content,
)
from .model_transport import (
    SyncModelTransportPort,
    classify_transport_exception,
    model_request_sha256,
    require_sync_model_transport,
)
from .pipeline_control import canonical_json_bytes, canonical_sha256
from .planner_wire import PlannerSchemaGraphWireV1
from .provider_reasoning import observe_provider_reasoning
from .schema import DagEdgeContractV1
from .plan_lowering import (
    ExecutionPlanLowerer,
    PlanFrameworkValidationError,
    PlanStructuralValidator,
    PlanValidationError,
)
from .resource_runtime import ResourceDefinition
from .retrieval_runtime import FrozenCandidatePoolResult
from .public_inputs import path_sha256
from .recovery_control import (
    CompletedStepCheckpoint,
    PlanAdaptationDraft,
    PlanAdaptationDecisionV3,
    PlanAdaptationInputEnvelope,
    RecoveryControlError,
    RecoveryLineage,
    StructuredExecutionFailureEvidence,
    TemporaryToolSourceBundle,
    assert_recovery_projection_safe,
    validate_adapted_plan_lineage,
    validate_adaptation_decision_identity,
)


COMPILER_OUTPUT_INSTRUCTIONS = (
    "Resource-level advisory_output_description is manifest prose, not a machine Schema, "
    "entrypoint guarantee or execution permission. Read it alongside native and semantic "
    "output facts; never replace those facts with a Compiler target contract. "
    "final_artifact_contract.contract_scope is the existing Planner scope: intermediate "
    "outputs need not reproduce global final fields, but must meet current node requirements "
    "and formal downstream edges. final_contract is final within this subtask. "
    "When correcting, read semantic_correction and its previous_decision, output_reachability "
    "and revision_permissions before consulting candidates. previous_decision is the prior "
    "parsed response, not an accepted plan; return a complete replacement within those permissions. "
    "selected_dispatch_policy describes the rejected proposal's resource, not a command to "
    "select it again. "
    "Generative resources still require supported constrained generation and actual-output "
    "validation; their selection is not proof of correctness. "
    "Prefer concise intent and rationale text, but preserve all necessary task and execution details. "
    "Read an operation's local manifest_output_schema and output_capabilities when present. "
    "Only when both are omitted and that card provides shared_operation_output_facts, read "
    "the pair there: it is identical for all operations listed on that card in this request, "
    "not a guarantee for other entrypoints. Missing or null facts grant no inferred permission "
    "or Schema; respond with the original resource_id, capability_operation_id and wire fields. "
    "final_step_eligible=true grants final-step candidate selection only, not arbitrary target "
    "realizability. manifest_output_schema.selected_resource_allowed=true permits native "
    "contract inheritance through output_role=selected_resource, not general resource admission. "
    "intermediate_contract/final_contract specify targets; they neither perform conversions "
    "nor supply native output facts. requires_explicit_intermediate_schema does not require "
    "schema_graph for non-JSON representations: follow the existing wire conditions. Consult "
    "output_capabilities and output_conversion_rules for the source-to-target prerequisites. "
    "Model and Agent generation must satisfy their selected enforcement contract. Fixed-output "
    "Tools and Resources keep their declared outputs; a Compiler schema cannot rewrite them. "
    "Skills retain their declared execution behavior. Use an existing supported deterministic "
    "conversion or an explicitly authorized processing step with bound inputs to change representation. "
    "Never infer a machine schema from prose or fabricate one as evidence of native output. "
    "A missing native schema does not forbid declared semantic extraction or an authorized controller "
    "calling explicitly selected tools. Preserve explicit task requirements; do not weaken required "
    "fields or types, guess fixed answers, or invent stricter constraints to fit a resource. "
    "Carry the node task, input purpose, target output and acceptance relationship into each "
    "step intent and concrete contract. Distinguish original material from the requested "
    "transformed result and from completed artifacts. Observing a source value does not itself "
    "justify a result const: any exact constraint must describe the intended result after the "
    "required transformation while retaining required invariants. General-purpose rules must "
    "remain general; specialize a rule to one instance only when the task calls for it. Strict "
    "structure does not require embedding an instance verbatim. A generated or committed rule "
    "is not higher authority than the original task. Preserve explicit validation bindings; "
    "do not rewrite an accepted rule or guess missing material to resolve a conflict. "
    "Synthetic example: a tool with a declared textual payload can feed an authorized processor "
    "that generates a required structured result; assigning the tool that result schema alone "
    "does not perform the processing. If no executable composition exists, report concrete insufficiency. "
)

from .input_alignment import alignment_evidence

PLAN_COMPILER_PROMPT_VERSION = "executable-plan-compiler-v38-typed-container-lowering-hybrid-dag"
COMPILER_MODEL_INPUT_PROTOCOL = "sgar-compiler-decision-input-v9"
PLAN_COMPILER_MAX_TRANSPORT_RETRIES = 2
PLAN_COMPILER_MAX_TRANSPORT_ATTEMPTS = 3

COMPILER_SELECTION_OBJECTIVE = (
    "Selection objective for the API-based runtime: after proving feasibility and exact "
    "contract compatibility, including task-state delivery, prefer the smallest static DAG "
    "with the fewest generative calls. Prefer a fully deterministic Tool/Resource workflow "
    "when its declared operations, typed material bindings, physical output behavior, and "
    "verification evidence satisfy every obligation. If deterministic steps need semantic "
    "interpretation, use the smallest explicit hybrid graph that performs only the unresolved "
    "semantic work generatively and performs compatible extraction, conversion, validation, "
    "or materialization deterministically. Use an Agent/controller only when no smaller direct "
    "or hybrid composition can satisfy the obligations, and bind every callable Tool explicitly. "
    "Do not delegate a step whose complete behavior is already covered by selected deterministic "
    "operations, and do not split one coherent operation across multiple generative actors without "
    "a contract-required capability boundary. Among plans with the same required capabilities and "
    "generation-call count, "
    "minimize model cost using each candidate's exact model_pricing input_per_m, cache_per_m, and "
    "output_per_m values; do not treat unknown non-model cost as zero. Never choose a more costly "
    "Model merely because it has a higher retrieval rank, a stronger marketing description, or "
    "more general capability if a cheaper candidate is contract-equivalent. Do not privilege any "
    "named model, provider, family, or retrieval position; extra capability is justified only when "
    "it is necessary for an obligation and no cheaper contract-equivalent candidate or deterministic "
    "composition is sufficient. Compare every feasible model in the "
    "compiler_input.model_cost_comparison table; a selected plan must not claim cost equivalence "
    "when a cheaper contract-equivalent candidate is available. Retrieval rank/semantic score is "
    "evidence for eligibility, not a preference objective."
)


def plan_compiler_schema_failure_audit(
    exc: BaseException,
    *,
    response_sha256: str | None,
    adaptation: bool,
) -> dict[str, Any]:
    """Return content-free schema diagnostics for strict response rejection."""

    schema_type = PlanAdaptationDecisionV3 if adaptation else CompilerDecisionProposalV3
    locations: list[list[str | int]] = []
    type_counts: dict[str, int] = {}
    if isinstance(exc, ValidationError):
        for item in exc.errors(include_input=False, include_url=False):
            location = [
                value if isinstance(value, int) else str(value)
                for value in tuple(item.get("loc") or ())
            ]
            locations.append(location)
            error_type = str(item.get("type") or "validation_error")
            type_counts[error_type] = type_counts.get(error_type, 0) + 1
    else:
        locations.append(["response"])
        error_type = type(exc).__name__ or "response_error"
        type_counts[error_type] = 1
    return {
        "validation_error_count": len(locations),
        "validation_error_path_hashes": [
            canonical_sha256({"location": item}) for item in locations
        ],
        "validation_error_type_counts": dict(sorted(type_counts.items())),
        "expected_schema_sha256": canonical_sha256(schema_type.model_json_schema()),
        "response_sha256": response_sha256,
    }

INPUT_ALIGNMENT_RULES = (
    " Assess inputs before proposing execution. input_assessment is required: sufficient=true with "
    "missing_information=[] only when the task text and actually authorized materials suffice. "
    "If material is absent, sufficient=false and list missing_information entries with requirement_ref "
    "from requirement_catalog and missing_information. Return an insufficient plan using "
    "required_input_missing, no steps or final contract; do not guess or obtain new authorization. "
    "Planner input_requirement=task_text_only has no material inputs; requires_material needs all "
    "declared sources bound through actual artifact_handle or step_output dataflow. Scheduling "
    "depends_on alone does not deliver data. If Planner omitted a needed input, report the gap. "
    "Return constraint_basis=[]; it is a reserved compatibility field, not a planning requirement. "
    "Do not enumerate attribution paths or duplicate the output contract in explanatory records. "
    "Preserve explicit task requirements. Do not guess source values or fix unobserved answers in "
    "the output contract. Use authorized material for source-dependent work. Choose unspecified "
    "implementation details without changing task requirements. Evaluator checks actual task compliance. "
)

_COMPILER_FEW_SHOT_V3: tuple[dict[str, Any], ...] = (
    {
        "name": "requirements_only_structured_final_producer",
        "compiler_input_excerpt": {
            "obligations": ["obligation:structured_brief"],
            "final_artifact_contract": {
                "artifact_type": "json",
                "schema_generation_required": True,
            },
            "candidate_operations": [
                "example.model.writer::generate_structured_brief",
            ],
        },
        "decision": {
            "protocol": "sgar-compiler-decision-v5",
            "is_sufficient": True,
            "insufficiency_code": None,
            "input_assessment": {"sufficient": True, "missing_information": []},
            "constraint_basis": [],
            "unsatisfied_obligation_ids": [],
            "capability_gaps": [],
            "steps": [
                {
                    "step_id": "generate_brief",
                    "resource_id": "example.model.writer",
                    "capability_operation_id": "example.model.writer::generate_structured_brief",
                    "intent": "Generate the requested structured brief from the declared requirements.",
                    "depends_on": [],
                    "input_mappings": [],
                    "satisfied_obligation_ids": ["obligation:structured_brief"],
                    "capability_evidence_refs": ["example.model.writer::generate_structured_brief"],
                    "output_role": "final",
                    "intermediate_contract": None,
                    "agent_base_model_resource_id": None,
                },
            ],
            "final_step_id": "generate_brief",
            "final_contract": {
                "artifact_type": "json",
                "description": "A structured brief with one required statement field.",
                "schema_graph": {
                    "schema_id": "example_brief_schema",
                    "root_node_id": "example_brief_root",
                    "nodes": [
                        {"node_id": "example_brief_root", "kind": "object"},
                        {"node_id": "example_statement", "kind": "string"},
                    ],
                    "object_fields": [
                        {
                            "object_node_id": "example_brief_root",
                            "field_name": "statement",
                            "value_node_id": "example_statement",
                            "required": True,
                        }
                    ],
                    "array_items": [],
                    "map_values": [],
                    "combinator_branches": [],
                },
            },
            "concise_rationale": "The operation declares no required material port and directly satisfies the requirements-only final obligation.",
        },
    },
    {
        "name": "single_authorized_input_transformation",
        "compiler_input_excerpt": {
            "obligations": ["obligation:normalized_delivery"],
            "candidate_operations": [
                "example.tool.transformer::normalize_material",
            ],
        },
        "decision": {
            "protocol": "sgar-compiler-decision-v5",
            "is_sufficient": True,
            "insufficiency_code": None,
            "input_assessment": {"sufficient": True, "missing_information": []},
            "constraint_basis": [],
            "unsatisfied_obligation_ids": [],
            "capability_gaps": [],
            "steps": [
                {"step_id": "normalize_material", "resource_id": "example.tool.transformer", "capability_operation_id": "example.tool.transformer::normalize_material", "intent": "Transform the complete authorized material into the required normalized delivery.", "depends_on": [], "input_mappings": [{"target_port": "source", "source_kind": "artifact_handle", "source_id": "artifact:public_input:example_source", "from_step": None, "literal_value": None}], "satisfied_obligation_ids": ["obligation:normalized_delivery"], "capability_evidence_refs": ["example.tool.transformer::normalize_material"], "output_role": "final", "intermediate_contract": None, "agent_base_model_resource_id": None},
            ],
            "final_step_id": "normalize_material",
            "concise_rationale": "The selected operation has an authorized binding for the exact source and is eligible to produce the final artifact.",
        },
    },
    {
        "name": "parallel_inputs_then_merge",
        "compiler_input_excerpt": {
            "obligations": ["obligation:comparative_delivery"],
            "candidate_operations": [
                "example.tool.extractor::extract_first",
                "example.tool.extractor::extract_second",
                "example.model.writer::merge_findings",
            ],
        },
        "decision": {
            "protocol": "sgar-compiler-decision-v5",
            "is_sufficient": True,
            "insufficiency_code": None,
            "input_assessment": {"sufficient": True, "missing_information": []},
            "constraint_basis": [],
            "unsatisfied_obligation_ids": [],
            "capability_gaps": [],
            "steps": [
                {"step_id": "extract_first", "resource_id": "example.tool.extractor", "capability_operation_id": "example.tool.extractor::extract_first", "intent": "Extract the authorized facts from the first input.", "depends_on": [], "input_mappings": [{"target_port": "source", "source_kind": "artifact_handle", "source_id": "artifact:public_input:first", "from_step": None, "literal_value": None}], "satisfied_obligation_ids": [], "capability_evidence_refs": ["example.tool.extractor::extract_first"], "output_role": "selected_resource", "intermediate_contract": None, "agent_base_model_resource_id": None},
                {"step_id": "extract_second", "resource_id": "example.tool.extractor", "capability_operation_id": "example.tool.extractor::extract_second", "intent": "Extract the authorized facts from the second input.", "depends_on": [], "input_mappings": [{"target_port": "source", "source_kind": "artifact_handle", "source_id": "artifact:public_input:second", "from_step": None, "literal_value": None}], "satisfied_obligation_ids": [], "capability_evidence_refs": ["example.tool.extractor::extract_second"], "output_role": "selected_resource", "intermediate_contract": None, "agent_base_model_resource_id": None},
                {"step_id": "merge_findings", "resource_id": "example.model.writer", "capability_operation_id": "example.model.writer::merge_findings", "intent": "Merge both extracted fact sets into the required comparative delivery.", "depends_on": ["extract_first", "extract_second"], "input_mappings": [{"target_port": "first", "source_kind": "step_output", "source_id": None, "from_step": "extract_first", "literal_value": None}, {"target_port": "second", "source_kind": "step_output", "source_id": None, "from_step": "extract_second", "literal_value": None}], "satisfied_obligation_ids": ["obligation:comparative_delivery"], "capability_evidence_refs": ["example.model.writer::merge_findings"], "output_role": "final", "intermediate_contract": None, "agent_base_model_resource_id": None},
            ],
            "final_step_id": "merge_findings",
            "concise_rationale": "The two independent authorized inputs are processed in parallel and both outputs are explicitly bound to the final merge.",
        },
    },
    {
        "name": "generated_artifact_with_explicit_verification",
        "compiler_input_excerpt": {
            "obligations": ["obligation:generated_and_verified_delivery"],
            "candidate_operations": [
                "example.model.writer::generate_delivery",
                "example.tool.verifier::verify_delivery",
            ],
        },
        "decision": {
            "protocol": "sgar-compiler-decision-v5",
            "is_sufficient": True,
            "insufficiency_code": None,
            "input_assessment": {"sufficient": True, "missing_information": []},
            "constraint_basis": [],
            "unsatisfied_obligation_ids": [],
            "capability_gaps": [],
            "steps": [
                {"step_id": "generate_delivery", "resource_id": "example.model.writer", "capability_operation_id": "example.model.writer::generate_delivery", "intent": "Generate the requested candidate delivery.", "depends_on": [], "input_mappings": [], "satisfied_obligation_ids": [], "capability_evidence_refs": ["example.model.writer::generate_delivery"], "output_role": "selected_resource", "intermediate_contract": None, "agent_base_model_resource_id": None},
                {"step_id": "verify_delivery", "resource_id": "example.tool.verifier", "capability_operation_id": "example.tool.verifier::verify_delivery", "intent": "Apply the explicitly required deterministic verification to the generated delivery.", "depends_on": ["generate_delivery"], "input_mappings": [{"target_port": "document", "source_kind": "step_output", "source_id": None, "from_step": "generate_delivery", "literal_value": None}], "satisfied_obligation_ids": ["obligation:generated_and_verified_delivery"], "capability_evidence_refs": ["example.tool.verifier::verify_delivery"], "output_role": "final", "intermediate_contract": None, "agent_base_model_resource_id": None},
            ],
            "final_step_id": "verify_delivery",
            "concise_rationale": "The generated artifact is explicitly bound to the declared verifier because deterministic verification is itself an execution obligation.",
        },
    },
    {
        "name": "genuinely_insufficient_final_operation",
        "compiler_input_excerpt": {
            "execution_obligations": ["obligation:synthetic_final_artifact"],
            "candidate_cards": [
                {
                    "resource_id": "example.tool.partial",
                    "has_final_step_eligible_operation": False,
                    "capability_operations": [
                        {
                            "capability_operation_id": "example.tool.partial::emit_text",
                            "final_step_eligible": False,
                        }
                    ],
                }
            ],
        },
        "decision": {
            "protocol": "sgar-compiler-decision-v5",
            "is_sufficient": False,
            "insufficiency_code": "contract_not_achievable",
            "input_assessment": {"sufficient": True, "missing_information": []},
            "constraint_basis": [],
            "unsatisfied_obligation_ids": ["obligation:synthetic_final_artifact"],
            "capability_gaps": ["No authorized operation is eligible to produce the required final artifact."],
            "steps": [],
            "final_step_id": None,
            "final_contract": None,
            "concise_rationale": "The candidate pool has no operation authorized to produce the required final artifact.",
        },
    },
)

_COMPILER_FEW_SHOT_JSON = canonical_json_bytes(_COMPILER_FEW_SHOT_V3).decode("utf-8")
if len(_COMPILER_FEW_SHOT_JSON.encode("utf-8")) > 24 * 1024:
    raise RuntimeError("compiler_few_shot_size_limit_exceeded")

FINAL_CONTRACT_INSTRUCTIONS = (
    "final_contract presence is determined only by is_sufficient and the framework's "
    "final_artifact_contract.schema_generation_required. If is_sufficient=false, final_contract "
    "must be null. If is_sufficient=true and schema_generation_required=true, final_contract "
    "must be non-null with a non-broad schema_graph covering this subtask's output semantics "
    "and acceptance conditions. If is_sufficient=true and schema_generation_required=false, "
    "final_contract must be null: use the already bound current output contract. Do not change "
    "the framework flag. schema_sha256 describes that current output contract binding, not the "
    "presence of any Schema elsewhere. An authorized upstream json_schema_document bound to "
    "response_format remains validation material; it neither fills final_contract nor changes "
    "schema_generation_required. Preserve its declared purpose and constraints. When producing "
    "a Schema document, the output contract describes the document itself, not its business instance. "
)

SCHEMA_NODE_CONSTRAINT_INSTRUCTIONS = (
    " Schema-graph constraints describe each node's own kind, not a child node or the business data "
    "described by a Schema document. min_items/max_items and unique_items apply only to array; "
    "min_properties/max_properties only to object or map; min_length/max_length/pattern/format only "
    "to string; minimum/maximum/exclusive_minimum/exclusive_maximum/multiple_of only to integer or number. "
    "Populate only the enum group matching kind; leave other enum groups empty. Use null for inactive "
    "nullable constraints and false for inactive unique_items, not 0 or a guessed bound. "
    "For example, an object containing an array keeps its own min_items/max_items null; array size "
    "constraints belong on the linked array node. Keep lower bounds <= upper bounds, multiple_of > 0, "
    "and integer-node numeric constraints integral. A null-kind node has nullable=false. "
    "On correction, use the reported node path, kind and conflicting fields; preserve the task and "
    "completed checkpoints. Do not remove an intended requirement merely to pass validation. "
)

PLAN_COMPILER_SYSTEM_PROMPT_V3 = (
    INPUT_ALIGNMENT_RULES + COMPILER_OUTPUT_INSTRUCTIONS + COMPILER_SELECTION_OBJECTIVE + FINAL_CONTRACT_INSTRUCTIONS +
    "You are the SGAR semantic Plan Compiler. Return exactly one strict "
    "sgar-compiler-decision-v5 object. Decide only which authorized resource capability "
    "operations are necessary, how they form one static acyclic DAG, how declared input "
    "sources map to semantic ports, which obligations each step satisfies, and which step "
    "is final. The framework deterministically derives executable dispatch metadata, runtime "
    "bindings, generated result identities, paths, hashes, format enforcement, and lowering. "
    "The original task owns explicit requirements; "
    "Planner semantics define the subtask scope, and you choose implementation details only "
    "where those requirements leave room. Never turn an unverified answer guess into const, "
    "execution_requirements are user-declared hard constraints. Satisfy every listed relation "
    "exactly from candidate_cards: direct_executor selects the step resource, agent_base_model "
    "selects the Agent backing Model, controller_callable_tool selects controller_callable_tools, "
    "and advisory_skill requires an explicit Skill step whose output is bound to the controller "
    "and whose resource ID appears in that controller step's advisory_profile_refs. Never invent "
    "or substitute an explicit identity or operation. "
    "enum, fixed counts or bounds. Preserve explicit input, output and interface requirements. "
    "authorized_artifact_sources[*].execution_contract describes an accepted producer artifact, not a "
    "new requirement on your output. final_artifact_contract.content_kind is authoritative: "
    "the framework binds it to the final output; do not echo it in final_contract. "
    "final_contract constrains the actual output of THIS subtask, not the object described by "
    "that output, and final means final within this subtask. For json_schema_document, build "
    "a schema_graph describing the Schema DOCUMENT (its type/properties/required/etc. fields), "
    "not the business instance the document will validate. For intermediate outputs declare "
    "their own non-null content_kind and their actual artifact shape. Authorized input descriptors carry the "
    "producer's content_kind: a json_schema_document input is a validation definition, not a "
    "data instance. If its declared purpose is validation, apply its constraints to the data "
    "artifact instead of treating the schema document as the data or silently replacing it. "
    "Preserve all declared input and "
    "downstream obligations; do not turn examples or explanatory descriptions into unsupported "
    "restrictions. Before returning, check that an output satisfying each schema_graph can "
    "actually fulfill the step intent and the Planner's acceptance conditions. "
    "A schema_graph must explicitly declare every structural relation. "
    "Each object node must have at least one object_field whose object_node_id names that object "
    "node and whose value_node_id names a declared node. min_properties does not substitute for "
    "object_fields. Each array node requires exactly one array_items relation, each map node requires "
    "exactly one map_values relation, and each all_of, any_of, or one_of node requires at least two "
    "combinator_branches. Every declared node must be reachable from root_node_id. "
    "For source_kind=artifact_handle, copy source_id exactly from "
    "compiler_input.authorized_artifact_sources[].source_id. That canonical source ID is "
    "not a runtime handle, semantic_ref, locator, or path. "
    "compiler_visibility describes only what the Compiler sees; it does not determine Runtime "
    "delivery. Treat available_delivery_modes and candidate operation "
    "authorized_artifact_bindings as authoritative framework facts. A handle_only visibility "
    "does not make an authorized material unavailable. Select an artifact_handle mapping only "
    "when the exact resource_id, capability_operation_id, target_port, and canonical source_id "
    "appear in an authorized binding. Claim material-driven candidate-pool insufficiency only "
    "when no authorized artifact binding and no valid step_output path can satisfy the required "
    "input. "
    "Never output or guess those framework-owned values. Use only candidate resource IDs, "
    "capability_operation_ids, obligation IDs, source IDs, and Agent base Models present in "
    "compiler_input. Write intent, rationale, and capability-gap text in English while "
    "preserving literal identifiers exactly. Never infer capability from business words, filenames, resource IDs, "
    "rank, or semantic score; capability_operations and execution_obligations are authoritative. "
    "No resource type is mandatory by itself: choose Model-only, Tool-only, or a typed multi-step "
    "combination only when its declared operations, ports, material access, determinism, and evidence "
    "satisfy the supplied obligations. Verification may be an explicit verifier step or the mandatory "
    "framework Evaluator contract; never add a Tool merely to satisfy a type quota. "
    "Compatibility includes both semantic value production and physical delivery. Check each "
    "edge's declared port kind, accepted artifact types, produced artifact types, native or semantic "
    "output view, file-versus-value representation, required filename or extension, runtime namespace, "
    "writable roots, and network policy. A prose capability summary, matching filename, or generic "
    "format label cannot replace these typed facts. When the final artifact contract declares a "
    "required produced file, the plan must explicitly select an authorized operation whose declared "
    "side effect materializes compatible content at the declared runtime path. Select it as a typed "
    "DAG step when its declared output remains compatible with the downstream/final value contract, "
    "or as an explicitly bound controller callable when the controller must both produce the payload "
    "and preserve a different final return representation. The selected final operation may cover "
    "this directly only when it explicitly declares the physical delivery behavior. A generated value "
    "alone is not proof that a required file exists. Bind the materializer's destination port to the "
    "declared logical runtime path. Bind its payload port to the exact producer step_output, or—only "
    "for a justified controller whose unresolved responsibility is to produce that payload—declare "
    "the payload as a controller-supplied dynamic port. Never put the producer's instruction text, "
    "an answer guess, or an unrelated literal into that payload port. "
    "Execution lowering is framework-owned: plans must use logical artifact references and typed "
    "ports, never host filesystem paths, shell commands, executable paths, working directories, "
    "or ad-hoc environment values. At lowering time every path must resolve inside the active "
    "execution namespace and every selected resource must have a declared container-compatible "
    "entrypoint, input/output representation, and side-effect contract. A path or value that "
    "cannot be lowered into that namespace is an execution-contract failure; do not repair it by "
    "guessing a host path, reading the host workspace, or falling back to an unsealed executor. "
    "For every obligation a step claims to satisfy, its dependency closure must explicitly consume "
    "every source listed in that obligation's authorized_inputs. authorized_artifact_sources is the "
    "allowed source pool; execution_obligations[*].authorized_inputs is the required source subset "
    "for that obligation. For any "
    "final step, select only a capability operation whose final_step_eligible is true. This field is "
    "authoritative for selection for all resource types. Pending Model and Agent schemas require "
    "bound generic capability at selection and exact selected-schema enforcement after compilation; "
    "selection eligibility alone is not executability. Apply the shared output-fact and native "
    "contract inheritance rules above; retain a source-to-target path accepted by the existing "
    "realizer or authorized explicit processing without changing node requirements. "
    "For Tool and Skill resources eligibility is derived from their declared operation "
    "output and executable runtime contract. "
    "Decision order: (1) identify every obligation, (2) identify its authorized inputs and physical "
    "delivery requirements, (3) match exact operation ports, artifact/file types, and bindings, "
    "(4) confirm value compatibility, task-state delivery, and final-step eligibility, (5) honor "
    "runtime namespace and network constraints, and (6) construct the smallest execution DAG. An "
    "operation with no required material port may satisfy a requirements-only obligation; do "
    "not invent an artifact input. A handle_only compiler visibility does not mean Runtime "
    "cannot deliver material when an authorized delivery binding says it can. Apply the "
    "selection objective above after feasibility checks: deterministic coverage, generation-call "
    "count, exact model unit price, then DAG simplicity distinguish feasible plans. Skills and "
    "Resources must be explicit steps whose actual outputs are bound to consumers. A Model or "
    "Agent controller may be given only explicitly selected callable Tools. When a deterministic "
    "operation can consume a preceding Model/Agent output through a declared typed port, prefer "
    "an explicit static DAG edge over an interactive controller Tool call; use a controller-callable "
    "Tool only when observation, branching, or iterative interaction is itself an unresolved "
    "capability requirement. For each callable "
    "Tool, every required input must be explicitly classified as a sealed fixed mapping or a "
    "controller-supplied dynamic port. Do not assume any unselected Tool or implicit input. "
    "A single subtask plan may contain at most one Model or Agent controller step; deterministic "
    "Tool or Resource steps may precede or follow it. The controller is justified only when its "
    "declared unresolved behavior cannot be represented by a smaller feasible non-controller or "
    "hybrid DAG. This is a general graph-minimization rule, not permission to omit required semantic "
    "work, typed conversions, verification, or physical delivery. "
    "For a sufficient decision, the union of satisfied_obligation_ids must equal the supplied "
    "obligation set. For an insufficient decision, identify every unsatisfied obligation and "
    "prove a concrete missing operation, port, authorized binding, runtime requirement, or "
    "final-step eligibility fact. Never contradict authoritative candidate facts when claiming "
    "insufficiency. Do not emit prose outside JSON or reveal chain-of-thought. "
    "The following synthetic examples demonstrate protocol shape only; their IDs are never "
    "authorized for the current request:\n" + _COMPILER_FEW_SHOT_JSON
)

# Read-only import compatibility; live requests use the v3 content above.
PLAN_COMPILER_SYSTEM_PROMPT_V3 += SCHEMA_NODE_CONSTRAINT_INSTRUCTIONS
PLAN_COMPILER_SYSTEM_PROMPT_V2 = PLAN_COMPILER_SYSTEM_PROMPT_V3
PLAN_COMPILER_PROMPT_SHA256 = canonical_sha256(
    {
        "version": PLAN_COMPILER_PROMPT_VERSION,
        "system_prompt": PLAN_COMPILER_SYSTEM_PROMPT_V3,
        "draft_protocol": "sgar-compiler-decision-v5",
        "request_policy": "sgar-control-role-policy-v2",
    }
)

PLAN_ADAPTATION_PROMPT_VERSION = "executable-plan-adaptation-v21-node-constraint-guidance"
PLAN_ADAPTATION_SYSTEM_PROMPT_V1 = (
    INPUT_ALIGNMENT_RULES + COMPILER_OUTPUT_INSTRUCTIONS + COMPILER_SELECTION_OBJECTIVE + FINAL_CONTRACT_INSTRUCTIONS +
    "Preserve the original task and Planner macro delivery standard. Concrete schemas are "
    "implementation contracts and cannot override explicit task requirements. Never hard-code "
    "unverified answer guesses as const, enum or numeric bounds. "
    "You are the SGAR sealed Plan Adaptation Compiler. Return exactly one strict "
    "sgar-plan-adaptation-decision-v5 object. Copy failure_evidence_sha256 and "
    "previous_plan_sha256 exactly. Keep every checkpointed successful step semantically "
    "unchanged, list it in preserved_completed_step_ids, and modify only the failed frontier. "
    "The nested plan_decision uses the same sgar-compiler-decision-v5 fields and decision order "
    "as initial compilation: select only supplied resource_id and capability_operation_id "
    "values, map typed sources to declared target ports, cover every execution obligation, "
    "and return one complete static acyclic DAG with one final step. Capability operation "
    "records and execution obligations in base_compiler_input are authoritative. Never infer "
    "from business words, filenames, paths, resource IDs, retrieval scores, or failed output. "
    "Write all newly generated intent, rationale, and gap text in English while preserving "
    "literal identifiers exactly. "
    "Do not output executable dispatch metadata, runtime bindings, generated result identities, "
    "Schema strings, host paths, commands, runners, temporary resources, or unsealed replacements; "
    "the framework derives all such "
    "execution fields. Never repeat a rerun_forbidden_call_signature. A Model or Agent controller "
    "may use only explicitly selected callable Tools whose required inputs are partitioned into "
    "sealed fixed mappings and controller-supplied dynamic ports; no Tool or input is implicit. "
    "If the failed frontier cannot be recomposed from the sealed "
    "candidate operations without changing a completed checkpoint, the structured adaptation "
    "must fail validation rather than inventing a fallback. Contrastive examples: if one frontier "
    "step fails while its predecessors are checkpointed, preserve those predecessor checkpoints "
    "and recompose only the failed frontier and its unfinished dependants. If a proposed repair "
    "would modify a completed checkpoint, repeat a forbidden call signature, or use an unauthorized "
    "resource, return a structured insufficient adaptation: a valid terminal no-plan decision, "
    "not a request for another semantic correction. Do not "
    "reveal chain-of-thought."
)
PLAN_ADAPTATION_SYSTEM_PROMPT_V1 += SCHEMA_NODE_CONSTRAINT_INSTRUCTIONS
PLAN_ADAPTATION_SYSTEM_PROMPT_V1 += (
    " accepted_previous_plan is the read-only definition of the actually accepted plan, not "
    "previous_decision (the last rejected proposal). Preserve checkpointed steps using its "
    "exact definitions. concrete_output_contract contains the original description, content_kind "
    "and schema_hint; express that contract using the existing final/intermediate schema_graph "
    "wire, or native inheritance when exactly applicable. Return the complete replacement DAG. "
    "Derive no new authority from this view. Completed checkpoints and recovery_lineage remain "
    "the execution-state and no-replay constraints."
)

PLAN_ADAPTATION_PROMPT_SHA256 = canonical_sha256(
    {
        "version": PLAN_ADAPTATION_PROMPT_VERSION,
        "system_prompt": PLAN_ADAPTATION_SYSTEM_PROMPT_V1,
        "draft_protocol": "sgar-plan-adaptation-decision-v5",
        "request_policy": "sgar-control-role-policy-v2",
    }
)


class PlanCompilerPayloadGuard:
    """Fail-closed, hash-only audit for every actual Compiler send."""

    _SECRET_KEYS = frozenset(
        {
            "api_key",
            "authorization",
            "password",
            "secret",
            "gold",
            "validator_result",
        }
    )

    def __init__(self, public_context: CompilerPublicContext) -> None:
        self.public_context = public_context
        self.provenance_sha256 = canonical_sha256(
            {
                "context_sha256": public_context.context_sha256,
                "source_ids": public_context.provenance_source_ids,
            }
        )
        self._lock = threading.RLock()
        self._checks: list[dict[str, str]] = []

    @staticmethod
    def _assert_no_secret_keys(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).strip().casefold() in PlanCompilerPayloadGuard._SECRET_KEYS:
                    raise PlanCompilerIdentityError("plan_compiler_payload_secret_key")
                PlanCompilerPayloadGuard._assert_no_secret_keys(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                PlanCompilerPayloadGuard._assert_no_secret_keys(child)

    def __call__(self, payload: Mapping[str, Any]) -> None:
        projected = deepcopy(dict(payload))
        _ensure_host_free(projected, field_name="plan_compiler_model_payload")
        self._assert_no_secret_keys(projected)
        messages = projected.get("messages")
        if not isinstance(messages, list) or len(messages) < 2:
            raise PlanCompilerIdentityError("plan_compiler_payload_messages_missing")
        user_content = messages[-1].get("content") if isinstance(messages[-1], Mapping) else None
        try:
            envelope_payload = json.loads(str(user_content))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlanCompilerIdentityError("plan_compiler_payload_envelope_invalid") from exc
        compiler_input = envelope_payload.get("compiler_input")
        adaptation_input = envelope_payload.get("adaptation_input")
        decision_input = (
            compiler_input
            if isinstance(compiler_input, Mapping)
            and compiler_input.get("protocol") == COMPILER_MODEL_INPUT_PROTOCOL
            else None
        )
        if decision_input is None and isinstance(adaptation_input, Mapping):
            candidate = adaptation_input.get("base_compiler_input")
            if (
                isinstance(candidate, Mapping)
                and candidate.get("protocol") == COMPILER_MODEL_INPUT_PROTOCOL
            ):
                decision_input = candidate
        if isinstance(decision_input, Mapping):
            decision_projection = dict(decision_input)
            claimed_projection_sha256 = str(
                decision_projection.pop("projection_sha256", "")
            )
            if claimed_projection_sha256 != canonical_sha256(decision_projection):
                raise PlanCompilerIdentityError(
                    "plan_compiler_payload_projection_hash_mismatch"
                )
            expected_edges = [
                item.model_dump(mode="json")
                for item in self.public_context.incoming_edge_contracts
            ]
            if decision_input.get("incoming_edge_contracts") != expected_edges:
                raise PlanCompilerIdentityError(
                    "plan_compiler_payload_context_projection_mismatch"
                )
            payload_sha256 = canonical_sha256(projected)
            with self._lock:
                self._checks.append(
                    {
                        "payload_sha256": payload_sha256,
                        "provenance_sha256": self.provenance_sha256,
                    }
                )
            return
        context = envelope_payload.get("public_context")
        if not isinstance(context, Mapping) and isinstance(compiler_input, Mapping):
            context = compiler_input.get("public_context")
        if not isinstance(context, Mapping) and isinstance(adaptation_input, Mapping):
            base = adaptation_input.get("base_envelope")
            context = base.get("public_context") if isinstance(base, Mapping) else None
        if not isinstance(context, Mapping):
            base_envelope = envelope_payload.get("base_envelope")
            context = (
                base_envelope.get("public_context")
                if isinstance(base_envelope, Mapping)
                else None
            )
        if not isinstance(context, Mapping):
            raise PlanCompilerIdentityError("plan_compiler_payload_context_missing")
        if context.get("context_sha256") != self.public_context.context_sha256:
            raise PlanCompilerIdentityError("plan_compiler_payload_context_hash_mismatch")
        payload_sha256 = canonical_sha256(projected)
        with self._lock:
            self._checks.append(
                {
                    "payload_sha256": payload_sha256,
                    "provenance_sha256": self.provenance_sha256,
                }
            )

    @property
    def checks(self) -> tuple[dict[str, str], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._checks)


def build_compiler_public_context(
    context_packet: Mapping[str, Any] | Any,
    *,
    allowed_upstream_task_ids: tuple[str, ...] = (),
) -> CompilerPublicContext:
    """Project runtime handles into a content-free, host-free Compiler context."""

    if hasattr(context_packet, "model_dump"):
        raw = context_packet.model_dump(mode="python")
    elif isinstance(context_packet, Mapping):
        raw = dict(context_packet)
    else:
        raise PlanCompilerIdentityError("compiler_context_packet_invalid")
    allowed_upstream = set(str(item) for item in allowed_upstream_task_ids)
    incoming_edges = sorted(
        (
            DagEdgeContractV1.model_validate(item)
            for item in (raw.get("incoming_edge_contracts") or ())
        ),
        key=lambda item: (item.producer_id, item.consumer_id, item.input_slot),
    )
    incoming_semantic_edges_v2 = sorted(
        (
            SemanticEdgeContractV2.model_validate(item)
            for item in (raw.get("incoming_semantic_edges_v2") or ())
        ),
        key=lambda item: (item.producer_id, item.consumer_id, item.producer_output_ref),
    )
    semantic_edge_producers = {
        item.producer_id for item in incoming_semantic_edges_v2
    }
    current_subtask = raw.get("current_subtask") or {}
    v6_context = (
        isinstance(current_subtask, Mapping)
        and str(current_subtask.get("semantic_contract_protocol") or "").strip()
        == "sgar-node-semantic-contract-v3"
    )
    declared_protocol = str(current_subtask.get("semantic_contract_protocol") or "").strip() if isinstance(current_subtask, Mapping) else ""
    if declared_protocol and declared_protocol != "sgar-node-semantic-contract-v3":
        raise PlanCompilerIdentityError("compiler_input_semantic_protocol_unsupported_start_new_run")
    authorized_public_input_refs = {
        str(item).strip()
        for item in (
            current_subtask.get("authorized_public_input_refs") or ()
            if isinstance(current_subtask, Mapping)
            else ()
        )
        if str(item).strip()
    }
    edge_producers = {item.producer_id for item in incoming_edges}
    edges_by_producer = {
        item.producer_id: item
        for item in incoming_edges
    }
    semantic_edges_by_producer = {
        item.producer_id: item
        for item in incoming_semantic_edges_v2
    }
    descriptors: list[CompilerContextDescriptor] = []
    source_ids: set[str] = set()
    for handle in raw.get("artifact_handles") or ():
        item = dict(handle) if isinstance(handle, Mapping) else {}
        handle_id = str(item.get("handle_id") or "").strip()
        host_path = str(item.get("host_path") or "").strip()
        producer_task = str(item.get("producer_task") or "").strip() or None
        if not handle_id or not host_path:
            continue
        if producer_task and producer_task not in allowed_upstream:
            raise PlanCompilerIdentityError("compiler_context_cross_task_handle")
        if (
            producer_task in semantic_edge_producers
            and str(item.get("kind") or "").strip() != "task_final"
        ):
            # A V6 semantic edge names the upstream node's declared output, not
            # its internal step artifacts, aliases, or source overlays.  Keep
            # only the single committed task-final handle in the Compiler
            # context so the semantic producer has one deterministic runtime
            # identity.  Legacy V4/V5 contexts retain their existing behavior.
            continue
        path = Path(host_path)
        if not path.exists():
            raise PlanCompilerIdentityError("compiler_context_handle_path_missing")
        tool_path = str(item.get("tool_path") or "").replace("\\", "/")
        if tool_path and not tool_path.startswith("/app/"):
            tool_path = ""
        logical_path = str(item.get("logical_path") or "").replace("\\", "/")
        logical_locator = tool_path or (
            logical_path if logical_path and not os.path.isabs(logical_path) else f"handle:{handle_id}"
        )
        semantic_ref = str(item.get("logical_name") or "").strip()
        if not semantic_ref and producer_task:
            semantic_ref = producer_task
        if not semantic_ref and logical_path.startswith("inputs/"):
            path_parts = tuple(part for part in logical_path.split("/") if part)
            if len(path_parts) >= 2:
                semantic_ref = path_parts[1]
        if not semantic_ref:
            semantic_ref = logical_locator
        if (
            v6_context
            and str(item.get("kind") or "").strip() == "input_file"
            and semantic_ref not in authorized_public_input_refs
        ):
            continue
        kind = str(item.get("kind") or "authorized").strip() or "authorized"
        if producer_task:
            semantic_edge = semantic_edges_by_producer.get(producer_task)
            output_ref = (
                semantic_edge.producer_output_ref
                if semantic_edge is not None
                else semantic_ref
            )
            source_id = (
                "artifact:node_output:"
                f"{_portable_source_identity_component(producer_task)}:"
                f"{_portable_source_identity_component(output_ref)}"
            )
        elif kind == "input_file":
            source_id = (
                "artifact:public_input:"
                f"{_portable_source_identity_component(semantic_ref)}"
            )
        elif kind == "completed_output":
            source_id = (
                "artifact:completed_output:"
                f"{_portable_source_identity_component(semantic_ref)}"
            )
        else:
            source_id = (
                "artifact:authorized:"
                f"{_portable_source_identity_component(kind)}:"
                f"{_portable_source_identity_component(semantic_ref)}"
            )
        edge = edges_by_producer.get(producer_task or "")
        source_ids.add(source_id)
        declared_artifact_type = str(item.get("artifact_type") or "unknown")
        extension = Path(logical_locator).suffix.lower()
        portable_artifact_type = declared_artifact_type
        if declared_artifact_type.strip().casefold() in {"file", "binary", "unknown"}:
            portable_artifact_type = {
                ".csv": "csv",
                ".json": "json",
                ".md": "markdown",
                ".markdown": "markdown",
                ".txt": "plaintext",
            }.get(extension, declared_artifact_type)
        material_size = path.stat().st_size if path.is_file() else None
        utf8_decodable: bool | None = None
        if material_size is not None and material_size <= 2_000_000:
            try:
                path.read_bytes().decode("utf-8", errors="strict")
                utf8_decodable = True
            except UnicodeDecodeError:
                utf8_decodable = False
        descriptors.append(
            CompilerContextDescriptor(
                handle_id=handle_id,
                semantic_ref=semantic_ref,
                logical_locator=logical_locator,
                artifact_type=portable_artifact_type,
                runtime_path=tool_path or None,
                mime_type=(
                    str(item.get("media_type") or item.get("mime_type") or "").strip()
                    or None
                ),
                extension=extension or None,
                execution_contract=dict((item.get("provenance") or {}).get("execution_contract") or {}),
                schema_hint=((item.get("provenance") or {}).get("execution_contract") or {}).get("json_schema"),
                size=material_size,
                utf8_decodable=utf8_decodable,
                sha256=path_sha256(path),
                provenance_source_id=source_id,
                producer_task=producer_task,
                producer_step=(str(item.get("producer_step") or "").strip() or None),
                current_run=bool(item.get("current_run", False)),
                edge_contract_sha256s=(
                    (edge.edge_contract_sha256,) if edge is not None else ()
                ),
            )
        )
    descriptors.sort(
        key=lambda item: (item.logical_locator, item.sha256, item.provenance_source_id)
    )
    downstream = tuple(
        sorted(
            {
                str(item.get("consumer_id") or "").strip()
                for item in (raw.get("downstream_consumption") or ())
                if isinstance(item, Mapping) and str(item.get("consumer_id") or "").strip()
            }
        )
    )
    if edge_producers != allowed_upstream:
        raise PlanCompilerIdentityError("compiler_context_edge_dependency_mismatch")
    return CompilerPublicContext(
        descriptors=tuple(descriptors),
        downstream_consumers=downstream,
        provenance_source_ids=tuple(sorted(source_ids)),
        incoming_edge_contracts=tuple(incoming_edges),
        incoming_semantic_edges_v2=tuple(incoming_semantic_edges_v2),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _message_sha256(exc: BaseException | str) -> str:
    text = str(exc)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _response_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_component(value: str) -> str:
    # Plan artifacts add graph/revision fields and a semantic-attempt suffix.
    # Keep the variable component compact enough for Windows workspaces while
    # retaining deterministic collision resistance in the truncated form.
    return bounded_path_component(value, fallback="subtask", max_length=18)


def _legacy_safe_component(value: str) -> str:
    """Read-only filename projection used by pre-v3 stored artifacts."""

    return bounded_path_component(value, fallback="subtask")


class PlanCompilerError(RuntimeError):
    pass


class PlanCompilerPersistenceError(PlanCompilerError):
    pass


class PlanCompilerIdentityError(PlanCompilerError):
    pass


class PlanCompilerResponseError(PlanCompilerError):
    pass


class PlanCompilationStore:
    """Append-only trace plus atomic, immutable terminal artifacts."""

    def __init__(self, run_dir: str | Path, *, run_id: str) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.run_id = str(run_id).strip()
        if not self.run_id:
            raise PlanCompilerPersistenceError("plan_compilation_run_id_missing")
        self.artifact_dir = self.run_dir / "plan_compiler"
        self.trace_path = self.run_dir / "trace.jsonl"
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            self.run_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PlanCompilerPersistenceError("plan_compilation_store_unavailable") from exc
        self._lock = threading.RLock()

    def artifact_path(self, revision: PlanRevisionRef) -> Path:
        subtask = revision.subtask_revision
        filename = (
            f"{subtask.graph_revision}_{_safe_component(subtask.subtask_id)}_"
            f"{subtask.subtask_revision}_{revision.plan_revision}.json"
        )
        return self.artifact_dir / filename

    def _legacy_artifact_path(self, revision: PlanRevisionRef) -> Path:
        subtask = revision.subtask_revision
        filename = (
            f"{subtask.graph_revision}_{_legacy_safe_component(subtask.subtask_id)}_"
            f"{subtask.subtask_revision}_{revision.plan_revision}.json"
        )
        return self.artifact_dir / filename

    def semantic_attempts_path(self, revision: PlanRevisionRef) -> Path:
        target = self.artifact_path(revision)
        return target.with_name(target.stem + ".sem.json")

    def _legacy_semantic_attempt_paths(self, revision: PlanRevisionRef) -> tuple[Path, ...]:
        current_artifact = self.artifact_path(revision)
        legacy_artifact = self._legacy_artifact_path(revision)
        return tuple(
            dict.fromkeys(
                (
                    current_artifact.with_name(
                        current_artifact.stem + "_semantic_attempts.json"
                    ),
                    legacy_artifact.with_name(
                        legacy_artifact.stem + "_semantic_attempts.json"
                    ),
                )
            )
        )

    def persist_semantic_attempts(
        self,
        revision: PlanRevisionRef,
        attempts: tuple[Mapping[str, Any], ...],
        *,
        accepted_attempt: int | None,
    ) -> None:
        projection = {
            "protocol": "sgar-plan-compiler-semantic-attempts-v3",
            "run_id": self.run_id,
            "plan_revision_sha256": revision.revision_sha256,
            "attempts": [dict(item) for item in attempts],
            "accepted_attempt": accepted_attempt,
        }
        projection["audit_sha256"] = canonical_sha256(projection)
        _ensure_host_free(projection, field_name="plan_compiler_semantic_attempts")
        target = self.semantic_attempts_path(revision)
        temp = temporary_sibling_path(target)
        serialized = json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise PlanCompilerPersistenceError(
                "plan_compiler_semantic_attempts_persist_failed"
            ) from exc

    def load_semantic_attempts(
        self,
        revision: PlanRevisionRef,
    ) -> dict[str, Any] | None:
        target = self.semantic_attempts_path(revision)
        if not target.exists():
            legacy_target = next(
                (
                    candidate
                    for candidate in self._legacy_semantic_attempt_paths(revision)
                    if candidate.exists()
                ),
                None,
            )
            if legacy_target is None:
                return None
            target = legacy_target
        try:
            projection = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(projection, dict):
                raise ValueError("semantic_attempts_root_invalid")
            if projection.get("protocol") not in {
                "sgar-plan-compiler-semantic-attempts-v2",
                "sgar-plan-compiler-semantic-attempts-v3",
            }:
                raise ValueError("semantic_attempts_protocol_unsupported")
            supplied_hash = projection.get("audit_sha256")
            unhashed = dict(projection)
            unhashed.pop("audit_sha256", None)
            if supplied_hash != canonical_sha256(unhashed):
                raise ValueError("semantic_attempts_audit_hash_mismatch")
            _ensure_host_free(projection, field_name="plan_compiler_semantic_attempts")
            return projection
        except Exception as exc:
            raise PlanCompilerPersistenceError(
                "plan_compiler_semantic_attempts_invalid"
            ) from exc

    def append_event(self, event_type: str, payload: Mapping[str, Any]) -> str:
        event_id = uuid.uuid4().hex
        event = {
            "schema_version": PLAN_COMPILATION_ARTIFACT_PROTOCOL,
            "event_type": str(event_type),
            "event_id": event_id,
            "timestamp_utc": _utc_now(),
            "run_id": self.run_id,
            **dict(payload),
        }
        serialized = canonical_json_bytes(event).decode("utf-8")
        try:
            with self._lock:
                with self.trace_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(serialized)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise PlanCompilerPersistenceError("plan_compilation_event_append_failed") from exc
        return event_id

    def load(self, revision: PlanRevisionRef) -> SealedPlanCompilationArtifact | None:
        target = self.artifact_path(revision)
        if not target.exists():
            legacy_target = self._legacy_artifact_path(revision)
            if not legacy_target.exists():
                return None
            target = legacy_target
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            return SealedPlanCompilationArtifact.model_validate(payload)
        except Exception as exc:
            raise PlanCompilerPersistenceError(
                "plan_compilation_artifact_invalid"
            ) from exc

    def persist(
        self,
        artifact: SealedPlanCompilationArtifact,
    ) -> SealedPlanCompilationArtifact:
        target = self.artifact_path(artifact.plan_revision)
        with self._lock:
            if target.exists():
                existing = self.load(artifact.plan_revision)
                if existing is not None and existing.artifact_sha256 == artifact.artifact_sha256:
                    return existing
                raise PlanCompilerPersistenceError(
                    "plan_compilation_artifact_overwrite_forbidden"
                )
            temp = temporary_sibling_path(target)
            serialized = json.dumps(
                artifact.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            try:
                with temp.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(serialized)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, target)
            except OSError as exc:
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
                raise PlanCompilerPersistenceError(
                    "plan_compilation_artifact_persist_failed"
                ) from exc
        return artifact

    def has_unmatched_started(self, revision_sha256: str) -> bool:
        if not self.trace_path.exists():
            return False
        started = 0
        terminal = 0
        try:
            with self.trace_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("plan_revision_sha256") != revision_sha256:
                        continue
                    event_type = event.get("event_type")
                    if event_type == "plan_compilation_started":
                        started += 1
                    elif event_type in {
                        "plan_sealed",
                        "plan_compilation_failed",
                        "plan_compilation_interrupted",
                    }:
                        terminal += 1
        except Exception as exc:
            raise PlanCompilerPersistenceError("plan_compilation_trace_invalid") from exc
        return started > terminal


def _draft_response_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sgar_executable_plan",
            "strict": True,
            "schema": system_role_schema("plan_compiler"),
        },
    }


def _compiler_step_schema(
    schema: Mapping[str, Any],
    *,
    definition_name: str = "CompilerStepProposalV2",
) -> dict[str, Any]:
    definitions = schema.get("$defs") or schema.get("definitions")
    if not isinstance(definitions, Mapping):
        raise PlanCompilerIdentityError("compiler_response_schema_definitions_missing")
    step_schema = definitions.get(definition_name)
    if not isinstance(step_schema, Mapping):
        raise PlanCompilerIdentityError("compiler_response_step_schema_missing")
    properties = step_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise PlanCompilerIdentityError("compiler_response_step_properties_missing")
    return dict(properties)


def _compiler_input_mapping_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    definitions = schema.get("$defs") or schema.get("definitions")
    if not isinstance(definitions, Mapping):
        raise PlanCompilerIdentityError("compiler_response_schema_definitions_missing")
    mapping_schema = definitions.get("CompilerInputMappingV3")
    if not isinstance(mapping_schema, Mapping):
        raise PlanCompilerIdentityError("compiler_input_mapping_schema_missing")
    properties = mapping_schema.get("properties")
    if not isinstance(properties, Mapping):
        raise PlanCompilerIdentityError("compiler_input_mapping_properties_missing")
    return dict(properties)


def _narrow_string_enum(
    property_schema: dict[str, Any],
    allowed_values: tuple[str, ...],
    *,
    nullable: bool,
) -> None:
    values = list(dict.fromkeys(allowed_values))
    if nullable and not values:
        property_schema.clear()
        property_schema["type"] = "null"
        return
    if nullable:
        alternatives = property_schema.get("anyOf")
        if not isinstance(alternatives, list):
            raise PlanCompilerIdentityError("compiler_nullable_enum_schema_invalid")
        string_schema = next(
            (
                item
                for item in alternatives
                if isinstance(item, dict) and item.get("type") == "string"
            ),
            None,
        )
        if not isinstance(string_schema, dict):
            raise PlanCompilerIdentityError("compiler_nullable_string_schema_missing")
        string_schema["enum"] = values
        return
    property_schema.clear()
    property_schema.update({"type": "string", "enum": values})


def _narrow_array_item_enum(
    property_schema: dict[str, Any],
    allowed_values: tuple[str, ...],
) -> None:
    values = list(dict.fromkeys(allowed_values))
    candidates: list[dict[str, Any]] = []
    if property_schema.get("type") == "array":
        candidates.append(property_schema)
    alternatives = property_schema.get("anyOf")
    if isinstance(alternatives, list):
        candidates.extend(
            item
            for item in alternatives
            if isinstance(item, dict) and item.get("type") == "array"
        )
    array_schema = candidates[0] if candidates else None
    items = array_schema.get("items") if isinstance(array_schema, Mapping) else None
    if not isinstance(items, dict):
        raise PlanCompilerIdentityError("compiler_array_item_schema_missing")
    if values:
        items["enum"] = values
        return
    # JSON Schema requires a non-empty ``enum``.  A null-only item schema keeps
    # every undeclared string out of the provider response; the full local
    # response model still rejects non-string sentinels, so only [] is accepted.
    items.clear()
    items["type"] = "null"


def _portable_source_identity_component(value: str) -> str:
    """Return a path-free stable component without exposing runtime handles."""

    normalized = str(value or "").strip()
    if (
        normalized
        and normalized not in {".", ".."}
        and "/" not in normalized
        and "\\" not in normalized
        and not os.path.isabs(normalized)
    ):
        return normalized
    return f"ref-{canonical_sha256({'semantic_ref': normalized})[:20]}"


def _authorized_artifact_source_projection(
    envelope: PlanCompilerInputEnvelope,
) -> list[dict[str, Any]]:
    """Return the source portion of the sealed V4 delivery contract."""

    sources, _bindings, _contract_sha256 = _material_delivery_contract_projection(
        envelope
    )
    return sources


def _material_delivery_contract_projection(
    envelope: PlanCompilerInputEnvelope,
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, str], list[dict[str, Any]]],
    str,
]:
    """Project only Resolver-proven source-operation-port delivery facts."""

    descriptors_by_source: dict[str, list[CompilerContextDescriptor]] = {}
    for descriptor in envelope.public_context.descriptors:
        descriptors_by_source.setdefault(
            descriptor.provenance_source_id, []
        ).append(descriptor)
    source_records: dict[str, dict[str, Any]] = {}
    available_resolutions: dict[str, list[str]] = {}
    for material in sorted(envelope.materials, key=lambda item: item.source_id):
        matches = descriptors_by_source.get(material.source_id, [])
        if len(matches) != 1:
            raise PlanCompilerIdentityError(
                "compiler_authorized_artifact_source_not_unique"
            )
        descriptor = matches[0]
        if (
            not descriptor.handle_id
            or material.handle_id != descriptor.handle_id
            or material.artifact_type != descriptor.artifact_type
        ):
            raise PlanCompilerIdentityError(
                "compiler_authorized_artifact_source_material_mismatch"
            )
        source_records[material.source_id] = {
            "source_id": descriptor.provenance_source_id,
            "semantic_ref": descriptor.semantic_ref,
            "artifact_type": descriptor.artifact_type,
            "compiler_visibility": material.coverage_status,
            "execution_contract": deepcopy(descriptor.execution_contract),
            "schema_hint": deepcopy(descriptor.schema_hint),
            "content_kind": descriptor.execution_contract.get("content_kind"),
            "consumption_purposes": [edge.purpose for edge in envelope.public_context.incoming_semantic_edges_v2
                if edge.producer_id == descriptor.producer_task],
            "available_delivery_modes": [],
            "delivery_evidence_sha256": "",
            "provenance": {
                "producer_task": descriptor.producer_task,
                "producer_step": descriptor.producer_step,
                "current_run": descriptor.current_run,
                "edge_bound": bool(descriptor.edge_contract_sha256s),
            },
        }
        available_resolutions[material.source_id] = []

    binding_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    material_by_source = {item.source_id: item for item in envelope.materials}
    for card in envelope.candidate_cards:
        for operation in card.capability_operations:
            bindings: list[dict[str, Any]] = []
            for port in operation.input_ports:
                for source_id, material in material_by_source.items():
                    resolution = resolve_material_delivery(
                        material=material,
                        candidate_card=card,
                        operation=operation,
                        target_port=port.name,
                        runtime_capabilities=envelope.runtime_capabilities,
                    )
                    if resolution.status != "available":
                        continue
                    available_resolutions[source_id].append(
                        resolution.evidence_sha256
                    )
                    for delivery_mode in resolution.available_delivery_modes:
                        bindings.append(
                            {
                                "target_port": port.name,
                                "source_id": source_id,
                                "delivery_mode": delivery_mode,
                                "compatibility_evidence_sha256": (
                                    resolution.evidence_sha256
                                ),
                            }
                        )
            binding_index[(card.resource_id, operation.capability_operation_id)] = (
                sorted(
                    bindings,
                    key=lambda item: (
                        item["target_port"],
                        item["source_id"],
                        item["delivery_mode"],
                    ),
                )
            )

    mode_order = ("inline", "artifact_handle")
    projected_sources: list[dict[str, Any]] = []
    for source_id in sorted(source_records):
        record = source_records[source_id]
        source_bindings = [
            binding
            for bindings in binding_index.values()
            for binding in bindings
            if binding["source_id"] == source_id
        ]
        record["available_delivery_modes"] = [
            mode
            for mode in mode_order
            if any(item["delivery_mode"] == mode for item in source_bindings)
        ]
        record["delivery_evidence_sha256"] = canonical_sha256(
            {
                "source_id": source_id,
                "available_resolution_sha256s": sorted(
                    set(available_resolutions[source_id])
                ),
            }
        )
        projected_sources.append(record)

    binding_projection = [
        {
            "resource_id": resource_id,
            "capability_operation_id": operation_id,
            "authorized_artifact_bindings": bindings,
        }
        for (resource_id, operation_id), bindings in sorted(binding_index.items())
    ]
    delivery_contract_sha256 = canonical_sha256(
        {
            "protocol": "sgar-material-delivery-contract-v1",
            "resolver_protocol": "sgar-material-delivery-resolution-v1",
            "compiler_input_sha256": envelope.input_sha256,
            "candidate_pool_sha256": (
                envelope.candidate_pool_snapshot.candidate_pool_sha256
            ),
            "runtime_capabilities_sha256": (
                envelope.runtime_capabilities.capabilities_sha256
            ),
            "authorized_artifact_sources": projected_sources,
            "candidate_operation_bindings": binding_projection,
        }
    )
    return projected_sources, binding_index, delivery_contract_sha256


def build_candidate_constrained_response_requirement(
    envelope: PlanCompilerInputEnvelope,
    *,
    role: Literal["plan_compiler", "plan_adaptation"],
) -> OutputFormatRequirement:
    """Narrow provider-enforced values to one immutable frozen candidate view."""

    schema = deepcopy(system_role_schema(role))
    properties = _compiler_step_schema(
        schema,
        definition_name="CompilerStepDecisionV3",
    )
    invariant_catalog = build_compiler_invariant_catalog(envelope)
    resource_ids = tuple(card.resource_id for card in envelope.candidate_cards)
    tool_entrypoint_ids = tuple(
        str(entrypoint.get("entrypoint_id") or "")
        for entrypoints in invariant_catalog.tool_entrypoints.values()
        for entrypoint in entrypoints
        if entrypoint.get("entrypoint_id")
    )
    agent_model_ids = tuple(
        model_id
        for model_ids in invariant_catalog.agent_base_models.values()
        for model_id in model_ids
    )
    profile_ids = tuple(
        profile_id
        for profiles in invariant_catalog.application_profiles.values()
        for profile_id in profiles
    )
    present_resource_types = set(invariant_catalog.candidate_resource_types.values())
    operation_kinds = tuple(
        operation
        for resource_type in sorted(present_resource_types)
        for operation in invariant_catalog.allowed_operation_kinds.get(resource_type, ())
    )

    _narrow_string_enum(
        properties["resource_id"],
        resource_ids,
        nullable=False,
    )
    _narrow_string_enum(
        properties["agent_base_model_resource_id"],
        agent_model_ids,
        nullable=True,
    )
    capability_operation_ids = tuple(
        operation.capability_operation_id
        for card in envelope.candidate_cards
        for operation in card.capability_operations
    )
    _narrow_string_enum(
        properties["capability_operation_id"],
        capability_operation_ids,
        nullable=False,
    )
    _narrow_array_item_enum(
        properties["satisfied_obligation_ids"],
        tuple(item.obligation_id for item in envelope.execution_obligations),
    )
    authorized_source_ids = tuple(
        item["source_id"] for item in _authorized_artifact_source_projection(envelope)
    )
    _narrow_string_enum(
        _compiler_input_mapping_schema(schema)["source_id"],
        tuple(sorted(set(authorized_source_ids).union(resource_ids))),
        nullable=True,
    )
    return OutputFormatRequirement(
        artifact_type="json",
        structured=True,
        strict_required=True,
        json_schema=schema,
        schema_source=(
            f"system_role.{role}.candidate_pool."
            f"{envelope.candidate_pool_snapshot.candidate_pool_sha256}"
        ),
    )


def _provider_constraint_contract(
    *,
    envelope: PlanCompilerInputEnvelope,
    requirement: OutputFormatRequirement,
    response_mode: str,
    constraint_projection: Mapping[str, Any],
) -> dict[str, Any]:
    wire = requirement.portable_wire_schema
    projection: dict[str, Any] = {
        "protocol": "sgar-compiler-provider-constraint-contract-v1",
        "response_mode": response_mode,
        "candidate_pool_sha256": (
            envelope.candidate_pool_snapshot.candidate_pool_sha256
        ),
        "compiler_input_sha256": envelope.input_sha256,
        "constraint_projection_sha256": constraint_projection.get(
            "projection_sha256"
        ),
        "model_schema_sha256": requirement.schema_sha256,
        "requirement_sha256": requirement.requirement_sha256,
        "wire_schema_sha256": requirement.wire_schema_sha256,
        "native_eligible": bool(wire is not None and wire.native_eligible),
    }
    projection["contract_sha256"] = canonical_sha256(projection)
    return projection


def _target_dispatch_policy(
    *,
    correction_view: CompilerProposalCorrectionViewV1 | None,
    primary_issue: CompilerProposalValidationIssueV1 | None,
    constraint_projection: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return only an authorized candidate's public dispatch policy."""

    if correction_view is None or primary_issue is None:
        return None
    path = tuple(primary_issue.internal_path or primary_issue.path)
    if len(path) < 2 or path[0] != "steps" or not isinstance(path[1], int):
        return None
    steps = correction_view.normalized_structure.get("steps")
    if not isinstance(steps, (list, tuple)) or not 0 <= path[1] < len(steps):
        return None
    step = steps[path[1]]
    if not isinstance(step, Mapping):
        return None
    resource_id = step.get("resource_id")
    policies = constraint_projection.get("dispatch_policies")
    if not isinstance(resource_id, str) or not isinstance(policies, Mapping):
        return None
    policy = policies.get(resource_id)
    if not isinstance(policy, Mapping):
        return None
    return {"resource_id": resource_id, **dict(policy)}


def _selected_step_format_requirement(
    *, step: Any, draft: CompilerPlanDraft, envelope: PlanCompilerInputEnvelope,
    decision: CompilerDecisionProposalV3 | None,
) -> OutputFormatRequirement:
    """Recover existing shape authority; schema validity never certifies source facts."""
    phase = classify_output_schema_phase(envelope.contract_projection)
    final = draft.final_output is not None and draft.final_output.step_id == step.step_id

    def reject(code: str) -> None:
        raise CompilerSchemaProjectionError(
            code, origin="selected_schema_binding", responsibility="research",
            contract_path=f"steps.{step.step_id}.expected_output_contract",
            actual_type="machine_shape_mismatch",
        )

    declaration = None
    if decision is not None:
        matches = [item for item in decision.steps if item.step_id == step.step_id]
        if len(matches) != 1 or matches[0].resource_id != step.resource_id:
            reject("compiler_schema_origin_step_identity_mismatch")
        declaration = matches[0]
        if final != (decision.final_step_id == step.step_id):
            reject("compiler_schema_origin_final_identity_mismatch")
    if declaration is not None and declaration.output_role == "selected_resource":
        card = next(item for item in envelope.candidate_cards if item.resource_id == step.resource_id)
        native = _manifest_output_contract(card, step.entrypoint_id)
        status = _manifest_output_schema_status(card, step.entrypoint_id)
        if status["machine_schema_status"] != "available":
            reject("compiler_resource_native_schema_unavailable")
        if native.get("artifact_type") != step.expected_output_contract.artifact_type:
            reject("compiler_resource_native_artifact_type_mismatch")
        requirement = OutputFormatRequirement.from_json_schema(
            artifact_type="json", json_schema=status["schema"],
            schema_source="resource_native_output_contract",
        )
    elif final and phase == "authoritative_schema":
        requirement = OutputFormatRequirement.from_contract_projection(envelope.contract_projection)
    else:
        # Legacy direct draft callers keep their original requirement identity.
        source = "compiler_generated_step_schema"
        if declaration is not None:
            if declaration.output_role == "intermediate":
                if declaration.intermediate_contract is None:
                    reject("compiler_intermediate_contract_missing")
                source = "compiler_generated_intermediate_contract"
            elif declaration.output_role == "final" and phase == "compiler_pending":
                if decision is None or decision.final_contract is None:
                    reject("compiler_generated_final_contract_missing")
                source = "compiler_generated_final_contract"
            else:
                reject("compiler_schema_origin_unresolved")
        requirement = OutputFormatRequirement.from_json_schema(
            artifact_type=step.expected_output_contract.artifact_type,
            json_schema=step.expected_output_contract.schema_hint, schema_source=source,
        )
    if requirement.artifact_type != step.expected_output_contract.artifact_type:
        reject("compiler_authoritative_artifact_type_mismatch")
    if requirement.schema_sha256 != canonical_sha256(step.expected_output_contract.schema_hint):
        reject("compiler_authoritative_schema_mismatch")
    # A native final producer must also satisfy the request's independent exact shape.
    if final and phase == "authoritative_schema":
        authoritative = OutputFormatRequirement.from_contract_projection(envelope.contract_projection)
        if (authoritative.artifact_type != requirement.artifact_type
            or authoritative.schema_sha256 != requirement.schema_sha256):
            reject("compiler_authoritative_final_schema_mismatch")
    return requirement


def _build_compiler_semantic_correction(
    *,
    failure: PlanCompilationFailure,
    primary_issue: CompilerProposalValidationIssueV1 | None,
    previous_response_sha256: str | None,
    previous_projected_draft_sha256: str | None,
    previous_proposal: Mapping[str, Any],
    normalization_audit: CompilerProposalNormalizationAuditV1 | None,
    constraint_projection: Mapping[str, Any],
    feasibility_audit: Any,
    envelope: PlanCompilerInputEnvelope,
    correction_view: CompilerProposalCorrectionViewV1 | None = None,
    adaptation: bool = False,
    secret_values: Sequence[str] = (),
    host_roots: Sequence[str] = (),
) -> dict[str, Any]:
    """Preserve the existing correction surface and explain typed projection rejections."""

    correction = {
        "failure_layer": failure.failure_layer,
        "authority_source": primary_issue.authority_source if primary_issue else "",
        "responsibility_stage": primary_issue.responsibility_stage if primary_issue else failure.failure_stage,
        "failure_code": failure.failure_code,
        "invariant_id": (
            primary_issue.invariant_id
            if primary_issue is not None
            else invariant_id_for_failure_code(failure.failure_code)
        ),
        "path": (
            list(primary_issue.path)
            if primary_issue is not None
            else [failure.failure_stage]
        ),
        "expected_active_fields": (
            list(primary_issue.expected_active_fields)
            if primary_issue is not None
            else []
        ),
        "observed_value": primary_issue.observed_value if primary_issue else None,
        "observed_active_fields": (
            list(primary_issue.observed_active_fields)
            if primary_issue is not None
            else []
        ),
        "previous_response_sha256": previous_response_sha256,
        "previous_projected_draft_sha256": previous_projected_draft_sha256,
        "previous_proposal_sha256": canonical_sha256(
            previous_proposal
        ),
        "normalization_audit_sha256": (
            normalization_audit.audit_sha256
            if normalization_audit is not None
            else None
        ),
        "correction_view": correction_view.model_dump(mode="json") if correction_view else None,
        "internal_path": list(primary_issue.internal_path) if primary_issue else [],
        "step_id": primary_issue.step_id if primary_issue else None,
        "output_reachability": primary_issue.output_reachability if primary_issue else None,
        "selected_dispatch_policy": _target_dispatch_policy(
            correction_view=correction_view,
            primary_issue=primary_issue,
            constraint_projection=constraint_projection,
        ),
        "candidate_pool_feasibility": {
            "protocol": feasibility_audit.protocol,
            "potentially_feasible": (
                feasibility_audit.potentially_feasible
            ),
            "audit_sha256": feasibility_audit.audit_sha256,
            "obligation_evidence": [
                item.model_dump(mode="json")
                for item in feasibility_audit.obligation_evidence
            ],
        },
    }

    if failure.failure_code in {
        "compiler_authoritative_schema_mismatch", "compiler_authoritative_final_schema_mismatch",
        "compiler_authoritative_artifact_type_mismatch", "compiler_resource_native_artifact_type_mismatch",
        "compiler_resource_native_schema_unavailable", "compiler_generated_step_schema_invalid",
    }:
        correction["schema_correction"] = (
            "Revise generated representation to satisfy the exact request/resource contract; "
            "do not change authoritative request/resource facts. Shape validation does not "
            "prove source grounding or business quality."
        )
    if (adaptation and correction["path"] and correction["path"][0] != "plan_decision"
            and not (primary_issue and primary_issue.authority_source == "PlanAdaptationDecisionV3")):
        correction["path"] = ["plan_decision", *correction["path"]]
    safe_decision, redactions = None, ()
    decision_status = "unavailable"
    if previous_proposal:
        try:
            safe_decision, redactions = compiler_attempt_diagnostics.sanitize_diagnostic_value(
                previous_proposal, secret_values=secret_values, host_roots=host_roots,
            )
            _ensure_host_free(safe_decision, field_name="previous_compiler_decision")
            decision_status = "redacted" if redactions else "available"
        except Exception:
            safe_decision = None
    correction["previous_decision"] = {
        "status": decision_status,
        "response_kind": "adaptation" if adaptation else "initial",
        "value": safe_decision,
        "redactions": list(redactions),
        "unavailable_reason": (
            "no_parsed_decision" if not previous_proposal else "safe_decision_unavailable"
        ) if safe_decision is None else None,
    }
    correction["revision_permissions"] = (
        "path/response_path locate the error, not a modification whitelist. Return a complete "
        "replacement adaptation response. Recompose only the failed frontier within the same "
        "frozen candidates and authorized inputs; preserve completed/checkpointed steps, "
        "failure_evidence_sha256, previous_plan_sha256 and recovery lineage. Do not replay "
        "completed or rerun-forbidden side effects."
        if adaptation else
        "path/response_path locate the error, not a modification whitelist. Return a complete "
        "replacement decision. Within the same frozen candidates, authorized inputs and node "
        "requirements, revise steps, resources, bindings, output representation or final_step_id "
        "as needed. Changing prose or Schema identifiers does not perform output processing."
    )
    if primary_issue and primary_issue.output_reachability:
        correction["output_reachability"] = {
            **deepcopy(primary_issue.output_reachability),
            "next_actions": [
                "Repair the rejected source-to-target realization using the source, target, "
                "reason_codes and conversion prerequisites below. Changing only prose, Schema "
                "identifiers or evidence references while preserving that relationship will not "
                "resolve the rejection. Within node requirements and revision_permissions, "
                "choose a supported representation/conversion or necessary authorized processing; "
                "changing resources or adding steps is not mandatory. Return a complete replacement.",
                *primary_issue.output_reachability.get("next_actions", []),
            ],
        }
        correction["correction_view"] = {"step_id": primary_issue.step_id,
            "response_path": correction["path"], "proposal_sha256": correction["previous_proposal_sha256"]}
        correction["candidate_pool_feasibility"] = {
            "potentially_feasible": feasibility_audit.potentially_feasible,
            "candidate_facts_ref": "adaptation_input.base_compiler_input.candidate_cards" if adaptation else "compiler_input.candidate_cards",
        }
    return correction


def _share_operation_output_facts(card: dict[str, Any]) -> dict[str, Any]:
    """Share an identical complete pair only within this model-visible card."""

    operations = card["capability_operations"]
    fields = ("manifest_output_schema", "output_capabilities")
    if len(operations) <= 1 or any(
        field not in operation for operation in operations for field in fields
    ):
        return card
    facts = {field: operations[0][field] for field in fields}
    # Compare JSON values, not hashes or Python equality (True == 1).
    serialized = canonical_json_bytes(facts)
    if any(
        canonical_json_bytes({field: operation[field] for field in fields}) != serialized
        for operation in operations[1:]
    ):
        return card
    return {
        **card,
        "shared_operation_output_facts": deepcopy(facts),
        "capability_operations": [
            {key: value for key, value in operation.items() if key not in fields}
            for operation in operations
        ],
    }


def _compiler_model_input_projection(
    envelope: PlanCompilerInputEnvelope,
) -> dict[str, Any]:
    """Expose each decision fact once and omit framework-owned encodings."""

    from .executable_plan import planner_final_content_kind
    from .compiler_output_facts import compiler_output_capabilities, CONVERSION_REQUIREMENTS
    from .executable_plan import _manifest_output_contract
    final_content_kind = planner_final_content_kind(envelope.contract_projection)
    semantic_v2 = envelope.contract_projection.semantic_contract_v2
    requirement = (
        None
        if semantic_v2 is not None
        else OutputFormatRequirement.from_contract_projection(
            envelope.contract_projection
        )
    )
    (
        authorized_artifact_sources,
        binding_index,
        delivery_contract_sha256,
    ) = _material_delivery_contract_projection(envelope)
    final_artifact_type = envelope.contract_projection.artifact_type
    schema_generation_required = bool(
        semantic_v2 is not None
        and final_artifact_type == "json"
        and envelope.contract_projection.json_schema is None
    )
    cards_by_id = {card.resource_id: card for card in envelope.candidate_cards}
    final_step_eligibility = {
        (card.resource_id, operation.capability_operation_id): resolve_final_step_eligibility(
            card,
            operation,
            final_artifact_type,
            schema_generation_required,
            envelope.runtime_capabilities,
            phase="selection",
            schema_phase=classify_output_schema_phase(envelope.contract_projection),
            candidate_cards=cards_by_id,
        )
        for card in envelope.candidate_cards
        for operation in card.capability_operations
    }
    from .input_alignment import requirement_catalog, validate_authoritative_input_requirements
    validate_authoritative_input_requirements(envelope)
    projection: dict[str, Any] = {
        "protocol": COMPILER_MODEL_INPUT_PROTOCOL,
        "selection_objective": {
            "protocol": "sgar-compiler-selection-objective-v1",
            "priority_order": [
                "feasibility_and_contract_compatibility",
                "typed_task_state_delivery_coverage",
                "deterministic_tool_or_resource_coverage",
                "minimum_generative_model_call_count",
                "minimum_exact_model_unit_cost",
                "minimum_dag_complexity",
            ],
            "deterministic_workflow_preferred": True,
            "agent_loop_requires_no_simpler_feasible_composition": True,
            "physical_delivery_must_be_explicitly_covered": True,
            "retrieval_rank_is_not_a_preference": True,
            "unknown_non_model_cost_is_not_zero": True,
            "model_cost_fields": ["input_per_m", "cache_per_m", "output_per_m"],
        },
        "requirement_catalog": requirement_catalog(envelope),
        "output_conversion_rules": CONVERSION_REQUIREMENTS,
        "compiler_policy": envelope.compiler_policy.model_dump(mode="json"),
        "plan_revision": envelope.plan_revision.model_dump(mode="json"),
        "candidate_pool_sha256": (
            envelope.candidate_pool_snapshot.candidate_pool_sha256
        ),
        "final_artifact_contract": {
            **({"contract_scope": semantic_v2.output.contract_scope} if semantic_v2 is not None else {}),
            "content_kind": final_content_kind,
            "artifact_type": final_artifact_type,
            "output_extension": envelope.contract_projection.output_extension,
            "expected_output": envelope.contract_projection.expected_output,
            "required_content": list(envelope.contract_projection.required_content),
            "produced_files": [
                item.model_dump(mode="json")
                for item in envelope.contract_projection.produced_files
            ],
            "schema_sha256": (
                requirement.schema_sha256
                if requirement is not None
                else (
                    canonical_sha256(envelope.contract_projection.json_schema)
                    if envelope.contract_projection.json_schema is not None
                    else None
                )
            ),
            "schema_generation_required": schema_generation_required,
            "interface_contract": envelope.contract_projection.interface_contract,
        },
        "execution_obligations": [
            item.model_dump(
                mode="json",
                exclude={"output_contract_sha256"},
            )
            for item in envelope.execution_obligations
        ],
        "execution_requirements": [
            item.model_dump(mode="json")
            for item in envelope.execution_requirements
        ],
        "authorized_artifact_sources": authorized_artifact_sources,
        "delivery_contract_sha256": delivery_contract_sha256,
        "incoming_edge_contracts": [
            item.model_dump(mode="json")
            for item in envelope.public_context.incoming_edge_contracts
        ],
        "candidate_cards": [
            {
                "resource_id": card.resource_id,
                "resource_type": card.resource_type,
                "availability_status": card.availability_status,
                "compatibility_status": card.compatibility_status,
                **({"advisory_output_description": deepcopy(card.advisory_output_description)}
                   if card.advisory_output_description is not None else {}),
                "semantic_summary": card.semantic_summary,
                "semantic_summary_status": card.semantic_summary_status,
                "semantic_limitations": list(card.semantic_limitations),
                "has_final_step_eligible_operation": any(
                    final_step_eligibility[
                        (card.resource_id, item.capability_operation_id)
                    ]
                    for item in card.capability_operations
                ),
                "capability_operations": [
                    {
                        **item.model_dump(
                            mode="json",
                            exclude={"execution_operation_kind", "entrypoint_id"},
                        ),
                        "final_step_eligible": final_step_eligibility[
                            (card.resource_id, item.capability_operation_id)
                        ],
                        "manifest_output_schema": _manifest_output_schema_status(card, item.entrypoint_id),
                        "output_capabilities": compiler_output_capabilities(_manifest_output_contract(card, item.entrypoint_id), card.resource_type),
                        "authorized_artifact_bindings": binding_index[
                            (card.resource_id, item.capability_operation_id)
                        ],
                    }
                    for item in card.capability_operations
                ],
                "base_input_contract": list(card.base_input_contract),
                "base_output_semantics": {
                    key: value
                    for key, value in card.base_output_contract.items()
                    if key not in {"schema", "schema_hint", "json_schema"}
                },
                "agent_base_model_candidates": list(
                    card.agent_base_model_candidates
                ),
                "structured_output_capability": card.runtime_requirements.get("sgar_structured_output_capability"),
                "backing_model_capabilities": {
                    model_id: cards_by_id[model_id].runtime_requirements.get("sgar_structured_output_capability")
                    for model_id in card.agent_base_model_candidates if model_id in cards_by_id
                },
                "model_pricing": (
                    card.model_pricing.model_dump(mode="json")
                    if card.model_pricing is not None
                    else None
                ),
                "selection_facts": {
                    "deterministic_operation_ids": [
                        item.capability_operation_id
                        for item in card.capability_operations
                        if item.determinism == "deterministic"
                    ],
                    "nondeterministic_operation_ids": [
                        item.capability_operation_id
                        for item in card.capability_operations
                        if item.determinism == "nondeterministic"
                    ],
                    "unknown_determinism_operation_ids": [
                        item.capability_operation_id
                        for item in card.capability_operations
                        if item.determinism == "unknown"
                    ],
                },
            }
            for card in envelope.candidate_cards
        ],
        "runtime_capabilities": envelope.runtime_capabilities.model_dump(mode="json"),
    }
    projection["candidate_cards"] = [
        _share_operation_output_facts(card) for card in projection["candidate_cards"]
    ]
    projection["model_cost_comparison"] = [
        {
            "resource_id": card.model_pricing.resource_id,
            "api_model_id": card.model_pricing.api_model_id,
            "input_per_m": card.model_pricing.input_per_m,
            "cache_per_m": card.model_pricing.cache_per_m,
            "output_per_m": card.model_pricing.output_per_m,
            "pricing_unit": card.model_pricing.pricing_unit,
            "generation_call_cost_is_comparable_only_by_usage": True,
        }
        for card in envelope.candidate_cards
        if card.resource_type == "Model" and card.model_pricing is not None
    ]
    projection["projection_sha256"] = canonical_sha256(projection)
    _ensure_host_free(projection, field_name="compiler_model_input_projection")
    return projection


def _compiler_ingress_field_audit(content: str, *, adaptation: bool = False) -> dict[str, Any]:
    """Observe critical field presence before optional-null normalization."""
    from .model_response_contracts import _decode_unique_json_object
    from .compiler_attempt_diagnostics import sanitize_diagnostic_value
    raw, _ = _decode_unique_json_object(content)
    decision = raw.get("plan_decision", {}) if adaptation and isinstance(raw, Mapping) else raw
    observations: list[dict[str, Any]] = []
    issues: list[CompilerProposalValidationIssueV1] = []
    if isinstance(decision, Mapping):
        for field in ("input_assessment", "constraint_basis"):
            state = "missing" if field not in decision else "null" if decision[field] is None else "declared"
            observations.append({"path": ("plan_decision." if adaptation else "") + field, "state": state})
        contracts = []
        if isinstance(decision.get("final_contract"), Mapping):
            contracts.append(("final_contract", decision["final_contract"], True))
        steps = decision.get("steps")
        for i, step in enumerate(steps if isinstance(steps, list) else []):
            if isinstance(step, Mapping) and step.get("output_role") == "intermediate" and not isinstance(step.get("intermediate_contract"), Mapping):
                state = "missing" if "intermediate_contract" not in step else "null" if step["intermediate_contract"] is None else "invalid"
                path = (["plan_decision"] if adaptation else []) + ["steps", str(i), "intermediate_contract"]
                observations.append({"path": ".".join(path), "state": state})
                issues.append(CompilerProposalValidationIssueV1(
                    invariant_id="compiler_intermediate_contract_required", failure_code="compiler_intermediate_contract_required",
                    failure_layer="protocol", path=tuple(path), expected_active_fields=("explicit intermediate contract with content_kind",),
                    observed_active_fields=(state,), authority_source="CompilerIntermediateContractV4", responsibility_stage="plan_compiler_ingress"))
            if isinstance(step, Mapping) and isinstance(step.get("intermediate_contract"), Mapping):
                contracts.append((f"steps.{i}.intermediate_contract", step["intermediate_contract"], False))
        for path, contract, final in contracts:
            value = contract.get("content_kind")
            state = "missing" if "content_kind" not in contract else "null" if value is None else "declared" if value in ("value", "json_schema_document") else "invalid"
            sanitized_value, _ = sanitize_diagnostic_value(value)
            observed_value = str(sanitized_value)[:120] if "content_kind" in contract else None
            observations.append({"path": path + ".content_kind", "state": state, "value": observed_value})
            if (final and state != "missing") or (not final and state != "declared"):
                code = "compiler_final_content_kind_framework_owned" if final else "compiler_intermediate_content_kind_required"
                issues.append(CompilerProposalValidationIssueV1(
                    invariant_id=code, failure_code=code, failure_layer="protocol",
                    path=tuple((["plan_decision"] if adaptation else []) + path.split(".") + ["content_kind"]),
                    expected_active_fields=("omit_framework_owned_field",) if final else ("value", "json_schema_document"),
                    observed_active_fields=(state,), observed_value=observed_value, authority_source="Planner.output" if final else "CompilerIntermediateContractV4",
                    responsibility_stage="plan_compiler_ingress",
                ))
    return {"fields": observations, "issues": issues}


def _normalize_compiler_schema_graph_defaults(value: Any) -> tuple[Any, Sequence[str]]:
    """Discard retired metadata and fill declared mechanical Schema defaults."""

    if not isinstance(value, Mapping):
        return value, ()
    normalized = deepcopy(dict(value))
    if isinstance(normalized.get("plan_decision"), Mapping):
        nested, actions = _normalize_compiler_schema_graph_defaults(normalized["plan_decision"])
        normalized["plan_decision"] = nested
        return normalized, tuple("plan_decision." + action for action in actions)
    actions: list[str] = []
    if normalized.get("constraint_basis") != []:
        normalized["constraint_basis"] = []
        actions.append("constraint_basis:discarded_retired_metadata")

    contracts: list[tuple[str, dict[str, Any]]] = []
    final_contract = normalized.get("final_contract")
    if isinstance(final_contract, dict):
        contracts.append(("final_contract", final_contract))
    for index, step in enumerate(normalized.get("steps") or ()):
        intermediate = step.get("intermediate_contract") if isinstance(step, dict) else None
        if isinstance(intermediate, dict):
            contracts.append((f"steps[{index}].intermediate_contract", intermediate))
    for path, contract in contracts:
        graph = contract.get("schema_graph")
        if graph is None:
            continue
        projected = PlannerSchemaGraphWireV1.model_validate(graph).model_dump(mode="json")
        if projected != graph:
            contract["schema_graph"] = projected
            actions.append(f"{path}.schema_graph:mechanical_defaults")
    return normalized, tuple(actions)


def build_plan_compiler_call_kwargs(
    envelope: PlanCompilerInputEnvelope,
    *,
    response_mode: StructuredResponseModeInput,
    correction: Mapping[str, Any] | None = None,
    role_policy: ControlRoleInvocationPolicyV1 | None = None,
) -> dict[str, Any]:
    system_prompt = PLAN_COMPILER_SYSTEM_PROMPT_V2
    selected_mode = normalize_structured_response_mode(response_mode)
    invariant_catalog = build_compiler_invariant_catalog(envelope)
    constraint_projection = compiler_constraint_prompt_projection(
        invariant_catalog,
        candidate_pool_sha256=(
            envelope.candidate_pool_snapshot.candidate_pool_sha256
        ),
    )
    response_requirement = build_candidate_constrained_response_requirement(
        envelope,
        role="plan_compiler",
    )
    role_contract = build_structured_role_contract(
        "plan_compiler",
        mode=selected_mode,
        projector_version="compiler-plan-projector-v2",
        request_dynamic_invariant=initial_compiler_model_invariant_projection(
            invariant_catalog
        ),
        domain_validator_dynamic_invariant=compiler_invariant_validator_projection(
            invariant_catalog
        ),
    )
    prompt_payload = {
        "compiler_input": _compiler_model_input_projection(envelope),
        "structured_role_contract": _compiler_prompt_contract_projection(
            role_contract,
            selected_mode=selected_mode,
        ),
        "provider_constraint_contract": _provider_constraint_contract(
            envelope=envelope,
            requirement=response_requirement,
            response_mode=selected_mode,
            constraint_projection=constraint_projection,
        ),
        "semantic_correction": (
            project_initial_compiler_correction_v3(correction)
            if correction is not None
            else None
        ),
        "control_role_policy": (
            {
                "protocol": "sgar-control-role-policy-v2",
                "role": role_policy.role,
                "role_policy_sha256": role_policy.role_policy_sha256,
                "reasoning_effort": role_policy.reasoning_effort,
                "allow_model_failover": role_policy.allow_model_failover,
            }
            if role_policy is not None
            else None
        ),
    }
    if role_policy is not None:
        if role_policy.role != "plan_compiler":
            raise PlanCompilerIdentityError("compiler_control_role_policy_role_mismatch")
        if role_policy.api_model_id != envelope.compiler_model_api_id:
            raise PlanCompilerIdentityError("compiler_control_role_policy_model_mismatch")
        if selected_mode != role_policy.response_mode:
            raise PlanCompilerIdentityError("compiler_control_role_policy_response_mode_mismatch")
    kwargs: dict[str, Any] = {
        "model": envelope.compiler_model_api_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": canonical_json_bytes(prompt_payload).decode("utf-8"),
            },
        ],
    }
    if role_policy is not None:
        kwargs.update(role_policy.request_fields())
    kwargs["response_format"] = structured_response_format(
        response_requirement,
        mode=selected_mode,
    )
    return kwargs


def _adaptation_response_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sgar_plan_adaptation",
            "strict": True,
            "schema": system_role_schema("plan_adaptation"),
        },
    }


def _accepted_previous_plan_projection(envelope: PlanAdaptationInputEnvelope) -> dict[str, Any]:
    """Accepted decisions only; derived runtime metadata remains framework-owned."""
    from .binding_protocol import parse_binding_source
    from .executable_plan import CompilerLiteralValueV3

    try:
        base, plan = envelope.base_envelope, envelope.previous_plan
        cards = {card.resource_id: card for card in base.candidate_cards}
        authorized = {item["source_id"] for item in _authorized_artifact_source_projection(base)}
        handles: dict[str, list[str]] = {}
        for material in base.materials:
            if material.source_id in authorized and material.handle_id:
                handles.setdefault(material.handle_id, []).append(material.source_id)
        step_outputs = {step.step_id: step.output_key for step in plan.steps}

        def literal(value: Any) -> dict[str, Any]:
            kind = ("boolean" if type(value) is bool else "integer" if type(value) is int
                    else "number" if type(value) is float else "string" if isinstance(value, str)
                    else "string_list" if isinstance(value, list) and all(isinstance(x, str) for x in value)
                    else None)
            if kind is None:
                raise ValueError("literal_not_expressible")
            return CompilerLiteralValueV3.model_validate({"value_type": kind, kind + "_value": value}).model_dump(mode="json")

        def mappings(bindings: Mapping[str, Any], contexts: Sequence[Any] = ()) -> list[dict[str, Any]]:
            result = []
            for port, raw in bindings.items():
                for item in (raw if isinstance(raw, list) else [raw]):
                    binding = parse_binding_source(item)
                    row = {"target_port": port, "source_kind": binding.variant,
                           "source_id": None, "from_step": None, "literal_value": None}
                    if binding.variant == "literal":
                        row["literal_value"] = literal(binding.value)
                    elif binding.variant == "artifact_handle":
                        matches = handles.get(str(binding.value), [])
                        if len(matches) != 1:
                            raise ValueError("authorized_handle_mapping_missing_or_ambiguous")
                        row["source_id"] = matches[0]
                    elif binding.variant == "resource":
                        if binding.value not in cards:
                            raise ValueError("resource_not_authorized")
                        row["source_id"] = binding.value
                    elif binding.variant == "step_output":
                        if (binding.from_step not in step_outputs
                            or binding.output_key != step_outputs[binding.from_step]):
                            raise ValueError("step_output_mapping_invalid")
                        row["from_step"] = binding.from_step
                    else:
                        raise ValueError("binding_not_expressible")
                    result.append(row)
            for binding in contexts:
                if binding.source_id not in authorized or binding.source_id not in handles.get(binding.handle_id, []):
                    raise ValueError("context_source_not_authorized")
                result.append({"target_port": binding.target_port, "source_kind": "artifact_handle",
                               "source_id": binding.source_id, "from_step": None, "literal_value": None})
            return result

        steps, callable_tools = [], []
        for step in plan.steps:
            card = cards.get(step.resource_id)
            operation = next((op for op in card.capability_operations
                              if op.capability_operation_id == step.capability_operation_id), None) if card else None
            if operation is None or operation.entrypoint_id != step.entrypoint_id:
                raise ValueError("accepted_operation_not_available")
            if step.output_key != step.step_id + "_output":
                raise ValueError("accepted_step_not_expressible_in_current_wire")
            if set(step.consumed_context_source_ids) != {b.source_id for b in step.context_bindings}:
                raise ValueError("accepted_context_binding_incomplete")
            steps.append({
                "step_id": step.step_id, "resource_id": step.resource_id,
                "capability_operation_id": step.capability_operation_id,
                "entrypoint_id": step.entrypoint_id, "intent": step.intent,
                "depends_on": list(step.depends_on),
                "input_mappings": mappings(step.input_bindings, step.context_bindings),
                "satisfied_obligation_ids": list(step.satisfied_obligation_ids),
                "capability_evidence_refs": [operation.capability_operation_id],
                "output_key": step.output_key,
                "output_role": "final" if step.step_id == plan.final_output.step_id else "intermediate",
                "concrete_output_contract": step.expected_output_contract.model_dump(mode="json"),
                "agent_base_model_resource_id": step.agent_base_model_resource_id,
                "advisory_profile_refs": list(step.advisory_profile_refs),
            })
            for tool in getattr(step.controller_session_spec, "callable_tools", ()):
                callable_tools.append({
                    "resource_id": tool.resource_id, "capability_operation_id": tool.capability_operation_id,
                    "capability_evidence_refs": list(tool.capability_evidence_refs),
                    "fixed_input_mappings": mappings(tool.fixed_input_bindings),
                    "dynamic_input_ports": [port["name"] for port in tool.dynamic_input_ports],
                })
        result = {"plan_sha256": plan.plan_sha256, "steps": steps,
                  "final_output": plan.final_output.model_dump(mode="json"),
                  "controller_callable_tools": callable_tools}
        assert_recovery_projection_safe(result)
        return result
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise PlanCompilerIdentityError("plan_adaptation_accepted_plan_input_unrepresentable:" + str(exc)) from exc



def _checkpoint_modification_issue(
    adaptation_input: PlanAdaptationInputEnvelope, adapted_plan: ExecutablePlan,
) -> CompilerProposalValidationIssueV1:
    """Explain an already rejected checkpoint using only existing safe decision views.

    This is diagnostic projection, never an alternative checkpoint comparison.
    Schema paths describe concrete facts; response paths point at the model's
    graph field rather than pretending a compiled $defs path is response wire.
    """
    from .recovery_control import executable_step_semantic_sha256

    previous = _accepted_previous_plan_projection(adaptation_input)
    returned = _accepted_previous_plan_projection(
        adaptation_input.model_copy(update={"previous_plan": adapted_plan})
    )
    actual_steps = {step.step_id: step for step in adapted_plan.steps}
    checkpoint = next(cp for cp in adaptation_input.checkpoints
                      if cp.step_id in actual_steps and
                      executable_step_semantic_sha256(actual_steps[cp.step_id]) != cp.step_semantic_sha256)
    old_index, old = next((i, row) for i, row in enumerate(previous["steps"])
                          if row["step_id"] == checkpoint.step_id)
    index, new = next((i, row) for i, row in enumerate(returned["steps"])
                      if row["step_id"] == checkpoint.step_id)
    missing = object()
    differences = []

    def visit(left: Any, right: Any, path: tuple[str | int, ...] = ()) -> None:
        if left is not missing and right is not missing and canonical_json_bytes(left) == canonical_json_bytes(right):
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                visit(left.get(key, missing), right.get(key, missing), (*path, key))
        elif isinstance(left, list) and isinstance(right, list):
            for i in range(max(len(left), len(right))):
                visit(left[i] if i < len(left) else missing,
                      right[i] if i < len(right) else missing, (*path, i))
        else:
            def value(item: Any) -> dict[str, Any]:
                return {"state": "missing"} if item is missing else {"state": "present", "value": item}
            response_path = ("steps", index)
            if path and path[0] == "concrete_output_contract":
                response_path = (("final_contract",) if new["output_role"] == "final"
                                 else (*response_path, "intermediate_contract"))
                if len(path) > 1:
                    response_path += ("schema_graph",) if path[1] == "schema_hint" else path[1:]
            elif path and path[0] not in {"entrypoint_id", "output_key"}:
                response_path += path
            differences.append({"accepted_path": ["steps", old_index, *path],
                                "response_path": ["plan_decision", *response_path],
                                "expected": value(left), "observed": value(right)})

    visit(old, new)
    return CompilerProposalValidationIssueV1(
        invariant_id="adaptation_checkpoint_modified", failure_code="adaptation_checkpoint_modified",
        failure_layer="connection", responsibility_stage="plan_adaptation_validation",
        authority_source="adaptation_input.accepted_previous_plan + completed_checkpoints",
        step_id=checkpoint.step_id, path=("steps", index), internal_path=("steps", index),
        expected_active_fields=(
            "Restore this completed step exactly from accepted_previous_plan; only the unfinished frontier may be revised. "
            "Even validation-equivalent extra constraints change a completed step. A missing Schema keyword must remain "
            "absent in the compiled Schema; use the corresponding schema_graph field's unset/null form. "
            "Do not copy compiled $defs paths into the response or change checkpoint hashes.",
            canonical_json_bytes([{k: v for k, v in row.items() if k != "observed"}
                                  for row in differences]).decode("utf-8"),
        ),
        observed_value=canonical_json_bytes([{k: v for k, v in row.items() if k != "expected"}
                                              for row in differences]).decode("utf-8"),
    )


def build_plan_adaptation_call_kwargs(
    envelope: PlanAdaptationInputEnvelope,
    *,
    response_mode: StructuredResponseModeInput,
    correction: Mapping[str, Any] | None = None,
    role_policy: ControlRoleInvocationPolicyV1 | None = None,
) -> dict[str, Any]:
    system_prompt = PLAN_ADAPTATION_SYSTEM_PROMPT_V1
    selected_mode = normalize_structured_response_mode(response_mode)
    invariant_catalog = build_compiler_invariant_catalog(envelope.base_envelope)
    constraint_projection = compiler_constraint_prompt_projection(
        invariant_catalog,
        candidate_pool_sha256=(
            envelope.base_envelope.candidate_pool_snapshot.candidate_pool_sha256
        ),
    )
    response_requirement = build_candidate_constrained_response_requirement(
        envelope.base_envelope,
        role="plan_adaptation",
    )
    role_contract = build_structured_role_contract(
        "plan_adaptation",
        mode=selected_mode,
        projector_version="compiler-plan-projector-v2",
        request_dynamic_invariant=adaptation_model_invariant_projection(
            invariant_catalog
        ),
        domain_validator_dynamic_invariant=compiler_invariant_validator_projection(
            invariant_catalog
        ),
    )
    prompt_payload = {
        "adaptation_input": {
            "protocol": envelope.protocol,
            "base_compiler_input": _compiler_model_input_projection(
                envelope.base_envelope
            ),
            "previous_plan_sha256": envelope.previous_plan.plan_sha256,
            "accepted_previous_plan": _accepted_previous_plan_projection(envelope),
            "failure_evidence": envelope.failure_evidence.model_dump(mode="json"),
            "completed_checkpoints": [
                item.model_dump(mode="json")
                for item in envelope.checkpoints
            ],
            "recovery_lineage": envelope.recovery_lineage.model_dump(mode="json"),
            "prior_adaptation_failure_sha256s": list(
                envelope.prior_adaptation_failure_sha256s
            ),
        },
        "structured_role_contract": _compiler_prompt_contract_projection(
            role_contract,
            selected_mode=selected_mode,
        ),
        "provider_constraint_contract": _provider_constraint_contract(
            envelope=envelope.base_envelope,
            requirement=response_requirement,
            response_mode=selected_mode,
            constraint_projection=constraint_projection,
        ),
        "semantic_correction": dict(correction) if correction is not None else None,
        "control_role_policy": (
            {
                "protocol": "sgar-control-role-policy-v2",
                "role": role_policy.role,
                "role_policy_sha256": role_policy.role_policy_sha256,
                "reasoning_effort": role_policy.reasoning_effort,
                "allow_model_failover": role_policy.allow_model_failover,
            }
            if role_policy is not None
            else None
        ),
    }
    if role_policy is not None:
        if role_policy.role != "plan_adaptation":
            raise PlanCompilerIdentityError("adaptation_control_role_policy_role_mismatch")
        if role_policy.api_model_id != envelope.base_envelope.compiler_model_api_id:
            raise PlanCompilerIdentityError("adaptation_control_role_policy_model_mismatch")
        if selected_mode != role_policy.response_mode:
            raise PlanCompilerIdentityError("adaptation_control_role_policy_response_mode_mismatch")
    kwargs: dict[str, Any] = {
        "model": envelope.base_envelope.compiler_model_api_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": canonical_json_bytes(prompt_payload).decode("utf-8"),
            },
        ],
    }
    if role_policy is not None:
        kwargs.update(role_policy.request_fields())
    kwargs["response_format"] = structured_response_format(
        response_requirement,
        mode=selected_mode,
    )
    return kwargs


def _compiler_prompt_contract_projection(
    contract: Any,
    *,
    selected_mode: str,
) -> dict[str, Any]:
    projection = structured_role_prompt_projection(
        contract,
        selected_mode=selected_mode,
    )
    if selected_mode == "native_strict_schema":
        projection.pop("model_output_schema", None)
        projection.pop("portable_wire_schema", None)
        projection.pop("local_validator_contract", None)
        projection["provider_schema_source"] = "response_format"
    return projection


def _request_provider_constraint_audit(
    api_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    messages = api_kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        raise PlanCompilerIdentityError("compiler_request_messages_missing")
    final_message = messages[-1]
    content = final_message.get("content") if isinstance(final_message, Mapping) else None
    try:
        payload = json.loads(str(content))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PlanCompilerIdentityError("compiler_request_payload_invalid") from exc
    contract = payload.get("provider_constraint_contract")
    if not isinstance(contract, Mapping):
        raise PlanCompilerIdentityError("compiler_provider_constraint_contract_missing")
    audit = dict(contract)
    supplied_contract_hash = audit.pop("contract_sha256", None)
    if supplied_contract_hash != canonical_sha256(audit):
        raise PlanCompilerIdentityError("compiler_provider_constraint_contract_hash_mismatch")
    audit["contract_sha256"] = supplied_contract_hash

    response_format = api_kwargs.get("response_format")
    if not isinstance(response_format, Mapping):
        raise PlanCompilerIdentityError("compiler_response_format_missing")
    if response_format.get("type") == "json_schema":
        json_schema = response_format.get("json_schema")
        wire_schema = json_schema.get("schema") if isinstance(json_schema, Mapping) else None
        if not isinstance(wire_schema, Mapping):
            raise PlanCompilerIdentityError("compiler_provider_wire_schema_missing")
        if canonical_sha256(wire_schema) != audit.get("wire_schema_sha256"):
            raise PlanCompilerIdentityError("compiler_provider_wire_schema_hash_mismatch")
    elif response_format.get("type") != "json_object":
        raise PlanCompilerIdentityError("compiler_response_format_type_invalid")
    _ensure_host_free(audit, field_name="compiler_provider_constraint_audit")
    return audit


def require_execution_resource_requirements(
    decision: CompilerDecisionProposalV3,
    envelope: PlanCompilerInputEnvelope,
) -> None:
    """Reject a sufficient Compiler decision that violates a user-bound resource relation."""

    if not decision.is_sufficient or not envelope.execution_requirements:
        return
    cards = {item.resource_id: item for item in envelope.candidate_cards}
    steps = tuple(decision.steps)
    skill_steps = {
        step.step_id: step
        for step in steps
        if cards.get(step.resource_id) is not None
        and cards[step.resource_id].resource_type == "Skill"
    }

    def model_identity_matches(resource_id: str, api_model_id: str | None) -> bool:
        card = cards.get(resource_id)
        return bool(
            card is not None
            and card.resource_type == "Model"
            and (
                api_model_id is None
                or (
                    card.model_pricing is not None
                    and card.model_pricing.api_model_id == api_model_id
                )
            )
        )

    for requirement in envelope.execution_requirements:
        matched = False
        if requirement.relation == "direct_executor":
            matched = any(
                cards.get(step.resource_id) is not None
                and cards[step.resource_id].resource_type == requirement.resource_type
                and (requirement.resource_id is None or step.resource_id == requirement.resource_id)
                and (
                    requirement.operation_id is None
                    or step.capability_operation_id == requirement.operation_id
                )
                and (
                    requirement.api_model_id is None
                    or model_identity_matches(step.resource_id, requirement.api_model_id)
                )
                for step in steps
            )
        elif requirement.relation == "agent_base_model":
            matched = any(
                cards.get(step.resource_id) is not None
                and cards[step.resource_id].resource_type == "Agent"
                and step.agent_base_model_resource_id is not None
                and (
                    requirement.resource_id is None
                    or step.agent_base_model_resource_id == requirement.resource_id
                )
                and model_identity_matches(
                    step.agent_base_model_resource_id,
                    requirement.api_model_id,
                )
                for step in steps
            )
        elif requirement.relation == "controller_callable_tool":
            matched = any(
                (requirement.resource_id is None or item.resource_id == requirement.resource_id)
                and (
                    requirement.operation_id is None
                    or item.capability_operation_id == requirement.operation_id
                )
                and cards.get(item.resource_id) is not None
                and cards[item.resource_id].resource_type == "Tool"
                for item in decision.controller_callable_tools
            )
        elif requirement.relation == "advisory_skill":
            for controller in steps:
                controller_card = cards.get(controller.resource_id)
                if controller_card is None or controller_card.resource_type != "Agent":
                    continue
                referenced = tuple(controller.advisory_profile_refs)
                candidate_ids = tuple(
                    skill_id
                    for skill_id in referenced
                    if (requirement.resource_id is None or skill_id == requirement.resource_id)
                    and any(step.resource_id == skill_id for step in skill_steps.values())
                )
                if not candidate_ids:
                    continue
                bound_skill_steps = {
                    str(mapping.from_step)
                    for mapping in controller.input_mappings
                    if mapping.source_kind == "step_output" and mapping.from_step is not None
                }
                matched = any(
                    step_id in bound_skill_steps
                    and skill_steps[step_id].resource_id in candidate_ids
                    for step_id in skill_steps
                )
                if matched:
                    break
        if not matched:
            raise PlanFrameworkValidationError(
                f"compiler_execution_requirement_unsatisfied:{requirement.requirement_id}"
            )


def _response_content(response: Any) -> str:
    try:
        choices = response.choices
        if not choices:
            raise ValueError
        content = choices[0].message.content
    except Exception as exc:
        raise PlanCompilerResponseError("plan_compiler_response_content_missing") from exc
    if not isinstance(content, str) or not content.strip():
        raise PlanCompilerResponseError("plan_compiler_response_content_invalid")
    return content


def _failure(
    exc: BaseException,
    *,
    responsibility: Literal["framework", "infrastructure", "research", "budget"],
    failure_stage: str,
    failure_code: str,
    failure_layer: Literal[
        "framework",
        "infrastructure",
        "budget",
        "protocol",
        "selection",
        "connection",
        "executability",
    ],
    transport_attempt: int,
    request_sha256: str | None,
    response_received: bool,
    retryable: bool = False,
) -> PlanCompilationFailure:
    return PlanCompilationFailure(
        responsibility=responsibility,
        failure_stage=failure_stage,
        failure_code=failure_code,
        failure_layer=failure_layer,
        exception_type=type(exc).__name__,
        retryable=retryable,
        transport_attempt=transport_attempt,
        request_sha256=request_sha256,
        response_received=response_received,
        message_sha256=_message_sha256(exc),
        output_diagnostic=getattr(exc, "output_diagnostic", None),
    )


class ExecutablePlanCompiler:
    """One strict semantic compile per immutable Plan revision identity."""

    def __init__(
        self,
        *,
        transport: SyncModelTransportPort,
        cost_ledger: RunCostLedger,
        compiler_model_resource_id: str,
        compiler_model_api_id: str,
        store: PlanCompilationStore,
        response_mode: StructuredResponseModeInput = "json_schema",
        adaptation_response_mode: StructuredResponseModeInput | None = None,
        control_role_policy: ControlRolePolicyV1 | None = None,
        capability_probe_service: ExactCapabilityProbeService | None = None,
        validator: PlanStructuralValidator | None = None,
        lowerer: ExecutionPlanLowerer | None = None,
    ) -> None:
        self.transport = require_sync_model_transport(transport)
        self.cost_ledger = cost_ledger
        self.compiler_model_resource_id = str(compiler_model_resource_id)
        self.compiler_model_api_id = str(compiler_model_api_id)
        self.control_role_policy = control_role_policy
        self.compiler_role_policy = (
            control_role_policy.for_role("plan_compiler")
            if control_role_policy is not None
            else None
        )
        self.adaptation_role_policy = (
            control_role_policy.for_role("plan_adaptation")
            if control_role_policy is not None
            else None
        )
        if self.compiler_role_policy is not None:
            if (
                self.compiler_role_policy.resource_id != self.compiler_model_resource_id
                or self.compiler_role_policy.api_model_id != self.compiler_model_api_id
            ):
                raise PlanCompilerIdentityError("compiler_control_role_policy_identity_mismatch")
        self.store = store
        self.capability_probe_service = capability_probe_service
        self._response_mode_argument = str(response_mode)
        self._adaptation_response_mode_argument = str(
            adaptation_response_mode or response_mode
        )
        self.response_mode = normalize_structured_response_mode(response_mode)
        self.adaptation_response_mode = normalize_structured_response_mode(
            adaptation_response_mode or response_mode
        )
        if self.compiler_role_policy is not None:
            if self.response_mode != self.compiler_role_policy.response_mode:
                raise PlanCompilerIdentityError("compiler_control_role_response_mode_invalid")
            if self.adaptation_response_mode != self.adaptation_role_policy.response_mode:
                raise PlanCompilerIdentityError("adaptation_control_role_response_mode_invalid")
        self.validator = validator or PlanStructuralValidator()
        self.lowerer = lowerer or ExecutionPlanLowerer()
        self._condition = threading.Condition(threading.RLock())
        self._inflight: set[str] = set()
        self._cache: dict[str, SealedPlanCompilationArtifact] = {}
        self._terminal_errors: dict[str, BaseException] = {}
        self._identity_inputs: dict[str, str] = {}
        self._normalized_proposals: dict[tuple[str, int], dict[str, Any]] = {}
        self._normalization_audits: dict[
            tuple[str, int], CompilerProposalNormalizationAuditV1
        ] = {}
        self._validation_issues: dict[
            tuple[str, int], tuple[CompilerProposalValidationIssueV1, ...]
        ] = {}
        self._correction_views: dict[
            tuple[str, int], CompilerProposalCorrectionViewV1
        ] = {}
        self.last_in_memory_artifact: SealedPlanCompilationArtifact | None = None

    def _append_diagnostic_persistence_failure(
        self,
        *,
        envelope: PlanCompilerInputEnvelope,
        semantic_attempt: int,
        operation: str,
        exc: Exception,
    ) -> None:
        """Best-effort content-free notice that can never change compilation."""

        try:
            self.store.append_event(
                "plan_compiler_diagnostic_persistence_failed",
                {
                    "plan_revision_sha256": envelope.plan_revision.revision_sha256,
                    "semantic_attempt": int(semantic_attempt),
                    "diagnostic_operation": str(operation),
                    "exception_type": type(exc).__name__,
                    "message_sha256": _message_sha256(exc),
                },
            )
        except Exception:
            pass

    def _persist_attempt_diagnostic_best_effort(
        self,
        *,
        run_id: str,
        envelope: PlanCompilerInputEnvelope,
        api_kwargs: Mapping[str, Any],
        semantic_attempt: int,
        request_sha256: str,
        response_sha256: str | None,
        structured_json: Any,
        validation_status: str,
        failure_class: str | None,
        validation_error: ValidationError | None = None,
    ) -> None:
        try:
            revision = envelope.plan_revision
            subtask = revision.subtask_revision
            secret_values = tuple(
                value for value in (os.environ.get("LLM_API_KEY"),) if value
            )
            diagnostic = compiler_attempt_diagnostics.build_attempt_diagnostic(
                run_id=run_id,
                attempt_index=semantic_attempt,
                graph_revision=subtask.graph_revision,
                subtask_id=subtask.subtask_id,
                subtask_revision=subtask.subtask_revision,
                plan_revision=revision.plan_revision,
                plan_revision_sha256=revision.revision_sha256,
                model_id=envelope.compiler_model_api_id,
                model_resource_id=envelope.compiler_model_resource_id,
                prompt_version=envelope.prompt_version,
                prompt_sha256=envelope.prompt_sha256,
                request_sha256=request_sha256,
                provider_constraint_contract=_request_provider_constraint_audit(
                    api_kwargs
                ),
                api_kwargs=api_kwargs,
                local_schema_name=CompilerDecisionProposalV3.__name__,
                local_schema_version=COMPILER_DECISION_PROTOCOL,
                local_schema_sha256=canonical_sha256(
                    CompilerDecisionProposalV3.model_json_schema()
                ),
                candidate_pool_sha256=(
                    envelope.candidate_pool_snapshot.candidate_pool_sha256
                ),
                response_sha256=response_sha256,
                structured_json=structured_json,
                structured_json_stage="normalized_response" if structured_json is not None else "unavailable",
                validation_status=validation_status,
                failure_class=failure_class,
                validation_error=validation_error,
                secret_values=secret_values,
                host_roots=(str(self.store.run_dir),),
            )
            compiler_attempt_diagnostics.persist_attempt_diagnostic(
                self.store.artifact_path(revision),
                attempt_index=semantic_attempt,
                diagnostic=diagnostic,
            )
        except Exception as exc:
            self._append_diagnostic_persistence_failure(
                envelope=envelope,
                semantic_attempt=semantic_attempt,
                operation="attempt",
                exc=exc,
            )

    def _persist_correction_diagnostic_best_effort(
        self,
        *,
        envelope: PlanCompilerInputEnvelope,
        semantic_attempt: int,
        correction: Mapping[str, Any],
        next_request_sha256: str,
    ) -> None:
        try:
            compiler_attempt_diagnostics.persist_correction_diagnostic(
                self.store.artifact_path(envelope.plan_revision),
                source_attempt_index=semantic_attempt,
                next_attempt_index=semantic_attempt + 1,
                correction=correction,
                next_request_sha256=next_request_sha256,
                secret_values=tuple(
                    value for value in (os.environ.get("LLM_API_KEY"),) if value
                ),
                host_roots=(str(self.store.run_dir),),
            )
        except Exception as exc:
            self._append_diagnostic_persistence_failure(
                envelope=envelope,
                semantic_attempt=semantic_attempt,
                operation="correction",
                exc=exc,
            )

    def _bind_selected_format_contracts(
        self,
        *,
        draft: CompilerPlanDraft,
        envelope: PlanCompilerInputEnvelope,
        decision: CompilerDecisionProposalV3 | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Bind Compiler-generated JSON schemas to selected Model producers.

        Retrieval can only check generic structured-output capability for a V6
        node because its exact schema does not exist yet.  After selection, this
        method probes only the selected backing model against the exact compiled
        schema and returns immutable enforcement contracts for validation and
        lowering.
        """

        cards = {item.resource_id: item for item in envelope.candidate_cards}
        bound: dict[str, dict[str, Any]] = {}
        for step in draft.steps:
            declaration = next((item for item in decision.steps if item.step_id == step.step_id), None) if decision else None
            expected_type = None
            if draft.final_output is not None and draft.final_output.step_id == step.step_id:
                expected_type = envelope.contract_projection.artifact_type
            if declaration is not None and declaration.output_role == "selected_resource":
                native_type = _manifest_output_contract(cards[step.resource_id], step.entrypoint_id).get("artifact_type")
                if expected_type is not None and expected_type != native_type:
                    raise CompilerSchemaProjectionError(
                        "compiler_resource_native_artifact_type_mismatch", origin="selected_schema_binding",
                        responsibility="research", contract_path=f"steps.{step.step_id}.expected_output_contract",
                        actual_type="machine_shape_mismatch",
                    )
                expected_type = native_type
            if expected_type is not None and expected_type != step.expected_output_contract.artifact_type:
                raise CompilerSchemaProjectionError(
                    "compiler_authoritative_artifact_type_mismatch", origin="selected_schema_binding",
                    responsibility="research", contract_path=f"steps.{step.step_id}.expected_output_contract",
                    actual_type="machine_shape_mismatch",
                )
            artifact_type = str(
                step.expected_output_contract.artifact_type or ""
            ).strip().lower()
            if artifact_type != "json" or step.resource_id not in cards:
                continue
            selected_card = cards[step.resource_id]
            if selected_card.resource_type == "Model":
                model_card = selected_card
            elif (
                selected_card.resource_type == "Agent"
                and step.agent_base_model_resource_id
            ):
                model_card = cards.get(step.agent_base_model_resource_id)
                if model_card is None or model_card.resource_type != "Model":
                    raise PlanFrameworkValidationError(
                        "compiler_selected_agent_base_model_card_missing"
                    )
                if model_card.resource_id not in selected_card.agent_base_model_candidates:
                    raise PlanFrameworkValidationError("compiler_selected_agent_base_model_outside_closure")
            else:
                continue

            try:
                requirement = _selected_step_format_requirement(
                    step=step, draft=draft, envelope=envelope, decision=decision,
                )
            except ModelResponseContractError as exc:
                raise CompilerSchemaProjectionError(
                    "compiler_generated_step_schema_invalid",
                    origin="selected_schema_binding", responsibility="framework",
                    contract_path=f"steps.{step.step_id}.expected_output_contract.schema_hint",
                    actual_type=type(step.expected_output_contract.schema_hint).__name__,
                ) from exc
            existing = model_card.runtime_requirements.get("sgar_format_contract")
            if classify_output_schema_phase(envelope.contract_projection) != "compiler_pending" and isinstance(existing, Mapping) and (
                str(existing.get("schema_sha256") or "")
                == str(requirement.schema_sha256 or "")
            ):
                if not _selected_schema_identity_valid(
                    card=selected_card, cards=cards,
                    selected_backing_model_id=step.agent_base_model_resource_id,
                    contract=existing, expected_schema_sha256=requirement.schema_sha256,
                    identity_required=False,
                ):
                    raise PlanFrameworkValidationError("compiler_existing_format_contract_invalid")
                bound[step.step_id] = dict(existing)
                continue

            capability = model_card.runtime_requirements.get(
                "sgar_structured_output_capability"
            )
            if not isinstance(capability, Mapping):
                raise PlanFrameworkValidationError(
                    "compiler_selected_structured_capability_missing"
                )
            capability_projection = dict(capability)
            supplied_capability_hash = str(
                capability_projection.pop("capability_sha256", "")
            )
            if supplied_capability_hash != canonical_sha256(capability_projection):
                raise PlanFrameworkValidationError(
                    "compiler_selected_structured_capability_hash_mismatch"
                )
            capability = _bound_generic_capability(model_card)
            if capability is None:
                raise PlanFrameworkValidationError("compiler_selected_generic_authority_invalid")
            if capability["endpoint_identity_sha256"] != self.transport.endpoint_identity.identity_sha256:
                raise PlanFrameworkValidationError("compiler_selected_endpoint_identity_mismatch")
            if self.capability_probe_service is None:
                raise PlanFrameworkValidationError(
                    "compiler_schema_bound_probe_service_missing"
                )
            if model_card.model_pricing is None:
                raise PlanFrameworkValidationError(
                    "compiler_selected_model_api_identity_missing"
                )
            evidence = self.capability_probe_service.probe(
                resource_id=model_card.resource_id,
                model_id=model_card.model_pricing.api_model_id,
                requirement=requirement,
                cost_ledger=self.cost_ledger,
                subtask_id=envelope.contract_projection.revision.subtask_id,
                subtask_revision=(
                    envelope.contract_projection.revision.subtask_revision
                ),
            )
            reasons: list[str] = []
            if capability.get("manifest_json_mode_status") == "supported":
                reasons.append("manifest_json_mode_supported")
            bound[step.step_id] = build_schema_bound_candidate_format_contract(
                requirement=requirement,
                evidence=evidence,
                reason_codes=reasons,
                selected_model_resource_id=model_card.resource_id,
                selected_model_api_id=model_card.model_pricing.api_model_id,
                endpoint_identity_sha256=self.transport.endpoint_identity.identity_sha256,
                generic_capability=capability,
            )
            if not _selected_schema_identity_valid(
                card=selected_card, cards=cards,
                selected_backing_model_id=step.agent_base_model_resource_id,
                contract=bound[step.step_id], expected_schema_sha256=requirement.schema_sha256,
                identity_required=True,
            ):
                raise PlanFrameworkValidationError("compiler_selected_schema_identity_invalid")
        return bound

    def _input_envelope(
        self,
        *,
        revision: PlanRevisionRef,
        candidate_pool: FrozenCandidatePoolResult,
        public_context: CompilerPublicContext,
        resource_definitions: Mapping[str, ResourceDefinition],
        pricing_catalog: ModelPricingCatalog,
        runtime_capabilities: RuntimeCapabilities,
        prompt_version: str = PLAN_COMPILER_PROMPT_VERSION,
        prompt_sha256: str = PLAN_COMPILER_PROMPT_SHA256,
    ) -> PlanCompilerInputEnvelope:
        price = pricing_catalog.resolve(
            resource_id=self.compiler_model_resource_id,
            api_model_id=self.compiler_model_api_id,
        )
        if price.resource_id != self.compiler_model_resource_id:
            raise PlanCompilerIdentityError("compiler_model_resource_identity_mismatch")
        if self.cost_ledger.catalog.pricing_catalog_sha256 != pricing_catalog.pricing_catalog_sha256:
            raise PlanCompilerIdentityError("compiler_pricing_ledger_mismatch")
        cards = build_candidate_execution_cards(
            candidate_pool,
            resource_definitions=resource_definitions,
            pricing_catalog=pricing_catalog,
        )
        semantic_requirements = tuple(
            candidate_pool.contract_projection.semantic_requirements
        )
        semantic_contract_v2 = candidate_pool.contract_projection.semantic_contract_v2
        if semantic_contract_v2 is not None:
            obligations = (
                compile_execution_obligation_v2(
                    semantic_contract_v2,
                    output_contract_sha256=(
                        candidate_pool.contract_projection.contract_sha256
                    ),
                ),
            )
        else:
            obligations = (
                compile_execution_obligations(
                    semantic_requirements,
                    output_contract_sha256=(
                        candidate_pool.contract_projection.contract_sha256
                    ),
                )
                if semantic_requirements
                else ()
            )
        authorized_evidence_source_ids = set(public_context.provenance_source_ids)
        authorized_evidence_source_ids.update(
            f"subtask_output:{edge.producer_id}"
            for edge in public_context.incoming_edge_contracts
        )
        if any(
            set(obligation.required_evidence_source_ids)
            - authorized_evidence_source_ids
            for obligation in obligations
            if hasattr(obligation, "required_evidence_source_ids")
        ):
            raise PlanCompilerIdentityError(
                "compiler_execution_obligation_evidence_source_unbound"
            )
        materials = tuple(
            MaterialDescriptorV1(
                source_id=descriptor.provenance_source_id,
                logical_name=descriptor.semantic_ref,
                artifact_type=descriptor.artifact_type,
                mime_type=descriptor.mime_type,
                content_sha256=descriptor.sha256,
                original_bytes=int(descriptor.size or 0),
                included_bytes=0,
                included_sha256=None,
                coverage_status="handle_only",
                handle_id=descriptor.handle_id,
                runtime_path=descriptor.runtime_path,
                utf8_decodable=descriptor.utf8_decodable,
            )
            for descriptor in public_context.descriptors
            if descriptor.handle_id is not None
        )
        return PlanCompilerInputEnvelope(
            plan_revision=revision,
            contract_projection=candidate_pool.contract_projection,
            retrieval_runtime_identity_sha256=(
                candidate_pool.runtime_identity.identity_sha256
            ),
            candidate_pool_snapshot=candidate_pool.candidate_pool_snapshot,
            retrieval_evidence_sha256=candidate_pool.retrieval_evidence_sha256,
            candidate_cards=cards,
            dependency_edges=candidate_pool.dependency_edges,
            public_context=public_context,
            execution_obligations=obligations,
            execution_requirements=tuple(
                candidate_pool.contract_projection.execution_requirements
            ),
            materials=materials,
            runtime_capabilities=runtime_capabilities,
            pricing_catalog_sha256=pricing_catalog.pricing_catalog_sha256,
            compiler_model_resource_id=self.compiler_model_resource_id,
            compiler_model_api_id=self.compiler_model_api_id,
            compiler_policy=CompilerPolicy(),
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
        )

    def _failed_artifact(
        self,
        *,
        run_id: str,
        envelope: PlanCompilerInputEnvelope,
        attempts: tuple[PlanTransportAttempt, ...],
        failure: PlanCompilationFailure,
        accounting_operation_id: str | None,
        response_sha256: str | None = None,
        draft_sha256: str | None = None,
        status: Literal["failed", "interrupted"] = "failed",
        compiler_input_sha256: str | None = None,
    ) -> SealedPlanCompilationArtifact:
        return SealedPlanCompilationArtifact(
            run_id=run_id,
            plan_revision=envelope.plan_revision,
            status=status,
            contract_sha256=envelope.contract_projection.contract_sha256,
            candidate_pool_sha256=(
                envelope.candidate_pool_snapshot.candidate_pool_sha256
            ),
            retrieval_evidence_sha256=envelope.retrieval_evidence_sha256,
            pricing_catalog_sha256=envelope.pricing_catalog_sha256,
            prompt_sha256=envelope.prompt_sha256,
            compiler_input_sha256=(compiler_input_sha256 or envelope.input_sha256),
            compiler_model_resource_id=envelope.compiler_model_resource_id,
            compiler_model_api_id=envelope.compiler_model_api_id,
            accounting_operation_id=accounting_operation_id,
            transport_attempts=attempts,
            compiler_response_sha256=response_sha256,
            compiler_draft_sha256=draft_sha256,
            failure=failure,
        )

    def compile(
        self,
        *,
        run_id: str,
        revision: PlanRevisionRef,
        candidate_pool: FrozenCandidatePoolResult,
        public_context: CompilerPublicContext,
        resource_definitions: Mapping[str, ResourceDefinition],
        pricing_catalog: ModelPricingCatalog,
        runtime_capabilities: RuntimeCapabilities,
        payload_guard: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> SealedPlanCompilationArtifact:
        if run_id != self.store.run_id or run_id != self.cost_ledger.run_id:
            raise PlanCompilerIdentityError("plan_compiler_run_identity_mismatch")
        if revision.compile_purpose.value != "initial" or revision.plan_revision != 0:
            raise PlanCompilerIdentityError("stage3b_only_accepts_initial_plan")
        envelope = self._input_envelope(
            revision=revision,
            candidate_pool=candidate_pool,
            public_context=public_context,
            resource_definitions=resource_definitions,
            pricing_catalog=pricing_catalog,
            runtime_capabilities=runtime_capabilities,
        )
        return self._compile_revision(
            run_id=run_id,
            envelope=envelope,
            compiler_input_sha256=envelope.input_sha256,
            api_kwargs=build_plan_compiler_call_kwargs(
                envelope,
                response_mode=self._response_mode_argument,
                role_policy=self.compiler_role_policy,
            ),
            resource_definitions=resource_definitions,
            payload_guard=payload_guard,
        )

    def adapt(
        self,
        *,
        run_id: str,
        revision: PlanRevisionRef,
        previous_artifact: SealedPlanCompilationArtifact,
        failure_evidence: StructuredExecutionFailureEvidence,
        checkpoints: tuple[CompletedStepCheckpoint, ...],
        recovery_lineage: RecoveryLineage,
        candidate_pool: FrozenCandidatePoolResult,
        public_context: CompilerPublicContext,
        resource_definitions: Mapping[str, ResourceDefinition],
        pricing_catalog: ModelPricingCatalog,
        runtime_capabilities: RuntimeCapabilities,
        prior_adaptation_failure_sha256s: tuple[str, ...] = (),
        diagnostic_excerpt: str = "",
        temporary_tool_source: TemporaryToolSourceBundle | None = None,
        payload_guard: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> SealedPlanCompilationArtifact:
        if run_id != self.store.run_id or run_id != self.cost_ledger.run_id:
            raise PlanCompilerIdentityError("plan_compiler_run_identity_mismatch")
        if revision.compile_purpose.value != "execution_adaptation":
            raise PlanCompilerIdentityError("plan_adaptation_purpose_invalid")
        if revision.plan_revision not in {1, 2}:
            raise PlanCompilerIdentityError("plan_adaptation_revision_out_of_range")
        if previous_artifact.status != "success" or previous_artifact.executable_plan is None:
            raise PlanCompilerIdentityError("plan_adaptation_previous_artifact_not_sealed")
        base_envelope = self._input_envelope(
            revision=revision,
            candidate_pool=candidate_pool,
            public_context=public_context,
            resource_definitions=resource_definitions,
            pricing_catalog=pricing_catalog,
            runtime_capabilities=runtime_capabilities,
            prompt_version=PLAN_ADAPTATION_PROMPT_VERSION,
            prompt_sha256=PLAN_ADAPTATION_PROMPT_SHA256,
        )
        try:
            adaptation_input = PlanAdaptationInputEnvelope(
                base_envelope=base_envelope,
                previous_plan_artifact_sha256=previous_artifact.artifact_sha256,
                previous_plan=previous_artifact.executable_plan,
                failure_evidence=failure_evidence,
                checkpoints=checkpoints,
                recovery_lineage=recovery_lineage,
                temporary_tool_source=temporary_tool_source,
                prior_adaptation_failure_sha256s=prior_adaptation_failure_sha256s,
                diagnostic_excerpt=diagnostic_excerpt,
            )
        except Exception as exc:
            raise PlanCompilerIdentityError("plan_adaptation_input_invalid") from exc
        try:
            api_kwargs = build_plan_adaptation_call_kwargs(
                adaptation_input, response_mode=self._adaptation_response_mode_argument,
                role_policy=self.adaptation_role_policy)
        except PlanCompilerIdentityError as exc:
            return self._failed_artifact(
                run_id=run_id, envelope=base_envelope, attempts=(), accounting_operation_id=None,
                compiler_input_sha256=adaptation_input.input_sha256,
                failure=_failure(exc, responsibility="framework", failure_stage="plan_adaptation_input",
                    failure_code="plan_adaptation_accepted_plan_input_unrepresentable", failure_layer="framework",
                    transport_attempt=0, request_sha256=None, response_received=False))
        return self._compile_revision(
            run_id=run_id,
            envelope=base_envelope,
            compiler_input_sha256=adaptation_input.input_sha256,
            api_kwargs=api_kwargs,
            resource_definitions=resource_definitions,
            payload_guard=payload_guard,
            adaptation_input=adaptation_input,
        )

    def _compile_revision(
        self,
        *,
        run_id: str,
        envelope: PlanCompilerInputEnvelope,
        compiler_input_sha256: str,
        api_kwargs: Mapping[str, Any],
        resource_definitions: Mapping[str, ResourceDefinition],
        payload_guard: Callable[[Mapping[str, Any]], None] | None,
        adaptation_input: PlanAdaptationInputEnvelope | None = None,
    ) -> SealedPlanCompilationArtifact:
        revision = envelope.plan_revision
        key = revision.revision_sha256
        with self._condition:
            existing_input = self._identity_inputs.setdefault(key, compiler_input_sha256)
            if existing_input != compiler_input_sha256:
                raise PlanCompilerIdentityError("plan_revision_input_identity_changed")
            while key in self._inflight:
                self._condition.wait()
            if key in self._cache:
                return self._cache[key]
            if key in self._terminal_errors:
                raise self._terminal_errors[key]
            existing = self.store.load(revision)
            if existing is not None:
                expected_prompt = PLAN_ADAPTATION_PROMPT_SHA256 if adaptation_input is not None else PLAN_COMPILER_PROMPT_SHA256
                if existing.prompt_sha256 != expected_prompt:
                    raise PlanCompilerIdentityError("persisted_compiler_prompt_changed_start_new_run")
                if existing.compiler_input_sha256 != compiler_input_sha256:
                    raise PlanCompilerIdentityError(
                        "persisted_plan_revision_input_identity_changed"
                    )
                self._cache[key] = existing
                return existing
            if self.store.has_unmatched_started(key):
                interrupted_exc = PlanCompilerPersistenceError(
                    "plan_compilation_previous_attempt_incomplete"
                )
                interrupted = self._failed_artifact(
                    run_id=run_id,
                    envelope=envelope,
                    attempts=(),
                    accounting_operation_id=None,
                    failure=_failure(
                        interrupted_exc,
                        responsibility="framework",
                        failure_stage="plan_compilation_persistence",
                        failure_code="plan_compilation_previous_attempt_incomplete",
                        failure_layer="framework",
                        transport_attempt=0,
                        request_sha256=None,
                        response_received=False,
                    ),
                    status="interrupted",
                    compiler_input_sha256=compiler_input_sha256,
                )
                persisted = self.store.persist(interrupted)
                self.store.append_event(
                    "plan_compilation_interrupted",
                    {
                        "plan_revision_sha256": key,
                        "artifact_sha256": persisted.artifact_sha256,
                    },
                )
                self._cache[key] = persisted
                return persisted
            self._inflight.add(key)

        try:
            feasibility_audit = audit_candidate_pool_feasibility(envelope)
            self.store.append_event(
                "candidate_pool_feasibility_audited",
                {
                    "plan_revision_sha256": key,
                    "candidate_pool_sha256": (
                        envelope.candidate_pool_snapshot.candidate_pool_sha256
                    ),
                    "potentially_feasible": feasibility_audit.potentially_feasible,
                    "audit_sha256": feasibility_audit.audit_sha256,
                },
            )
            self.store.append_event(
                "plan_compilation_started",
                {
                    "plan_revision_sha256": key,
                    "compiler_input_sha256": compiler_input_sha256,
                    "candidate_pool_sha256": (
                        envelope.candidate_pool_snapshot.candidate_pool_sha256
                    ),
                    "request_sha256": model_request_sha256(dict(api_kwargs)),
                },
            )
            semantic_records: list[dict[str, Any]] = []
            current_kwargs = dict(api_kwargs)
            invariant_catalog = build_compiler_invariant_catalog(envelope)
            accepted_attempt: int | None = None
            result: SealedPlanCompilationArtifact | None = None
            insufficiency_codes = {
                "candidate_pool_insufficient",
                "contract_not_achievable",
                "runtime_requirements_unmet",
                "required_dependency_unusable",
            }
            semantic_attempts = (
                (1,)
                if self.capability_probe_service is not None
                and self.capability_probe_service.enforcement_policy == "single_attempt"
                else (1, 2)
            )
            for semantic_attempt in semantic_attempts:
                result = self._compile_once(
                    run_id=run_id,
                    envelope=envelope,
                    compiler_input_sha256=compiler_input_sha256,
                    api_kwargs=current_kwargs,
                    resource_definitions=resource_definitions,
                    payload_guard=payload_guard,
                    adaptation_input=adaptation_input,
                    semantic_attempt=semantic_attempt,
                )
                issues = self._validation_issues.get((key, semantic_attempt), ())
                correction_view = self._correction_views.get((key, semantic_attempt))
                safe_issue_audits = build_compiler_safe_issue_audits(
                    issues,
                    proposal=(
                        self._normalized_proposals.get((key, semantic_attempt), {}).get(
                            "plan_decision", self._normalized_proposals.get((key, semantic_attempt), {})
                        )
                    ),
                    catalog=invariant_catalog,
                )
                semantic_records.append(
                    {
                        "semantic_attempt": semantic_attempt,
                        "control_role_policy_sha256": (
                            self.control_role_policy.policy_sha256
                            if self.control_role_policy is not None
                            else None
                        ),
                        "reasoning_effort": (
                            (
                                self.adaptation_role_policy
                                if adaptation_input is not None
                                else self.compiler_role_policy
                            ).reasoning_effort
                            if self.control_role_policy is not None
                            else None
                        ),
                        "request_sha256": model_request_sha256(current_kwargs),
                        "response_sha256": result.compiler_response_sha256,
                        "projected_draft_sha256": result.compiler_draft_sha256,
                        "accounting_operation_id": result.accounting_operation_id,
                        "status": result.status,
                        "failure_layer": (
                            result.failure.failure_layer if result.failure else None
                        ),
                        "failure_code": (
                            result.failure.failure_code if result.failure else None
                        ),
                        "normalization_audit_sha256": (
                            self._normalization_audits[(key, semantic_attempt)].audit_sha256
                            if (key, semantic_attempt) in self._normalization_audits
                            else None
                        ),
                        "validation_issue_summary_sha256": (
                            canonical_sha256(
                                [
                                    item.model_dump(mode="json")
                                    for item in self._validation_issues[
                                        (key, semantic_attempt)
                                    ]
                                ]
                            )
                            if (key, semantic_attempt) in self._validation_issues
                            else None
                        ),
                        "safe_issue_audits": safe_issue_audits,
                        "validation_issues": [item.model_dump(mode="json") for item in issues],
                        "candidate_pool_feasibility_audit": (
                            feasibility_audit.model_dump(mode="json")
                        ),
                        "provider_constraint_contract": (
                            _request_provider_constraint_audit(current_kwargs)
                        ),
                        "accounting_reference": result.accounting_operation_id,
                    }
                )
                if result.status == "success":
                    accepted_attempt = semantic_attempt
                    break
                failure = result.failure
                retryable_semantic = (
                    semantic_attempt < semantic_attempts[-1]
                    and failure is not None
                    and failure.responsibility == "research"
                    and failure.failure_code != "required_input_missing"
                    and failure.failure_code != "plan_adaptation_insufficient"
                    and failure.failure_stage in {
                        "plan_compiler_protocol",
                        "plan_compiler_projection",
                        "plan_compiler_validation",
                        "plan_adaptation_validation",
                    }
                    and (
                        failure.failure_code not in insufficiency_codes
                        or feasibility_audit.potentially_feasible
                    )
                )
                if not retryable_semantic:
                    break
                issues = self._validation_issues.get((key, semantic_attempt), ())
                primary_issue = issues[0] if issues else None
                correction_view = self._correction_views.get((key, semantic_attempt))
                normalization_audit = self._normalization_audits.get(
                    (key, semantic_attempt)
                )
                constraint_projection = compiler_constraint_prompt_projection(
                    invariant_catalog,
                    candidate_pool_sha256=(
                        envelope.candidate_pool_snapshot.candidate_pool_sha256
                    ),
                )
                correction = _build_compiler_semantic_correction(
                    failure=failure,
                    primary_issue=primary_issue,
                    previous_response_sha256=result.compiler_response_sha256,
                    previous_projected_draft_sha256=result.compiler_draft_sha256,
                    previous_proposal=self._normalized_proposals.get((key, semantic_attempt), {}),
                    secret_values=tuple(v for v in (os.environ.get("LLM_API_KEY"),) if v),
                    host_roots=(str(self.store.run_dir),),
                    correction_view=correction_view,
                    adaptation=adaptation_input is not None,
                    normalization_audit=normalization_audit,
                    constraint_projection=constraint_projection,
                    feasibility_audit=feasibility_audit,
                    envelope=envelope,
                )
                terminal_progress.compilation("correction", revision, correction=correction)
                current_kwargs = (
                    build_plan_adaptation_call_kwargs(
                        adaptation_input,
                        response_mode=self._adaptation_response_mode_argument,
                        correction=correction,
                        role_policy=self.adaptation_role_policy,
                    )
                    if adaptation_input is not None
                    else build_plan_compiler_call_kwargs(
                        envelope,
                        response_mode=self._response_mode_argument,
                        correction=correction,
                        role_policy=self.compiler_role_policy,
                    )
                )
                if adaptation_input is None:
                    self._persist_correction_diagnostic_best_effort(
                        envelope=envelope,
                        semantic_attempt=semantic_attempt,
                        correction=correction,
                        next_request_sha256=model_request_sha256(current_kwargs),
                    )
            if result is None:
                raise PlanCompilerError("plan_compiler_semantic_terminal_state_missing")
            self.store.persist_semantic_attempts(
                revision,
                tuple(semantic_records),
                accepted_attempt=accepted_attempt,
            )
            if result.compiler_input_sha256 != compiler_input_sha256:
                projection = result.model_dump(mode="json")
                projection["compiler_input_sha256"] = compiler_input_sha256
                projection["artifact_sha256"] = ""
                result = SealedPlanCompilationArtifact.model_validate(projection)
            self.last_in_memory_artifact = result
            persisted = self.store.persist(result)
            terminal_event = (
                "plan_sealed" if persisted.status == "success" else "plan_compilation_failed"
            )
            self.store.append_event(
                terminal_event,
                {
                    "plan_revision_sha256": key,
                    "artifact_sha256": persisted.artifact_sha256,
                    "status": persisted.status,
                    "failure_code": (
                        persisted.failure.failure_code if persisted.failure else None
                    ),
                },
            )
            with self._condition:
                self._cache[key] = persisted
            terminal_progress.compilation("finished", revision, result=persisted,
                                          issues=self._validation_issues.get((key, semantic_attempt), ()))
            return persisted
        except (asyncio.CancelledError, KeyboardInterrupt) as exc:
            try:
                self.store.append_event(
                    "plan_compilation_interrupted",
                    {
                        "plan_revision_sha256": key,
                        "status": "interrupted",
                        "failure_code": "plan_compilation_interrupted",
                        "exception_type": type(exc).__name__,
                        "message_sha256": _message_sha256(exc),
                    },
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            with self._condition:
                self._terminal_errors[key] = exc
            raise
        finally:
            with self._condition:
                self._inflight.discard(key)
                self._normalized_proposals.pop((key, 1), None)
                self._normalized_proposals.pop((key, 2), None)
                self._normalization_audits.pop((key, 1), None)
                self._normalization_audits.pop((key, 2), None)
                self._validation_issues.pop((key, 1), None)
                self._validation_issues.pop((key, 2), None)
                self._correction_views.pop((key, 1), None)
                self._correction_views.pop((key, 2), None)
                self._condition.notify_all()

    def _compile_once(
        self,
        *,
        run_id: str,
        envelope: PlanCompilerInputEnvelope,
        compiler_input_sha256: str,
        api_kwargs: Mapping[str, Any],
        resource_definitions: Mapping[str, ResourceDefinition],
        payload_guard: Callable[[Mapping[str, Any]], None] | None,
        adaptation_input: PlanAdaptationInputEnvelope | None = None,
        semantic_attempt: Literal[1, 2] = 1,
    ) -> SealedPlanCompilationArtifact:
        key = envelope.plan_revision.revision_sha256
        accounting_stage = "command_adaptation" if adaptation_input is not None else "plan_compiler"
        operation_prefix = "plan_adaptation" if adaptation_input is not None else "plan_compiler"
        active_role_policy = (
            self.adaptation_role_policy
            if adaptation_input is not None
            else self.compiler_role_policy
        )
        operation_id = f"{operation_prefix}:{key}:semantic:{semantic_attempt}"
        call_context = ModelCallContext(
            operation_id=operation_id,
            stage=accounting_stage,
            subtask_id=envelope.plan_revision.subtask_revision.subtask_id,
            subtask_revision=(
                envelope.plan_revision.subtask_revision.subtask_revision
            ),
            selected_resource_id=self.compiler_model_resource_id,
            model_resource_id=self.compiler_model_resource_id,
            request_policy_sha256=(
                active_role_policy.role_policy_sha256
                if active_role_policy is not None
                else None
            ),
            reasoning_effort=(
                active_role_policy.reasoning_effort
                if active_role_policy is not None
                else None
            ),
        )
        terminal_progress.compilation("start", envelope.plan_revision, attempt=semantic_attempt)
        terminal_progress.compiler_input(api_kwargs, envelope.plan_revision, semantic_attempt)
        frozen_api_kwargs = deepcopy(dict(api_kwargs))
        request_hash = model_request_sha256(frozen_api_kwargs)
        self.store.append_event(
            "plan_compiler_semantic_started",
            {
                "plan_revision_sha256": key,
                "compiler_input_sha256": compiler_input_sha256,
                "candidate_pool_sha256": (
                    envelope.candidate_pool_snapshot.candidate_pool_sha256
                ),
                "request_sha256": request_hash,
                "accounting_operation_id": operation_id,
                "semantic_attempt": semantic_attempt,
                "control_role_policy_sha256": (
                    self.control_role_policy.policy_sha256
                    if self.control_role_policy is not None
                    else None
                ),
                "role_policy_sha256": (
                    active_role_policy.role_policy_sha256
                    if active_role_policy is not None
                    else None
                ),
                "reasoning_effort": (
                    active_role_policy.reasoning_effort
                    if active_role_policy is not None
                    else None
                ),
            },
        )

        attempts: list[PlanTransportAttempt] = []
        response: Any = None
        for attempt in range(1, PLAN_COMPILER_MAX_TRANSPORT_ATTEMPTS + 1):
            if model_request_sha256(frozen_api_kwargs) != request_hash:
                raise PlanCompilerIdentityError("plan_compiler_request_hash_changed")
            if payload_guard is not None:
                try:
                    payload_guard(deepcopy(frozen_api_kwargs))
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as exc:
                    return self._failed_artifact(
                        run_id=run_id,
                        envelope=envelope,
                        attempts=tuple(attempts),
                        accounting_operation_id=operation_id,
                        failure=_failure(
                            exc,
                            responsibility="framework",
                            failure_stage="plan_compiler_payload_guard",
                            failure_code="plan_compiler_payload_guard_failed",
                            failure_layer="framework",
                            transport_attempt=len(attempts),
                            request_sha256=request_hash,
                            response_received=False,
                        ),
                    )
            try:
                response = self.transport.send(
                    ledger=self.cost_ledger,
                    context=call_context,
                    **deepcopy(frozen_api_kwargs),
                )
            except BudgetControlError as exc:
                attempts.append(
                    PlanTransportAttempt(
                        attempt=attempt,
                        request_sha256=request_hash,
                        outcome="budget_failure",
                        failure_code="budget_control",
                        response_received=False,
                    )
                )
                return self._failed_artifact(
                    run_id=run_id,
                    envelope=envelope,
                    attempts=tuple(attempts),
                    accounting_operation_id=operation_id,
                    failure=_failure(
                        exc,
                        responsibility="budget",
                        failure_stage="plan_compiler_transport",
                        failure_code="budget_control",
                        failure_layer="budget",
                        transport_attempt=attempt,
                        request_sha256=request_hash,
                        response_received=False,
                    ),
                )
            except PricingCatalogError as exc:
                attempts.append(
                    PlanTransportAttempt(
                        attempt=attempt,
                        request_sha256=request_hash,
                        outcome="framework_failure",
                        failure_code="model_pricing_unresolved",
                        response_received=False,
                    )
                )
                return self._failed_artifact(
                    run_id=run_id,
                    envelope=envelope,
                    attempts=tuple(attempts),
                    accounting_operation_id=operation_id,
                    failure=_failure(
                        exc,
                        responsibility="framework",
                        failure_stage="plan_compiler_accounting",
                        failure_code="model_pricing_unresolved",
                        failure_layer="framework",
                        transport_attempt=attempt,
                        request_sha256=request_hash,
                        response_received=False,
                    ),
                )
            except AccountingPersistenceError as exc:
                attempts.append(
                    PlanTransportAttempt(
                        attempt=attempt,
                        request_sha256=request_hash,
                        outcome="framework_failure",
                        failure_code="model_accounting_persistence_failed",
                        response_received=False,
                    )
                )
                return self._failed_artifact(
                    run_id=run_id,
                    envelope=envelope,
                    attempts=tuple(attempts),
                    accounting_operation_id=operation_id,
                    failure=_failure(
                        exc,
                        responsibility="framework",
                        failure_stage="plan_compiler_accounting",
                        failure_code="model_accounting_persistence_failed",
                        failure_layer="framework",
                        transport_attempt=attempt,
                        request_sha256=request_hash,
                        response_received=False,
                    ),
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:
                retryable, failure_code = classify_transport_exception(exc)
                if retryable:
                    attempts.append(
                        PlanTransportAttempt(
                            attempt=attempt,
                            request_sha256=request_hash,
                            outcome="infrastructure_failure",
                            failure_code=failure_code,
                            response_received=False,
                        )
                    )
                    self.store.append_event(
                        "plan_compiler_attempt",
                        {
                            "plan_revision_sha256": key,
                            "attempt": attempt,
                            "request_sha256": request_hash,
                            "outcome": "infrastructure_failure",
                            "failure_code": failure_code,
                        },
                    )
                    if attempt < PLAN_COMPILER_MAX_TRANSPORT_ATTEMPTS:
                        continue
                    return self._failed_artifact(
                        run_id=run_id,
                        envelope=envelope,
                        attempts=tuple(attempts),
                        accounting_operation_id=operation_id,
                        failure=_failure(
                            exc,
                            responsibility="infrastructure",
                            failure_stage="plan_compiler_transport",
                            failure_code=failure_code,
                            failure_layer="infrastructure",
                            transport_attempt=attempt,
                            request_sha256=request_hash,
                            response_received=False,
                            retryable=False,
                        ),
                    )
                attempts.append(
                    PlanTransportAttempt(
                        attempt=attempt,
                        request_sha256=request_hash,
                        outcome="framework_failure",
                        failure_code="plan_compiler_request_rejected",
                        response_received=False,
                    )
                )
                return self._failed_artifact(
                    run_id=run_id,
                    envelope=envelope,
                    attempts=tuple(attempts),
                    accounting_operation_id=operation_id,
                    failure=_failure(
                        exc,
                        responsibility="framework",
                        failure_stage="plan_compiler_transport",
                        failure_code="plan_compiler_request_rejected",
                        failure_layer="framework",
                        transport_attempt=attempt,
                        request_sha256=request_hash,
                        response_received=False,
                    ),
                )
            else:
                attempts.append(
                    PlanTransportAttempt(
                        attempt=attempt,
                        request_sha256=request_hash,
                        outcome="success",
                        response_received=True,
                    )
                )
                self.store.append_event(
                    "plan_compiler_attempt",
                    {
                        "plan_revision_sha256": key,
                        "attempt": attempt,
                        "request_sha256": request_hash,
                        "outcome": "success",
                    },
                )
                break

        if response is None:
            raise PlanCompilerError("plan_compiler_transport_terminal_state_missing")

        response_content: str | None = None
        response_hash: str | None = None
        decoded: Any = None
        compiler_diagnostic_recorded = False
        draft: CompilerPlanDraft | None = None
        adaptation_draft: PlanAdaptationDraft | None = None
        effective_draft_sha256: str | None = None
        projection_audit: CompilerProjectionAuditV1 | None = None
        try:
            reasoning_observation = observe_provider_reasoning(response)
            response_content = _response_content(response)
            terminal_progress.compiler_response(response_content, envelope.plan_revision, semantic_attempt)
            response_hash = _response_sha256(response_content)
            self.store.append_event(
                "plan_compiler_response_received",
                {
                    "plan_revision_sha256": key,
                    "request_sha256": request_hash,
                    "compiler_response_sha256": response_hash,
                    "provider_reasoning_observation": reasoning_observation.model_dump(
                        mode="json"
                    ),
                },
            )
            field_audit = _compiler_ingress_field_audit(response_content, adaptation=adaptation_input is not None)
            self.store.append_event("compiler_semantic_field_ingress", {
                "plan_revision_sha256": key, "semantic_attempt": semantic_attempt,
                "raw_response_sha256": response_hash, "fields": field_audit["fields"],
            })
            if field_audit["issues"]:
                issue = field_audit["issues"][0]
                raise CompilerSchemaProjectionError(issue.failure_code, origin="compiler_response",
                    responsibility="research", contract_path=".".join(map(str, issue.path)),
                    actual_type=issue.observed_active_fields[0], issues=field_audit["issues"])
            if adaptation_input is None:
                response_requirement = build_candidate_constrained_response_requirement(
                    envelope,
                    role="plan_compiler",
                )
                wire_projection = response_requirement.portable_wire_schema
                sent_response_format = frozen_api_kwargs.get("response_format")
                sent_schema = (
                    sent_response_format.get("json_schema", {}).get("schema")
                    if isinstance(sent_response_format, Mapping)
                    else None
                )
                if self.response_mode == "native_strict_schema" and (
                    wire_projection is None
                    or not isinstance(sent_schema, Mapping)
                    or canonical_sha256(sent_schema)
                    != response_requirement.wire_schema_sha256
                ):
                    raise PlanCompilerIdentityError("compiler_response_requirement_identity_mismatch")
                decoded, _ingress_audit = normalize_structured_response_content(
                    response_content,
                    requirement=response_requirement,
                    mode=self.response_mode,
                    instance_normalizer=_normalize_compiler_schema_graph_defaults,
                )
                self.store.append_event("compiler_ingress_normalized", {
                    "plan_revision_sha256": key, "semantic_attempt": semantic_attempt,
                    "role": "plan_compiler", **_ingress_audit.model_dump(mode="json"),
                })
                if isinstance(decoded, Mapping):
                    self._normalized_proposals[(key, semantic_attempt)] = dict(decoded)
                try:
                    decision = CompilerDecisionProposalV3.model_validate_json(
                        canonical_json_bytes(decoded).decode("utf-8"),
                        strict=True,
                    )
                except ValidationError as exc:
                    self._persist_attempt_diagnostic_best_effort(
                        run_id=run_id,
                        envelope=envelope,
                        api_kwargs=frozen_api_kwargs,
                        semantic_attempt=semantic_attempt,
                        request_sha256=request_hash,
                        response_sha256=response_hash,
                        structured_json=decoded,
                        validation_status="failed",
                        failure_class="plan_compiler_response_shape_invalid",
                        validation_error=exc,
                    )
                    compiler_diagnostic_recorded = True
                    raise
                self._persist_attempt_diagnostic_best_effort(
                    run_id=run_id,
                    envelope=envelope,
                    api_kwargs=frozen_api_kwargs,
                    semantic_attempt=semantic_attempt,
                    request_sha256=request_hash,
                    response_sha256=response_hash,
                    structured_json=decoded,
                    validation_status="passed",
                    failure_class=None,
                )
                self.store.append_event("compiler_response_shape_passed", {
                    "plan_revision_sha256": key, "semantic_attempt": semantic_attempt,
                    "semantic_projection_passed": False,
                })
                compiler_diagnostic_recorded = True
                # This is the strict ingress decision, not a successful normalization.
                self._normalized_proposals[(key, semantic_attempt)] = decision.model_dump(mode="json")
                proposal = project_compiler_decision_v3(
                    decision=decision,
                    envelope=envelope,
                )
                require_execution_resource_requirements(decision, envelope)
                normalized_payload, normalization_audit = normalize_compiler_proposal_payload(
                    proposal.model_dump(mode="json")
                )
                self._normalization_audits[(key, semantic_attempt)] = normalization_audit
                require_compiler_proposal_invariants(
                    normalized_payload,
                    build_compiler_invariant_catalog(envelope),
                )
                self._validation_issues[(key, semantic_attempt)] = ()
                self._correction_views[(key, semantic_attempt)] = (
                    build_compiler_proposal_correction_view(normalized_payload)
                )
                self._normalized_proposals[(key, semantic_attempt)] = (
                    decision.model_dump(mode="json")
                )
                draft, projection_audit = project_compiler_plan_proposal(
                    proposal=proposal,
                    envelope=envelope,
                )
                effective_draft_sha256 = draft.draft_sha256
            else:
                decoded, _ingress_audit = normalize_structured_response_content(
                    response_content,
                    requirement=build_candidate_constrained_response_requirement(envelope, role="plan_adaptation"),
                    mode=self.adaptation_response_mode,
                    instance_normalizer=_normalize_compiler_schema_graph_defaults,
                )
                self.store.append_event("compiler_ingress_normalized", {
                    "plan_revision_sha256": key, "semantic_attempt": semantic_attempt,
                    "role": "plan_adaptation", **_ingress_audit.model_dump(mode="json"),
                })
                if isinstance(decoded, Mapping):
                    self._normalized_proposals[(key, semantic_attempt)] = dict(decoded)
                adaptation_decision = PlanAdaptationDecisionV3.model_validate_json(
                    canonical_json_bytes(decoded).decode("utf-8"),
                    strict=True,
                )
                assert_recovery_projection_safe(
                    adaptation_decision.model_dump(mode="python")
                )
                self._validation_issues[(key, semantic_attempt)] = ()
                self._correction_views[(key, semantic_attempt)] = (
                    build_compiler_proposal_correction_view(
                        adaptation_decision.plan_decision.model_dump(mode="json")
                    )
                )
                self._normalized_proposals[(key, semantic_attempt)] = (
                    adaptation_decision.model_dump(mode="json")
                )
                try:
                    validate_adaptation_decision_identity(
                        adaptation_input=adaptation_input, decision=adaptation_decision)
                except RecoveryControlError as exc:
                    # The same model-owned identity rejection as post-lowering lineage;
                    # an insufficient response has no DAG on which to run that stage.
                    return self._failed_artifact(
                        run_id=run_id, envelope=envelope, attempts=tuple(attempts),
                        accounting_operation_id=operation_id, response_sha256=response_hash,
                        compiler_input_sha256=compiler_input_sha256,
                        failure=_failure(exc, responsibility="research",
                            failure_stage="plan_adaptation_validation", failure_code=str(exc),
                            failure_layer="connection", transport_attempt=len(attempts),
                            request_sha256=request_hash, response_received=True))
                projected_plan_proposal = project_compiler_decision_v3(
                    decision=adaptation_decision.plan_decision,
                    envelope=envelope,
                )
                require_execution_resource_requirements(
                    adaptation_decision.plan_decision,
                    envelope,
                )
                if not adaptation_decision.plan_decision.is_sufficient:
                    reason = ValueError("plan_adaptation_insufficient")
                    reason.output_diagnostic = {
                        "insufficiency_code": adaptation_decision.plan_decision.insufficiency_code.value,
                        "capability_gaps": list(adaptation_decision.plan_decision.capability_gaps),
                        "unsatisfied_obligation_ids": list(adaptation_decision.plan_decision.unsatisfied_obligation_ids),
                        "concise_rationale": adaptation_decision.plan_decision.concise_rationale,
                        "input_assessment": adaptation_decision.plan_decision.input_assessment.model_dump(mode="json"),
                        "previous_plan_sha256": adaptation_decision.previous_plan_sha256,
                        "failure_evidence_sha256": adaptation_decision.failure_evidence_sha256,
                        "preserved_completed_step_ids": list(adaptation_decision.preserved_completed_step_ids),
                    }
                    return self._failed_artifact(
                        run_id=run_id, envelope=envelope, attempts=tuple(attempts),
                        accounting_operation_id=operation_id, response_sha256=response_hash,
                        compiler_input_sha256=compiler_input_sha256,
                        failure=_failure(reason, responsibility="research",
                            failure_stage="plan_adaptation_decision", failure_code="plan_adaptation_insufficient",
                            failure_layer="selection", transport_attempt=len(attempts),
                            request_sha256=request_hash, response_received=True))

                normalized_nested, normalization_audit = normalize_compiler_proposal_payload(
                    projected_plan_proposal.model_dump(mode="json")
                )
                normalized_payload = {
                    "adaptation_kind": adaptation_decision.adaptation_kind.value,
                    "preserved_completed_step_ids": list(
                        adaptation_decision.preserved_completed_step_ids
                    ),
                    "failure_evidence_sha256": adaptation_decision.failure_evidence_sha256,
                    "previous_plan_sha256": adaptation_decision.previous_plan_sha256,
                    "concise_adaptation_rationale": adaptation_decision.concise_adaptation_rationale,
                    "temporary_tool_transform": None,
                    "plan": normalized_nested,
                }
                self._normalization_audits[(key, semantic_attempt)] = normalization_audit
                require_compiler_proposal_invariants(
                    normalized_nested,
                    build_compiler_invariant_catalog(envelope),
                )
                require_plan_adaptation_invariants(
                    normalized_payload,
                    build_compiler_invariant_catalog(envelope),
                )
                nested_draft, projection_audit = project_compiler_plan_proposal(
                    proposal=projected_plan_proposal,
                    envelope=envelope,
                )
                adaptation_draft = PlanAdaptationDraft(
                    adaptation_kind=adaptation_decision.adaptation_kind,
                    preserved_completed_step_ids=adaptation_decision.preserved_completed_step_ids,
                    failure_evidence_sha256=adaptation_decision.failure_evidence_sha256,
                    previous_plan_sha256=adaptation_decision.previous_plan_sha256,
                    concise_adaptation_rationale=adaptation_decision.concise_adaptation_rationale,
                    temporary_tool_transform=None,
                    plan=nested_draft,
                )
                draft = nested_draft
                effective_draft_sha256 = adaptation_draft.draft_sha256
            self.store.append_event("compiler_semantic_projection_passed", {
                "plan_revision_sha256": key, "semantic_attempt": semantic_attempt,
                "role": "plan_adaptation" if adaptation_input is not None else "plan_compiler",
                "semantic_projection_passed": True,
            })
            if projection_audit is not None:
                self.store.append_event(
                    "plan_compiler_projection_audited",
                    {
                        "plan_revision_sha256": key,
                        "projection_audit_sha256": projection_audit.audit_sha256,
                        "projected_draft_sha256": projection_audit.projected_draft_sha256,
                        "semantic_round_trip": projection_audit.semantic_round_trip,
                    },
                )
        except RecoveryControlError as exc:
            self.store.append_event(
                "plan_adaptation_projection_unsafe",
                {
                    "plan_revision_sha256": key,
                    "request_sha256": request_hash,
                    "response_sha256": response_hash,
                },
            )
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                failure=_failure(
                    exc,
                    responsibility="framework",
                    failure_stage="plan_adaptation_projection",
                    failure_code="plan_adaptation_projection_unsafe",
                    failure_layer="framework",
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )
        except CompilerProposalInvariantError as exc:
            self._validation_issues[(key, semantic_attempt)] = exc.issues
            normalized_payload = (
                normalized_payload if "normalized_payload" in locals() else {}
            )
            if normalized_payload:
                self._correction_views[(key, semantic_attempt)] = (
                    build_compiler_proposal_correction_view(
                        normalized_payload.get("plan", {})
                        if adaptation_input is not None and isinstance(normalized_payload, Mapping)
                        else normalized_payload
                    )
                )
            self.store.append_event(
                "plan_compiler_proposal_invariant_invalid",
                {
                    "plan_revision_sha256": key,
                    "request_sha256": request_hash,
                    "response_sha256": response_hash,
                    "issue_sha256": canonical_sha256(
                        [item.model_dump(mode="json") for item in exc.issues]
                    ),
                    "issue_count": len(exc.issues),
                },
            )
            primary_issue = exc.issues[0]
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                failure=_failure(
                    exc,
                    responsibility="research",
                    failure_stage="plan_compiler_protocol",
                    failure_code=primary_issue.failure_code,
                    failure_layer=primary_issue.failure_layer,
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )
        except CompilerSchemaProjectionError as exc:
            if exc.issues:
                self._validation_issues[(key, semantic_attempt)] = exc.issues
                parsed_proposal = self._normalized_proposals.get((key, semantic_attempt), {})
                self._correction_views[(key, semantic_attempt)] = CompilerProposalCorrectionViewV1(
                    proposal_sha256=canonical_sha256(parsed_proposal),
                    normalized_structure=dict(parsed_proposal),
                )
            self.store.append_event("plan_compiler_schema_boundary_rejected", {
                "plan_revision_sha256": key, "failure_code": exc.code,
                "origin": exc.origin, "responsibility": exc.responsibility,
                "contract_path": exc.contract_path, "actual_type": exc.actual_type,
                "request_sha256": request_hash, "response_sha256": response_hash,
                **({
                    "phase": "v3_to_v2_projection",
                    "semantic_attempt": semantic_attempt,
                    "issue_count": len(exc.issues),
                    "issue_sha256": canonical_sha256([item.model_dump(mode="json") for item in exc.issues]),
                } if exc.issues else {}),
            })
            return self._failed_artifact(
                run_id=run_id, envelope=envelope, attempts=tuple(attempts),
                accounting_operation_id=operation_id, response_sha256=response_hash,
                failure=_failure(
                    exc, responsibility=exc.responsibility,
                    failure_stage=("planner_input_declaration" if exc.code == "required_input_missing" else "plan_compiler_projection"), failure_code=exc.code,
                    failure_layer="framework" if exc.responsibility == "framework" else "selection",
                    transport_attempt=len(attempts), request_sha256=request_hash, response_received=True,
                ),
            )
        except (PlanCompilerResponseError, ValidationError, ModelResponseContractError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                # Reuse value-safe diagnostics in memory; persistence is not required for correction.
                diagnostics = compiler_attempt_diagnostics.validation_error_diagnostics(
                    exc, secret_values=tuple(v for v in (os.environ.get("LLM_API_KEY"),) if v),
                    host_roots=(str(self.store.run_dir),),
                )
                self._validation_issues[(key, semantic_attempt)] = tuple(
                    CompilerProposalValidationIssueV1(
                        invariant_id="plan_compiler_response_shape_invalid",
                        path=tuple(item["loc"]), failure_layer="protocol",
                        failure_code="plan_compiler_response_shape_invalid",
                        authority_source=("PlanAdaptationDecisionV3" if adaptation_input is not None else "CompilerDecisionProposalV3"),
                        responsibility_stage="plan_compiler_protocol",
                        expected_active_fields=("Satisfy the response field type and cross-field conditions reported by the local validator.",),
                        observed_active_fields=(item["type"],), observed_value=item["msg"],
                    ) for item in diagnostics
                )
            error_text = str(exc)
            if isinstance(exc, ModelResponseContractError):
                if "json_invalid" in error_text:
                    failure_code = "plan_compiler_response_json_invalid"
                else:
                    failure_code = "plan_compiler_response_shape_invalid"
            elif isinstance(exc, ValidationError):
                failure_code = "plan_compiler_response_shape_invalid"
            elif isinstance(exc, PlanCompilerResponseError):
                failure_code = "plan_compiler_response_json_invalid"
            elif "projection" in error_text:
                failure_code = "plan_compiler_projection_failure"
            else:
                failure_code = "plan_compiler_response_shape_invalid"
            failure_responsibility: Literal["framework", "research"] = (
                "framework" if failure_code == "plan_compiler_projection_failure" else "research"
            )
            failure_layer: Literal["framework", "protocol"] = (
                "framework" if failure_responsibility == "framework" else "protocol"
            )
            if adaptation_input is None and not compiler_diagnostic_recorded:
                self._persist_attempt_diagnostic_best_effort(
                    run_id=run_id,
                    envelope=envelope,
                    api_kwargs=frozen_api_kwargs,
                    semantic_attempt=semantic_attempt,
                    request_sha256=request_hash,
                    response_sha256=response_hash,
                    structured_json=decoded,
                    validation_status="ingress_failed",
                    failure_class=failure_code,
                    validation_error=(exc if isinstance(exc, ValidationError) else None),
                )
            self.store.append_event(
                f"{failure_code}",
                {
                    "plan_revision_sha256": key,
                    "request_sha256": request_hash,
                    **plan_compiler_schema_failure_audit(
                        exc,
                        response_sha256=response_hash,
                        adaptation=adaptation_input is not None,
                    ),
                },
            )
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                failure=_failure(
                    exc,
                    responsibility=failure_responsibility,
                    failure_stage=(
                        "plan_compiler_projection"
                        if failure_responsibility == "framework"
                        else "plan_compiler_protocol"
                    ),
                    failure_code=failure_code,
                    failure_layer=failure_layer,
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )

        try:
            selected_format_contracts = self._bind_selected_format_contracts(
                draft=draft,
                envelope=envelope,
                decision=(decision if adaptation_input is None else adaptation_decision.plan_decision),
            )
            if selected_format_contracts:
                self.store.append_event(
                    "plan_selected_format_contracts_bound",
                    {
                        "plan_revision_sha256": key,
                        "step_contract_hashes": {
                            step_id: contract["format_contract_sha256"]
                            for step_id, contract in sorted(
                                selected_format_contracts.items()
                            )
                        },
                    },
                )
            plan, audit = self.validator.validate(
                envelope=envelope,
                draft=draft,
                resource_definitions=resource_definitions,
                compiler_input_sha256=compiler_input_sha256,
                invariant_catalog=build_compiler_invariant_catalog(envelope),
                selected_format_contracts=selected_format_contracts,
            )
            if adaptation_input is not None:
                if adaptation_draft is None:
                    raise RecoveryControlError("adaptation_draft_missing")
                validate_adapted_plan_lineage(
                    adapted_plan=plan,
                    adaptation_input=adaptation_input,
                    adaptation_draft=adaptation_draft,
                )
        except CompilerSchemaProjectionError as exc:
            self.store.append_event("plan_compiler_schema_boundary_rejected", {
                "plan_revision_sha256": key, "failure_code": exc.code,
                "origin": exc.origin, "responsibility": exc.responsibility,
                "contract_path": exc.contract_path, "actual_type": exc.actual_type,
            })
            return self._failed_artifact(
                run_id=run_id, envelope=envelope, attempts=tuple(attempts),
                accounting_operation_id=operation_id, response_sha256=response_hash,
                draft_sha256=effective_draft_sha256,
                failure=_failure(
                    exc, responsibility=exc.responsibility,
                    failure_stage="plan_compiler_selected_schema", failure_code=exc.code,
                    failure_layer="framework" if exc.responsibility == "framework" else "selection",
                    transport_attempt=len(attempts), request_sha256=request_hash, response_received=True,
                ),
            )
        except PlanValidationError as exc:
            from .compiler_output_facts import compiler_response_path
            response_proposal = self._normalized_proposals.get((key, semantic_attempt), {})
            issue = CompilerProposalValidationIssueV1(
                invariant_id=exc.invariant_id,
                path=compiler_response_path(exc.path or ("plan",), response_proposal),
                internal_path=exc.path,
                failure_layer=exc.failure_layer,
                failure_code=exc.code,
                authority_source="resource_runtime.output_contract" if exc.output_reachability else "compiler_validation",
                responsibility_stage="plan_compiler_validation",
                expected_active_fields=("exact or supported deterministic conversion; otherwise explicit authorized processing",) if exc.output_reachability else (),
                observed_active_fields=tuple(exc.output_reachability["reason_codes"]) if exc.output_reachability else (),
                output_reachability=exc.output_reachability,
                step_id=exc.step_id,
            )
            self._validation_issues[(key, semantic_attempt)] = (issue,)
            if exc.output_reachability:
                exc.output_diagnostic = issue.model_dump(mode="json")
                if adaptation_input is not None:
                    exc.output_diagnostic["response_path"] = ["plan_decision", *issue.path]
                else:
                    exc.output_diagnostic["response_path"] = list(issue.path)
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                draft_sha256=effective_draft_sha256,
                failure=_failure(
                    exc,
                    responsibility="research",
                    failure_stage="plan_compiler_validation",
                    failure_code=exc.code,
                    failure_layer=exc.failure_layer,
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )
        except RecoveryControlError as exc:
            if str(exc) == "adaptation_checkpoint_modified" and adaptation_input is not None:
                try:
                    self._validation_issues[(key, semantic_attempt)] = (
                        _checkpoint_modification_issue(adaptation_input, plan),
                    )
                except Exception:
                    # Diagnostic failure must not replace the original rejection or alter recovery.
                    self._validation_issues[(key, semantic_attempt)] = ()
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                draft_sha256=effective_draft_sha256,
                compiler_input_sha256=compiler_input_sha256,
                failure=_failure(
                    exc,
                    responsibility="research",
                    failure_stage="plan_adaptation_validation",
                    failure_code=str(exc),
                    failure_layer="connection",
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )
        except PlanFrameworkValidationError as exc:
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                draft_sha256=effective_draft_sha256,
                failure=_failure(
                    exc,
                    responsibility="framework",
                    failure_stage="plan_compiler_validation",
                    failure_code=exc.code,
                    failure_layer="framework",
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )

        self.store.append_event(
            "plan_validated",
            {
                "plan_revision_sha256": key,
                "draft_sha256": effective_draft_sha256,
                "validated_plan_sha256": plan.plan_sha256,
                "validation_audit_sha256": audit.audit_sha256,
            },
        )
        try:
            lowered, lowering_audit = self.lowerer.lower(
                run_id=run_id,
                plan=plan,
                envelope=envelope,
                resource_definitions=resource_definitions,
                selected_format_contracts=selected_format_contracts,
            )
        except PlanFrameworkValidationError as exc:
            return self._failed_artifact(
                run_id=run_id,
                envelope=envelope,
                attempts=tuple(attempts),
                accounting_operation_id=operation_id,
                response_sha256=response_hash,
                draft_sha256=effective_draft_sha256,
                failure=_failure(
                    exc,
                    responsibility="framework",
                    failure_stage="plan_compiler_lowering",
                    failure_code=exc.code,
                    failure_layer="framework",
                    transport_attempt=len(attempts),
                    request_sha256=request_hash,
                    response_received=True,
                ),
            )
        self.store.append_event(
            "plan_lowered",
            {
                "plan_revision_sha256": key,
                "validated_plan_sha256": plan.plan_sha256,
                "lowered_plan_semantic_sha256": lowered.plan_semantic_sha256,
                "lowering_sha256": lowered.lowering_sha256,
                "lowering_audit_sha256": lowering_audit.audit_sha256,
            },
        )
        return SealedPlanCompilationArtifact(
            run_id=run_id,
            plan_revision=envelope.plan_revision,
            status="success",
            contract_sha256=envelope.contract_projection.contract_sha256,
            candidate_pool_sha256=(
                envelope.candidate_pool_snapshot.candidate_pool_sha256
            ),
            retrieval_evidence_sha256=envelope.retrieval_evidence_sha256,
            pricing_catalog_sha256=envelope.pricing_catalog_sha256,
            prompt_sha256=envelope.prompt_sha256,
            compiler_input_sha256=compiler_input_sha256,
            compiler_model_resource_id=envelope.compiler_model_resource_id,
            compiler_model_api_id=envelope.compiler_model_api_id,
            accounting_operation_id=operation_id,
            transport_attempts=tuple(attempts),
            compiler_response_sha256=response_hash,
            compiler_draft_sha256=effective_draft_sha256,
            executable_plan=plan,
            validation_audit={
                **audit.model_dump(mode="json"),
                "input_alignment": alignment_evidence(
                    decision if adaptation_input is None else adaptation_decision.plan_decision, envelope),
                "compiler_projection": (
                    projection_audit.model_dump(mode="json")
                    if projection_audit is not None
                    else None
                ),
                **(
                    {
                        "adaptation": {
                            "adaptation_kind": adaptation_draft.adaptation_kind.value,
                            "preserved_completed_step_ids": list(
                                adaptation_draft.preserved_completed_step_ids
                            ),
                            "failure_evidence_sha256": (
                                adaptation_draft.failure_evidence_sha256
                            ),
                            "previous_plan_sha256": adaptation_draft.previous_plan_sha256,
                            "adaptation_draft_sha256": adaptation_draft.draft_sha256,
                            "temporary_tool_transform": (
                                adaptation_draft.temporary_tool_transform.model_dump(
                                    mode="json"
                                )
                                if adaptation_draft.temporary_tool_transform is not None
                                else None
                            ),
                        }
                    }
                    if adaptation_draft is not None
                    else {}
                ),
            },
            lowered_plan=lowered.model_dump(mode="json"),
            lowering_audit=lowering_audit.model_dump(mode="json"),
            lowered_plan_semantic_sha256=lowered.plan_semantic_sha256,
            lowering_sha256=lowered.lowering_sha256,
        )


__all__ = [
    "ExecutablePlanCompiler",
    "PLAN_COMPILER_PROMPT_SHA256",
    "PLAN_COMPILER_PROMPT_VERSION",
    "PLAN_COMPILER_SYSTEM_PROMPT_V2",
    "PLAN_ADAPTATION_PROMPT_SHA256",
    "PLAN_ADAPTATION_PROMPT_VERSION",
    "PLAN_ADAPTATION_SYSTEM_PROMPT_V1",
    "PlanCompilationStore",
    "PlanCompilerError",
    "PlanCompilerIdentityError",
    "PlanCompilerPayloadGuard",
    "PlanCompilerPersistenceError",
    "PlanCompilerResponseError",
    "build_candidate_constrained_response_requirement",
    "build_plan_compiler_call_kwargs",
    "build_plan_adaptation_call_kwargs",
    "build_compiler_public_context",
    "plan_compiler_schema_failure_audit",
]
