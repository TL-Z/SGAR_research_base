# verify_and_derive.py
# Task B: Manifest Validation & Legacy Schema Deriver (Auto-Discovery Version)
# ==============================================================================
# Automatically scans for all category draft files (*_draft.json) in Pool/resources/json/,
# runs rigorous schema validations for each found category, auto-derives all legacy fields,
# and outputs the final production-ready JSON files and a single unified Markdown report.

import os
import sys
import json
from typing import Dict, Any, List

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
JSON_DIR = os.path.join(PROJECT_ROOT, 'Pool', 'resources', 'json')
REPORT_FILE = os.path.join(PROJECT_ROOT, 'docs', 'verification_report', 'manifest_ingestion_verification_report.md')

# The mapping of category names to their expected draft and production filenames
CATEGORIES = {
    "Tool": ("tools_draft.json", "tools.json"),
    "Model": ("models_draft.json", "models.json"),
    "Agent": ("agents_draft.json", "agents.json"),
    "Skill": ("skills_draft.json", "skills.json")
}

def validate_canonical_manifest(m: Dict[str, Any]) -> List[str]:
    errors = []
    r_id = m.get("resource_id", "unknown")
    r_type = m.get("resource_type", "unknown")
    
    # 1. Top-level field presence
    required_keys = [
        "manifest_version", "resource_id", "resource_type", "status",
        "capability", "constraint", "io", "routing", "execution",
        "utility", "provenance", "type_specific", "memory"
    ]
    for k in required_keys:
        if k not in m:
            errors.append(f"Missing required top-level field: '{k}'")
            
    # 2. Strict ID Naming Convention
    allowed_types = ["Tool", "Model", "Agent", "Skill", "Resource", "Device", "MultiAgentSystem"]
    if r_type not in allowed_types:
        errors.append(f"Invalid resource_type '{r_type}'. Allowed types: {allowed_types}")
    else:
        prefix = r_type.lower()
        if prefix == "multiagentsystem":
            prefix = "multiagent"
        if "resource_id" in m and not r_id.startswith(f"{prefix}."):
            errors.append(f"resource_id '{r_id}' does not match standard '{prefix}.<name>.v1'")
            
    # 3. Capability inner structure
    cap = m.get("capability", {})
    if not isinstance(cap.get("core_primitives"), list) or len(cap.get("core_primitives", [])) == 0:
        errors.append("capability.core_primitives must be a non-empty array.")
    if not isinstance(cap.get("problem_space"), str) or len(cap.get("problem_space", "")) == 0:
        errors.append("capability.problem_space must be a non-empty string.")
    if not isinstance(cap.get("domain_tags"), list) or len(cap.get("domain_tags", [])) == 0:
        errors.append("capability.domain_tags must be a non-empty array.")
        
    # 4. Type-Specific Validation Rules
    ts = m.get("type_specific", {})
    if r_type == "Tool":
        # Tool-specific check
        if "tool" not in ts:
            errors.append("Missing type_specific.tool block for Tool resource.")
        io = m.get("io", {})
        if "input_contract" not in io or not isinstance(io["input_contract"], list):
            errors.append("io.input_contract must be an array.")
        # Physical File existence verification
        exec_info = m.get("execution", {})
        uri = exec_info.get("uri", "")
        if not uri.startswith("file://"):
            errors.append(f"execution.uri '{uri}' must start with 'file://'")
        else:
            rel_path = uri[len("file://"):]
            abs_path = os.path.join(PROJECT_ROOT, rel_path)
            if not os.path.exists(abs_path):
                errors.append(f"Target python script file not found at physical path: '{abs_path}'")
                
    elif r_type == "Model":
        model_block = ts.get("model")
        if not isinstance(model_block, dict):
            errors.append("Missing type_specific.model block for Model resource.")
        else:
            if not model_block.get("model_id"):
                errors.append("type_specific.model.model_id is required for Model resources.")
        exec_info = m.get("execution", {})
        if not isinstance(exec_info, dict) or not exec_info.get("model_id"):
            errors.append("execution.model_id is required for Model resources.")
        io = m.get("io", {})
        if not isinstance(io.get("input_contract"), list) or len(io.get("input_contract", [])) == 0:
            errors.append("io.input_contract must be a non-empty array for Model resources.")
        if "output_contract" not in io or not isinstance(io.get("output_contract"), dict):
            errors.append("io.output_contract must be an object for Model resources.")
            
    elif r_type == "Agent":
        if "agent" not in ts:
            errors.append("Missing type_specific.agent block for Agent resource.")
            
    elif r_type == "Skill":
        skill_block = ts.get("skill")
        if not isinstance(skill_block, dict):
            errors.append("Missing type_specific.skill block for Skill resource.")
        else:
            required_skill_fields = [
                "canonical_name",
                "source_namespace",
                "skill_kind",
                "portability",
                "workflow_hint",
                "recommended_roles",
                "avoid_when",
                "required_resource_ids",
                "optional_resource_ids",
                "reference_catalog",
            ]
            for field_name in required_skill_fields:
                if field_name not in skill_block:
                    errors.append(
                        f"Missing type_specific.skill.{field_name} for Skill resource."
                    )
            if skill_block.get("portability") not in {
                "pure_prompt",
                "tool_assisted",
                "agent_bound",
                "runtime_bound",
            }:
                errors.append("Invalid type_specific.skill.portability.")
        exec_info = m.get("execution", {})
        if exec_info.get("runtime") != "prompt_skill":
            errors.append("Skill execution.runtime must be 'prompt_skill'.")
        uri = str(exec_info.get("uri", ""))
        if not uri.startswith("file://"):
            errors.append(f"Skill execution.uri '{uri}' must start with 'file://'.")
        else:
            rel_path = uri[len("file://"):]
            abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, rel_path))
            if not os.path.isfile(abs_path):
                errors.append(f"Skill entrypoint not found: '{abs_path}'.")
            elif os.path.getsize(abs_path) > 128 * 1024:
                errors.append("Skill entrypoint exceeds the 128 KiB runtime limit.")
            elif isinstance(skill_block, dict):
                package_root = os.path.dirname(abs_path)
                for reference in skill_block.get("reference_catalog", []):
                    if not isinstance(reference, dict) or not reference.get("path"):
                        errors.append("Skill reference_catalog entries require a path.")
                        continue
                    reference_path = os.path.abspath(
                        os.path.join(package_root, str(reference["path"]))
                    )
                    if (
                        reference_path != package_root
                        and not reference_path.startswith(package_root + os.sep)
                    ):
                        errors.append(
                            f"Skill reference escapes its package: {reference['path']}."
                        )
                    elif not os.path.isfile(reference_path):
                        errors.append(
                            f"Skill reference not found: {reference['path']}."
                        )
            
    elif r_type == "Resource":
        if "resource" not in ts:
            errors.append("Missing type_specific.resource block for Resource element.")
            
    elif r_type == "Device":
        if "device" not in ts:
            errors.append("Missing type_specific.device block for Device target.")
            
    return errors

