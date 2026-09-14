# scan_and_generate_draft.py
# Task A: Scanner & LLM-assisted Manifest Generator
# ==============================================================================
# Scans Pool/resources/tools/ for Python scripts, extracts factual fields,
# computes SHA256 hashes, and calls an LLM to generate canonical V1 semantic manifests.

import os
import sys
import json
import hashlib
import argparse
from typing import Dict, Any, List
from openai import OpenAI

# Setup directories relative to this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..', '..'))
TOOLS_DIR = os.path.join(PROJECT_ROOT, 'Pool', 'resources', 'tools')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'Pool', 'resources', 'json')
OUTPUT_DRAFT_FILE = os.path.join(OUTPUT_DIR, 'tools_draft.json')


def _load_project_env_value(*names: str) -> str:
    dotenv_values = {}
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
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


LLM_PROMPT_TEMPLATE = """你是 S-GAR 资源池 manifest 转化器。
请根据给定 source pack 生成符合 S-GAR Manifest V1 规范 of JSON 对象。

要求：
1. 只输出一个 JSON 对象，不要 Markdown，不要用 ```json 语法包裹。
2. 不得编造 source_uri、source_hash、文件路径、模型 ID。
3. 不确定 license 时填 "unknown"。
4. 确保 "status" 设定为 "active"。
5. capability 描述资源能做什么，用于 v_cap 检索。包含：
   - core_primitives (2-5个能力标签，数组)
   - problem_space (一段生动的自然语言中文功能与痛点描述字符串)
   - domain_tags (2-4个专业技术标签，数组)
6. constraint 描述输入、输出、环境约束，用于 v_con 检索。包含：
   - env_requirements (如 Python 版本，依赖包等，数组)
   - io_signature (一句话总结，例如 "Input: {{file_path: str}} | Output: {{validation_result: json}}")
   - artifact_input (输入媒介类型，如 ["file_path"]，数组)
   - artifact_output (输出媒介类型，如 ["json", "plaintext"]，数组)
7. io 必须是机器可读的输入输出契约。包含：
   - input_contract (参数列表，每个参数必须包含 name, kind, required, extensions[可选], cli_position)
   - output_contract (包含 artifact_type 和 description)
8. routing 只填写 family_id (如 "tool.file_reader") 和空的 dependency_slots (数组)。
9. utility 只填写 latency_ms (预估执行毫秒数，数字), token_cost_factor (设为 0.0), expected_success_rate (预估成功率，如 0.98，数字)。
10. type_specific 只填写该资源类型最少必要字段。对于 Tool 来说是：
    "type_specific": {{
      "tool": {{
        "tool_kind": "local_script",
        "language": "python",
        "deterministic": true
      }}
    }}
11. memory 设为空对象，包含 success_trajectories (数组) 和 failure_reflections (数组)：
    "memory": {{
      "success_trajectories": [],
      "failure_reflections": []
    }}

请注意：只生成规范定义的 canonical JSON 结构：
manifest_version, resource_id, resource_type, status, capability, constraint, io, routing, execution, utility, provenance, type_specific, memory

特别重要！！！不要生成任何 legacy 兼容字段（例如外层的 input_contract、output_contract 或 type 字段），这些将在后续处理中自动派生。

【Source Pack】:
{source_pack_json}

请输出规范的 JSON："""

def calculate_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()

def scan_tools() -> List[Dict[str, Any]]:
    tools_pack = []
    if not os.path.exists(TOOLS_DIR):
        print(f"[Error] Tools directory not found at: {TOOLS_DIR}")
        return []
    
    print(f"Scanning for Python scripts in {TOOLS_DIR}...")
    for filename in os.listdir(TOOLS_DIR):
        if not filename.endswith('.py'):
            continue
        
        filepath = os.path.join(TOOLS_DIR, filename)
        sha256_hash = calculate_sha256(filepath)
        
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            code_content = f.read()
            
        # Parse basic info
        base_name = os.path.splitext(filename)[0]
        resource_id = f"tool.{base_name}.v1"
        family_id = f"tool.{base_name}"
        rel_uri = f"Pool/resources/tools/{filename}"
        
        source_pack = {
            "resource_id": resource_id,
            "resource_type": "Tool",
            "status": "active",
            "file_name": filename,
            "relative_uri": rel_uri,
            "sha256": sha256_hash,
            "file_excerpt": code_content[:2000] # Give LLM enough context but keep tokens bounded
        }
        
        tools_pack.append(source_pack)
        
    print(f"Found {len(tools_pack)} Python scripts.")
    return tools_pack

def generate_manifest(client: OpenAI, source_pack: Dict[str, Any], model: str) -> Dict[str, Any]:
    print(f"Generating manifest draft for {source_pack['resource_id']}...")
    source_pack_str = json.dumps(source_pack, ensure_ascii=False, indent=2)
    prompt = LLM_PROMPT_TEMPLATE.format(source_pack_json=source_pack_str)
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1000
        )
        content = response.choices[0].message.content.strip()
        # Clean any markdown block wrappers if present (failsafe)
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        manifest = json.loads(content)
        
        # Inject dynamic factual fields locally to guarantee absolute accuracy
        manifest["manifest_version"] = "1.0"
        manifest["resource_id"] = source_pack["resource_id"]
        manifest["resource_type"] = "Tool"
        manifest["status"] = "active"
        
        manifest["execution"] = {
            "runtime": "python_script",
            "uri": f"file://{source_pack['relative_uri']}",
            "execution_status": "active"
        }
        
        manifest["provenance"] = {
            "source_dataset": "local",
            "source_item_id": source_pack["relative_uri"],
            "source_uri": f"file://{source_pack['relative_uri']}",
            "source_hash": f"sha256:{source_pack['sha256']}",
            "conversion_method": "llm_generated",
            "confidence": 0.95,
            "license": "Apache-2.0"
        }
        
        print(f"  [Success] Draft generated for {source_pack['resource_id']}.")
        return manifest
    except Exception as e:
        print(f"  [Error] Failed to generate manifest for {source_pack['resource_id']}: {e}")
        return {}

