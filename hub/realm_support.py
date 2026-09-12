from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import time
from typing import Any

from fastapi import HTTPException

DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
MAP_RE = re.compile(r"^([0-9]{1,5})(?:>([0-9]{1,5}))?(?:@([0-9]{1,5}))?$")
WS_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{1,127}$")
ALPN_RE = re.compile(r"^[A-Za-z0-9./_-]{1,32}(?:,[A-Za-z0-9./_-]{1,32})*$")
REALM_TRANSPORTS = {"tcp", "tls", "ws", "wss"}
REALM_PROFILES = {"stable", "balanced", "lowping", "low_ping", "turbo"}
REALM_WS_MASKS = {"skipped", "fixed", "standard"}


def _port(value: Any, label: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"Invalid Realm {label} port") from exc
    if not 1 <= port <= 65535:
        raise HTTPException(422, f"Realm {label} port must be between 1 and 65535")
    return port


def validate_host(value: Any, label: str = "host") -> str:
    text = str(value or "").strip().rstrip(".")
    if not text or len(text) > 253 or any(ord(char) < 33 for char in text):
        raise HTTPException(422, f"Invalid Realm {label}")
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        labels = text.split(".")
        if (
            not HOST_RE.fullmatch(text)
            or any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in labels)
        ):
            raise HTTPException(422, f"Invalid Realm {label}")
        return text.casefold()


def validate_domain(value: Any, label: str = "domain") -> str:
    text = str(value or "").strip().casefold().rstrip(".")
    if not DOMAIN_RE.fullmatch(text):
        raise HTTPException(422, f"Realm {label} must be a valid DNS domain")
    return text


def canonical_realm_mappings(
    user_ports: list[int] | tuple[int, ...],
    tunnel_port: int,
    explicit: list[str] | tuple[str, ...] | None = None,
    *,
    maximum: int = 256,
) -> list[str]:
    base = _port(tunnel_port, "backbone")
    raw_specs = [str(item).strip() for item in (explicit or []) if str(item).strip()]
    if not raw_specs:
        raw_specs = [str(item) for item in user_ports]
    if not raw_specs:
        raise HTTPException(422, "DARK Realm requires at least one port mapping")
    if len(raw_specs) > maximum:
        raise HTTPException(422, f"DARK Realm supports at most {maximum} mappings per logical tunnel")

    mappings: list[str] = []
    public_seen: set[int] = set()
    backbone_seen: set[int] = set()
    for index, raw in enumerate(raw_specs):
        match = MAP_RE.fullmatch(raw)
        if not match:
            raise HTTPException(
                422,
                f"Invalid Realm mapping '{raw}'. Use public, public>target or public>target@backbone",
            )
        public = _port(match.group(1), "public")
        target = _port(match.group(2) or public, "target")
        backbone = _port(match.group(3) or base + index, "backbone")
        if public in public_seen:
            raise HTTPException(422, f"Duplicate Realm public port: {public}")
        if backbone in backbone_seen:
            raise HTTPException(422, f"Duplicate Realm backbone port: {backbone}")
        public_seen.add(public)
        backbone_seen.add(backbone)
        mappings.append(f"{public}>{target}@{backbone}")
    return mappings


