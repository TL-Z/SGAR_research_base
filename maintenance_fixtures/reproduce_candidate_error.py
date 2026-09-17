from __future__ import annotations

import json
from pathlib import Path

import retrieve

from sgar_mvp.src.resource_loader import load_real_pool_with_index
from sgar_mvp.src.retrieval_runtime import (
    AppliedReadyStateCapabilityService,
    FrozenCandidatePoolResult,
    RetrievalContractProjection,
    RetrievalRuntimeIdentity,
    _CandidatePoolBuilder,
    _default_profile_encoder,
    build_retrieval_runtime_identity,
    load_applied_model_ready_state,
    project_retrieval_contract,
)
from sgar_mvp.src.pipeline_control import (
    RetrievalAttemptOutcome,
    RetrievalAttemptRecord,
    SubtaskRevisionRef,
    canonical_sha256,
)
from sgar_mvp.src.model_response_contracts import DEFAULT_CAPABILITY_PROBE_ENFORCEMENT_POLICY


ROOT = Path(__file__).resolve().parents[1]
RUN = Path("/ssd/zhoutianle/runtime/sgar/maintenance/resource-hash-readiness-composition-20260916/composition-run/20260915T184804Z_3bde37251fc8")


def main() -> None:
    event = next(
        item
        for item in (json.loads(line) for line in (RUN / "trace.jsonl").read_text().splitlines())
        if item.get("event_type") == "planner_trace"
    )
    subtask = event["subtasks"][0]
    from sgar_mvp.src.schema import Subtask

    subtask_obj = Subtask.model_validate(subtask)
    revision = SubtaskRevisionRef(graph_revision=0, subtask_id="task_001", subtask_revision=0)
    public_context = [
        {
            "logical_name": "dataset",
            "source_name": "v2_material.json",
            "artifact_type": "json",
            "sha256": "389cf515dc408f226aeb1ef46129df1b525eb396db14f884671069e00c7d2fb3",
            "coverage_status": "handle_only",
            "original_bytes": 86,
            "included_bytes": 0,
            "handle_available": True,
        }
    ]
    contract = project_retrieval_contract(revision, subtask_obj, public_context=public_context)
    artifact = {
        "revision": revision.model_dump(mode="json"),
        "contract_sha256": contract.contract_sha256,
        "raw_query_text": "",
        "capability_text": "A verification-oriented implementation agent for dataset evidence notes that can obtain the complete declared JSON material from the authorized handle for `v2_material.json` via `tool.mcp.fs_read_file.v1::read_text_file`, use the real Tool result in the same Agent controller session, derive the dataset row count and total value, and author a concise Markdown note that cites the read result and checks for unsupported claims.",
        "constraint_text": "Must use the supplied JSON dataset `v2_material.json` as the sole evidence source and read its complete content through the authorized handle; coverage is `handle_only` with `handle_available` true, so the content must be obtained rather than inferred from metadata. Must invoke `tool.mcp.fs_read_file.v1::read_text_file`, use the real Tool result returned to the same Agent controller session, state row count 2 and total value 8, cite that Tool result, avoid unsupported values or outside evidence, and emit one concise required Markdown artifact at `resource_composition_evidence.md` with output extension `.md`.",
        "think": "The typed contract declares a handle-only JSON input, an authorized read operation with same-session Tool-result grounding, and a concise Markdown output whose acceptance checks are row count 2, total value 8, citation, and dataset-only evidence; these facts determine the read, derivation, citation, and verification profile.",
        "explicit_hard_requirements": {"public_input_artifact_types": ["json"]},
        "prompt_version": "ideal-resource-profiler-en-v3",
        "prompt_sha256": "1cc62d801ba97b3c47f15516845be0f7d83027038e7dc0dc625c6eea3350ed99",
    }
    from sgar_mvp.src.retrieval_runtime import IdealResourceProfileArtifact, EncodedIdealResourceProfile

    artifact_obj = IdealResourceProfileArtifact.model_validate(artifact)
    vector = tuple(float(x) for x in retrieve.encode_query(artifact_obj.capability_text, role="capability"))
    attempt = RetrievalAttemptRecord(
        revision=revision,
        attempt=1,
        query_sha256=canonical_sha256(artifact_obj.raw_query_text),
        profile_sha256=artifact_obj.profile_sha256,
        policy_sha256="83b05aaba36dae05af16c278aeeaf7148a8e44c8a1c33d58909b55171bdfd296",
        index_sha256="a06620387f88be4c1b5a1f3599023dccff0dded4f928386a7b1a8ecc34baaf9e",
        pool_sha256="567bde83d6c8959a9edbdb4c68a6f4b7717781e688a49e4dbce9eca5535e7283",
        outcome=RetrievalAttemptOutcome.SUCCESS,
    )
    profile = EncodedIdealResourceProfile(
        artifact=artifact_obj,
        capability_vector=vector,
        dimension=len(vector),
        retrieval_attempts=(attempt,),
    )
    endpoint = json.loads((ROOT / "sgar_mvp/runtime_state/model_ready_state.json").read_text())["endpoint_identity_sha256"]
    identity = build_retrieval_runtime_identity(
        project_root=ROOT, provider_endpoint_identity_sha256=endpoint
    )
    library, _, resource_index = load_real_pool_with_index()
    ready_state = load_applied_model_ready_state(ROOT, expected_endpoint_identity_sha256=endpoint)
    capability = AppliedReadyStateCapabilityService(
        ready_state, enforcement_policy=DEFAULT_CAPABILITY_PROBE_ENFORCEMENT_POLICY
    )
    print("counts", len(library), len(resource_index), len(identity.eligible_resource_ids))
    print("library-not-identity", sorted({item.id for item in library} - set(identity.eligible_resource_ids))[:20])
    print("identity-not-library", sorted(set(identity.eligible_resource_ids) - {item.id for item in library})[:20])
    builder = _CandidatePoolBuilder(
        identity=identity,
        contract=contract,
        profile=profile,
        library=library,
        resource_index=resource_index,
        local_text_encoder=lambda text: retrieve.encode_query(text, role="capability"),
        capability_probe_service=capability,
        model_liveness_probe_service=None,
        cost_ledger=None,
        model_readiness_authority="applied_ready_state",
    )
    try:
        result = builder.build()
    except Exception as exc:
        print(type(exc).__name__)
        if hasattr(exc, "errors"):
            print(json.dumps(exc.errors(), indent=2, default=str))
        else:
            print(repr(exc))
        raise
    print(result.model_dump_json(indent=2)[:500])


if __name__ == "__main__":
    main()
