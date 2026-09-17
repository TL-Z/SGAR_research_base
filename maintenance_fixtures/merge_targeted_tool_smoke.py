"""Merge a bounded Tool smoke refresh into a complete prior evidence set."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--refresh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    refresh = json.loads(args.refresh.read_text(encoding="utf-8"))
    by_id = {item["resource_id"]: item for item in base.get("tools", [])}
    refreshed_ids = []
    for item in refresh.get("tools", []):
        resource_id = item["resource_id"]
        by_id[resource_id] = item
        refreshed_ids.append(resource_id)
    tools = sorted(by_id.values(), key=lambda item: item["resource_id"])
    payload = {
        **base,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "catalog_active": len(tools),
            "ready": sum(item.get("status") == "ready" for item in tools),
            "blocked": sum(item.get("status") == "blocked" for item in tools),
            "transient_failure": sum(
                item.get("status") == "transient_failure" for item in tools
            ),
        },
        "tools": tools,
        "targeted_refresh": {
            "base_report": str(args.base.resolve()),
            "refresh_report": str(args.refresh.resolve()),
            "resource_ids": sorted(refreshed_ids),
            "merge_policy": "replace_exact_resource_id_only",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
