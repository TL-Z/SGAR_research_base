"""Validate the active local index and run one directed query per resource type."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
import sys

import faiss

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import retrieve
from sgar_mvp.src.retrieval_runtime import (
    build_retrieval_runtime_identity,
    validate_loaded_retrieval_backend,
)


INDEX_DIR = ROOT / "Pool/index_meta"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = json.loads((INDEX_DIR / "index_build_manifest.json").read_text())
    policy = json.loads((ROOT / "sgar_mvp/config/retrieval_policy.json").read_text())
    readiness = json.loads((ROOT / "sgar_mvp/config/resource_readiness_rc1.json").read_text())
    effective = json.loads((ROOT / "Pool/resources/json/effective_combine.json").read_text())
    capability = faiss.read_index(str(INDEX_DIR / "faiss_cap.index"))
    constraint = faiss.read_index(str(INDEX_DIR / "faiss_con.index"))
    with (INDEX_DIR / "resource_meta.pkl").open("rb") as handle:
        metadata = pickle.load(handle)
    identity = build_retrieval_runtime_identity(project_root=ROOT)
    validate_loaded_retrieval_backend(identity, project_root=ROOT)
    queries = {
        "Model": "general reasoning language model with structured JSON output",
        "Agent": "implementation agent that can coordinate a bounded task",
        "Tool": "read one local text file without modifying it",
        "Skill": "verify evidence before claiming a task is complete",
    }
    results = {}
    for resource_type, query in queries.items():
        profile = retrieve.encode_query_profile(query, use_hyde=False)
        ranked = retrieve.rank_resources(
            profile,
            strategy=retrieve.RETRIEVAL_POLICY.active_strategy,
            resource_type=resource_type,
            top_k=3,
        )
        results[resource_type] = [item["resource_id"] for item in ranked]
    resource_count = len(metadata["idx_to_id"])
    report = {
        "protocol": "sgar-active-index-validation-v1",
        "generation_id": manifest["generation_id"],
        "resource_count": resource_count,
        "effective_count": len(effective),
        "readiness_effective_count": readiness["summary"]["effective_total"],
        "dimension": metadata["dim"],
        "capability_index": {"rows": capability.ntotal, "dimension": capability.d},
        "constraint_index": {"rows": constraint.ntotal, "dimension": constraint.d},
        "embedding_model": policy["embedding_model"],
        "embedding_runtime_identity_sha256": policy[
            "embedding_runtime_identity_sha256"
        ],
        "component_sha256": {
            name: _sha256(INDEX_DIR / name)
            for name in (
                "faiss_cap.index",
                "faiss_con.index",
                "resource_meta.pkl",
                "retrieval_profile_audit.json",
                "index_build_manifest.json",
            )
        },
        "runtime_identity_sha256": identity.identity_sha256,
        "loader_validation": "passed",
        "directed_queries": results,
    }
    counts = {
        resource_count,
        len(effective),
        readiness["summary"]["effective_total"],
        capability.ntotal,
        constraint.ntotal,
    }
    if counts != {328} or metadata["dim"] != 2560:
        raise SystemExit("active_index_count_or_dimension_mismatch")
    if any(not values for values in results.values()):
        raise SystemExit("directed_retrieval_empty")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
