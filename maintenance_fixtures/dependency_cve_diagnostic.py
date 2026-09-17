"""Bounded runtime diagnosis for the dependency CVE checker Tool."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess


def _command(command: list[str], timeout: int = 10) -> dict[str, object]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "status": "passed" if result.returncode == 0 else "failed",
            "return_code": result.returncode,
            "stdout": result.stdout[:1000],
            "stderr": result.stderr[:1000],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timed_out", "timeout_seconds": timeout}


def _endpoint(host: str) -> dict[str, object]:
    resolved = _command(["getent", "ahosts", host], timeout=5)
    report: dict[str, object] = {"dns": resolved, "tcp": None, "tls_ca": None}
    if resolved["status"] != "passed":
        return report
    try:
        with socket.create_connection((host, 443), timeout=10) as connection:
            report["tcp"] = {"status": "passed", "peer": connection.getpeername()[0]}
            with ssl.create_default_context().wrap_socket(
                connection,
                server_hostname=host,
            ) as tls_connection:
                report["tls_ca"] = {
                    "status": "passed",
                    "protocol": tls_connection.version(),
                }
    except Exception as exc:
        report["tcp_or_tls_error"] = type(exc).__name__ + ":" + str(exc)
    return report


def main() -> int:
    cache_roots = [Path("/root/.cache/pip-audit"), Path("/root/.cache/pip")]
    report = {
        "process_started": True,
        "pip_audit_path": shutil.which("pip-audit"),
        "pip_audit_version": _command(["pip-audit", "--version"]),
        "cache": {
            str(path): {
                "exists": path.exists(),
                "file_count": sum(1 for item in path.rglob("*") if item.is_file())
                if path.exists()
                else 0,
            }
            for path in cache_roots
        },
        "endpoints": {
            "pypi.org": _endpoint("pypi.org"),
            "api.osv.dev": _endpoint("api.osv.dev"),
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