def load_existing_tools() -> Dict[str, Dict[str, Any]]:
    tools_path = os.path.join(OUTPUT_DIR, 'tools.json')
    if not os.path.exists(tools_path):
        return {}
    try:
        with open(tools_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {item.get("provenance", {}).get("source_item_id", ""): item for item in data if isinstance(item, dict)}
    except Exception as e:
        print(f"[Warning] Failed to load existing tools.json for caching: {e}")
        return {}

def run_ingestion(client: OpenAI, api_key: str, base_url: str, model: str, limit: int = 0, only: str = None) -> List[Dict[str, Any]]:
    """
    Exposed entry point for unified S-GAR scanner gateway routing.
    """
    tools_pack = scan_tools()
    
    # Filter only target tool if requested
    if only:
        tools_pack = [p for p in tools_pack if only in p["file_name"]]
        print(f"Filtered target: {len(tools_pack)} tools match '{only}'")
        
    existing_tools = load_existing_tools()
    
    drafts = []
    new_calls_made = 0
    
    for pack in tools_pack:
        # Check cache (incremental compilation)
        existing = existing_tools.get(pack["relative_uri"])
        if existing and existing.get("provenance", {}).get("source_hash") == f"sha256:{pack['sha256']}":
            print(f"  [Skipped] {pack['resource_id']} code signature matches tools.json cache. Reusing.")
            # Clone and strip derived legacy fields to keep draft clean
            clean_draft = json.loads(json.dumps(existing))
            for legacy_field in ["type", "input_contract", "output_contract"]:
                clean_draft.pop(legacy_field, None)
            drafts.append(clean_draft)
            continue
            
        # Check limit
        if limit > 0 and new_calls_made >= limit:
            print(f"  [Omitted] {pack['resource_id']} needs ingestion but limit of {limit} reached. Skipped.")
            continue
            
        # Ingest new tool via LLM
        manifest = generate_manifest(client, pack, model)
        if manifest:
            drafts.append(manifest)
            new_calls_made += 1
            
    # Merge unmodified cached tools that weren't in the scanned/filtered set
    # (to prevent losing other tools when running with --only)
    scanned_uris = {p["relative_uri"] for p in tools_pack}
    for uri, existing in existing_tools.items():
        if uri not in scanned_uris:
            clean_draft = json.loads(json.dumps(existing))
            for legacy_field in ["type", "input_contract", "output_contract"]:
                clean_draft.pop(legacy_field, None)
            drafts.append(clean_draft)
            
    return drafts

def main():
    parser = argparse.ArgumentParser(description="S-GAR Manifest Draft Ingestor")
    parser.add_argument("--api-key", "-k", help="OpenAI-compatible API Key (defaults to $env:LLM_API_KEY or $env:OPENAI_API_KEY)")
    parser.add_argument("--base-url", "-b", help="API Base URL (defaults to $env:LLM_BASE_URL, $env:OPENAI_BASE_URL, or https://svip.xty.app/v1)")
    parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM Model to use (default: gpt-4o-mini)")
    parser.add_argument("--only", "-o", help="Only ingest a single tool by name (e.g. 'todo_scanner')")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Limit the number of NEW LLM API calls to make (default: 0 = unlimited)")
    args = parser.parse_args()
    
    api_key = args.api_key or _load_project_env_value("LLM_API_KEY", "OPENAI_API_KEY")
    if not api_key:
        print("[Error] No OpenAI API Key found!")
        print("Please supply it through one of the following methods:")
        print("  1. Pass it as a command-line argument: --api-key <your_key> (or -k <your_key>)")
        print("  2. Set it in your PowerShell terminal before running the script:")
        print("     $env:LLM_API_KEY=\"your_api_key_here\"")
        sys.exit(1)
        
    base_url = (
        args.base_url
        or _load_project_env_value("LLM_BASE_URL", "OPENAI_BASE_URL")
        or "https://svip.xty.app/v1"
    ).rstrip("/")
    
    # Mask API key for secure logging
    key_source = "command-line argument (--api-key)" if args.api_key else "terminal environment variable"
    masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "..."
    
    print("=" * 60)
    print("S-GAR LLM INGESTION RUN DETAILS:")
    print(f"  - Target Model: {args.model}")
    print(f"  - API Base URL: {base_url}")
    print(f"  - API Key Source: {key_source}")
    print(f"  - Active Key Signature: {masked_key}")
    if args.only:
        print(f"  - Filter: Only processing '{args.only}'")
    if args.limit > 0:
        print(f"  - Ingestion Limit: At most {args.limit} new LLM calls")
    print("=" * 60 + "\n")
        
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    drafts = run_ingestion(client, api_key, base_url, args.model, args.limit, args.only)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_DRAFT_FILE, 'w', encoding='utf-8') as f:
        json.dump(drafts, f, ensure_ascii=False, indent=4)
        
    print(f"\n[Task A Complete] Saved {len(drafts)} drafts to {OUTPUT_DRAFT_FILE}")

if __name__ == '__main__':
    main()
