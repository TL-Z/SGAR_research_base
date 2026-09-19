"""Canonical capability-operation registry for Tool routing.

Execution ``OperationKind`` answers *how* a selected resource runs.  This
module answers *what* a Tool does.  Tool manifests register their operations
through ``capability.core_primitives`` so the registry remains data-driven.
"""

from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet

EXACT_CAPABILITY_OPERATIONS = frozenset(
    {
        "convert_json_to_yaml",
        "convert_yaml_to_json",
        "convert_json_to_toml",
        "convert_xml_to_json",
        "project_csv_columns",
        "infer_json_schema",
        "parse_sql_ddl",
        "solve_symbolic",
        "detect_language",
        "transpile_sql_dialect",
        "audit_bandit",
        "scan_secrets",
        "audit_dependencies",
    }
)

CAPABILITY_TO_EXECUTION_OPERATION = {
    "write_complete_text_file": "write_file",
    "create_text_artifact": "write_file",
    "overwrite_file_contents": "write_file",
    "convert_json_to_yaml": "convert_format",
    "convert_yaml_to_json": "convert_format",
    "convert_json_to_toml": "convert_format",
    "convert_xml_to_json": "convert_format",
    "project_csv_columns": "transform_data",
    "infer_json_schema": "parse_data",
    "parse_sql_ddl": "parse_data",
    "solve_symbolic": "compute_math",
    "detect_language": "analyze_text",
    "transpile_sql_dialect": "convert_format",
    "audit_bandit": "audit_security",
    "scan_secrets": "audit_security",
    "audit_dependencies": "audit_security",
}

# The only canonical source for exact Tool capabilities.  A Tool manifest may
# advertise one of these semantic operations; the orchestrator executes it
# through the mapped OperationKind enum below.
CAPABILITY_OPERATION_REGISTRY = {
    operation: {"execution_operation_kind": execution_kind, "resource_fragments": ()}
    for operation, execution_kind in CAPABILITY_TO_EXECUTION_OPERATION.items()
}
for _fragment, _operation in {
    "tool_json_to_yaml": "convert_json_to_yaml",
    "tool_yaml_to_json": "convert_yaml_to_json",
    "tool_json_to_toml": "convert_json_to_toml",
    "tool_xml_to_json": "convert_xml_to_json",
    "tool_csv_column_filter": "project_csv_columns",
    "tool_lib_genson_schema": "infer_json_schema",
    "tool_sql_ddl_to_json_schema": "parse_sql_ddl",
    "tool_lib_sympy_solve": "solve_symbolic",
    "tool_lib_langdetect_detect": "detect_language",
    "tool_lib_sqlglot_transpile": "transpile_sql_dialect",
}.items():
    CAPABILITY_OPERATION_REGISTRY[_operation]["resource_fragments"] = (_fragment,)


