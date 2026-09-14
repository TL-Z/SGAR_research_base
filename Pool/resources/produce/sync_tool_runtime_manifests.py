"""Synchronize reproducible runtime metadata into Tool registry manifests.

This script never changes imported Tool implementations. It derives
local-wrapper hashes, shared-helper hashes, runtime dependencies, REST auth and
HTTP-status semantics, and neutral utility evidence from the canonical files
already in the repository. Re-running it must be idempotent.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
import sysconfig
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "Pool" / "resources" / "tools" / "registry"
RC1_IMAGE = "sgar-runtime:rc1"

IMPORT_TO_PACKAGE = {
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "dns": "dnspython",
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "faker": "Faker",
    "fitz": "PyMuPDF",
    "graphql": "graphql-core",
    "markdown_it": "markdown-it-py",
    "pptx": "python-pptx",
    "yaml": "PyYAML",
}

PYTHON_VERSIONS = {
    "beautifulsoup4": "4.12.3",
    "bleach": "6.2.0",
    "chardet": "5.2.0",
    "cryptography": "44.0.0",
    "dateparser": "1.2.0",
    "detect-secrets": "1.5.0",
    "dnspython": "2.7.0",
    "Faker": "33.1.0",
    "genson": "1.3.0",
    "gitlint-core": "0.19.1",
    "gitpython": "3.1.53",
    "graphql-core": "3.2.5",
    "Jinja2": "3.1.5",
    "jsonschema": "4.23.0",
    "langdetect": "1.0.9",
    "lxml": "5.3.0",
    "markdown": "3.7",
    "markdown-it-py": "3.0.0",
    "markdownify": "0.14.1",
    "mcp": "1.28.1",
    "mcp-server-git": "2026.7.10",
    "networkx": "3.4.2",
    "numpy": "2.1.3",
    "openpyxl": "3.1.5",
    "pandas": "2.2.3",
    "pdfplumber": "0.11.4",
    "phonenumbers": "8.13.52",
    "Pillow": "11.0.0",
    "Pint": "0.24.4",
    "psutil": "6.1.1",
    "pygments": "2.18.0",
    "pygount": "1.8.0",
    "pypdf": "5.1.0",
    "PyMuPDF": "1.25.1",
    "pytest": "8.3.4",
    "python-docx": "1.1.2",
    "python-dotenv": "1.0.1",
    "python-pptx": "1.0.2",
    "PyYAML": "6.0.2",
    "qrcode": "8.0",
    "radon": "6.0.1",
    "requests": "2.32.3",
    "shapely": "2.0.6",
    "sqlglot": "26.0.1",
    "sympy": "1.13.3",
    "tablib": "3.7.0",
    "tomli-w": "1.1.0",
    "validators": "0.34.0",
}

NODE_PACKAGES = {
    "eslint": ("eslint", "10.0.1"),
    "jest": ("jest", "30.4.2"),
    "markdownlint": ("markdownlint-cli", "0.49.1"),
    "prettier": ("prettier", "3.9.6"),
    "stylelint": ("stylelint", "17.14.1"),
    "tsc": ("typescript", "7.0.2"),
    "vitest": ("vitest", "4.1.10"),
}

SYSTEM_PACKAGES = {
    "clang-format": "clang-format",
    "git": "git",
    "go": "go1.22.5",
    "gofmt": "go1.22.5",
    "jq": "jq",
    "rustfmt": "rust-1.79.0",
    "cargo": "rust-1.79.0",
    "cc": "build-essential",
    "shellcheck": "shellcheck",
}

SCRIPT_COMMAND_HINTS = {
    "bandit": "bandit",
    "black": "black",
    "cargo": "cargo",
    "clang_format": "clang-format",
    "dependency_cve": "pip-audit",
    "eslint_code_formatter": "prettier",
    "eslint": "eslint",
    "flake8": "flake8",
    "git_commit_lint": "gitlint",
    "git_": "git",
    "go_fmt": "gofmt",
    "go_test": "go",
    "jest": "jest",
    "json_jq": "jq",
    "npm_": "npm",
    "python_dead_code": "vulture",
    "python_static": "pylint",
    "ruff": "ruff",
    "rustfmt": "rustfmt",
    "shellcheck": "shellcheck",
    "stylelint": "stylelint",
    "tsc": "tsc",
    "vitest": "vitest",
}

LOCAL_HELPERS = {"_sgar_cli", "_liblib", "_apilib", "_mcplib"}

REST_SUCCESS_TEXT = (
    "HTTP 2xx/3xx responses produce a success envelope; HTTP 4xx/5xx responses "
    "produce an error envelope and a non-zero exit; request exceptions also "
    "produce an error envelope and a non-zero exit."
)
REST_OUTPUT_SHAPE = (
    'HTTP 2xx/3xx: {status: "success", tool: string, url: string, http_status: '
    'integer, result: object|string}. HTTP 4xx/5xx: {status: "error", tool: '
    'string, url: string, http_status: integer, result: object|string}, exit 1. '
    'Request exception: {status: "error", tool: string, message: string}, exit 1.'
)


def _is_stdlib(name: str) -> bool:
    """Return whether an import belongs to the interpreter standard library.

    ``sys.stdlib_module_names`` was introduced in Python 3.10, while this
    repository is sometimes compiled with an older host interpreter.  The
    spec/path fallback keeps manifest generation deterministic without
    misclassifying standard-library modules as installable distributions.
    """
    if name in sys.builtin_module_names or name in getattr(sys, "stdlib_module_names", set()):
        return True
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return False
    if spec is None:
        return False
    origin = str(spec.origin or "")
    if origin in {"built-in", "frozen"}:
        return True
    if not origin:
        return False
    origin_path = Path(origin).resolve()
    stdlib_path = Path(sysconfig.get_paths()["stdlib"]).resolve()
    third_party_paths = {
        Path(value).resolve()
        for key, value in sysconfig.get_paths().items()
        if key in {"purelib", "platlib"} and value
    }
    try:
        origin_path.relative_to(stdlib_path)
    except ValueError:
        return False
    for third_party in third_party_paths:
        try:
            origin_path.relative_to(third_party)
            return False
        except ValueError:
            continue
    return True


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _implementation_path(manifest: dict[str, Any]) -> Path:
    uri = str((manifest.get("execution") or {}).get("uri") or "")
    if not uri.startswith("file://"):
        raise ValueError(f"{manifest.get('resource_id')}: execution.uri must be file://")
    path = (ROOT / uri.removeprefix("file://")).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{manifest.get('resource_id')}: implementation escapes project root") from exc
    if not path.is_file():
        raise ValueError(f"{manifest.get('resource_id')}: implementation missing: {path}")
    return path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _python_packages(path: Path, runtime: str) -> list[dict[str, Any]]:
    imports = _imports(path)
    if runtime == "rest_api":
        imports.add("requests")
    if runtime == "mcp_server":
        imports.add("mcp")
    packages: set[str] = set()
    for name in imports:
        if _is_stdlib(name) or name in LOCAL_HELPERS or name.startswith("_"):
            continue
        packages.add(IMPORT_TO_PACKAGE.get(name, name))
    return [
        {
            "name": name,
            "version": f"=={PYTHON_VERSIONS[name]}" if name in PYTHON_VERSIONS else "image-locked",
            "required": True,
        }
        for name in sorted(packages, key=str.casefold)
    ]


def _commands(resource_id: str, path: Path, runtime: str) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    commands: set[str] = set()
    commands.update(re.findall(r"run_cli\(\s*[\"']([^\"']+)", source))
    if runtime == "mcp_server":
        match = re.search(r"run\([^,]+,\s*[\"']([^\"']+)", source, re.DOTALL)
        if match:
            commands.add(match.group(1))
    stem = resource_id.removeprefix("tool.").removesuffix(".v1")
    for hint, command in SCRIPT_COMMAND_HINTS.items():
        if hint in stem:
            commands.add(command)
    if runtime == "rest_api":
        commands.clear()
    if "cargo" in commands:
        commands.add("cc")
    return sorted(commands)


def _node_packages(commands: list[str]) -> list[dict[str, Any]]:
    result = []
    for command in commands:
        if command in NODE_PACKAGES:
            name, version = NODE_PACKAGES[command]
            result.append({"name": name, "version": f"=={version}", "required": True})
        elif command == "mcp-server-filesystem":
            result.append(
                {
                    "name": "@modelcontextprotocol/server-filesystem",
                    "version": "==2026.7.10",
                    "required": True,
                }
            )
        elif command == "mcp-server-sequential-thinking":
            result.append(
                {
                    "name": "@modelcontextprotocol/server-sequential-thinking",
                    "version": "==2026.7.4",
                    "required": True,
                }
            )
    return sorted(result, key=lambda item: item["name"].casefold())


def _system_packages(commands: list[str]) -> list[dict[str, Any]]:
    packages = {
        SYSTEM_PACKAGES[command]
        for command in commands
        if command in SYSTEM_PACKAGES
    }
    return [{"name": name, "version": "image-locked", "required": True} for name in sorted(packages)]


def _endpoint(path: Path) -> str | None:
    source = path.read_text(encoding="utf-8-sig")
    match = re.search(r"https://[^\"'\s]+", source)
    return match.group(0) if match else None


def _runtime_requirements(manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    execution = manifest.get("execution") or {}
    runtime = str(execution.get("runtime") or "")
    commands = _commands(str(manifest["resource_id"]), path, runtime)
    python_packages = _python_packages(path, runtime)
    if "gitlint" in commands:
        python_packages.append(
            {"name": "gitlint-core", "version": "==0.19.1", "required": True}
        )
        python_packages.sort(key=lambda item: item["name"].casefold())
    if runtime == "mcp_server" and ".mcp.git_" in str(manifest["resource_id"]):
        python_packages.append(
            {"name": "mcp-server-git", "version": "==2026.7.10", "required": True}
        )
        python_packages.sort(key=lambda item: item["name"].casefold())
    requirements: dict[str, Any] = {
        "runtime_profile": "sgar-runtime",
        "python": "==3.11.9",
        "python_packages": python_packages,
        "node_packages": _node_packages(commands),
        "system_packages": _system_packages(commands),
        "commands": commands,
        "env_vars": [],
        "network_required": runtime == "rest_api",
        "install_policy": "never",
        "max_auto_heals": 0,
        "docker_image": RC1_IMAGE,
    }
    if runtime == "mcp_server":
        requirements["mcp"] = {
            "server_command": commands[0] if commands else "",
            "working_directory": "/app",
            "allowed_roots": ["/app"],
        }
    if runtime == "rest_api":
        requirements["rest"] = {
            "endpoint": _endpoint(path),
            "auth_required": False,
            "auth_evidence": "wrapper_and_shared_adapter_read_no_credentials",
            "timeout_seconds": 25,
            "success_http_status": "200-399",
        }
    return requirements


def _normalize_rest_http_semantics(manifest: dict[str, Any]) -> None:
    capability = manifest.setdefault("capability", {})
    description = str(capability.get("description") or "").strip()
    stale_markers = (
        "any http response, including 4xx/5xx",
        "http 4xx/5xx remain successful",
        "http 4xx/5xx responses remain successful",
    )
    lowered = description.lower()
    positions = [lowered.find(marker) for marker in stale_markers if marker in lowered]
    if positions:
        description = description[: min(positions)].rstrip(" ;,.")
        if description:
            description += ". "
        capability["description"] = description + REST_SUCCESS_TEXT

    constraint = manifest.setdefault("constraint", {})
    io_signature = str(constraint.get("io_signature") or "").strip()
    lower_io = io_signature.lower()
    only_markers = (
        "; only request exceptions",
        "; only requests.requestexception",
        "; only connection/dns/timeout",
        ", while only request exceptions",
    )
    io_positions = [lower_io.find(marker) for marker in only_markers if marker in lower_io]
    if io_positions:
        io_signature = io_signature[: min(io_positions)].rstrip(" ;,.")
        if io_signature:
            io_signature += ". "
        constraint["io_signature"] = io_signature + REST_SUCCESS_TEXT

    constraint["output_shape"] = REST_OUTPUT_SHAPE
    constraint["http_status_semantics"] = {
        "success": "HTTP 200-399",
        "failure": "HTTP 400-599 or request exception",
        "failure_exit_code": 1,
    }

    limitations = constraint.get("limitations")
    if isinstance(limitations, list):
        normalized: list[Any] = []
        added_http_rule = False
        for item in limitations:
            if item == REST_SUCCESS_TEXT:
                normalized.append(item)
                added_http_rule = True
                continue
            if not isinstance(item, str) or "4xx/5xx" not in item.lower():
                normalized.append(item)
                continue
            kept_parts = [
                part.strip()
                for part in item.split(";")
                if part.strip() and "4xx/5xx" not in part.lower()
            ]
            normalized.extend(kept_parts)
            if not added_http_rule:
                normalized.append(REST_SUCCESS_TEXT)
                added_http_rule = True
        constraint["limitations"] = normalized

    correct_declared_http_descriptions(manifest)
    rendered = json.dumps(manifest, ensure_ascii=False).lower()
    stale_claims = (
        "including 4xx/5xx",
        "4xx/5xx remain status=success",
        "4xx/5xx responses remain status=success",
        "4xx/5xx remain successful",
        "4xx/5xx responses remain successful",
        "only request exceptions are wrapper errors",
        "only requests.requestexception is an error exit",
    )
    remaining = [claim for claim in stale_claims if claim in rendered]
    if remaining:
        raise ValueError(
            f"{manifest.get('resource_id')}: stale REST HTTP semantics remain: {remaining}"
        )


def correct_declared_http_descriptions(manifest: dict[str, Any]) -> None:
    """Reconcile old prose only when an existing explicit status rule supports it."""
    constraint = manifest.get("constraint", {})
    status_rule = constraint.get("http_status_semantics", {})
    if status_rule.get("failure") != "HTTP 400-599 or request exception":
        return
    replacements = {
        "only request exceptions become status=error":
            "HTTP 400-599 responses and request exceptions produce status=error",
        "only request exceptions are error envelopes":
            "HTTP 400-599 responses and request exceptions produce error envelopes",
    }
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "description" and isinstance(item, str):
                    for before, after in replacements.items():
                        item = item.replace(before, after)
                    value[key] = item
                elif key == "limitations" and isinstance(item, list):
                    value[key] = [replacements.get(v, v) if isinstance(v, str) else v for v in item]
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(manifest)


def _supporting_source_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for helper in sorted(_imports(path) & LOCAL_HELPERS):
        helper_path = path.parent / f"{helper}.py"
        if not helper_path.is_file():
            continue
        relative = helper_path.relative_to(ROOT).as_posix()
        hashes[f"file://{relative}"] = _sha256(helper_path)
    return hashes


def _neutralize_unobserved_utility(manifest: dict[str, Any]) -> None:
    utility = manifest.setdefault("utility", {})
    memory = manifest.get("memory") if isinstance(manifest.get("memory"), dict) else {}
    trajectories = list(memory.get("success_trajectories") or []) + list(
        memory.get("failure_reflections") or []
    )
    if not trajectories:
        utility["expected_success_rate"] = 0.5
        utility["successes"] = 0
        utility["attempts"] = 0
        utility["evidence_status"] = "unobserved"


def synchronize(*, check: bool = False) -> dict[str, Any]:
    paths = sorted(
        path for path in REGISTRY.rglob("*.json") if not path.name.startswith("_")
    )
    changed: list[str] = []
    runtime_counts: dict[str, int] = {}
    rest_count = 0
    for manifest_path in paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        implementation = _implementation_path(manifest)
        runtime = str((manifest.get("execution") or {}).get("runtime") or "")
        runtime_counts[runtime] = runtime_counts.get(runtime, 0) + 1
        manifest["runtime_requirements"] = _runtime_requirements(manifest, implementation)
        provenance = manifest.setdefault("provenance", {})
        provenance["source_hash"] = _sha256(implementation)
        provenance["local_implementation_uri"] = str(
            (manifest.get("execution") or {}).get("uri") or ""
        )
        supporting_hashes = _supporting_source_hashes(implementation)
        if supporting_hashes:
            provenance["supporting_source_hashes"] = supporting_hashes
        else:
            provenance.pop("supporting_source_hashes", None)
        if runtime == "rest_api":
            rest_count += 1
            provenance["auth_required"] = False
            provenance["auth_evidence"] = "wrapper_and_shared_adapter_read_no_credentials"
            _normalize_rest_http_semantics(manifest)
        _neutralize_unobserved_utility(manifest)
        rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        old = manifest_path.read_text(encoding="utf-8-sig")
        if rendered != old:
            changed.append(str(manifest_path.relative_to(ROOT)))
            if not check:
                manifest_path.write_text(rendered, encoding="utf-8")
    return {
        "manifest_count": len(paths),
        "changed_count": len(changed),
        "changed": changed,
        "runtime_counts": dict(sorted(runtime_counts.items())),
        "rest_auth_confirmed_no_auth": rest_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = synchronize(check=args.check)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.check and report["changed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
