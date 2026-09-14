"""Commit the exact active retrieval changes sealed by PromotionReceiptV8."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.release_promotion import finalize_release_promotion


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promotion-receipt", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    payload = finalize_release_promotion(
        project_root=PROJECT_ROOT,
        promotion_receipt_path=args.promotion_receipt,
        evidence_path=args.evidence,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
