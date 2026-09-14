"""Audit registered production prompt surfaces without making model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.internal_language import build_prompt_surface_registry
from sgar_mvp.src.pipeline_control import canonical_json_bytes


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit English framework prompt surfaces and sealed identities."
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    registry = build_prompt_surface_registry()
    payload = registry.model_dump(mode="json")
    encoded = canonical_json_bytes(payload) + b"\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
    else:
        sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