def prepare_realm_settings(
    values: dict[str, Any],
    *,
    gateway_host: str,
    certificate: dict[str, Any] | None = None,
    pair_mode: bool = False,
    now: int | None = None,
) -> dict[str, Any]:
    transport = str(values.get("transport") or "tcp").casefold()
    profile = str(values.get("profile") or "balanced").casefold()
    if transport not in REALM_TRANSPORTS:
        raise HTTPException(422, "DARK Realm supports TCP, TLS, WS and WSS")
    if profile not in REALM_PROFILES:
        raise HTTPException(422, "Unsupported DARK Realm performance profile")
    profile = "lowping" if profile == "low_ping" else profile

    user_ports = [int(item) for item in values.get("user_ports") or []]
    mappings = canonical_realm_mappings(
        user_ports,
        int(values.get("tunnel_port") or 3080),
        values.get("port_mappings") or None,
    )
    gateway = validate_host(gateway_host, "Gateway host")
    target_host = validate_host(values.get("target_host") or "127.0.0.1", "target host")

    tls_domain = ""
    sni = ""
    alpn = str(values.get("alpn") or "").strip()
    tls_insecure = bool(values.get("tls_insecure", False))
    if alpn and not ALPN_RE.fullmatch(alpn):
        raise HTTPException(422, "Invalid Realm ALPN list")

    if transport in {"tls", "wss"}:
        if certificate:
            tls_domain = validate_domain(certificate.get("certificate_domain"), "certificate domain")
            if gateway.casefold() != tls_domain:
                gateway = tls_domain
            tls_insecure = False
        else:
            tls_domain = validate_domain(values.get("tls_domain") or gateway, "TLS domain")
        sni = validate_domain(values.get("sni") or tls_domain, "SNI")
        if not pair_mode and not certificate:
            raise HTTPException(422, "Managed Realm TLS/WSS requires a valid Gateway certificate from TLS Vault")
    else:
        tls_insecure = False

    ws_host = ""
    ws_path = ""
    ws_mask = "skipped"
    if transport in {"ws", "wss"}:
        ws_host = validate_domain(values.get("ws_host") or tls_domain or gateway, "WebSocket Host")
        ws_path = str(values.get("ws_path") or "/dark-realm").strip()
        if not WS_PATH_RE.fullmatch(ws_path) or ".." in ws_path:
            raise HTTPException(422, "Realm WebSocket Path must start with / and contain only safe URL characters")
        ws_mask = str(values.get("ws_mask") or "skipped").casefold()
        if ws_mask not in REALM_WS_MASKS:
            raise HTTPException(422, "Realm WebSocket mask must be skipped, fixed or standard")

    result = {
        "plugin_id": "dark-realm",
        "name": str(values.get("name") or ""),
        "endpoint": gateway,
        "gateway_host": gateway,
        "target_host": target_host,
        "tunnel_port": int(values.get("tunnel_port") or 3080),
        "user_ports": [int(item.split(">", 1)[0]) for item in mappings],
        "port_mappings": mappings,
        "maps": ",".join(mappings),
        "transport": transport,
        "profile": profile,
        "restart_every": str(values.get("restart_every") or "off"),
        "tls_domain": tls_domain,
        "tls_insecure": tls_insecure,
        "sni": sni,
        "alpn": alpn,
        "ws_host": ws_host,
        "ws_path": ws_path,
        "ws_mask": ws_mask,
        "created_at": int(values.get("created_at") or now or time.time()),
    }
    if certificate:
        result.update(certificate)
    return result


def realm_pair_code(settings: dict[str, Any]) -> str:
    profile = "low_ping" if settings.get("profile") == "lowping" else str(settings.get("profile") or "balanced")
    payload = "\n".join(
        (
            "schema=1",
            f"name={settings['name']}",
            f"gateway={settings['gateway_host']}",
            f"target={settings.get('target_host') or '127.0.0.1'}",
            f"transport={settings['transport']}",
            f"profile={profile}",
            f"maps={settings['maps']}",
            f"tls_domain={settings.get('tls_domain') or ''}",
            f"tls_insecure={1 if settings.get('tls_insecure') else 0}",
            f"sni={settings.get('sni') or ''}",
            f"alpn={settings.get('alpn') or ''}",
            f"ws_host={settings.get('ws_host') or ''}",
            f"ws_path={settings.get('ws_path') or ''}",
            f"ws_mask={settings.get('ws_mask') or 'skipped'}",
            f"created={int(settings.get('created_at') or time.time())}",
        )
    )
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    checksum = hashlib.sha256(payload.encode()).hexdigest()
    return f"DR1.{encoded}.{checksum}"
