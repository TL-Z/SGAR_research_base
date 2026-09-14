"""Compatibility entry point for the canonical resource-pool compiler.

Do not add aggregation logic here.  ``produce/build_pool.py`` is the only
implementation so legacy invocations cannot produce a different catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path


PRODUCE_DIR = Path(__file__).resolve().parent / "produce"
if str(PRODUCE_DIR) not in sys.path:
    sys.path.insert(0, str(PRODUCE_DIR))

from build_pool import compile_pool


def combine_jsons() -> dict[str, object]:
    return compile_pool(write=True)


if __name__ == "__main__":
    report = combine_jsons()
    print(
        f"combine_json compatibility wrapper: built {report['resource_count']} resources; "
        f"combine sha256={report['combine_sha256']}"
    )
