"""Application-level direct networking, independent of host proxy settings.

No registry, shell profile, machine environment or original resource is edited.
Explicitly injected HTTP clients remain caller-owned (including offline mocks).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

PROXY_VARIABLES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
)
BYPASS_VARIABLES = ("NO_PROXY", "no_proxy")


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


def direct_container_environment_args() -> list[str]:
    """Override proxy defaults even when Docker/image configuration supplies them."""
    result = []
    for name in PROXY_VARIABLES:
        result.extend(("-e", name + "="))
    for name in BYPASS_VARIABLES:
        result.extend(("-e", name + "=*"))
    return result
