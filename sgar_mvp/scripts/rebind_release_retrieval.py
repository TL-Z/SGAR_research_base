"""Build an identity-only retrieval generation for a new Release Source Seal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sgar_mvp.src.release_promotion import rebind_release_index_generation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-source-seal", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = rebind_release_index_generation(
        project_root=PROJECT_ROOT,
        source_seal_path=args.release_source_seal,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
