"""Restore retrieval policy and indexes from a sealed promotion receipt."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.release_promotion import rollback_release_retrieval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promotion-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = rollback_release_retrieval(
        receipt_path=args.promotion_receipt,
        output_path=args.output,
    )
    print(args.output.resolve())
    print(payload["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
