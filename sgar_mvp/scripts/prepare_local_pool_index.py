"""Prepare an index-bound, explicitly unsealed local model-pool generation.

Run as a module with --project-root and --staged-root. The staged root must
already contain reviewed catalogs, registry, readiness and applied model health.
This only writes staging; activation is a separate operator action.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(project_root: Path, staged_root: Path):
    root, stage = project_root.resolve(), staged_root.resolve()
    if root == stage or root in stage.parents or stage in root.parents:
        raise ValueError("staging_must_be_separate_from_project")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    release = _read(root / "sgar_mvp/config/embedding_release.json")
    if release.get("cache_root"):
        os.environ["HF_HOME"] = str(release["cache_root"])
    import numpy as np
    import faiss
    from build_index import build_metadata, build_faiss_index
    from retrieval_profiles import build_constraint_profile, validate_resource_profiles
    from sgar_mvp.src.embedding_runtime import LocalEmbeddingEncoder, load_embedding_release_config
    from sgar_mvp.src.pipeline_control import canonical_sha256
    from sgar_mvp.src.model_selection import require_registered_models
    from sgar_mvp.src.retrieval_runtime import build_retrieval_runtime_identity, load_applied_model_ready_state
    from sgar_mvp.src.retrieval_policy import RetrievalPolicy

    old_dir = root / "Pool/index_meta"
    old_policy = _read(root / "sgar_mvp/config/retrieval_policy.json")
    for key, name in (("capability", "faiss_cap.index"), ("constraint", "faiss_con.index"), ("metadata", "resource_meta.pkl")):
        if _hash(old_dir / name) != old_policy["index_sha256"][key]:
            raise ValueError("parent_index_identity_mismatch")
    old = pickle.loads((old_dir / "resource_meta.pkl").read_bytes())
    resources = _read(stage / "Pool/resources/json/effective_combine.json")
    catalog = _read(stage / "Pool/resources/json/combine.json")
    require_registered_models(catalog, root=stage, complete=True)
    require_registered_models(resources, root=stage)
    new_by_id = {r["resource_id"]: r for r in resources}
    old_by_id = old["id_to_resource"]
    if len(new_by_id) != len(resources):
        raise ValueError("duplicate_resource_identity")
    changed = set(new_by_id) ^ set(old_by_id)
    changed |= {k for k in set(new_by_id) & set(old_by_id) if canonical_sha256(new_by_id[k]) != canonical_sha256(old_by_id[k])}
    metadata_only_vector_reuse = []
    for rid in sorted(changed):
        old_resource, new_resource = old_by_id.get(rid), new_by_id.get(rid)
        if old_resource is None or new_resource is None:
            resource = old_resource or new_resource
            if resource is not None and (resource.get("resource_type") or resource.get("type", {}).get("resource_type")) != "Model":
                raise ValueError("local_non_model_resource_set_change_requires_full_rebuild")
            continue
        resource_type = new_resource.get("resource_type") or new_resource.get("type", {}).get("resource_type")
        if resource_type == "Model":
            continue
        old_profile = build_constraint_profile(old_resource)
        new_profile = build_constraint_profile(new_resource)
        if (
            old_profile.capability_text != new_profile.capability_text
            or old_profile.soft_constraint_text != new_profile.soft_constraint_text
        ):
            raise ValueError("local_non_model_profile_change_requires_reembedding")
        metadata_only_vector_reuse.append(rid)
    audit = validate_resource_profiles(resources, project_root=root)
    if not audit["valid"]:
        raise ValueError("resource_profile_invalid")
    config = load_embedding_release_config(root / "sgar_mvp/config/embedding_release.json")
    encoder = LocalEmbeddingEncoder(config)
    local_identity = encoder.identity().model_dump(mode="json")
    runtime_identity = encoder.runtime_identity_v2().model_dump(mode="json")
    if runtime_identity != old["embedding_runtime_identity_v2"] or config.model_dump(mode="json") != old["embedding_runtime_config"]:
        raise ValueError("embedding_runtime_changed_full_rebuild_required")
    profiles = [build_constraint_profile(r) for r in resources]
    audit["metadata_only_vector_reuse_resource_ids"] = metadata_only_vector_reuse
    matrices = []
    encoded = 0
    for vectors_key, texts_key, attribute in (("cap_vectors", "capability_texts", "capability_text"), ("con_vectors", "constraint_texts", "soft_constraint_text")):
        rows = np.empty((len(resources), old["dim"]), dtype="float32")
        missing, texts = [], []
        for i, profile in enumerate(profiles):
            rid, text = profile.resource_id, getattr(profile, attribute)
            if rid in old["id_to_idx"] and old[texts_key].get(rid) == text:
                rows[i] = old[vectors_key][old["id_to_idx"][rid]]
            else:
                missing.append(i); texts.append(text)
        if texts:
            rows[missing] = encoder.encode_documents(texts)
            encoded += len(texts)
        matrices.append(rows)
    encoder.close()
    ready_path = stage / "sgar_mvp/config/resource_readiness_rc1.json"
    readiness = _read(ready_path)
    health = _read(stage / "sgar_mvp/runtime_state/model_ready_state.json")
    load_applied_model_ready_state(stage, expected_endpoint_identity_sha256=health["endpoint_identity_sha256"])
    aliases = old.get("duplicate_resource_aliases", {})
    metadata = build_metadata(resources, *matrices, aliases, readiness=readiness, readiness_hash=_hash(ready_path), embedding_config=config, embedding_identity=local_identity, embedding_runtime_identity_v2=runtime_identity, source_seal=None)
    audit.update(indexed_resource_count=len(resources), duplicate_resource_aliases=aliases)
    out = stage / "Pool/index_meta"; out.mkdir(parents=True, exist_ok=True)
    for name, rows in zip(("faiss_cap.index", "faiss_con.index"), matrices):
        faiss.write_index(build_faiss_index(rows), str(out / name))
    (out / "resource_meta.pkl").write_bytes(pickle.dumps(metadata, protocol=pickle.HIGHEST_PROTOCOL))
    _write(out / "retrieval_profile_audit.json", audit)
    files = {name: _hash(out / name) for name in ("faiss_cap.index", "faiss_con.index", "resource_meta.pkl", "retrieval_profile_audit.json")}
    manifest = {
        "protocol": "sgar-retrieval-index-build-v1", "generation_kind": "local_model_pool_update",
        "generation_id": canonical_sha256(files)[:32], "release_source_seal": None,
        "catalog_sha256": _hash(stage / "Pool/resources/json/combine.json"),
        "effective_pool_sha256": _hash(stage / "Pool/resources/json/effective_combine.json"),
        "embedding_configuration_sha256": config.configuration_sha256,
        "embedding_identity_sha256": local_identity["identity_sha256"],
        "embedding_runtime_identity_v2_sha256": runtime_identity["identity_sha256"],
        "index_meta_v2_sha256": metadata["index_meta_v2"]["index_meta_sha256"],
        "resource_count": len(resources), "resource_order_sha256": metadata["resource_order_sha256"],
        "dimension": metadata["dim"], "files_sha256": files,
        "build_code_sha256": _hash(Path(__file__)),
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    _write(out / "index_build_manifest.json", manifest)
    policy = dict(old_policy)
    policy.update(policy_version="retrieval-policy-v7-local-" + manifest["generation_id"], release_source_seal_sha256=None, effective_pool_sha256=manifest["effective_pool_sha256"], index_meta_sha256=manifest["index_meta_v2_sha256"], index_build_manifest_sha256=manifest["manifest_sha256"], index_sha256={k: files[n] for k,n in (("capability","faiss_cap.index"),("constraint","faiss_con.index"),("metadata","resource_meta.pkl"))})
    RetrievalPolicy.model_validate(policy)
    _write(stage / "sgar_mvp/config/retrieval_policy.json", policy)
    identity = build_retrieval_runtime_identity(project_root=stage, provider_endpoint_identity_sha256=health["endpoint_identity_sha256"], honor_release_environment=False)
    return {"encoded_document_count": encoded, "resource_count": len(resources), "changed_resource_ids": sorted(changed), "metadata_only_vector_reuse_resource_ids": metadata_only_vector_reuse, "runtime_identity_sha256": identity.identity_sha256, "release_sealed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--staged-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.project_root, args.staged_root), indent=2))
