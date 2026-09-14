"""Normalize canonical resource semantics to the release English policy.

Only the three approved semantic fields are changed.  Resource identities,
execution contracts, ports, dependencies, utilities, and provider API names
are deliberately outside this migration surface.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MODELS_PATH = ROOT / "Pool" / "resources" / "json" / "models.json"
REGISTRY_ROOT = ROOT / "Pool" / "resources" / "tools" / "registry"
LEGACY_MARKER = "\n\nLegacy description:\n"
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

CANONICAL_TAGS = {
    "Python工具": "python-tools",
    "Web开发": "web-development",
    "代码分析": "code-analysis",
    "代码审计": "code-audit",
    "代码格式化": "code-formatting",
    "代码检查": "code-checking",
    "代码质量": "code-quality",
    "依赖管理": "dependency-management",
    "内存管理": "memory-management",
    "安全": "security",
    "安全审计": "security-audit",
    "工具": "tools",
    "开发工具": "development-tools",
    "性能优化": "performance-optimization",
    "数据处理": "data-processing",
    "文件处理": "file-processing",
    "文本处理": "text-processing",
    "文本提取": "text-extraction",
    "格式转换": "format-conversion",
    "测试": "testing",
    "测试工具": "testing-tools",
    "版本控制": "version-control",
    "编程工具": "programming-tools",
    "网络": "networking",
    "网络安全": "network-security",
    "自动化": "automation",
    "自动化工具": "automation-tools",
    "自动化测试": "automated-testing",
    "软件工程": "software-engineering",
    "软件开发": "software-development",
    "静态分析": "static-analysis",
}


class ResourceSemanticMigrationError(ValueError):
    pass


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, payload: Any, *, indent: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
    path.write_text(rendered, encoding="utf-8", newline="\n")


def _replace_tags(tags: Any, *, resource_id: str) -> list[str]:
    if not isinstance(tags, list):
        raise ResourceSemanticMigrationError(
            f"{resource_id}:semantic_tag_list_invalid"
        )
    normalized: list[str] = []
    for raw_tag in tags:
        tag = str(raw_tag)
        if CJK.search(tag):
            replacement = CANONICAL_TAGS.get(tag)
            if replacement is None:
                raise ResourceSemanticMigrationError(
                    f"{resource_id}:unmapped_cjk_tag:{tag}"
                )
            tag = replacement
        if tag not in normalized:
            normalized.append(tag)
    return normalized


def _normalize_model(manifest: dict[str, Any]) -> bool:
    capability = manifest.get("capability")
    if not isinstance(capability, dict):
        raise ResourceSemanticMigrationError(
            f"{manifest.get('resource_id')}:capability_missing"
        )
    problem_space = str(capability.get("problem_space") or "").strip()
    if not CJK.search(problem_space):
        return False
    if LEGACY_MARKER not in problem_space:
        raise ResourceSemanticMigrationError(
            f"{manifest.get('resource_id')}:unstructured_cjk_problem_space"
        )
    english, _legacy = problem_space.split(LEGACY_MARKER, 1)
    english = english.strip()
    if not english or CJK.search(english):
        raise ResourceSemanticMigrationError(
            f"{manifest.get('resource_id')}:english_problem_space_invalid"
        )
    capability["problem_space"] = english
    return True


def _normalize_tool(manifest: dict[str, Any]) -> bool:
    resource_id = str(manifest.get("resource_id") or "")
    capability = manifest.get("capability")
    nested_type = manifest.get("type")
    if not isinstance(capability, dict) or not isinstance(nested_type, dict):
        raise ResourceSemanticMigrationError(
            f"{resource_id}:tool_semantic_surface_missing"
        )
    raw_capability_tags = capability.get("domain_tags")
    raw_mirrored_tags = nested_type.get("resource_tag")
    capability_tags = _replace_tags(
        raw_capability_tags, resource_id=resource_id
    )
    if raw_mirrored_tags is None:
        if any(CJK.search(str(item)) for item in (raw_capability_tags or ())):
            raise ResourceSemanticMigrationError(
                f"{resource_id}:cjk_tool_tag_mirror_missing"
            )
        return False
    mirrored_tags = _replace_tags(raw_mirrored_tags, resource_id=resource_id)
    if capability_tags != mirrored_tags:
        raise ResourceSemanticMigrationError(
            f"{resource_id}:tool_tag_mirror_mismatch"
        )
    changed = (
        capability_tags != capability.get("domain_tags")
        or mirrored_tags != nested_type.get("resource_tag")
    )
    capability["domain_tags"] = capability_tags
    nested_type["resource_tag"] = mirrored_tags
    return changed


def normalize(*, write: bool) -> dict[str, int]:
    models = _load(MODELS_PATH)
    if not isinstance(models, list):
        raise ResourceSemanticMigrationError("models_catalog_not_array")
    changed_models = sum(
        _normalize_model(item) for item in models if isinstance(item, dict)
    )

    changed_tools = 0
    changed_tool_paths: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(REGISTRY_ROOT.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        manifest = _load(path)
        if not isinstance(manifest, dict):
            raise ResourceSemanticMigrationError(
                f"{path.relative_to(ROOT)}:tool_manifest_not_object"
            )
        if _normalize_tool(manifest):
            changed_tools += 1
            changed_tool_paths.append((path, manifest))

    if write:
        if changed_models:
            _write(MODELS_PATH, models, indent=4)
        for path, manifest in changed_tool_paths:
            _write(path, manifest, indent=2)
    return {
        "changed_models": changed_models,
        "changed_tools": changed_tools,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = normalize(write=not args.check)
    if args.check and any(result.values()):
        raise SystemExit(f"resource_semantics_not_normalized:{result}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
