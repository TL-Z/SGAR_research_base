"""Independent validator for one terminal SGAR production run."""

from __future__ import annotations

import argparse
import json

from .run_workspace import RunWorkspaceError, validate_run_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--allow-legacy", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_run_manifest(
            args.run_dir,
            require_production_conformance=not args.allow_legacy,
        )
    except RunWorkspaceError as exc:
        report = {
            "protocol": "sgar-run-manifest-v1",
            "valid": False,
            "error": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    raise SystemExit(0 if report.get("valid") is True else 2)


if __name__ == "__main__":
    main()
