import os
import json
import re
import hashlib
from typing import Any, Dict, List, Optional, Tuple


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", "..", ".."))
MODEL_SOURCE_DIR = os.path.join(PROJECT_ROOT, "Pool", "resources", "models")
MODEL_HEALTH_FILE = os.path.join(PROJECT_ROOT, "sgar_mvp", "config", "model_health.json")


def _load_json_lenient(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(text, strict=False)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _rel_path(path: str) -> str:
    return os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")


def _sanitize_model_id(model_id: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
    return safe or "unknown"


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a", "unknown"}:
        return None
    try:
        return float(text.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _pricing_from_legacy_tags(tags: List[Any]) -> Optional[Dict[str, Optional[float]]]:
    if len(tags) < 4:
        return None
    input_price = _as_float(tags[1])
    output_price = _as_float(tags[2])
    cache_price = _as_float(tags[3])
    if input_price is None or output_price is None:
        return None
    return {
        "input_per_m": input_price,
        "cache_per_m": cache_price,
        "output_per_m": output_price,
    }


def _load_unavailable_model_ids() -> set[str]:
    if not os.path.exists(MODEL_HEALTH_FILE):
        return set()
    try:
        payload = _load_json_lenient(MODEL_HEALTH_FILE)
    except Exception:
        return set()

    unavailable = set(str(mid) for mid in payload.get("unavailable_model_ids", []))
    for item in payload.get("models", []):
        if isinstance(item, dict) and item.get("status") == "unavailable" and item.get("model_id"):
            unavailable.add(str(item["model_id"]))
    return unavailable


def _provider_family(model_id: str) -> Tuple[str, str, str]:
    lower = model_id.lower()
    if lower.startswith(("gpt-", "o1", "o3", "o4")) or "codex" in lower:
        family = "codex" if "codex" in lower else "gpt"
        return "openai", family, f"model.openai.{family}"
    if lower.startswith("claude"):
        return "anthropic", "claude", "model.anthropic.claude"
    if lower.startswith("qwen"):
        family = "qwen3" if lower.startswith("qwen3") else "qwen"
        return "qwen", family, f"model.qwen.{family}"
    if lower.startswith("deepseek"):
        family = "deepseek_reasoner" if "reason" in lower or "thinking" in lower else "deepseek"
        return "deepseek", family, f"model.deepseek.{family}"
    if lower.startswith("gemini"):
        return "google", "gemini", "model.google.gemini"
    if lower.startswith("glm"):
        return "zhipu", "glm", "model.zhipu.glm"
    if lower.startswith(("kimi", "moonshot")):
        return "moonshot", "kimi", "model.moonshot.kimi"
    if lower.startswith(("mistral", "ministral", "codestral")):
        return "mistral", "mistral", "model.mistral.general"
    return "unknown", "general", "model.unknown.general"


def _feature_flags(model_id: str, capability: Dict[str, Any], constraint: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    """Legacy callers get unknowns, never capabilities inferred from prose or names."""
    del model_id, capability, constraint
    return dict.fromkeys(("tool_calling", "json_mode", "vision", "reasoning", "coding"))


def _ordered_unique(values: List[Any]) -> List[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _build_problem_space(model_id: str, provider: str, family: str,
                         features: Dict[str, Any], source_problem_space: str) -> str:
    del provider, family, features
    return source_problem_space.strip() or f"Language model endpoint {model_id}; task capabilities are not documented."


def _declared_features(facts: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    flags = _feature_flags("", {}, {})
    declared = facts.get("supports", {})
    if not isinstance(declared, dict):
        raise ValueError("model_supports_mapping_invalid")
    for key, value in declared.items():
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"model_support_value_invalid:{key}")
        flags[str(key)] = value
    return flags


def _fixed_io_contract(features: Dict[str, Any], facts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Describe the configured chat envelope without inventing optional features.

    Native modality facts remain separate: a documented audio input does not
    implement an audio transport in the framework.
    """
    facts = facts or {}
    inputs = [{"name": "messages", "kind": "chat_messages", "required": True,
               "description": "Messages accepted by the configured gateway; native media support requires transport verification."}]
    if features.get("tool_calling") is True:
        inputs.append({"name": "tools", "kind": "tool_schema_array", "required": False,
                       "description": "Function schemas; the gateway must support the documented tool-call protocol."})
    if features.get("json_mode") is True:
        inputs.append({"name": "response_format", "kind": "json_schema" if features.get("structured_outputs") is True else "object", "required": False,
                       "description": "JSON output request; schema enforcement is a separate declared capability and requires gateway verification."})
    return {"input_contract": inputs, "output_contract": {
        "artifact_type": "plaintext", "description": "Generated text returned through the configured gateway."}}


def _convert_one(path: str, unavailable_model_ids: set[str], base_url: str) -> Dict[str, Any]:
    source = _load_json_lenient(path)
    source_model_id = str(source.get("resource_id") or os.path.splitext(os.path.basename(path))[0]).strip()
    execution = source.get("execution") if isinstance(source.get("execution"), dict) else {}
    api_model_id = execution.get("model_id", source_model_id)
    if not isinstance(api_model_id, str) or not api_model_id.strip():
        raise ValueError("source_api_model_id_invalid")
    api_model_id = api_model_id.strip()
    resource_id = f"model.{_sanitize_model_id(source_model_id)}.v1"
    tags = source.get("type", {}).get("resource_tag", [])
    tags = tags if isinstance(tags, list) else []
    context_window = str(tags[0]).strip() if tags else None
    pricing = _pricing_from_legacy_tags(tags)
    cap = source.get("capability") if isinstance(source.get("capability"), dict) else {}
    con = source.get("constraint") if isinstance(source.get("constraint"), dict) else {}
    specific = source.get("type_specific") if isinstance(source.get("type_specific"), dict) else {}
    facts = specific.get("model") if isinstance(specific.get("model"), dict) else {}
    features = _declared_features(facts)
    provenance = source.get("provenance") if isinstance(source.get("provenance"), dict) else {}
    provider, family, family_id = _provider_family(api_model_id)
    status = "unavailable" if any(x in unavailable_model_ids for x in (source_model_id, api_model_id, resource_id)) else "active"
    if status == "active" and str(provenance.get("integration_status", "")).startswith("source_registered_pending"):
        status = "inactive"
    utility = source.get("utility") if isinstance(source.get("utility"), dict) else {}
    memory = source.get("memory") if isinstance(source.get("memory"), dict) else {}
    attempts = int(utility.get("attempts") or 0)
    trajectory_count = len(memory.get("success_trajectories", []) or []) + len(memory.get("failure_reflections", []) or [])
    measured = attempts > 0 and trajectory_count >= attempts
    model = dict(facts)
    model.update({"model_id": api_model_id, "provider": provider, "family": family,
                  "context_window": context_window, "pricing": pricing,
                  "pricing_unit": "USD_per_million_tokens", "supports": features,
                  "supports_source": facts.get("evidence_status", "unknown")})
    domain_tags = _ordered_unique(cap.get("domain_tags", []))
    capability = dict(cap)
    capability.update({"core_primitives": _ordered_unique(cap.get("core_primitives", [])),
                       "problem_space": _build_problem_space(api_model_id, provider, family, features, cap.get("problem_space", "")),
                       "domain_tags": domain_tags})
    rel = _rel_path(path)
    return {
        "manifest_version": "1.0", "resource_id": resource_id, "resource_type": "Model", "status": status,
        "capability": capability,
        "constraint": {**con, "artifact_input": list(facts.get("input_modalities", ["text"])),
                       "artifact_output": list(facts.get("output_modalities", ["text"]))},
        "io": _fixed_io_contract(features, facts),
        "routing": {"family_id": family_id, "dependency_slots": []},
        "execution": {"runtime": "llm_chat_completion", "uri": execution.get("uri") or f"{base_url.rstrip('/')}/chat/completions",
                      "model_id": api_model_id, "execution_status": status},
        "utility": {"latency_ms": float(utility.get("latency_ms") or 0) if measured else 0,
                    "token_cost_factor": utility.get("token_cost_factor", 0),
                    "expected_success_rate": float(utility.get("expected_success_rate") or 0) if measured else 0,
                    "successes": int(utility.get("successes") or 0) if measured else 0,
                    "attempts": attempts if measured else 0},
        "provenance": {**provenance, "source_dataset": "local_model_pool", "source_item_id": rel,
                       "source_uri": f"file://{rel}", "source_hash": _sha256(path),
                       "conversion_method": "declared_model_capabilities_v2", "license": "provider-service-terms",
                       "utility_status": "empirical" if measured else "unmeasured_zero_placeholders"},
        "type_specific": {"model": model},
        "memory": memory or {"success_trajectories": [], "failure_reflections": []},
    }


def run_ingestion(
    client: Any,
    api_key: Optional[str],
    base_url: str,
    model: str,
    limit: int = 0,
    only: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Deterministic Model ingestion entry point.

    This converter does not call an LLM. It scans Pool/resources/models/*.json and
    converts each legacy model config into a strict Manifest.V1 draft.
    """
    print("=" * 60)
    print("S-GAR MODEL INGESTION MODULE:")
    print("  Mode: rule-based Manifest.V1 conversion (no LLM API calls)")
    print(f"  Source dir: {MODEL_SOURCE_DIR}")
    print("=" * 60 + "\n")

    if not os.path.isdir(MODEL_SOURCE_DIR):
        print(f"[Model Ingestion] Source directory not found: {MODEL_SOURCE_DIR}")
        return []

    from sgar_mvp.src.model_selection import load_model_selection, registered_models, apply_model_selection
    selection = load_model_selection(PROJECT_ROOT)
    registered = registered_models(selection)
    unavailable_model_ids = _load_unavailable_model_ids()
    drafts: List[Dict[str, Any]] = []
    files = sorted(
        os.path.join(MODEL_SOURCE_DIR, name)
        for name in os.listdir(MODEL_SOURCE_DIR)
        if name.endswith(".json") and os.path.isfile(os.path.join(MODEL_SOURCE_DIR, name))
    )

    for path in files:
        if only and only.lower() not in os.path.basename(path).lower():
            continue
        try:
            manifest = _convert_one(path, unavailable_model_ids, base_url)
        except Exception as exc:
            print(f"  [ERROR] Failed to convert {os.path.basename(path)}: {exc}")
            continue
        if manifest["resource_id"] not in registered:
            continue
        manifest = apply_model_selection(manifest, selection)
        drafts.append(manifest)
        model_id = manifest["type_specific"]["model"]["model_id"]
        print(f"  [OK] {model_id} -> {manifest['resource_id']} ({manifest['status']})")

    if limit and limit > 0:
        print("  [Notice] --limit is ignored for Model conversion because no LLM calls are made.")

    print(f"\n[Model Ingestion] Produced {len(drafts)} Manifest.V1 model draft(s).")
    return drafts