def validate_tool_manifest_operations(manifest: Dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Validate explicit manifest capability declarations.

    Legacy manifests use ``core_primitives`` as descriptive metadata and are
    compiled by ``tool_allowed_operation_kinds``.  New manifests should use
    ``capability.operation_kinds``; those declarations are strict and must be
    present in the canonical registry.
    """
    capability = manifest.get("capability", {}) if isinstance(manifest, dict) else {}
    if not isinstance(capability, dict):
        return False, ("capability must be an object",)
    declared = capability.get("operation_kinds")
    if declared is None:
        return True, ()
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, (list, tuple, set)):
        return False, ("capability.operation_kinds must be a list",)
    normalized = tuple(normalize_capability_operation(item) for item in declared)
    unknown = tuple(sorted(item for item in normalized if item not in CAPABILITY_OPERATION_REGISTRY))
    return not unknown, unknown


def execution_operation_kind_for_tool(manifest: Dict[str, Any]) -> str:
    """Return the executable OperationKind for a Tool's exact capability."""
    allowed = tool_allowed_operation_kinds(manifest)
    mapped = {
        CAPABILITY_TO_EXECUTION_OPERATION.get(operation, operation)
        for operation in allowed
    }
    return sorted(mapped)[0] if len(mapped) == 1 else "run_tool"


def normalize_capability_operation(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return normalized.strip("_")


def manifest_capability_operations(manifest: Dict[str, Any]) -> FrozenSet[str]:
    capability = manifest.get("capability", {}) if isinstance(manifest, dict) else {}
    if not isinstance(capability, dict):
        return frozenset()
    constraint = manifest.get("constraint", {}) if isinstance(manifest, dict) else {}
    constraint = constraint if isinstance(constraint, dict) else {}
    explicit = (
        capability.get("operation_kinds")
        or constraint.get("operation_kinds")
        or capability.get("core_primitives")
        or []
    )
    if isinstance(explicit, str):
        explicit = [explicit]
    return frozenset(
        operation
        for item in explicit
        if (operation := normalize_capability_operation(item))
    )


def _semantic_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for key in sorted(value):
            values.extend(_semantic_values(value[key]))
        return values
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            values.extend(_semantic_values(item))
        return values
    if isinstance(value, (set, frozenset)):
        values = []
        for item in sorted(value, key=str):
            values.extend(_semantic_values(item))
        return values
    if value is None:
        return []
    return [str(value)]


def _tool_semantic_text(manifest: Dict[str, Any]) -> str:
    """Collect positive manifest evidence without treating exclusions as capabilities."""
    capability = manifest.get("capability", {})
    routing = manifest.get("routing", {})
    constraint = manifest.get("constraint", {})
    execution = manifest.get("execution", {})
    io_contract = manifest.get("io", {})

    evidence: list[Any] = [manifest.get("resource_id", "")]
    if isinstance(routing, dict):
        evidence.extend((routing.get("family", ""), routing.get("intent_tags", [])))
    if isinstance(capability, dict):
        evidence.extend(
            capability.get(field, "")
            for field in (
                "core_primitives",
                "summary",
                "description",
                "problem_space",
                "domain_tags",
            )
        )
    if isinstance(constraint, dict):
        evidence.extend(
            constraint.get(field, "")
            for field in (
                "io_signature",
                "accepted_inputs",
                "output_shape",
                "artifact_input",
                "artifact_output",
                "env_requirements",
            )
        )
    if isinstance(execution, dict):
        evidence.append(execution.get("runtime", ""))
    evidence.extend(
        (
            io_contract,
            manifest.get("input_contract", []),
            manifest.get("output_contract", {}),
        )
    )

    normalized = " ".join(_semantic_values(evidence)).lower()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _tool_identity_text(manifest: Dict[str, Any]) -> str:
    """Collect high-signal identity fields that do not describe exclusions."""
    capability = manifest.get("capability", {})
    routing = manifest.get("routing", {})
    evidence: list[Any] = [manifest.get("resource_id", "")]
    if isinstance(routing, dict):
        evidence.extend((routing.get("family", ""), routing.get("intent_tags", [])))
    if isinstance(capability, dict):
        evidence.extend(
            capability.get(field, "")
            for field in ("core_primitives", "summary", "domain_tags")
        )
    normalized = " ".join(_semantic_values(evidence)).lower()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _has_any(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def tool_allowed_operation_kinds(manifest: Dict[str, Any]) -> FrozenSet[str]:
    """Classify an active Tool manifest by its general semantic operation.

    Rules are intentionally ordered from narrow semantic families to broad
    fallbacks.  Runtime and file-shaped inputs are supporting evidence only;
    they never make a document extractor a filesystem reader.
    """
    text = _tool_semantic_text(manifest if isinstance(manifest, dict) else {})
    identity = _tool_identity_text(manifest if isinstance(manifest, dict) else {})
    resource_id = normalize_capability_operation(
        manifest.get("resource_id", "") if isinstance(manifest, dict) else ""
    )

    # Preserve the concrete operation identity before broad families such as
    # ``convert_format`` and ``parse_data`` are considered.  These IDs are
    # deliberately source/target-specific so adjacent Tools cannot compete
    # for the same task merely because they share a transport format.
    for exact_operation, spec in CAPABILITY_OPERATION_REGISTRY.items():
        if any(fragment in resource_id for fragment in spec["resource_fragments"]):
            return frozenset({exact_operation})

    if _has_any(identity, "bandit security", "bandit scan", "bandit audit"):
        return frozenset({"audit_bandit"})
    if _has_any(identity, "detect secrets", "secret scan", "secret detection"):
        return frozenset({"scan_secrets"})
    if _has_any(identity, "dependency cve", "dependency vulnerability", "dependency audit"):
        return frozenset({"audit_dependencies"})

    # New manifests may declare a source/target-specific capability directly.
    # Keep those values authoritative; broad execution OperationKinds such as
    # ``convert_format`` are retained only for legacy manifests below.
    declared_operations = manifest_capability_operations(manifest)
    exact_declared = frozenset(
        operation
        for operation in declared_operations
        if operation in CAPABILITY_OPERATION_REGISTRY
    )
    if exact_declared:
        return exact_declared

    # Security scanners and auditors are more specific than generic code checks.
    if _has_any(
        identity,
        "bandit security",
        "security audit",
        "security header",
        "dependency cve",
        "known vulnerability",
        "pip audit",
        "npm audit",
        "detect secrets",
        "secret scan",
        "cors header presence probe",
    ):
        return frozenset({"audit_security"})

    # Test execution must win over generic script/process execution.
    if _has_any(
        identity,
        "test runner",
        "test execution",
        "execute pytest",
        "run pytest",
        "run cargo test",
        "run recursive go tests",
        "run default jest suite",
        "execute npm test",
        "execute vitest",
    ):
        return frozenset({"run_tests"})

    # Format checkers are format semantics even when they run in check-only mode.
    if _has_any(
        identity,
        "black format",
        "clang format",
        "gofmt",
        "go fmt",
        "rustfmt",
        "format compliance",
        "format check only",
    ):
        return frozenset({"format_code"})

    if _has_any(
        identity,
        "flake8",
        "eslint",
        "ruff lint",
        "ruff check",
        "shellcheck",
        "stylelint",
        "pyflakes",
        "gitlint",
        "lint code",
        "lint python",
        "lint javascript",
    ):
        return frozenset({"lint_code"})

    if _has_any(
        identity,
        "cyclomatic complexity",
        "dead code candidate",
        "static analyzer",
        "static analysis",
        "source line analysis",
        "source line reporting",
        "typescript compiler diagnostics",
        "tsc no emit",
    ):
        return frozenset({"analyze_code"})

    # Document metadata inspection is narrower than document text extraction.
    if _has_any(identity, "pdf page and metadata inspection", "metadata preview"):
        return frozenset({"inspect_metadata"})

    # Document semantics precede generic conversion and file handling.
    if _has_any(
        identity,
        "pdf text preview extraction",
        "extract page text",
        "docx basic paragraph",
        "extract docx",
        "pptx slide text",
        "extract slide",
        "xlsx all sheets",
        "extract every worksheet",
        "local html css text extraction",
        "extract text from local html",
    ):
        return frozenset({"extract_document"})

    # Query languages and structural parsers win over format-looking outputs.
    if _has_any(
        identity,
        "jq filter",
        "jq query",
        "xpath query",
        "evaluate single lxml xpath",
    ):
        return frozenset({"query_data"})

    if _has_any(
        identity,
        "date expression parsing",
        "parse one natural language date",
        "phone number parsing",
        "parse regional phone",
        "junit xml report summary",
        "parse junit xml",
        "sql ddl create table structural summary",
        "parse sql ddl",
        "parse local yaml",
    ):
        return frozenset({"parse_data"})

    if _has_any(
        identity,
        "format conversion",
        "text conversion",
        "to yaml",
        "to toml",
        "to json",
        "to markdown",
        "to html",
        "sql dialect transpilation",
        "transpile sql",
        "tablib export preview",
        "markdown rendering",
        "syntax highlight html",
    ):
        return frozenset({"convert_format"})

    if _has_any(
        identity,
        "schema validation",
        "validate instance against schema",
        "validator by name",
        "graphql sdl schema build",
        "structurally report success",
    ):
        return frozenset({"validate_artifact"})

    if _has_any(
        identity,
        "genson schema",
        "infer json schema",
        "schema from one json",
        "single sample json schema",
        "infer schema from one json",
    ):
        return frozenset({"parse_data"})

    if _has_any(
        identity,
        "csv column projection",
        "project named csv columns",
        "csv mean aggregation",
        "serialize pivot csv",
        "html security sanitization",
        "sanitize html",
        "template rendering",
        "render template variables",
        "compute sha256 digest",
        "schema inference",
        "infer schema from sample",
        "data transformation",
        "normalize record fields",
        "normalize structured records",
    ):
        return frozenset({"transform_data"})

    if _has_any(
        identity,
        "descriptive statistics",
        "pandas describe",
        "process rss measurement",
        "memory metric",
        "system memory point in time snapshot",
        "virtual memory snapshot",
    ):
        return frozenset({"analyze_data"})

    if _has_any(
        identity,
        "language detection",
        "detect language candidates",
        "encoding inference",
        "infer character encoding",
        "semantic word association",
    ):
        return frozenset({"analyze_text"})

    if _has_any(
        identity,
        "symbolic solving",
        "sympy",
        "matrix linalg",
        "numpy linalg",
        "physical unit conversion",
        "pint units",
        "planar geometry calculation",
        "wkt geometry",
        "graph centrality analysis",
        "networkx",
    ):
        return frozenset({"compute_math"})

    if _has_any(
        identity,
        "qr generation",
        "generate qr",
        "image generation",
        "generate image",
        "generate media",
    ):
        return frozenset({"generate_media"})

    # Git-family operations classify by semantic action, independent of runtime.
    git_semantics = resource_id.startswith("tool_mcp_git_") or _has_any(
        identity, " git ", "git read only", "git history", "git branch", "git index"
    )
    if git_semantics:
        if _has_any(
            identity,
            "stage selected paths",
            "commit staged changes",
            "create named branch",
            "git history mutation",
            "git branch mutation",
            "git index management",
        ):
            return frozenset({"mutate_version_control"})
        return frozenset({"inspect_version_control"})

    # MCP filesystem tools classify by action, not by their mcp_server runtime.
    filesystem_semantics = resource_id.startswith("tool_mcp_fs_") or _has_any(
        identity, "filesystem content", "filesystem discovery", "filesystem metadata", "filesystem mutation"
    )
    if filesystem_semantics:
        if _has_any(identity, "create directory", "create parent folder"):
            return frozenset({"create_directory"})
        if _has_any(identity, "move file", "rename file", "relocate path"):
            return frozenset({"move_path"})
        if _has_any(identity, "edit existing file", "replace text fragment"):
            return frozenset({"edit_file"})
        if _has_any(identity, "write complete text file", "overwrite file contents"):
            return frozenset({"write_file"})
        if _has_any(identity, "search files by pattern", "find matching paths", "find matching content"):
            return frozenset({"search_files"})
        if _has_any(identity, "inspect path metadata", "filesystem attributes"):
            return frozenset({"inspect_metadata"})
        if _has_any(
            identity,
            "list immediate directory entries",
            "recursive directory traversal",
            "directory tree generation",
        ):
            return frozenset({"list_directory"})
        if _has_any(identity, "read text file", "read multiple text files", "load file contents"):
            return frozenset({"read_file"})

    if _has_any(
        identity,
        "academic preprint search",
        "clinical trial registry search",
        "crossref bibliographic work search",
        "pubmed biomedical search",
        "search arxiv",
        "search clinicaltrials",
        "search crossref",
        "search pubmed",
    ):
        return frozenset({"search_knowledge"})

    if _has_any(
        identity,
        "dns record resolution",
        "resolve dns",
        "dns single query resolution",
        "ip address geolocation",
        "geolocate ip",
    ):
        return frozenset({"resolve_network"})

    if _has_any(
        identity,
        "current weather",
        "latest ecb fx rate",
        "spot currency",
        "spot price",
        "live iss",
        "current iss",
        "live aircraft",
        "recent usgs earthquakes",
        "recent earthquake",
    ):
        return frozenset({"fetch_live_data"})

    if _has_any(
        identity,
        "definition lookup",
        "reference lookup",
        "reference profile lookup",
        "public holiday calendar",
        "nutrition lookup",
        "product barcode lookup",
        "pokemon resource",
        "wikipedia summary",
        "poetry author lookup",
        "entity lookup",
        "university directory",
        "sports team directory",
        "recipe name search",
        "museum artwork search",
    ):
        return frozenset({"lookup_reference"})

    if _has_any(identity, "python script execution", "execute one workspace local python script"):
        return frozenset({"execute_script"})

    # REST-backed Tools without a narrower semantic family remain API queries.
    execution = manifest.get("execution", {}) if isinstance(manifest, dict) else {}
    runtime = execution.get("runtime", "") if isinstance(execution, dict) else ""
    if normalize_capability_operation(runtime) == "rest_api" and _has_any(
        text, "rest api", "api query", "query api", "api envelope", "http response"
    ):
        return frozenset({"query_api"})

    if _has_any(identity, "image header metadata", "inspect image format", "path metadata"):
        return frozenset({"inspect_metadata"})

    return frozenset({"run_tool"})


def normalize_task_capability_operations(text: str) -> FrozenSet[str]:
    """Normalize explicit node-local intent into registered primitive IDs."""
    value = str(text or "").lower()
    operations: set[str] = set()

    if "flake8" in value or re.search(r"\b(?:ruff|pylint|eslint)\s+(?:lint|linting|check)", value):
        return frozenset({"lint_code"})
    if (
        "bandit" in value
        and "dependency" not in value
        and _has_any(value, "security", "scan", "audit")
    ):
        return frozenset({"audit_bandit"})
    if _has_any(value, "detect secrets", "secret scan", "secret detection"):
        return frozenset({"scan_secrets"})
    if _has_any(value, "dependency cve", "dependency vulnerability", "dependency audit"):
        return frozenset({"audit_dependencies"})
    if "bandit" in value and _has_any(value, "security", "scan", "audit"):
        return frozenset({"audit_security"})
    if "pdf" in value:
        if any(term in value for term in ("extract", "text preview", "preview text", "read", "读取", "提取", "文本预览")):
            operations.add("extract_page_text")
        elif any(term in value for term in ("page count", "metadata", "页数", "元数据")):
            operations.add("count_pdf_pages")
    if any(term in value for term in ("immediate child", "immediate entr", "without recursion", "non-recursive", "直接内容", "列出")):
        operations.add("list_immediate_directory_entries")
    if any(term in value for term in ("directory tree", "recursive tree", "recursively list", "目录树", "递归")):
        operations.add("recursive_directory_traversal")
    if any(term in value for term in ("search files", "search file", "grep", "find files", "file pattern", "搜索文件", "匹配文件")):
        operations.add("search_files_by_pattern")
    if any(term in value for term in ("file metadata", "file info", "timestamps", "文件信息", "元数据")) and "pdf" not in value:
        operations.add("inspect_path_metadata")
    if any(term in value for term in ("read file", "file contents", "read the complete", "读取文件", "文件内容")) and "pdf" not in value:
        operations.add("read_text_file")
    if any(term in value for term in ("edit file", "replace text", "write file", "修改文件", "写入文件")):
        operations.add("edit_file_content")
    if any(term in value for term in ("create directory", "make directory", "创建目录")):
        operations.add("create_directory")
    if any(term in value for term in ("detect language", "language detection", "识别语言", "语言检测")):
        operations.add("detect_language_candidates")
    return frozenset(operations)


def normalize_task_general_operation_kinds(text: str) -> FrozenSet[str]:
    """Normalize explicit task action/object phrases into general Tool kinds.

    This deliberately does not infer an operation from a bare noun such as
    ``PDF`` or ``weather``.  It is used for pre-ranking compatibility, where
    absent evidence must remain UNKNOWN rather than excluding a Tool.
    """
    value = str(text or "").lower()
    operations: set[str] = set()

    if "flake8" in value or re.search(r"\b(?:ruff|pylint|eslint)\s+(?:lint|linting|check)", value):
        return frozenset({"lint_code"})
    if (
        "bandit" in value
        and "dependency" not in value
        and _has_any(value, "security", "scan", "audit")
    ):
        return frozenset({"audit_bandit"})

    exact_patterns = (
        (r"json\s*(?:file|document|data)?\s*(?:to|into|as)\s*toml", "convert_json_to_toml"),
        (r"xml\s*(?:file|document|data)?\s*(?:to|into|as)\s*json", "convert_xml_to_json"),
        (r"yaml\s*(?:file|document|data)?\s*(?:to|into|as)\s*json", "convert_yaml_to_json"),
        (r"json\s*(?:file|document|data)?\s*(?:to|into|as)\s*yaml", "convert_json_to_yaml"),
    )
    if _has_any(value, "convert", "transform", "serialize"):
        for pattern, operation in exact_patterns:
            if re.search(pattern, value):
                return frozenset({operation})
        # Natural language often inserts an object phrase between the source
        # and target formats ("JSON file ... convert its contents into TOML").
        # Preserve direction without requiring an exact adjacent "JSON to
        # TOML" phrase.
        format_positions = {
            fmt: value.find(fmt) for fmt in ("json", "yaml", "xml", "toml")
        }
        directional = {
            ("json", "yaml"): "convert_json_to_yaml",
            ("yaml", "json"): "convert_yaml_to_json",
            ("json", "toml"): "convert_json_to_toml",
            ("xml", "json"): "convert_xml_to_json",
        }
        for (source, target), operation in directional.items():
            if format_positions[source] >= 0 and format_positions[target] > format_positions[source]:
                return frozenset({operation})
    if "csv" in value and _has_any(value, "column", "columns", "project", "projection", "select"):
        return frozenset({"project_csv_columns"})
    if "sql" in value and _has_any(value, "ddl", "create table", "table names", "column definitions"):
        return frozenset({"parse_sql_ddl"})
    if "json schema" in value and _has_any(value, "infer", "generate", "sample", "structure"):
        return frozenset({"infer_json_schema"})
    if _has_any(value, "sympy", "symbolic solving", "symbolic solve") and _has_any(value, "solve", "equation"):
        return frozenset({"solve_symbolic"})
    if (
        _has_any(value, "detect language", "language detection", "language-detection")
        or re.search(r"detect\s+(?:the\s+)?language", value)
    ):
        return frozenset({"detect_language"})

    if (
        "pdf" in value
        and _has_any(value, "extract", "提取")
        and _has_any(value, "text", "文本", "preview")
    ) or _has_any(value, "pdf text extraction", "提取pdf文本", "从pdf提取文本", "提取 pdf 文本"):
        operations.add("extract_document")
    if _has_any(
        value,
        "list directory", "list the directory", "list immediate directory",
        "list immediate child", "directory entries", "列出目录", "列出该目录",
    ):
        operations.add("list_directory")
    if _has_any(
        value,
        "convert json", "convert yaml", "json to yaml", "yaml to json",
        "convert format", "format conversion", "json 转 yaml", "yaml 转 json",
        "转换json", "转换yaml", "格式转换",
    ) or (
        "json" in value
        and "yaml" in value
        and re.search(r"\b(?:convert|transform|serialize)\b", value) is not None
    ):
        operations.add("convert_format")
    if _has_any(
        value,
        "jsonpath", "jq", "sql query", "query sql", "查询jsonpath",
        "执行sql查询", "sql 查询",
    ):
        operations.add("query_data")
    if "json schema" in value and _has_any(
        value,
        "validate", "validation", "check against", "conform to", "verify against",
    ):
        operations.add("validate_artifact")
    if _has_any(
        value,
        "generate json schema", "generate schema", "generate a schema",
        "infer schema", "schema from sample", "推断schema", "生成schema",
    ):
        operations.add("parse_data")
    if _has_any(value, "lint code", "lint the", "run lint", "代码检查", "代码静态检查"):
        operations.add("lint_code")
    if _has_any(
        value,
        "security audit", "dependency audit", "bandit audit", "run bandit",
        "依赖安全审计", "安全审计", "bandit 安全",
    ):
        operations.add("audit_security")
    if _has_any(value, "run pytest", "execute pytest", "pytest tests", "运行pytest", "运行 pytest"):
        operations.add("run_tests")
    if _has_any(
        value,
        "search arxiv", "search pubmed", "arxiv search", "pubmed search",
        "search for papers", "检索arxiv", "检索pubmed", "搜索arxiv", "搜索pubmed",
    ):
        operations.add("search_knowledge")
    if (
        _has_any(value, "dns", "ip address", "ip地址")
        and _has_any(value, "resolve", "resolution", "解析")
    ) or _has_any(value, "dns解析", "解析dns", "解析ip", "ip解析"):
        operations.add("resolve_network")
    if _has_any(
        value,
        "live weather", "current weather", "weather lookup", "live exchange",
        "exchange price", "spot price", "实时天气", "当前天气", "实时汇率", "最新汇率",
    ):
        operations.add("fetch_live_data")
    if _has_any(
        value,
        "symbolic math", "symbolic sympy", "sympy math", "solve with sympy",
        "符号数学", "符号计算", "使用sympy", "使用 sympy",
    ):
        operations.add("compute_math")

    return frozenset(operations)
