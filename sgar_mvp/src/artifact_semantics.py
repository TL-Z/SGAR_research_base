"""Artifact purpose and local schema-document syntax, separate from execution support."""
from collections.abc import Mapping
from typing import Any

_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}
_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SINGLE = {"additionalProperties", "unevaluatedProperties", "contains", "additionalItems",
           "unevaluatedItems", "propertyNames", "not", "if", "then", "else"}
_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_COUNTS = {"minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties", "minContains", "maxContains"}
_NUMBERS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
_STRINGS = {"$schema", "$id", "$ref", "$dynamicRef", "$anchor", "$dynamicAnchor", "$comment", "title", "description", "format", "pattern", "contentEncoding", "contentMediaType"}
_KNOWN = _MAPS | _SINGLE | _LISTS | _COUNTS | _NUMBERS | _STRINGS | {
    "type", "required", "enum", "const", "items", "uniqueItems", "default", "examples",
    "readOnly", "writeOnly", "deprecated", "dependentRequired", "dependencies", "$vocabulary", "contentSchema"}


def require_artifact_semantics(*, content_kind: str, artifact_type: Any, schema: Any) -> None:
    if content_kind not in {"value", "json_schema_document"}:
        raise ValueError("artifact_content_kind_unknown")
    if content_kind == "json_schema_document" and artifact_type != "json":
        raise ValueError("schema_document_requires_json_artifact")
    # The outer shape cannot prove what a document means. Actual document syntax
    # and task semantics are checked after generation, not guessed from keywords.


def inspect_schema_document(value: Any) -> dict[str, Any]:
    """Check known keyword syntax without executing or fetching references.

    This is deliberately not a full dialect validator. Uncovered features are
    reported separately; their presence is not evidence of invalidity.
    """
    errors: list[str] = []
    uncovered: set[str] = set()
    def strings(v: Any) -> bool:
        return isinstance(v, list) and all(isinstance(x, str) for x in v) and len(v) == len(set(v))
    def visit(node: Any, path: str, depth: int = 0) -> None:
        if isinstance(node, bool):
            return
        if not isinstance(node, Mapping):
            errors.append(path + ":schema_must_be_object_or_boolean")
            return
        if depth > 64:
            uncovered.add("nesting_beyond_local_check_limit")
            return
        for key, val in node.items():
            loc = path + "." + key
            if key not in _KNOWN:
                uncovered.add("keyword:" + key)
            if key == "$schema":
                uncovered.add("dialect_specific_rules")
            if key in {"$ref", "$dynamicRef", "$vocabulary", "format", "pattern", "dependencies", "contentSchema"}:
                uncovered.add("semantic_feature:" + key)
            if key in _STRINGS and not isinstance(val, str):
                errors.append(loc + ":expected_string")
            elif key == "type":
                types = [val] if isinstance(val, str) else val
                if not strings(types) or not types or any(t not in _TYPES for t in types):
                    errors.append(loc + ":invalid_type")
            elif key == "required" and not strings(val):
                errors.append(loc + ":expected_unique_strings")
            elif key == "enum":
                if not isinstance(val, list) or not val:
                    errors.append(loc + ":expected_nonempty_array")
            elif key in _COUNTS and (type(val) is not int or val < 0):
                errors.append(loc + ":expected_nonnegative_integer")
            elif key in _NUMBERS:
                if key in {"exclusiveMinimum", "exclusiveMaximum"} and isinstance(val, bool):
                    uncovered.add("legacy_exclusive_bound_dialect")
                elif type(val) not in {int, float} or (key == "multipleOf" and val <= 0):
                    errors.append(loc + ":invalid_numeric_bound")
            elif key in {"uniqueItems", "readOnly", "writeOnly", "deprecated"} and not isinstance(val, bool):
                errors.append(loc + ":expected_boolean")
            elif key in _MAPS:
                if not isinstance(val, Mapping):
                    errors.append(loc + ":expected_object")
                else:
                    for name, child in val.items(): visit(child, loc + "." + name, depth + 1)
            elif key in _SINGLE or key == "contentSchema":
                visit(val, loc, depth + 1)
            elif key in _LISTS:
                if not isinstance(val, list) or not val:
                    errors.append(loc + ":expected_nonempty_array")
                else:
                    for i, child in enumerate(val): visit(child, loc + "[" + str(i) + "]", depth + 1)
            elif key == "items":
                if isinstance(val, list):
                    uncovered.add("legacy_tuple_items_dialect")
                    for i, child in enumerate(val): visit(child, loc + "[" + str(i) + "]", depth + 1)
                else: visit(val, loc, depth + 1)
            elif key == "dependentRequired":
                if not isinstance(val, Mapping) or any(not strings(v) for v in val.values()):
                    errors.append(loc + ":expected_string_array_map")
    visit(value, "$")
    return {"status": "invalid" if errors else "partial" if uncovered else "checked",
            "coverage": "known_keyword_syntax_only", "errors": errors,
            "uncovered_features": sorted(uncovered), "references_fetched": False}


def validate_schema_document(value: Any) -> bool:
    return inspect_schema_document(value)["status"] != "invalid"


