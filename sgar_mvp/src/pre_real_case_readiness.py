"""Single no-Benchmark activation gate before real-case suite preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .atomic_io import temporary_sibling_path
from .batch_supervisor import launch_supervised_case
from .pipeline_control import canonical_json_bytes, canonical_sha256
from .plan_replay import replay_sealed_plan_artifacts
from .portability_audit import audit_run_portability
from .production_conformance import framework_source_identity, run_production_conformance
from .secret_policy import (
    FORMAL_PROVIDER_CREDENTIAL_ENV_ALLOWLIST,
    sanitize_sensitive_text,
    validate_formal_secret_config,
)
from .structured_response_boundaries import validate_structured_response_boundaries
from .task_invocation import prepare_task_invocation
from .known_canary_failures import load_known_canary_failure_regression


PRE_REAL_CASE_READINESS_PROTOCOL = "sgar-pre-real-case-readiness-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FROZEN_PATHS = (
    "Pool/resources",
    "Pool/index_meta",
    "sgar_mvp/config/retrieval_policy.json",
    "sgar_mvp/config/resource_readiness_rc1.json",
    "sgar_mvp/config/rc1_runtime_lock.json",
)
_MAX_FAILURE_DIAGNOSTIC_BYTES = 8192


class PreRealCaseReadinessError(RuntimeError):
    pass


def _bounded_pytest_failure_diagnostic(
    *,
    stdout: bytes,
    stderr: bytes,
    root: Path,
    basetemp: Path,
) -> dict[str, Any]:
    """Return a bounded, sanitized tail without persisting raw test output."""

    stdout_tail = stdout[-(3 * _MAX_FAILURE_DIAGNOSTIC_BYTES) :].decode(
        "utf-8", errors="replace"
    )
    stderr_tail = stderr[-_MAX_FAILURE_DIAGNOSTIC_BYTES:].decode(
        "utf-8", errors="replace"
    )
    raw_tail = f"[pytest stdout tail]\n{stdout_tail}\n[pytest stderr tail]\n{stderr_tail}"
    secret_values = tuple(
        value
        for name in FORMAL_PROVIDER_CREDENTIAL_ENV_ALLOWLIST
        if (value := os.environ.get(name))
    )
    sanitized, redactions = sanitize_sensitive_text(
        raw_tail,
        secret_values=secret_values,
        host_roots=(
            str(root.resolve()),
            str(basetemp.resolve()),
            str(Path.home().resolve()),
            str(Path(sys.prefix).resolve()),
        ),
    )
    encoded = sanitized.encode("utf-8")[-_MAX_FAILURE_DIAGNOSTIC_BYTES:]
    excerpt = encoded.decode("utf-8", errors="ignore")
    excerpt_bytes = excerpt.encode("utf-8")
    return {
        "scope": "sanitized_tail",
        "excerpt": excerpt,
        "excerpt_sha256": hashlib.sha256(excerpt_bytes).hexdigest(),
        "excerpt_byte_size": len(excerpt_bytes),
        "max_excerpt_bytes": _MAX_FAILURE_DIAGNOSTIC_BYTES,
        "redactions": redactions,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = temporary_sibling_path(path)
    with temporary.open("xb") as handle:
        handle.write(canonical_json_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=60,
        check=False,
    )


def _commit_identity(root: Path) -> dict[str, Any]:
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    return {
        "valid": head.returncode == 0 and len(head.stdout.strip()) == 40,
        "commit": head.stdout.strip(),
        "branch": branch.stdout.strip(),
    }


def _frozen_asset_identity(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for locator in _FROZEN_PATHS:
        path = root / locator
        if path.is_file():
            records.append(
                {
                    "locator": locator,
                    "kind": "file",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        elif path.is_dir():
            members = [
                {
                    "locator": item.relative_to(path).as_posix(),
                    "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
                }
                for item in sorted(path.rglob("*"))
                if item.is_file()
            ]
            records.append(
                {
                    "locator": locator,
                    "kind": "directory",
                    "member_count": len(members),
                    "sha256": canonical_sha256(members),
                }
            )
        else:
            records.append({"locator": locator, "kind": "missing", "sha256": ""})
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", *_FROZEN_PATHS)
    clean = status.returncode == 0 and not status.stdout.strip()
    return {
        "valid": clean and all(item["kind"] != "missing" for item in records),
        "unchanged_from_head": clean,
        "asset_count": len(records),
        "assets_sha256": canonical_sha256(records),
        "records": records,
    }


def _pytest_once(root: Path, basetemp: Path, index: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(basetemp / f"run-{index}"),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        evidence: dict[str, Any] = {
            "run_index": index,
            "passed": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "stdout_byte_size": len(stdout),
            "stderr_byte_size": len(stderr),
        }
        if completed.returncode != 0:
            evidence["failure_diagnostic"] = _bounded_pytest_failure_diagnostic(
                stdout=stdout,
                stderr=stderr,
                root=root,
                basetemp=basetemp / f"run-{index}",
            )
        return evidence
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "run_index": index,
            "passed": False,
            "exit_code": 125,
            "failure_type": type(exc).__name__,
        }


def _full_test_evidence(
    root: Path,
    output: Path,
    *,
    run_full_tests: bool,
    test_runner: Callable[[int], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    if not run_full_tests:
        return {"valid": False, "run_count": 0, "runs": [], "status": "not_run"}
    if test_runner is not None:
        runs = [dict(test_runner(index)) for index in (1, 2)]
    else:
        # A report directory can be deeply nested and the system temp root may
        # be ACL-isolated from child processes.  Use the repository's ignored,
        # short runtime cache so both the parent and pytest share one authority.
        pytest_parent = root / ".sgar_cache" / "t"
        pytest_parent.mkdir(parents=True, exist_ok=True)
        pytest_root = pytest_parent / f"p-{uuid.uuid4().hex[:8]}"
        pytest_root.mkdir(parents=False, exist_ok=False)
        try:
            runs = [_pytest_once(root, pytest_root, index) for index in (1, 2)]
        finally:
            shutil.rmtree(pytest_root, ignore_errors=True)
    return {
        "valid": len(runs) == 2 and all(item.get("passed") is True for item in runs),
        "run_count": len(runs),
        "runs": runs,
        "status": "passed" if all(item.get("passed") is True for item in runs) else "failed",
    }


def _formal_boundary_type_check(root: Path) -> dict[str, Any]:
    """Run the pinned type checker over formal publication boundaries."""

    config_path = root / "pyrightconfig.json"
    if not config_path.is_file():
        return {
            "valid": False,
            "exit_code": 2,
            "failure_code": "formal_type_config_missing",
        }
    try:
        type_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        configured_includes = tuple(str(item) for item in type_config.get("include", ()))
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError):
        return {
            "valid": False,
            "exit_code": 2,
            "failure_code": "formal_type_config_invalid",
        }
    boundary_contract = validate_structured_response_boundaries()
    command = [
        sys.executable,
        "-m",
        "basedpyright",
        "--project",
        str(config_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "valid": False,
            "exit_code": 125,
            "failure_type": type(exc).__name__,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        }
    return {
        "valid": completed.returncode == 0 and boundary_contract["valid"],
        "exit_code": completed.returncode,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "configured_type_check_scope": {
            "include_count": len(configured_includes),
            "includes": list(configured_includes),
        },
        "structured_model_boundary_scope": boundary_contract,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        "stdout_byte_size": len(completed.stdout),
        "stderr_byte_size": len(completed.stderr),
    }


def _batch_supervisor_probe(output: Path) -> dict[str, Any]:
    root = output / "batch_supervisor_probe"
    root.mkdir(parents=True, exist_ok=False)
    sentinel = "readiness-private-sentinel"
    result = launch_supervised_case(
        [
            sys.executable,
            "-c",
            f"import sys;print({str(output)!r});print({sentinel!r}, file=sys.stderr)",
        ],
        timeout_seconds=30,
        case_root=root,
        stream_limit_bytes=1024 * 1024,
        secret_values=(sentinel,),
        host_roots=(str(output),),
    )
    stdout = (root / "supervisor_stdout.log").read_text(encoding="utf-8-sig", errors="strict")
    stderr = (root / "supervisor_stderr.log").read_text(encoding="utf-8-sig", errors="strict")
    return {
        "valid": bool(
            result.exit_code == 0
            and result.cleanup_verified
            and sentinel not in stderr
            and str(output) not in stdout
            and result.stdout["byte_size"] <= 1024 * 1024
            and result.stderr["byte_size"] <= 1024 * 1024
        ),
        "process_sha256": result.process_sha256,
        "bounded_streams": True,
        "cleanup_verified": result.cleanup_verified,
        "secret_sanitized": sentinel not in stderr,
        "host_path_sanitized": str(output) not in stdout,
    }


def run_pre_real_case_readiness(
    *,
    output_dir: str | Path,
    config_path: str | Path,
    project_root: str | Path = PROJECT_ROOT,
    replay_run_dir: str | Path | None = None,
    run_full_tests: bool = True,
    require_docker: bool = True,
    require_source_clean: bool = True,
    test_runner: Callable[[int], Mapping[str, Any]] | None = None,
    known_failure_regression_path: str | Path | None = None,
) -> dict[str, Any]:
    """Recompute the complete no-Benchmark readiness projection."""

    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "pre_real_case_readiness.json"
    if report_path.exists():
        raise PreRealCaseReadinessError("pre_real_case_readiness_report_exists")
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8-sig", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreRealCaseReadinessError("pre_real_case_config_invalid") from exc
    secret = validate_formal_secret_config(config, raise_on_error=False)
    source = framework_source_identity(root)
    commit = _commit_identity(root)
    frozen = _frozen_asset_identity(root)
    try:
        known_failures = (
            load_known_canary_failure_regression(
                known_failure_regression_path,
                expected_framework_source_sha256=source["framework_source_sha256"],
            ).model_dump(mode="json")
            if known_failure_regression_path is not None
            else {
                "protocol": "sgar-known-canary-failure-regression-v1",
                "valid": False,
                "blocking_failure_codes": ["known_failure_regression_missing"],
            }
        )
    except (OSError, UnicodeError, ValueError) as exc:
        known_failures = {
            "protocol": "sgar-known-canary-failure-regression-v1",
            "valid": False,
            "blocking_failure_codes": [
                str(getattr(exc, "failure_code", type(exc).__name__))
            ],
        }

    conformance_root = output / "conformance"
    conformance_root.mkdir(exist_ok=False)
    probe_nonce = uuid.uuid4().hex
    prepared = prepare_task_invocation(
        query=f"Validate the generic SGAR production chain {probe_nonce}.",
        input_specs=(),
        run_dir=conformance_root,
        request_id=f"pre-real-case-readiness-{probe_nonce}",
    )
    conformance_reports = [
        run_production_conformance(
            prepared_invocation=prepared,
            request_source_sha256=canonical_sha256({"source": "readiness-synthetic-v1"}),
            run_dir=conformance_root,
            config=config,
            network_policy_mode="disabled",
            project_root=root,
            require_docker=require_docker,
            require_source_clean=require_source_clean,
        )
        for _ in range(3)
    ]
    conformance = {
        "valid": all(item.get("valid") is True for item in conformance_reports),
        "repeat_count": len(conformance_reports),
        "stable_report_identity": len({item.get("report_sha256") for item in conformance_reports}) == 1,
        "report_sha256": conformance_reports[-1].get("report_sha256"),
        "network_requests_made": sum(int(item.get("network_requests_made") or 0) for item in conformance_reports),
        "paid_model_calls_made": sum(int(item.get("paid_model_calls_made") or 0) for item in conformance_reports),
        "checks": conformance_reports[-1].get("checks") or {},
        "identities": conformance_reports[-1].get("identities") or {},
        "errors": conformance_reports[-1].get("errors") or [],
    }
    tests = _full_test_evidence(
        root,
        output,
        run_full_tests=run_full_tests,
        test_runner=test_runner,
    )
    formal_type_check = _formal_boundary_type_check(root)
    replay = (
        replay_sealed_plan_artifacts(replay_run_dir).model_dump(mode="json")
        if replay_run_dir is not None
        else {
            "protocol": "sgar-sealed-plan-replay-v1",
            "valid": False,
            "framework_exceptions": ["replay_run_dir_not_provided"],
            "plan_hash_mutations": [],
        }
    )
    supervisor = _batch_supervisor_probe(output)
    portability = audit_run_portability(
        output,
        host_roots=(output, root),
    )
    batch_policy = {
        "valid": True,
        "protocol": "sgar-real-case-batch-v2",
        "live_total_cost_limit_required": True,
        "bounded_supervisor_logs": True,
    }
    gates = {
        "commit_identity": commit["valid"],
        "framework_source_clean": source["framework_source_clean"] if require_source_clean else True,
        "frozen_assets": frozen["valid"],
        "secret_policy": secret.valid,
        "production_conformance": bool(
            conformance["valid"]
            and conformance["repeat_count"] == 3
            and conformance["stable_report_identity"]
            and conformance["network_requests_made"] == 0
            and conformance["paid_model_calls_made"] == 0
        ),
        "full_test_suite_twice": tests["valid"],
        "formal_boundary_type_check": formal_type_check["valid"],
        "offline_sealed_plan_replay": replay.get("valid") is True,
        "batch_supervisor": supervisor["valid"],
        "batch_budget_policy": batch_policy["valid"],
        "portability": portability["valid"],
        "known_canary_failure_regression": known_failures.get("valid") is True,
    }
    projection = {
        "protocol": PRE_REAL_CASE_READINESS_PROTOCOL,
        "valid": all(gates.values()),
        "gates": gates,
        "commit_identity": commit,
        "framework_source": source,
        "frozen_assets": frozen,
        "secret_policy": secret.as_dict(),
        "production_conformance": conformance,
        "full_test_evidence": tests,
        "formal_boundary_type_check": formal_type_check,
        "offline_sealed_plan_replay": replay,
        "batch_supervisor_probe": supervisor,
        "batch_policy": batch_policy,
        "portability_audit": portability,
        "known_canary_failure_regression": known_failures,
        "user_owned_workspace_changes_excluded": [".gitignore", "tmp/"],
        "benchmark_cases_accessed": 0,
        "paid_model_calls_made": conformance["paid_model_calls_made"],
        "network_requests_made": conformance["network_requests_made"],
    }
    report = {**projection, "report_sha256": canonical_sha256(projection)}
    _atomic_json(report_path, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "sgar_mvp" / "config.json"))
    parser.add_argument("--replay-run-dir")
    parser.add_argument("--skip-full-tests", action="store_true")
    parser.add_argument("--skip-docker-probe", action="store_true")
    parser.add_argument("--allow-dirty-source", action="store_true")
    parser.add_argument("--known-failure-regression", required=True)
    args = parser.parse_args(argv)
    report = run_pre_real_case_readiness(
        output_dir=args.output_dir,
        config_path=args.config,
        replay_run_dir=args.replay_run_dir,
        run_full_tests=not args.skip_full_tests,
        require_docker=not args.skip_docker_probe,
        require_source_clean=not args.allow_dirty_source,
        known_failure_regression_path=args.known_failure_regression,
    )
    _write_utf8_json_stdout(report)
    return 0 if report["valid"] else 2


def _write_utf8_json_stdout(payload: Mapping[str, Any]) -> None:
    """Write CLI JSON as UTF-8 bytes independently of the host console codec."""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    output = getattr(sys.stdout, "buffer", None)
    if output is None:
        sys.stdout.write(serialized)
        sys.stdout.flush()
        return
    output.write(serialized.encode("utf-8"))
    output.flush()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRE_REAL_CASE_READINESS_PROTOCOL",
    "PreRealCaseReadinessError",
    "run_pre_real_case_readiness",
]