def derive_legacy_fields(m: Dict[str, Any]) -> Dict[str, Any]:
    # Clone manifest to avoid side effects
    derived = json.loads(json.dumps(m))
    
    # 1. Derive legacy "type" structure
    derived["type"] = {
        "resource_type": derived["resource_type"],
        "resource_tag": derived["capability"]["domain_tags"]
    }
    
    # 2. Derive legacy "input_contract" and "output_contract" if present in io
    io = derived.get("io", {})
    if "input_contract" in io:
        derived["input_contract"] = io["input_contract"]
    if "output_contract" in io:
        derived["output_contract"] = io["output_contract"]
        
    return derived

def generate_unified_report(all_results: Dict[str, List[Dict[str, Any]]]):
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    
    total_scanned = sum(len(res) for res in all_results.values())
    total_passed = sum(sum(1 for r in res if r["passed"]) for res in all_results.values())
    total_failed = total_scanned - total_passed
    
    markdown_lines = [
        "# S-GAR Manifest Ingestion & Verification Report",
        f"**Execution Status**: {'🟢 PASS' if total_failed == 0 else '🔴 FAIL'}",
        f"- **Total Scanned Resources**: {total_scanned}",
        f"- **Valid Manifests (Passed)**: {total_passed}",
        f"- **Invalid Manifests (Failed)**: {total_failed}",
        "",
        "## 1. Summary of Verification Failures & Issues"
    ]
    
    has_failures = False
    for category, results in all_results.items():
        failures = [r for r in results if not r["passed"]]
        if failures:
            if not has_failures:
                markdown_lines.extend([
                    "| Category | Resource ID | Errors Detected |",
                    "| :--- | :--- | :--- |"
                ])
                has_failures = True
            for r in failures:
                err_msg = "<br>".join(r["errors"])
                markdown_lines.append(f"| **{category}** | `{r['resource_id']}` | {err_msg} |")
                
    if not has_failures:
        markdown_lines.append("*🎉 All scanned resources successfully passed S-GAR schema validation!*")
        
    markdown_lines.append("")
    markdown_lines.append("## 2. Ingestion Detail Analysis (Sampled & Grouped)")
    
    for category, results in all_results.items():
        if not results:
            continue
        markdown_lines.append(f"### Category: `{category}` (Total: {len(results)})")
        
        type_failures = [x for x in results if not x["passed"]]
        type_successes = [x for x in results if x["passed"]]
        
        if type_failures:
            markdown_lines.append("#### 🔴 Failed Items:")
            for r in type_failures:
                markdown_lines.append(f"- **{r['resource_id']}**: {', '.join(r['errors'])}")
                
        if type_successes:
            sample_size = 3
            sample = type_successes[:sample_size]
            is_sampled = len(type_successes) > sample_size
            
            markdown_lines.append(f"#### 🟢 Successful Items (Sampled {len(sample)}/{len(type_successes)}):" if is_sampled else f"#### 🟢 Successful Items (All {len(type_successes)}):")
            for r in sample:
                cap = r["original"]["capability"]
                markdown_lines.append(f"##### `{r['resource_id']}`")
                markdown_lines.append(f"- **Capability problem_space**: *{cap.get('problem_space', 'None')}*")
                markdown_lines.append(f"- **Primitives**: `{', '.join(cap.get('core_primitives', []))}`")
                markdown_lines.append(f"- **Derived type.resource_tag**: `{', '.join(r['derived']['type']['resource_tag'])}`")
                if "provenance" in r["original"] and "source_hash" in r["original"]["provenance"]:
                    markdown_lines.append(f"- **SHA-256 Code Signature**: `{r['original']['provenance']['source_hash']}`")
            if is_sampled:
                markdown_lines.append(f"\n*... and {len(type_successes) - sample_size} more successfully verified items were omitted from detailed listing to prevent context explosion.*")
        markdown_lines.append("")
        
    markdown_lines.append("---")
    markdown_lines.append("*Report dynamically generated by verify_and_derive.py*")
    
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        f.write("\n".join(markdown_lines))
        
    print(f"[Task B Report] Sampled markdown report written to {REPORT_FILE}")

