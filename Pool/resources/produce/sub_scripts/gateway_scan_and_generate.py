# scan_and_generate_draft.py (Unified Gateway Version)
# S-GAR Unified Ingestion & Draft Generator Gateway
# ==============================================================================
# Scans local physical files for specified resource types, parses factual parameters,
# and calls an LLM to generate canonical V1 semantic manifests.
# Decoupled to category-specific modules to prevent Git merge conflicts.

import os
import sys
import json
import argparse
from typing import Dict, Any, List
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Setup directories relative to this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..', '..'))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'Pool', 'resources', 'json')


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


def main():
    parser = argparse.ArgumentParser(description="S-GAR Unified Manifest Draft Ingestor Gateway")
    parser.add_argument("--type", "-t", default="Tool", choices=["Tool", "Model", "Agent", "Skill"],
                        help="Resource category type to scan & ingest (default: Tool)")
    parser.add_argument("--api-key", "-k", help="OpenAI-compatible API Key (defaults to $env:LLM_API_KEY or $env:OPENAI_API_KEY)")
    parser.add_argument("--base-url", "-b", help="API Base URL (defaults to $env:LLM_BASE_URL, $env:OPENAI_BASE_URL, or https://svip.xty.app/v1)")
    parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM Model to use (default: gpt-4o-mini)")
    parser.add_argument("--only", "-o", help="Only ingest a single resource by name substring")
    parser.add_argument("--limit", "-l", type=int, default=0, help="Limit the number of NEW LLM API calls to make (default: 0 = unlimited)")
    args = parser.parse_args()
    
    # Model and Skill ingestion are deterministic and never require credentials.
    requires_llm = args.type not in {"Model", "Skill"}
    api_key = args.api_key or _load_project_env_value("LLM_API_KEY", "OPENAI_API_KEY")
    if requires_llm and not api_key:
        print(f"[Error] No API Key found for Ingestor!")
        print("Please supply it through one of the following methods:")
        print("  1. Pass it as a command-line argument: --api-key <your_key> (or -k <your_key>)")
        print("  2. Set it in your PowerShell terminal before running the script:")
        print("     $env:LLM_API_KEY=\"your_api_key_here\"")
        sys.exit(1)
        
    # 2. Resolve Base URL
    base_url = (
        args.base_url
        or _load_project_env_value("LLM_BASE_URL", "OPENAI_BASE_URL")
        or "https://svip.xty.app/v1"
    ).rstrip("/")
    
    # Mask API key for secure logging
    if requires_llm:
        key_source = "command-line argument (--api-key)" if args.api_key else "terminal environment variable"
        masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "..."
    else:
        key_source = "not required for deterministic ingestion"
        masked_key = "n/a"
    
    print("=" * 60)
    print("S-GAR UNIFIED INGESTION RUN DETAILS:")
    print(f"  - Target Category: {args.type}")
    print(f"  - Target Model:    {args.model}")
    print(f"  - API Base URL:    {base_url}")
    print(f"  - API Key Source:  {key_source}")
    print(f"  - Active Key Sig:  {masked_key}")
    if args.only:
        print(f"  - Filter Pattern:  Only processing items matching '{args.only}'")
    if args.limit > 0:
        print(f"  - Ingestion Limit: At most {args.limit} new LLM calls")
    print("=" * 60 + "\n")
    
    if requires_llm and OpenAI is None:
        print("[Error] OpenAI SDK is required for Tool/Agent ingestion.")
        sys.exit(1)
    client = OpenAI(api_key=api_key, base_url=base_url) if requires_llm else None
    
    # 3. Dynamic Gateway Routing based on target type
    if args.type == "Tool":
        try:
            from tools_scan_and_generate import run_ingestion as run_tool_ingestion
        except ImportError as e:
            print(f"[Error] Failed to load tool ingestion logic: {e}")
            sys.exit(1)
        
        drafts = run_tool_ingestion(client, api_key, base_url, args.model, args.limit, args.only)
        
    elif args.type == "Model":
        try:
            from models_scan_and_generate import run_ingestion as run_model_ingestion
        except ImportError as e:
            print(f"[Error] Failed to load model ingestion logic: {e}")
            sys.exit(1)
        drafts = run_model_ingestion(client, api_key, base_url, args.model, args.limit, args.only)
        
    elif args.type == "Agent":
        try:
            from agents_scan_and_generate import run_ingestion as run_agent_ingestion
        except ImportError as e:
            print(f"[Error] Failed to load agent ingestion logic: {e}")
            sys.exit(1)
        drafts = run_agent_ingestion(client, api_key, base_url, args.model, args.limit, args.only)
        
    elif args.type == "Skill":
        try:
            from skills_scan_and_generate import run_ingestion as run_skill_ingestion
        except ImportError as e:
            print(f"[Error] Failed to load skill ingestion logic: {e}")
            sys.exit(1)
        drafts = run_skill_ingestion(client, api_key, base_url, args.model, args.limit, args.only)
        
    else:
        print(f"[Error] Unsupported resource category: {args.type}")
        sys.exit(1)
        
    # 4. Save aggregated draft manifests to the dedicated output file
    if drafts:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_filename = f"{args.type.lower()}s_draft.json"
        output_draft_file = os.path.join(OUTPUT_DIR, output_filename)
        
        with open(output_draft_file, 'w', encoding='utf-8') as f:
            json.dump(drafts, f, ensure_ascii=False, indent=4)
            
        print(f"\n[Gateway Complete] Aggregated {len(drafts)} drafts saved for category '{args.type}' to {output_draft_file}")
    else:
        print(f"\n[Gateway Complete] No drafts were produced or loaded for category '{args.type}'. Output skipped.")

if __name__ == '__main__':
    main()
