"""Deterministic, provenance-preserving ingestion for SGAR Skill packages.

The ingestor never rewrites third-party Skill content and never executes bundled
scripts.  It discovers pinned upstream repositories, applies static admission
gates, collapses byte-identical SKILL.md files, and emits Manifest V1 records.
Only admitted packages are materialized into the runtime pool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PRODUCE_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = PRODUCE_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Pool.resources.produce.skill_semantics import apply_skill_semantics

SOURCE_CONFIG = PRODUCE_DIR / "skill_sources.json"
RESOURCE_ROOT = PROJECT_ROOT / "Pool" / "resources"
SKILL_POOL_ROOT = RESOURCE_ROOT / "skills"
JSON_DIR = RESOURCE_ROOT / "json"
REPORT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "verification_report"
    / "skill_pool_ingestion_verification_report.md"
)

MAX_SKILL_BYTES = 128 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MIN_SKILL_CHARS = 400
ADMISSION_SCORE = 65
CORE_SCORE = 80

ALLOWED_LICENSES = {
    "Apache-2.0",
    "MIT",
    "GPL-3.0",
    "CC-BY-SA-4.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
}
SOURCE_PRIORITY = {"official": 3, "maintained": 2, "community": 1}
IGNORED_PACKAGE_PARTS = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
PLACEHOLDER_PARTS = {
    "template",
    "templates",
    "fixture",
    "fixtures",
    "__macosx",
}

KNOWN_TOOL_IDS = {
    "python": "tool.python_script_runner.v1",
    "pytest": "tool.pytest_runner.v1",
    "playwright": "tool.playwright_e2e_runner.v1",
    "web": "tool.web_fetcher.v1",
    "repo": "tool.repo_searcher.v1",
    "file": "tool.file_reader.v1",
    "code": "tool.code_parser.v1",
    "security": "tool.bandit_security_scanner.v1",
    "sql": "tool.sql_analyzer.v1",
    "pdf": "tool.pdf2text.v1",
    "docx": "tool.docx2text.v1",
    "pptx": "tool.pptx2text.v1",
    "xlsx": "tool.xlsx2csv.v1",
    "validator": "tool.artifact_validator.v1",
}

DOMAIN_RULES: Sequence[Tuple[str, str]] = (
    ("test", "Testing"),
    ("debug", "Debugging"),
    ("review", "Code Review"),
    ("security", "Security"),
    ("vulnerab", "Security"),
    ("fuzz", "Security Testing"),
    ("frontend", "Frontend"),
    ("web", "Web Development"),
    ("backend", "Backend"),
    ("database", "Database"),
    ("sql", "Database"),
    ("devops", "DevOps"),
    ("deploy", "DevOps"),
    ("git", "Version Control"),
    ("document", "Documentation"),
    ("writing", "Documentation"),
    ("pdf", "Documents"),
    ("docx", "Documents"),
    ("pptx", "Presentations"),
    ("xlsx", "Spreadsheets"),
    ("research", "Research"),
    ("search", "Research"),
    ("agent", "Agent Engineering"),
    ("skill", "Agent Engineering"),
    ("mcp", "MCP"),
    ("design", "Design"),
    ("plan", "Planning"),
    ("architecture", "Architecture"),
    ("api", "API"),
    ("performance", "Performance"),
    ("python", "Python"),
    ("rust", "Rust"),
)


@dataclass
class Candidate:
    namespace: str
    repository: str
    commit: str
    trust_level: str
    source_root: Path
    skill_path: Path
    relative_skill_path: str
    package_root: Path
    name: str = ""
    description: str = ""
    text: str = ""
    content_hash: str = ""
    package_hash: str = ""
    package_bytes: int = 0
    license: str = "UNKNOWN"
    license_path: str = ""
    portability: str = "pure_prompt"
    lifecycle: str = "quarantined"
    score: int = 0
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    required_resource_ids: List[str] = field(default_factory=list)
    optional_resource_ids: List[str] = field(default_factory=list)
    dependency_slots: List[Dict[str, Any]] = field(default_factory=list)
    references: List[Dict[str, Any]] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    resource_id: str = ""
    destination_slug: str = ""
    duplicate_of: Optional[str] = None
    duplicate_aliases: List[Dict[str, str]] = field(default_factory=list)
    manifest: Optional[Dict[str, Any]] = None

    def audit_record(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("source_root", "skill_path", "package_root", "text", "manifest"):
            data.pop(key, None)
        return data


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _safe_slug(value: str, fallback: str = "skill") -> str:
    slug = value.strip().lower().replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return (slug or fallback)[:63].rstrip("-")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, text[match.end() :]


def _first_prose(body: str) -> str:
    blocks = re.split(r"\n\s*\n", body)
    for block in blocks:
        compact = " ".join(line.strip() for line in block.splitlines())
        if (
            len(compact) >= 60
            and not compact.startswith(("#", "```", "<", "|"))
        ):
            return compact[:1000]
    return "Reusable procedural guidance for the task described by this Skill."


def _package_files(package_root: Path) -> Iterable[Path]:
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_root)
        if any(part.lower() in IGNORED_PACKAGE_PARTS for part in relative.parts):
            continue
        yield path


def _package_fingerprint(package_root: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    for path in _package_files(package_root):
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total += len(data)
    return digest.hexdigest(), total


def _detect_license_text(text: str) -> str:
    lowered = text.lower()
    if (
        "may not" in lowered
        and "retain copies" in lowered
        and "outside the services" in lowered
    ):
        return "LicenseRef-Anthropic-Restricted"
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    if "mit license" in lowered:
        return "MIT"
    if "gnu general public license" in lowered and (
        "version 3" in lowered or "gpl-3.0" in lowered
    ):
        return "GPL-3.0"
    if "creative commons attribution-sharealike 4.0" in lowered:
        return "CC-BY-SA-4.0"
    if "bsd 3-clause" in lowered or "redistribution and use in source and binary forms" in lowered:
        return "BSD-3-Clause"
    return "UNKNOWN"


def _resolve_license(candidate: Candidate, source: Dict[str, Any]) -> Tuple[str, str]:
    if source.get("license_policy") == "repository" and source.get("license"):
        return str(source["license"]), ""
    current = candidate.package_root
    while True:
        matches = sorted(
            path
            for path in current.iterdir()
            if path.is_file()
            and re.match(r"^(LICENSE|COPYING|NOTICE)(?:\.|$)", path.name, re.IGNORECASE)
        )
        for path in matches:
            try:
                detected = _detect_license_text(
                    path.read_text(encoding="utf-8", errors="strict")
                )
            except (UnicodeDecodeError, OSError):
                continue
            if detected != "UNKNOWN":
                return detected, path.relative_to(candidate.source_root).as_posix()
        if current == candidate.source_root:
            break
        if candidate.source_root not in current.parents:
            break
        current = current.parent
    return "UNKNOWN", ""


def _markdown_references(
    body: str,
    package_root: Path,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    references: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen: set[str] = set()
    markdown_targets = re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", body)
    inline_targets = re.findall(
        r"`((?:\.\.?/)+(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|txt|json|ya?ml))`",
        body,
        flags=re.IGNORECASE,
    )
    for raw_target in [*markdown_targets, *inline_targets]:
        target = raw_target.strip().strip("<>").split()[0]
        if not target or re.match(r"^(?:https?|mailto|data):", target, re.IGNORECASE):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0]).strip()
        if not target:
            continue
        if any(marker in target for marker in ("{", "}", "$", "*")):
            continue
        candidate_path = (package_root / target).resolve()
        package_resolved = package_root.resolve()
        if candidate_path != package_resolved and package_resolved not in candidate_path.parents:
            errors.append(f"path_traversal_reference:{raw_target}")
            continue
        if not candidate_path.exists():
            errors.append(f"missing_reference:{target}")
            continue
        if candidate_path.is_dir():
            continue
        relative = candidate_path.relative_to(package_resolved).as_posix()
        if relative in seen:
            continue
        seen.add(relative)
        references.append(
            {
                "path": relative,
                "kind": "reference" if candidate_path.suffix.lower() in {".md", ".txt"} else "asset",
                "required": True,
            }
        )
    return references, errors


def _script_dependencies(candidate: Candidate) -> Tuple[List[str], List[str]]:
    required: List[str] = []
    blockers: List[str] = []
    mentioned_paths = set()
    for match in re.findall(
        r"(?:^|[^A-Za-z0-9_.-])((?:[A-Za-z0-9_.<>-]+/)*"
        r"(?:scripts?|tools?)/[A-Za-z0-9_./-]+\.(?:py|js|mjs|cjs|ts|sh|ps1))",
        candidate.text,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        normalized = match.replace("\\", "/")
        script_marker = re.search(r"(?:scripts?|tools?)/.+$", normalized, re.IGNORECASE)
        if script_marker:
            mentioned_paths.add(script_marker.group(0))
    for relative in sorted(mentioned_paths):
        path = (candidate.package_root / relative).resolve()
        if not path.is_file():
            blockers.append(f"required_script_missing:{relative}")
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            required.append(KNOWN_TOOL_IDS["python"])
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except (SyntaxError, UnicodeDecodeError) as exc:
                blockers.append(f"python_script_invalid:{relative}:{exc.__class__.__name__}")
        else:
            blockers.append(f"script_runtime_unregistered:{suffix}:{relative}")
    return list(dict.fromkeys(required)), blockers


def _classify(candidate: Candidate, source: Dict[str, Any]) -> None:
    lowered = candidate.text.lower()
    path_lower = candidate.relative_skill_path.lower()
    if source.get("catalog_only"):
        candidate.blockers.append(
            f"catalog_only:{source.get('catalog_reason', 'source policy')}"
        )

    if candidate.license not in ALLOWED_LICENSES:
        candidate.blockers.append(f"license_not_admitted:{candidate.license}")
    if len(candidate.text.strip()) < MIN_SKILL_CHARS:
        candidate.blockers.append("content_too_short")
    if candidate.skill_path.stat().st_size > MAX_SKILL_BYTES:
        candidate.blockers.append("skill_file_exceeds_128_kib")
    if candidate.package_bytes > MAX_PACKAGE_BYTES:
        candidate.blockers.append("package_exceeds_32_mib")
    if any(part.lower() in PLACEHOLDER_PARTS for part in candidate.skill_path.parts):
        candidate.blockers.append("template_or_fixture_path")
    if re.search(r"\b(?:todo|placeholder)\b", lowered) and len(candidate.text) < 1200:
        candidate.blockers.append("placeholder_content")
    if re.search(r"(?:[a-zA-Z]:\\Users\\|/Users/[^<\s]+|/home/[^<\s]+)", candidate.text):
        candidate.blockers.append("hardcoded_absolute_user_path")

    runtime_patterns = {
        "mcp_runtime_required": (
            r"\bmcp__[a-z0-9_]+",
            r"\.mcp\.json",
            r"\brequires?\b.{0,30}\bmcp (?:server|tool)",
        ),
        "authentication_required": (
            r"\b(?:api[_ -]?key|oauth|access token|auth token)\b",
            r"\b[A-Z][A-Z0-9_]{3,}_(?:TOKEN|API_KEY)\b",
            r"\b(?:authenticate|authenticated|authentication)\b.{0,60}"
            r"\b(?:cli|account|service|provider|github|shopify|cloud)\b",
            r"\b(?:gh|npm|docker|cloudflare|netlify|shopify)\s+auth\s+(?:login|status)\b",
        ),
        "claude_runtime_required": (
            r"\bsubagent_type\b",
            r"\b(?:Task|Agent)\s*\(",
            r"\.claude/(?:commands|agents|hooks)",
            r"\bclaude code\b.{0,80}\b(?:hook|command|plugin)\b",
        ),
    }
    runtime_hits: List[str] = []
    for reason, patterns in runtime_patterns.items():
        if any(re.search(pattern, candidate.text, re.IGNORECASE | re.DOTALL) for pattern in patterns):
            runtime_hits.append(reason)

    normalized_name = _safe_slug(candidate.name)
    if (
        "mcp" in normalized_name
        and normalized_name not in {"mcp-builder", "build-mcp"}
    ):
        runtime_hits.append("mcp_runtime_required")
    if re.search(
        r"\b(?:app|connector|vector store) from this plugin\b",
        candidate.text,
        re.IGNORECASE,
    ):
        runtime_hits.append("plugin_runtime_required")

    unregistered_runtimes = {
        "address-sanitizer",
        "aflpp",
        "atheris",
        "burpsuite-project-parser",
        "cargo-fuzz",
        "codeql",
        "constant-time-testing",
        "coverage-analysis",
        "debug-buttercup",
        "dwarf-expert",
        "fp-check",
        "genotoxic",
        "gh-cli",
        "github-triage",
        "libafl",
        "libfuzzer",
        "mermaid-to-proverif",
        "mutation-testing",
        "ossfuzz",
        "ruzzy",
        "sarif-parsing",
        "seatbelt-sandboxer",
        "semgrep",
        "semgrep-rule-creator",
        "semgrep-rule-variant-creator",
        "trailmark",
        "trailmark-finding-triage",
        "trailmark-review-gate",
        "trailmark-structural",
        "trailmark-summary",
        "trailmark-variant-neighborhood",
        "vector-forge",
        "wycheproof",
    }
    if candidate.namespace == "trailofbits" and normalized_name in unregistered_runtimes:
        runtime_hits.append(f"unregistered_external_runtime:{normalized_name}")

    required_scripts, script_blockers = _script_dependencies(candidate)
    candidate.required_resource_ids.extend(required_scripts)
    candidate.blockers.extend(script_blockers)

    if runtime_hits:
        candidate.portability = "runtime_bound"
        candidate.blockers.extend(runtime_hits)
    elif re.search(r"\b(?:sub-?agent|multiple agents|delegate to an agent)\b", lowered):
        candidate.portability = "agent_bound"
        candidate.dependency_slots.append(
            {
                "slot_id": "skill_agent_executor",
                "description": "A selected SGAR Agent capable of following this Skill protocol.",
                "allowed_types": ["Agent"],
                "top_k_per_type": 3,
                "required": True,
            }
        )
    elif candidate.required_resource_ids:
        candidate.portability = "tool_assisted"
    else:
        candidate.portability = "pure_prompt"

    optional: List[str] = []
    combined = f"{candidate.name} {candidate.description} {path_lower} {lowered[:12000]}"
    keyword_tools = (
        (r"\bpytest\b", "pytest"),
        (r"\bplaywright\b", "playwright"),
        (r"\b(?:http|website|web page)\b", "web"),
        (r"\brepositor(?:y|ies)\b", "repo"),
        (r"\bsecurity\b|\bvulnerab", "security"),
        (r"\bsql\b|\bdatabase\b", "sql"),
        (r"\bpdf\b", "pdf"),
        (r"\bdocx\b", "docx"),
        (r"\bpptx\b|\bpowerpoint\b", "pptx"),
        (r"\bxlsx\b|\bspreadsheet\b", "xlsx"),
        (r"\bvalidat", "validator"),
    )
    for pattern, tool_key in keyword_tools:
        if re.search(pattern, combined, re.IGNORECASE):
            optional.append(KNOWN_TOOL_IDS[tool_key])
    candidate.optional_resource_ids = [
        value
        for value in dict.fromkeys(optional)
        if value not in candidate.required_resource_ids
    ]


def _quality_score(candidate: Candidate) -> int:
    lowered = candidate.text.lower()
    description_words = len(candidate.description.split())
    task_relevance = 14
    if description_words >= 12:
        task_relevance += 3
    if not re.search(r"\b(?:bunny|airtable|notion|netlify|cloudflare|atlassian)\b", lowered):
        task_relevance += 3

    procedural = 8
    procedural += min(6, len(re.findall(r"(?m)^\s*(?:\d+\.|[-*])\s+", candidate.text)) // 4)
    procedural += min(6, len(candidate.headings) // 2)

    portability = {
        "pure_prompt": 15,
        "tool_assisted": 12,
        "agent_bound": 9,
        "runtime_bound": 0,
    }[candidate.portability]

    completeness = 5
    if candidate.name and candidate.description:
        completeness += 5
    if 800 <= len(candidate.text) <= MAX_SKILL_BYTES:
        completeness += 3
    if candidate.headings:
        completeness += 2

    dependency = 10 if not candidate.blockers else 3
    maintenance = 5 + SOURCE_PRIORITY.get(candidate.trust_level, 0)
    if candidate.license in ALLOWED_LICENSES:
        maintenance += 2
    testability = 6
    if re.search(r"\b(?:verify|validat|test|check|acceptance|output)\b", lowered):
        testability += 4
    return min(
        100,
        task_relevance
        + procedural
        + portability
        + completeness
        + dependency
        + maintenance
        + testability,
    )


def _domain_tags(candidate: Candidate) -> List[str]:
    haystack = (
        f"{candidate.name} {candidate.description}"
    ).lower()
    tags = [tag for pattern, tag in DOMAIN_RULES
            if pattern not in {"agent", "skill"} and pattern in haystack]
    if not tags:
        tags = ["General Workflow"]
    return list(dict.fromkeys(tags))[:8]


def _core_primitives(candidate: Candidate) -> List[str]:
    # A title identifies an unreviewed draft; headings are not capability facts.
    # Reviewed, source-bound overlays supply task primitives before promotion.
    return [re.sub(r"[^A-Za-z0-9 ]+", " ", candidate.name).strip().title() or "Procedural Guidance"]


def _skill_kind(candidate: Candidate) -> str:
    haystack = f"{candidate.name} {candidate.description}".lower()
    if re.search(r"\b(?:verify|validat|review|audit|test|check)\b", haystack):
        return "validator_hint"
    if re.search(r"\b(?:plan|design|architect|brainstorm|research)\b", haystack):
        return "planning_hint"
    if candidate.portability == "tool_assisted":
        return "tool_macro_hint"
    if candidate.portability == "agent_bound":
        return "agent_protocol_hint"
    return "instruction_hint"


def _recommended_roles(candidate: Candidate) -> List[str]:
    # Generic substring tags cannot establish a role. The reviewed overlay
    # may supply role hints supported by the existing manifest description.
    return []


def _manifest_for(candidate: Candidate, execution_uri: str) -> Dict[str, Any]:
    problem_space = candidate.description.strip() or _first_prose(
        _parse_frontmatter(candidate.text)[1]
    )
    avoid_when = []
    if candidate.portability == "tool_assisted":
        avoid_when.append("Required runner or Tool resources are unavailable.")
    elif candidate.portability == "agent_bound":
        avoid_when.append("No compatible Agent is selected for the same plan.")
    if candidate.optional_resource_ids:
        avoid_when.append("Prefer a more specific executable Tool when instructions alone are insufficient.")

    source_blob = (
        f"{candidate.repository.removesuffix('.git')}/blob/"
        f"{candidate.commit}/{candidate.relative_skill_path}"
    )
    estimated_tokens = max(1, len(candidate.text) // 4)
    skill_block = {
        "canonical_name": candidate.name,
        "source_namespace": candidate.namespace,
        "skill_kind": _skill_kind(candidate),
        "portability": candidate.portability,
        "workflow_hint": candidate.headings[:8],
        "recommended_roles": _recommended_roles(candidate),
        "avoid_when": avoid_when,
        "required_resource_ids": candidate.required_resource_ids,
        "optional_resource_ids": candidate.optional_resource_ids,
        "reference_catalog": candidate.references,
        "main_file": "SKILL.md",
        "main_file_bytes": candidate.skill_path.stat().st_size,
        "estimated_context_tokens": estimated_tokens,
        "max_injected_bytes": MAX_SKILL_BYTES,
    }
    manifest = {
        "manifest_version": "1.0",
        "resource_id": candidate.resource_id,
        "resource_type": "Skill",
        "status": "active",
        "capability": {
            "core_primitives": _core_primitives(candidate),
            "problem_space": problem_space,
            "domain_tags": _domain_tags(candidate),
        },
        "constraint": {
            "env_requirements": candidate.required_resource_ids,
            "io_signature": "Input: {task: text, context?: text} | Output: {instruction_hint: text}",
            "artifact_input": ["text", "context"],
            "artifact_output": ["instruction_hint"],
            "operation_kinds": ["apply_instruction"],
        },
        "io": {
            "input_contract": [
                {
                    "name": "task",
                    "kind": "text",
                    "required": True,
                    "description": "Atomic task that the Skill should guide.",
                },
                {
                    "name": "skill_references",
                    "kind": "relative_path_list",
                    "required": False,
                    "description": "Optional manifest-declared references to load.",
                },
            ],
            "output_contract": {
                "artifact_type": "instruction_hint",
                "description": "Unmodified Skill instructions and explicitly requested references.",
            },
        },
        "routing": {
            "family_id": f"skill.{_safe_slug(candidate.name)}",
            "dependency_slots": candidate.dependency_slots,
        },
        "execution": {
            "runtime": "prompt_skill",
            "uri": execution_uri,
            "execution_status": "active",
            "implicit_script_execution": False,
            "max_injected_bytes": MAX_SKILL_BYTES,
        },
        "utility": {
            "latency_ms": 0,
            "token_cost_factor": round(estimated_tokens / 1000, 4),
            "expected_success_rate": 0.5,
            "successes": 0,
            "attempts": 0,
        },
        "provenance": {
            "source_dataset": candidate.namespace,
            "source_item_id": candidate.relative_skill_path,
            "source_uri": source_blob,
            "source_repository": candidate.repository,
            "source_commit": candidate.commit,
            "source_hash": f"sha256:{candidate.content_hash}",
            "package_hash": f"sha256:{candidate.package_hash}",
            "conversion_method": "deterministic_skill_ingestion_v1",
            "confidence": 1.0,
            "license": candidate.license,
            "license_path": candidate.license_path,
            "trust_level": candidate.trust_level,
            "source_aliases": candidate.duplicate_aliases,
            "duplicate_source_count": 1 + len(candidate.duplicate_aliases),
            "quality_score": candidate.score,
            "lifecycle": candidate.lifecycle,
        },
        "type_specific": {"skill": skill_block},
        "memory": {"success_trajectories": [], "failure_reflections": []},
    }
    return apply_skill_semantics(manifest)


def _derive_legacy_fields(manifest: Dict[str, Any]) -> Dict[str, Any]:
    derived = json.loads(json.dumps(manifest))
    derived["type"] = {
        "resource_type": "Skill",
        "resource_tag": derived["capability"]["domain_tags"],
    }
    derived["input_contract"] = derived["io"]["input_contract"]
    derived["output_contract"] = derived["io"]["output_contract"]
    return derived


def _load_sources_config() -> Dict[str, Any]:
    config = _read_json(SOURCE_CONFIG)
    if not isinstance(config.get("sources"), list):
        raise ValueError(f"{SOURCE_CONFIG} must contain a sources array")
    return config


def _git(args: Sequence[str], cwd: Optional[Path] = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def prepare_pinned_sources(
    sources: Sequence[Dict[str, Any]],
    destination: Path,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source in sources:
        namespace = str(source["namespace"])
        target = destination / namespace
        if not target.exists():
            _git(["clone", "--no-checkout", "--filter=blob:none", source["repository"], str(target)])
        _git(["fetch", "--depth", "1", "origin", source["commit"]], cwd=target)
        _git(["checkout", "--detach", source["commit"]], cwd=target)


def _validate_source_checkout(source: Dict[str, Any], source_root: Path) -> None:
    if not source_root.is_dir():
        raise FileNotFoundError(f"Missing source checkout: {source_root}")
    actual = _git(["rev-parse", "HEAD"], cwd=source_root)
    if actual != source["commit"]:
        raise ValueError(
            f"Pinned commit mismatch for {source['namespace']}: "
            f"expected {source['commit']}, found {actual}"
        )


def _discover_candidates(
    source_parent: Path,
    sources: Sequence[Dict[str, Any]],
    only: Optional[str] = None,
) -> List[Candidate]:
    candidates: List[Candidate] = []
    for source in sources:
        source_root = source_parent / source["namespace"]
        _validate_source_checkout(source, source_root)
        paths: set[Path] = set()
        for pattern in source.get("skill_globs", ["**/SKILL.md"]):
            paths.update(path for path in source_root.glob(pattern) if path.is_file())
        for skill_path in sorted(paths):
            relative = skill_path.relative_to(source_root).as_posix()
            if only and only.lower() not in relative.lower():
                continue
            try:
                raw = skill_path.read_bytes()
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = ""
                raw = skill_path.read_bytes()
            metadata, body = _parse_frontmatter(text)
            name = str(metadata.get("name") or skill_path.parent.name).strip()
            description = str(metadata.get("description") or "").strip()
            package_hash, package_bytes = _package_fingerprint(skill_path.parent)
            headings = [
                re.sub(r"\s+#+$", "", match).strip()
                for match in re.findall(r"(?m)^#{1,3}\s+(.+)$", body)
            ]
            candidate = Candidate(
                namespace=source["namespace"],
                repository=source["repository"],
                commit=source["commit"],
                trust_level=source.get("trust_level", "community"),
                source_root=source_root,
                skill_path=skill_path,
                relative_skill_path=relative,
                package_root=skill_path.parent,
                name=name,
                description=description,
                text=text,
                content_hash=_sha256_bytes(raw),
                package_hash=package_hash,
                package_bytes=package_bytes,
                headings=headings,
            )
            candidate.license, candidate.license_path = _resolve_license(candidate, source)
            candidate.references, reference_errors = _markdown_references(body, candidate.package_root)
            candidate.blockers.extend(reference_errors)
            if not metadata.get("name") or not metadata.get("description"):
                candidate.blockers.append("missing_required_frontmatter")
            if not text:
                candidate.blockers.append("invalid_utf8")
            _classify(candidate, source)
            candidate.score = _quality_score(candidate)
            candidates.append(candidate)
    return candidates


def _assign_ids_and_deduplicate(candidates: List[Candidate]) -> List[Candidate]:
    groups: Dict[str, List[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.content_hash, []).append(candidate)

    active: List[Candidate] = []
    used_ids: set[str] = set()
    for group in groups.values():
        group.sort(
            key=lambda item: (
                not bool(item.blockers),
                item.score,
                SOURCE_PRIORITY.get(item.trust_level, 0),
                -len(item.relative_skill_path),
            ),
            reverse=True,
        )
        canonical = group[0]
        base_name = _safe_slug(canonical.name)
        resource_id = f"skill.{canonical.namespace}.{base_name}.v1"
        if resource_id in used_ids:
            suffix = canonical.content_hash[:8]
            resource_id = f"skill.{canonical.namespace}.{base_name}-{suffix}.v1"
        used_ids.add(resource_id)
        canonical.resource_id = resource_id
        canonical.destination_slug = (
            base_name
            if not any(item.destination_slug == base_name for item in active)
            else f"{base_name}-{canonical.content_hash[:8]}"
        )
        canonical.duplicate_aliases = [
            {
                "namespace": item.namespace,
                "repository": item.repository,
                "commit": item.commit,
                "path": item.relative_skill_path,
            }
            for item in group[1:]
        ]

        if canonical.blockers:
            canonical.lifecycle = "quarantined"
        elif (
            canonical.score >= CORE_SCORE
            and canonical.portability == "pure_prompt"
        ):
            canonical.lifecycle = "active_core"
            active.append(canonical)
        elif canonical.score >= ADMISSION_SCORE:
            canonical.lifecycle = "active_conditional"
            active.append(canonical)
        else:
            canonical.lifecycle = "quarantined"
            canonical.blockers.append(f"quality_score_below_{ADMISSION_SCORE}")

        for duplicate in group[1:]:
            duplicate.lifecycle = "deprecated"
            duplicate.duplicate_of = canonical.resource_id
            duplicate.warnings.append(f"exact_duplicate_of:{canonical.resource_id}")
    return active


def _copy_package(candidate: Candidate, destination: Path) -> None:
    def ignore(directory: str, names: List[str]) -> set[str]:
        return {name for name in names if name.lower() in IGNORED_PACKAGE_PARTS}

    shutil.copytree(candidate.package_root, destination, ignore=ignore)
    copied_skill = destination / "SKILL.md"
    if not copied_skill.is_file():
        raise FileNotFoundError(f"Copied Skill missing SKILL.md: {destination}")
    if _sha256_file(copied_skill) != candidate.content_hash:
        raise ValueError(f"Copied Skill hash mismatch: {candidate.resource_id}")


def _source_license_file(source: Dict[str, Any], source_root: Path) -> Optional[Path]:
    if source.get("license_policy") != "repository":
        return None
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        path = source_root / name
        if path.is_file():
            return path
    return None


def _replace_directory_atomically(staged: Path, target: Path) -> None:
    expected = (RESOURCE_ROOT / "skills").resolve()
    if target.resolve() != expected:
        raise ValueError(f"Refusing to replace unexpected Skill pool path: {target}")
    backup = target.with_name(f"{target.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


LEGACY_TARGETS: Dict[str, Tuple[str, str]] = {
    "skill_developer_agent": ("superpowers", "executing-plans"),
    "skill_enterprise_architecture": ("superpowers", "writing-plans"),
    "skill_python_testing": ("superpowers", "test-driven-development"),
    "skill_ts_best_practices": ("superpowers", "verification-before-completion"),
    "skill_git_worktrees": ("superpowers", "using-git-worktrees"),
    "skill_webapp_testing": ("anthropic", "webapp-testing"),
    "skill_agent_memory_reflexion": ("neolabhq", "reflect"),
    "skill_fix_broken_tests": ("neolabhq", "fix-tests"),
    "skill_claude_mcp_management": ("anthropic", "mcp-builder"),
    "skill_tough_coding": ("trailofbits", "modern-python"),
    "skill_xlsx_spreadsheet": ("anthropic", "xlsx"),
    "skill_global_state_management": ("anthropic", "frontend-design"),
    "skill_reflexion": ("neolabhq", "reflect"),
    "skill_system_planning": ("superpowers", "writing-plans"),
    "skill_automated_refactoring": ("trailofbits", "differential-review"),
    "skill_agent_spec": ("anthropic", "skill-creator"),
    "skill_subagent_planner": ("superpowers", "dispatching-parallel-agents"),
    "skill_test_driven_development_core": ("superpowers", "test-driven-development"),
    "skill_writing_agent": ("anthropic", "doc-coauthoring"),
    "skill_product_manager": ("superpowers", "brainstorming"),
}


def _migration_map(active: Sequence[Candidate]) -> Dict[str, Any]:
    lookup = {
        (candidate.namespace, _safe_slug(candidate.name)): candidate.resource_id
        for candidate in active
    }
    mappings = {}
    for old_id, selector in LEGACY_TARGETS.items():
        target = lookup.get(selector)
        mappings[old_id] = {
            "replacement_resource_id": target,
            "runtime_alias": False,
            "status": "mapped" if target else "no_admitted_equivalent",
        }
    return {"schema_version": "1.0", "runtime_aliases_enabled": False, "mappings": mappings}


def _render_report(candidates: Sequence[Candidate], active: Sequence[Candidate]) -> str:
    lifecycle_counts: Dict[str, int] = {}
    source_counts: Dict[str, Dict[str, int]] = {}
    for candidate in candidates:
        lifecycle_counts[candidate.lifecycle] = lifecycle_counts.get(candidate.lifecycle, 0) + 1
        bucket = source_counts.setdefault(candidate.namespace, {})
        bucket[candidate.lifecycle] = bucket.get(candidate.lifecycle, 0) + 1
    lines = [
        "# SGAR Skill Pool Ingestion Verification Report",
        "",
        "This report is generated from pinned upstream commits. Third-party Skill files are copied byte-for-byte and are never executed during ingestion.",
        "",
        "## Summary",
        "",
        f"- Discovered candidates: {len(candidates)}",
        f"- Active manifests: {len(active)}",
        f"- Active core: {lifecycle_counts.get('active_core', 0)}",
        f"- Active conditional: {lifecycle_counts.get('active_conditional', 0)}",
        f"- Quarantined: {lifecycle_counts.get('quarantined', 0)}",
        f"- Exact duplicates collapsed: {lifecycle_counts.get('deprecated', 0)}",
        "",
        "## Source results",
        "",
        "| Source | Core | Conditional | Quarantined | Duplicates |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for source, counts in sorted(source_counts.items()):
        lines.append(
            f"| {source} | {counts.get('active_core', 0)} | "
            f"{counts.get('active_conditional', 0)} | "
            f"{counts.get('quarantined', 0)} | {counts.get('deprecated', 0)} |"
        )
    blockers: Dict[str, int] = {}
    for candidate in candidates:
        for blocker in candidate.blockers:
            key = blocker.split(":", 1)[0]
            blockers[key] = blockers.get(key, 0) + 1
    lines.extend(
        [
            "",
            "## Admission blockers",
            "",
            "| Blocker | Count |",
            "| --- | ---: |",
        ]
    )
    for reason, count in sorted(blockers.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| `{reason}` | {count} |")
    lines.extend(
        [
            "",
            "## Invariants",
            "",
            "- Every active Skill has valid UTF-8 frontmatter, an admitted license, a pinned commit, and a local immutable entrypoint.",
            "- Exact SKILL.md duplicates produce one active manifest with source aliases.",
            "- Runtime-bound, authentication-bound, unknown-license, and unresolved-reference packages are excluded from the searchable pool.",
            "- Bundled scripts are copied but never executed implicitly.",
            "",
        ]
    )
    return "\n".join(lines)


def materialize(
    source_parent: Path,
    only: Optional[str] = None,
) -> List[Dict[str, Any]]:
    config = _load_sources_config()
    sources = config["sources"]
    candidates = _discover_candidates(source_parent, sources, only=only)
    active = _assign_ids_and_deduplicate(candidates)
    known_resource_ids: set[str] = set()
    for filename in (
        "tools.json",
        "models.json",
        "agents.json",
        "resources.json",
        "device.json",
    ):
        path = JSON_DIR / filename
        if not path.is_file():
            continue
        payload = _read_json(path)
        items = payload if isinstance(payload, list) else [payload]
        known_resource_ids.update(
            str(item.get("resource_id"))
            for item in items
            if isinstance(item, dict) and item.get("resource_id")
        )
    admitted: List[Candidate] = []
    for candidate in active:
        missing = sorted(
            set(candidate.required_resource_ids) - known_resource_ids
        )
        candidate.optional_resource_ids = [
            resource_id
            for resource_id in candidate.optional_resource_ids
            if resource_id in known_resource_ids
        ]
        if missing:
            candidate.lifecycle = "quarantined"
            candidate.blockers.extend(
                f"required_resource_unregistered:{resource_id}"
                for resource_id in missing
            )
            continue
        admitted.append(candidate)
    active = admitted

    stage_parent = PROJECT_ROOT / "tmp"
    stage_parent.mkdir(parents=True, exist_ok=True)
    staged: Optional[Path] = stage_parent / "skills-stage-work"
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    try:
        vendor_root = staged / "vendor"
        source_map = {source["namespace"]: source for source in sources}
        for candidate in active:
            commit_dir = vendor_root / candidate.namespace / candidate.commit
            destination = commit_dir / candidate.destination_slug
            _copy_package(candidate, destination)
            source = source_map[candidate.namespace]
            license_file = _source_license_file(source, candidate.source_root)
            if license_file is not None:
                license_destination = commit_dir / license_file.name
                if not license_destination.exists():
                    license_destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(license_file, license_destination)
            execution_uri = (
                "file://Pool/resources/skills/vendor/"
                f"{candidate.namespace}/{candidate.commit}/"
                f"{candidate.destination_slug}/SKILL.md"
            )
            candidate.manifest = _manifest_for(candidate, execution_uri)

        manifests = [candidate.manifest for candidate in active if candidate.manifest]
        if len(manifests) != len(active):
            raise AssertionError("Not every admitted Skill produced a manifest")
        ids = [manifest["resource_id"] for manifest in manifests]
        if len(ids) != len(set(ids)):
            raise ValueError("Generated duplicate Skill resource IDs")

        _replace_directory_atomically(staged, SKILL_POOL_ROOT)
        staged = None
    finally:
        if staged is not None and staged.exists():
            shutil.rmtree(staged)

    manifests = [candidate.manifest for candidate in active if candidate.manifest]
    derived = [_derive_legacy_fields(manifest) for manifest in manifests]
    _write_json_atomic(JSON_DIR / "skills_draft.json", manifests)
    _write_json_atomic(JSON_DIR / "skills.json", derived)

    source_lock = {
        "schema_version": "1.0",
        "source_config": SOURCE_CONFIG.relative_to(PROJECT_ROOT).as_posix(),
        "sources": [
            {
                "namespace": source["namespace"],
                "repository": source["repository"],
                "commit": source["commit"],
                "license_policy": source["license_policy"],
            }
            for source in sources
        ],
        "packages": [
            {
                "resource_id": candidate.resource_id,
                "namespace": candidate.namespace,
                "source_path": candidate.relative_skill_path,
                "content_hash": f"sha256:{candidate.content_hash}",
                "package_hash": f"sha256:{candidate.package_hash}",
                "license": candidate.license,
                "lifecycle": candidate.lifecycle,
            }
            for candidate in active
        ],
    }
    _write_json_atomic(JSON_DIR / "skills.lock.json", source_lock)
    _write_json_atomic(
        JSON_DIR / "skills_audit.json",
        {
            "schema_version": "1.0",
            "active_resource_ids": [candidate.resource_id for candidate in active],
            "candidates": [candidate.audit_record() for candidate in candidates],
        },
    )
    _write_json_atomic(JSON_DIR / "skills_migration_map.json", _migration_map(active))
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(candidates, active), encoding="utf-8")
    return manifests


def run_ingestion(
    client: Any,
    api_key: str,
    base_url: str,
    model: str,
    limit: int = 0,
    only: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Gateway-compatible deterministic ingestion; no LLM credentials are used."""
    del client, api_key, base_url, model
    config = _load_sources_config()
    configured_root = os.environ.get("SGAR_SKILL_SOURCE_ROOT", "").strip()
    if configured_root:
        source_parent = Path(configured_root).resolve()
        candidates = _discover_candidates(source_parent, config["sources"], only=only)
        active = _assign_ids_and_deduplicate(candidates)
        if limit > 0:
            active = active[:limit]
        return [
            _manifest_for(
                candidate,
                "file://Pool/resources/skills/vendor/"
                f"{candidate.namespace}/{candidate.commit}/"
                f"{candidate.destination_slug}/SKILL.md",
            )
            for candidate in active
        ]
    with tempfile.TemporaryDirectory(prefix="sgar-skill-sources-") as temporary:
        source_parent = Path(temporary)
        prepare_pinned_sources(config["sources"], source_parent)
        candidates = _discover_candidates(source_parent, config["sources"], only=only)
        active = _assign_ids_and_deduplicate(candidates)
        if limit > 0:
            active = active[:limit]
        return [
            _manifest_for(
                candidate,
                "file://Pool/resources/skills/vendor/"
                f"{candidate.namespace}/{candidate.commit}/"
                f"{candidate.destination_slug}/SKILL.md",
            )
            for candidate in active
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the pinned SGAR Skill resource pool")
    parser.add_argument(
        "--source-root",
        help="Directory containing source checkouts named by namespace; clones pinned commits when omitted.",
    )
    parser.add_argument("--only", help="Only process source paths containing this substring")
    parser.add_argument(
        "--materialize",
        action="store_true",
        help="Replace the active Skill pool and write manifests, locks, audit data, and report.",
    )
    args = parser.parse_args()
    config = _load_sources_config()
    if args.source_root:
        source_parent = Path(args.source_root).resolve()
        temporary_context = None
    else:
        temporary_context = tempfile.TemporaryDirectory(prefix="sgar-skill-sources-")
        source_parent = Path(temporary_context.name)
        prepare_pinned_sources(config["sources"], source_parent)
    try:
        if args.materialize:
            manifests = materialize(source_parent, only=args.only)
        else:
            candidates = _discover_candidates(source_parent, config["sources"], only=args.only)
            active = _assign_ids_and_deduplicate(candidates)
            manifests = [
                _manifest_for(candidate, f"file://{candidate.relative_skill_path}")
                for candidate in active
            ]
        print(
            json.dumps(
                {
                    "active_skills": len(manifests),
                    "materialized": bool(args.materialize),
                    "source_root": str(source_parent),
                },
                ensure_ascii=False,
            )
        )
    finally:
        if temporary_context is not None:
            temporary_context.cleanup()


if __name__ == "__main__":
    main()