class OutputContractViolation(ValueError):
    def __init__(self, failure_code: str):
        super().__init__(failure_code)
        self.failure_code = failure_code


def validate_output_contract_content(content: bytes, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate actual delivered JSON against its execution contract, for any resource."""
    from .model_response_contracts import strict_json_loads, validate_json_schema_instance, local_json_schema_support
    report: dict[str, Any] = {"status": "pass", "document_validation": {}}
    if contract.get("artifact_type") != "json":
        return report  # Other formats retain their existing descriptor/interface checks.
    try:
        value = strict_json_loads(content.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise OutputContractViolation("artifact_json_invalid") from None
    schema = contract.get("json_schema", contract.get("schema_hint"))
    if schema is None:
        raise OutputContractViolation("artifact_execution_schema_missing")
    if not isinstance(schema, Mapping):
        raise OutputContractViolation("artifact_execution_schema_unsupported")
    supported, _ = local_json_schema_support(schema)
    if not supported:
        raise OutputContractViolation("artifact_execution_schema_unsupported")
    valid, reason = validate_json_schema_instance(value, schema)
    if not valid:
        raise OutputContractViolation("artifact_execution_schema_mismatch:" + str(reason))
    if contract.get("content_kind", "value") == "json_schema_document":
        report["document_validation"] = inspect_schema_document(value)
        if report["document_validation"]["status"] == "invalid":
            raise OutputContractViolation("artifact_schema_document_invalid")
    return report


def validate_delivery_content(
    content: bytes,
    execution_contract: Mapping[str, Any],
    macro_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Check both declared structures against the same bytes, never schema hashes."""
    report = validate_output_contract_content(content, execution_contract)
    macro_schema = macro_contract.get("json_schema")
    if execution_contract.get("artifact_type") == "json" and macro_schema is not None:
        if macro_schema != execution_contract.get("json_schema", execution_contract.get("schema_hint")):
            try:
                validate_output_contract_content(content, macro_contract)
            except OutputContractViolation as exc:
                raise OutputContractViolation("artifact_macro_requirement_conflict:" + exc.failure_code) from None
        report["explicit_macro_structure"] = "pass"
    return report


def check_bound_schema_content(content: bytes, schema_content: str) -> dict[str, Any]:
    """Check one explicit validation document; never infer bindings or fetch refs."""
    from .model_response_contracts import strict_json_loads, local_json_schema_support, validate_json_schema_instance
    report: dict[str, Any] = {"coverage": "supported_local_schema_subset", "references_fetched": False}
    try:
        schema = strict_json_loads(schema_content)
    except (ValueError, UnicodeError):
        return {**report, "status": "unknown", "reason": "bound_schema_json_invalid"}
    syntax = inspect_schema_document(schema)
    if syntax["status"] == "invalid":
        return {**report, "status": "unknown", "reason": "bound_schema_syntax_invalid", "details": syntax["errors"]}
    # Ref resolution and format/dialect extensions outside our local subset must
    # not become false machine proofs. Values inside const/enum are plain data.
    limitations = set(syntax["uncovered_features"]) - {"dialect_specific_rules", "semantic_feature:$ref"}
    def inspect(node: Any) -> None:
        if isinstance(node, bool):
            return
        dialect = node.get("$schema")
        if dialect is not None and dialect not in {
            "https://json-schema.org/draft/2020-12/schema", "https://json-schema.org/draft/2019-09/schema",
            "http://json-schema.org/draft-07/schema#",
        }:
            limitations.add("unsupported_dialect")
        for keyword in ("$id", "$anchor", "format"):
            if keyword in node:
                limitations.add("uncovered_semantics:" + keyword)
        if "$ref" in node and set(node) - {"$ref", "$defs", "definitions", "$schema", "title", "description", "$comment"}:
            limitations.add("ref_sibling_constraints")
        for key, value in node.items():
            if key in _MAPS:
                for child in value.values(): inspect(child)
            elif key in _SINGLE or key == "contentSchema": inspect(value)
            elif key in _LISTS:
                for child in value: inspect(child)
            elif key == "items":
                for child in value if isinstance(value, list) else [value]: inspect(child)
    try:
        inspect(schema)
        supported, reasons = local_json_schema_support(schema, allow_boolean=True)
        limitations.update(reasons)
    except (ValueError, TypeError, RecursionError):
        return {**report, "status": "unknown", "reason": "bound_schema_outside_local_support"}
    if not supported or limitations:
        return {**report, "status": "unknown", "reason": "bound_schema_outside_local_support", "details": sorted(limitations)}
    try:
        value = strict_json_loads(content.decode("utf-8"))
    except (ValueError, UnicodeError):
        return {**report, "status": "fail", "reason": "bound_schema_instance_json_invalid"}
    try:
        valid, reason = validate_json_schema_instance(value, schema)
    except (ValueError, TypeError, RecursionError):
        return {**report, "status": "unknown", "reason": "bound_schema_outside_local_support"}
    return {**report, "status": "pass" if valid else "fail", "reason": reason}
