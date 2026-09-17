"""Application-level direct networking, independent of host proxy settings.

No registry, shell profile, machine environment or original resource is edited.
Explicitly injected HTTP clients remain caller-owned (including offline mocks).
"""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

PROXY_VARIABLES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
)
BYPASS_VARIABLES = ("NO_PROXY", "no_proxy")

TOOL_PROXY_VARIABLES = (
    "SGAR_TOOL_HTTP_PROXY",
    "SGAR_TOOL_HTTPS_PROXY",
    "SGAR_TOOL_NO_PROXY",
)


def load_tool_proxy_config(project_root: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Load optional project-scoped Tool proxy values without mutating the process."""
    values: dict[str, str] = {}
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    env_path = root / ".env"
    if env_path.is_file():
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name in TOOL_PROXY_VARIABLES:
                    values[name] = value.strip().strip('"').strip("'")
        except OSError:
            values = {}
    for name in TOOL_PROXY_VARIABLES:
        if name in os.environ:
            values[name] = os.environ.get(name, "").strip()
    return {name: values.get(name, "") for name in TOOL_PROXY_VARIABLES}


def tool_proxy_audit(config: Mapping[str, str]) -> dict[str, Any]:
    """Return a credential-free proxy status and stable configuration fingerprint."""
    normalized = {name: str(config.get(name, "") or "") for name in TOOL_PROXY_VARIABLES}
    fingerprint = hashlib.sha256(
        "\n".join(f"{name}={normalized[name]}" for name in TOOL_PROXY_VARIABLES).encode("utf-8")
    ).hexdigest()
    return {
        "proxy_enabled": bool(normalized["SGAR_TOOL_HTTP_PROXY"] or normalized["SGAR_TOOL_HTTPS_PROXY"]),
        "proxy_no_proxy_configured": bool(normalized["SGAR_TOOL_NO_PROXY"]),
        "proxy_config_fingerprint": fingerprint,
    }


def direct_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Build a child environment without inherited proxy routes."""
    result = {key: value for key, value in environment.items()
              if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy"}}
    # urllib/requests use these on Linux; the nonempty mapping also prevents
    # urllib from falling back to the Windows system proxy registry.
    result.update({key: "*" for key in BYPASS_VARIABLES})
    return result


def configure_direct_network() -> None:
    """Apply direct routing to this SGAR process and its inherited children."""
    for key in tuple(os.environ):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "no_proxy"}:
            del os.environ[key]
    os.environ.update({key: "*" for key in BYPASS_VARIABLES})


def direct_sync_http_client(**kwargs: Any) -> httpx.Client:
    from openai import DefaultHttpxClient
    return DefaultHttpxClient(**kwargs, trust_env=False)


def direct_async_http_client(**kwargs: Any) -> httpx.AsyncClient:
    from openai import DefaultAsyncHttpxClient
    return DefaultAsyncHttpxClient(**kwargs, trust_env=False)


def direct_container_environment_args(
    *,
    proxy_config: Mapping[str, str] | None = None,
    network_required: bool = False,
    network_policy_mode: str = "disabled",
) -> list[str]:
    """Pass proxy variable names without placing proxy values in Docker argv."""
    result = []
    for name in PROXY_VARIABLES:
        result.extend(("-e", name))
    for name in BYPASS_VARIABLES:
        result.extend(("-e", name))
    # Values are supplied through the subprocess environment, never argv.
    # The argument list contains names only so credentials cannot leak.
    return result


def tool_proxy_environment(
    config: Mapping[str, str] | None = None,
    *,
    network_required: bool = False,
    network_policy_mode: str = "disabled",
) -> dict[str, str]:
    """Return proxy values for a child environment, with no credentials in argv."""
    if not network_required or network_policy_mode != "declared":
        return {}
    values = config or {}
    http = str(values.get("SGAR_TOOL_HTTP_PROXY", "") or "")
    https = str(values.get("SGAR_TOOL_HTTPS_PROXY", "") or "")
    no_proxy = str(values.get("SGAR_TOOL_NO_PROXY", "") or "*")
    return {
        "HTTP_PROXY": http,
        "HTTPS_PROXY": https,
        "NO_PROXY": no_proxy,
        "http_proxy": http,
        "https_proxy": https,
        "no_proxy": no_proxy,
    }
