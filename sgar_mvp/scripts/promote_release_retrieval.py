"""Atomically activate one release-sealed Qwen retrieval generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.release_promotion import promote_release_retrieval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-source-seal", type=Path, required=True)
    parser.add_argument("--staging-index-dir", type=Path, required=True)
    parser.add_argument("--provider-capability", type=Path, required=True)
    parser.add_argument("--provider-probe-results", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    payload = promote_release_retrieval(
        project_root=PROJECT_ROOT,
        source_seal_path=args.release_source_seal,
        staging_index_dir=args.staging_index_dir,
        provider_capability_path=args.provider_capability,
        provider_probe_results_path=args.provider_probe_results,
        backup_dir=args.backup_dir,
        receipt_path=args.receipt,
    )
    print(args.receipt.resolve())
    print(payload["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
