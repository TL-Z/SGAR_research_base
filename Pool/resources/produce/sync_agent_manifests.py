"""Synchronize Agent Card metadata into the checked-in Agent manifests.

This script deliberately preserves the hand-authored capability, contract, and
routing metadata.  It only:

* refreshes Agent Card SHA-256 hashes;
* removes legacy fixed/default model constraints; and
* migrates the old hard-allowlist field to non-exclusive recommendation hints.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
JSON_DIR = PROJECT_ROOT / "Pool" / "resources" / "json"
AGENTS_JSON = JSON_DIR / "agents.json"
COMBINE_JSON = JSON_DIR / "combine.json"
COMBINE_SOURCES = (
    "agents.json",
    "device.json",
    "models.json",
    "resources.json",
    "skills.json",
    "tools.json",
)
DEPENDENCY_SECTION = "## Recommended Dependencies (Non-Exclusive)"
NEXT_SECTION = re.compile(r"^##\s+", re.MULTILINE)
DEPENDENCY_ID = re.compile(r"^\s*-\s*`([^`]+)`\s*$", re.MULTILINE)
FAKE_RUNTIME = re.compile(
    r"""(?ix)
    ["']status["']\s*:\s*["']simulated["']
    | \#\s*simulated\b
    | \bplaceholder\b
    | \bmock\W+result\b
    """
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_index() -> dict[str, dict[str, Any]]:
    resources: dict[str, dict[str, Any]] = {}
    ignored = {"agents.json", "combine.json"}
    for path in JSON_DIR.glob("*.json"):
        if path.name in ignored or path.name.endswith(("_draft.json", "_derived.json")):
            continue
        payload = _load_json(path)
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("resource_id"):
                resources[str(item["resource_id"])] = item
    return resources


def _recommended_dependencies(card_text: str, resource_id: str) -> list[str]:
    if DEPENDENCY_SECTION not in card_text:
        raise ValueError(f"{resource_id} does not declare non-exclusive dependencies")
    tail = card_text.split(DEPENDENCY_SECTION, 1)[1]
    next_heading = NEXT_SECTION.search(tail)
    section = tail[: next_heading.start()] if next_heading else tail
    dependencies = DEPENDENCY_ID.findall(section)
    if len(dependencies) != len(set(dependencies)):
        raise ValueError(f"{resource_id} has duplicate recommended dependencies")
    return dependencies


def _validate_dependency_entrypoint(
    agent_id: str,
    dependency_id: str,
    resource_index: dict[str, dict[str, Any]],
) -> None:
    dependency = resource_index[dependency_id]
    uri = str(dependency.get("execution", {}).get("uri") or "")
    if not uri.startswith("file://"):
        raise ValueError(
            f"{agent_id} dependency {dependency_id} has no local execution/read URI"
        )
    path = PROJECT_ROOT / uri.removeprefix("file://")
    if not path.is_file():
        raise FileNotFoundError(
            f"{agent_id} dependency {dependency_id} entrypoint missing: {path}"
        )
    if (
        dependency.get("resource_type") == "Tool"
        and FAKE_RUNTIME.search(path.read_text(encoding="utf-8", errors="ignore"))
    ):
        raise ValueError(
            f"{agent_id} recommends simulated/placeholder Tool {dependency_id}"
        )


def sync_agent_manifests() -> None:
    agents = _load_json(AGENTS_JSON)
    if not isinstance(agents, list):
        raise ValueError(f"{AGENTS_JSON} must contain a JSON array")

    resource_index = _resource_index()
    known_dependency_ids = set(resource_index)
    seen_agent_ids: set[str] = set()

    for manifest in agents:
        resource_id = str(manifest.get("resource_id", ""))
        if not resource_id or resource_id in seen_agent_ids:
            raise ValueError(f"Missing or duplicate Agent resource_id: {resource_id!r}")
        seen_agent_ids.add(resource_id)

        execution = manifest.setdefault("execution", {})
        if execution.get("runtime") != "prompt_agent":
            raise ValueError(f"{resource_id} is not a prompt_agent")
        execution.pop("default_base_model", None)
        execution.pop("allowed_base_models", None)

        agent_meta = manifest.setdefault("type_specific", {}).setdefault("agent", {})
        agent_meta.pop("default_base_model", None)
        agent_meta.pop("allowed_base_models", None)
        legacy_dependencies = agent_meta.pop("allowed_dependency_ids", None)
        if legacy_dependencies is not None:
            existing = agent_meta.get("recommended_dependency_ids")
            if existing is not None and existing != legacy_dependencies:
                raise ValueError(
                    f"{resource_id} has conflicting allowed/recommended dependencies"
                )
            agent_meta["recommended_dependency_ids"] = legacy_dependencies

        card_uri = str(
            agent_meta.get("agent_card_uri")
            or execution.get("uri")
            or manifest.get("provenance", {}).get("source_uri")
            or ""
        )
        if not card_uri.startswith("file://"):
            raise ValueError(f"{resource_id} has no local Agent Card URI")
        card_path = PROJECT_ROOT / card_uri.removeprefix("file://")
        if not card_path.is_file():
            raise FileNotFoundError(f"{resource_id} Agent Card missing: {card_path}")

        card_bytes = card_path.read_bytes()
        card_text = card_bytes.decode("utf-8")
        if "## Allowed Dependencies" in card_text:
            raise ValueError(f"{resource_id} still declares a hard dependency allowlist")
        dependencies = _recommended_dependencies(card_text, resource_id)
        agent_meta["recommended_dependency_ids"] = dependencies

        digest = f"sha256:{hashlib.sha256(card_bytes).hexdigest()}"
        manifest.setdefault("provenance", {})["source_hash"] = digest
        agent_meta["agent_card_hash"] = digest

        missing = sorted(set(dependencies) - known_dependency_ids)
        if missing:
            raise ValueError(f"{resource_id} has unresolved dependencies: {missing}")
        for dependency_id in dependencies:
            _validate_dependency_entrypoint(resource_id, dependency_id, resource_index)

    if len(agents) != 20:
        raise ValueError(f"Expected 20 Agent manifests, found {len(agents)}")

    AGENTS_JSON.write_text(
        json.dumps(agents, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )

    combined: list[dict[str, Any]] = []
    for name in COMBINE_SOURCES:
        payload = agents if name == "agents.json" else _load_json(JSON_DIR / name)
        items = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError(f"{name} must contain a manifest or manifest array")
        combined.extend(items)
    COMBINE_JSON.write_text(
        json.dumps(combined, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    print(
        f"Synchronized {len(agents)} Agent manifests and "
        f"{len(combined)} combined entries"
    )


if __name__ == "__main__":
    sync_agent_manifests()
