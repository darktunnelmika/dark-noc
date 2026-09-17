from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from fastapi import HTTPException

SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}
_BROWSER_HTTP_SCHEMES = {"http", "https"}


def _host_port(host: str, scheme: str) -> tuple[str, int] | None:
    host = str(host or "").strip()
    if not host or any(ord(char) < 33 for char in host):
        return None
    try:
        parsed = urlsplit(f"{scheme}://{host}")
        if parsed.username is not None or parsed.password is not None or not parsed.hostname:
            return None
        return parsed.hostname.rstrip(".").casefold(), parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None


def _origin_tuple(origin: str) -> tuple[str, str, int] | None:
    origin = str(origin or "").strip()
    if not origin or origin.casefold() == "null" or any(ord(char) < 33 for char in origin):
        return None
    try:
        parsed = urlsplit(origin)
        if (
            parsed.scheme.casefold() not in _BROWSER_HTTP_SCHEMES
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        scheme = parsed.scheme.casefold()
        return scheme, parsed.hostname.rstrip(".").casefold(), parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None


def _configured_origin() -> tuple[str, str, int] | None:
    host = os.getenv("DARK_NOC_PUBLIC_HOST", "").strip()
    if not host:
        return None
    port_raw = os.getenv("DARK_NOC_PUBLIC_PORT", "").strip() or "443"
    try:
        port = int(port_raw)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    normalized = _host_port(host, "https")
    if not normalized:
        return None
    return "https", normalized[0], port


def expected_browser_origin(headers: Mapping[str, str], request_scheme: str) -> tuple[str, str, int] | None:
    configured = _configured_origin()
    if configured:
        return configured
    scheme = "https" if str(request_scheme).casefold() in {"https", "wss"} else "http"
    host = headers.get("host", "")
    normalized = _host_port(host, scheme)
    return (scheme, *normalized) if normalized else None


def browser_origin_allowed(headers: Mapping[str, str], request_scheme: str) -> bool:
    """Validate browser-origin metadata without blocking non-browser API clients.

    Browsers send Origin on WebSocket handshakes and unsafe cross-origin requests.
    CLI/Agent clients often send neither Origin nor Sec-Fetch-Site, so requests
    without browser metadata remain supported.
    """
    fetch_site = str(headers.get("sec-fetch-site", "")).strip().casefold()
    if fetch_site in {"cross-site", "same-site"}:
        # DARK NOC has no supported cross-subdomain browser control surface.
        return False
    origin = str(headers.get("origin", "")).strip()
    if not origin:
        return fetch_site not in {"cross-site", "same-site"}
    actual = _origin_tuple(origin)
    expected = expected_browser_origin(headers, request_scheme)
    return bool(actual and expected and actual == expected)


def enforce_http_origin(method: str, path: str, headers: Mapping[str, str], request_scheme: str) -> None:
    if str(method).upper() in SAFE_HTTP_METHODS or not str(path).startswith("/api/"):
        return
    if not browser_origin_allowed(headers, request_scheme):
        raise HTTPException(403, "Cross-origin browser request denied")
