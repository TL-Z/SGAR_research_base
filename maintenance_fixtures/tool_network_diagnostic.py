"""One-shot DNS, TCP, TLS/CA, and HTTP diagnostic for a Tool endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import sys
import urllib.parse
import urllib.request


def main() -> int:
    url = sys.argv[1]
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    report = {
        "url": url,
        "host": host,
        "port": port,
        "dns": None,
        "tcp": None,
        "tls_ca": None,
        "http": None,
        "environment": {
            name: bool(os.environ.get(name))
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "SGAR_NETWORK_ALLOWLIST",
            )
        },
    }
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        )
        report["dns"] = {"status": "passed", "addresses": addresses}
    except Exception as exc:
        report["dns"] = {"status": "failed", "error": type(exc).__name__ + ":" + str(exc)}
        print(json.dumps(report, sort_keys=True))
        return 1
    try:
        with socket.create_connection((host, port), timeout=10) as connection:
            report["tcp"] = {"status": "passed", "peer": connection.getpeername()[0]}
            if parsed.scheme == "https":
                with ssl.create_default_context().wrap_socket(
                    connection,
                    server_hostname=host,
                ) as tls_connection:
                    certificate = tls_connection.getpeercert(binary_form=True) or b""
                    report["tls_ca"] = {
                        "status": "passed",
                        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
                    }
    except Exception as exc:
        key = "tls_ca" if parsed.scheme == "https" and report["tcp"] else "tcp"
        report[key] = {"status": "failed", "error": type(exc).__name__ + ":" + str(exc)}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "SGAR-bounded-diagnostic/1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(4096)
            report["http"] = {
                "status": "passed",
                "status_code": response.status,
                "body_prefix_sha256": hashlib.sha256(body).hexdigest(),
                "body_prefix_bytes": len(body),
            }
    except Exception as exc:
        report["http"] = {"status": "failed", "error": type(exc).__name__ + ":" + str(exc)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["http"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
