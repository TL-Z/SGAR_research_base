"""Synchronize the curated Model pool's availability with the relay catalog.

This is a zero-token catalog check.  It does not infer capabilities from model
names and does not send chat-completion prompts.  Detailed capability smoke
tests remain the responsibility of ``check_model_health.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import certifi
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from sgar_mvp.src.direct_network import direct_sync_http_client
MODELS_FILE = PROJECT_ROOT / "Pool" / "resources" / "json" / "models.json"
GATE_FILE = PROJECT_ROOT / "sgar_mvp" / "config" / "model_health.json"
DEFAULT_BASE_URL = "https://svip.xty.app/v1"

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


def _load_env_value(*names: str) -> str:
    dotenv_values: Dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            dotenv_values[name.strip()] = value.strip()
    for name in names:
        value = os.environ.get(name, "").strip() or dotenv_values.get(name, "")
        if value:
            return value
    return ""


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _api_model_id(raw: Dict[str, Any]) -> str:
    model = raw.get("type_specific", {}).get("model", {})
    execution = raw.get("execution", {})
    return str(model.get("model_id") or execution.get("model_id") or "")


def build_gate(
    manifests: List[Dict[str, Any]],
    offered_ids: set[str],
    previous: Dict[str, Any],
    base_url: str,
) -> Dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    previous_by_id = {
        str(item.get("model_id")): item
        for item in previous.get("models", [])
        if isinstance(item, dict) and item.get("model_id")
    }
    models: List[Dict[str, Any]] = []
    unavailable: List[str] = []
    for raw in manifests:
        resource_id = str(raw.get("resource_id") or "")
        model_id = _api_model_id(raw)
        old = dict(previous_by_id.get(model_id, {}))
        available = model_id in offered_ids
        if not available:
            unavailable.extend([model_id, resource_id])
        old.update(
            {
                "resource_id": resource_id,
                "model_id": model_id,
                "status": "catalog_available" if available else "unavailable",
                "catalog_available": available,
                "catalog_checked_at": checked_at,
            }
        )
        if available and old.get("error_code") == "model_not_found":
            old["error_code"] = None
            old["error_type"] = None
            old["message"] = "Present in provider model catalog; detailed smoke not rerun."
        elif not available:
            old["error_code"] = "catalog_model_not_found"
            old["message"] = "Not present in the provider model catalog."
        models.append(old)
    return {
        "generated_at": checked_at,
        "source": "provider_model_catalog",
        "base_url": base_url,
        "catalog_model_count": len(offered_ids),
        "curated_model_count": len(manifests),
        "available_model_count": sum(
            1 for item in models if item.get("catalog_available")
        ),
        "unavailable_model_count": sum(
            1 for item in models if not item.get("catalog_available")
        ),
        "unavailable_model_ids": sorted(set(item for item in unavailable if item)),
        "models": models,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-gate", action="store_true")
    parser.add_argument("--output", type=Path, default=GATE_FILE)
    args = parser.parse_args()

    api_key = _load_env_value("LLM_API_KEY", "OPENAI_API_KEY")
    if not api_key:
        raise ValueError("LLM_API_KEY or OPENAI_API_KEY is required")
    base_url = (
        _load_env_value("LLM_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL
    ).rstrip("/")
    client = OpenAI(http_client=direct_sync_http_client(), api_key=api_key, base_url=base_url, timeout=60.0)
    offered_ids = {str(model.id) for model in client.models.list()}
    manifests = _load_json(MODELS_FILE, [])
    previous = _load_json(GATE_FILE, {})
    gate = build_gate(manifests, offered_ids, previous, base_url)

    summary = {
        key: gate[key]
        for key in (
            "catalog_model_count",
            "curated_model_count",
            "available_model_count",
            "unavailable_model_count",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.write_gate:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(gate, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