def main():
    if not os.path.exists(JSON_DIR):
        print(f"[Error] JSON directory not found at: {JSON_DIR}")
        sys.exit(1)
        
    all_results = {}
    any_processed = False
    
    # Auto-discover and process all active draft files on disk
    for category, (draft_name, prod_name) in CATEGORIES.items():
        draft_path = os.path.join(JSON_DIR, draft_name)
        prod_path = os.path.join(JSON_DIR, prod_name)
        
        if not os.path.exists(draft_path):
            continue
            
        print(f"Auto-discovered active draft for category '{category}': {draft_name}")
        any_processed = True
        
        with open(draft_path, 'r', encoding='utf-8') as f:
            try:
                drafts = json.load(f)
            except Exception as e:
                print(f"[Error] Failed to load draft {draft_name}: {e}")
                continue
                
        results = []
        derived_manifests = []
        
        for m in drafts:
            errors = validate_canonical_manifest(m)
            r_id = m.get("resource_id", "unknown")
            passed = len(errors) == 0
            
            derived = None
            if passed:
                derived = derive_legacy_fields(m)
                derived_manifests.append(derived)
                
            results.append({
                "resource_id": r_id,
                "passed": passed,
                "errors": errors,
                "original": m,
                "derived": derived
            })
            
        # Preserve accumulated runtime utility data (successes/attempts) from existing production file.
        # If a resource already has real execution data (attempts > 10 means beyond the initial prior),
        # keep its successes/attempts/expected_success_rate rather than resetting to draft defaults.
        if os.path.exists(prod_path):
            try:
                with open(prod_path, 'r', encoding='utf-8') as f:
                    existing = {item["resource_id"]: item for item in json.load(f) if "resource_id" in item}
                preserved = 0
                for manifest in derived_manifests:
                    rid = manifest.get("resource_id")
                    old = existing.get(rid)
                    if old:
                        old_u = old.get("utility", {})
                        if old_u.get("attempts", 0) > 10:
                            u = manifest.setdefault("utility", {})
                            u["successes"] = old_u["successes"]
                            u["attempts"] = old_u["attempts"]
                            u["expected_success_rate"] = old_u["expected_success_rate"]
                            preserved += 1
                if preserved:
                    print(f"  [Preserve] Retained runtime utility data for {preserved} resource(s) with real execution history.")
            except Exception as e:
                print(f"  [Warning] Could not read existing {prod_name} for utility preservation: {e}")

        # Write output derived JSON directly to production file
        with open(prod_path, 'w', encoding='utf-8') as f:
            json.dump(derived_manifests, f, ensure_ascii=False, indent=4)

        print(f"  [Success] Compiled {len(derived_manifests)} active resources directly into production file: {prod_name}")
        all_results[category] = results
        
    if not any_processed:
        print("[Info] No active resource drafts (*_draft.json) were found on disk. Verification skipped.")
        sys.exit(0)
        
    generate_unified_report(all_results)

if __name__ == '__main__':
    main()
