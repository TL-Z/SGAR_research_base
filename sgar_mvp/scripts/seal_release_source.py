"""Create a committed-source or activated-system SGAR release seal."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.release_environment import require_release_storage_path
from sgar_mvp.src.release_source_seal import (
    build_activated_system_seal,
    build_release_source_seal,
    write_source_seal,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("release", "activated"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent-release-source-seal-sha256")
    parser.add_argument("--promotion-receipt", type=Path)
    args = parser.parse_args()
    output = require_release_storage_path(
        args.output, code="release_source_seal_output_outside_storage_root"
    )
    if args.stage == "release":
        if args.parent_release_source_seal_sha256 or args.promotion_receipt:
            raise RuntimeError("release_source_seal_unexpected_activated_arguments")
        payload = build_release_source_seal(PROJECT_ROOT)
    else:
        if not args.parent_release_source_seal_sha256 or args.promotion_receipt is None:
            raise RuntimeError("activated_system_seal_arguments_missing")
        payload = build_activated_system_seal(
            PROJECT_ROOT,
            parent_release_source_seal_sha256=args.parent_release_source_seal_sha256,
            promotion_receipt=args.promotion_receipt,
        )
    write_source_seal(output, payload)
    print(output)
    print(payload["seal_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
