from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

REALM_CORE_TAG = "v2.9.6"
REALM_RELEASE_API = f"https://api.github.com/repos/zhboner/realm/releases/tags/{REALM_CORE_TAG}"
DARK_REALM_MANAGER_URL = (
    "https://raw.githubusercontent.com/darktunnelmika/dark-realm/"
    "40bf72da188ee7c795c090c1a62122ca4ea3b99e/dark-realm.sh"
)
CORE_PATH = Path("/usr/local/bin/realm")
MANAGER_PATH = Path("/usr/local/bin/darkrealm")
BASE_DIR = Path("/etc/dark-realm")
TUNNEL_DIR = BASE_DIR / "tunnels"
CORE_VERSION_PATH = BASE_DIR / "core.version"
UNIT_PATH = Path("/etc/systemd/system/dark-realm@.service")
RESTART_UNIT_PATH = Path("/etc/systemd/system/dark-realm-restart@.service")
RESTART_TIMER_PATH = Path("/etc/systemd/system/dark-realm-restart@.timer")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
WS_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{1,127}$")
MAP_RE = re.compile(r"^([0-9]{1,5})>([0-9]{1,5})@([0-9]{1,5})$")
PROFILE_VALUES = {
    "stable": {"nofile": 262144, "timeout": 10, "keepalive": 20, "probes": 5, "log": "warn", "restart": 3},
    "balanced": {"nofile": 524288, "timeout": 5, "keepalive": 15, "probes": 3, "log": "warn", "restart": 2},
    "lowping": {"nofile": 524288, "timeout": 3, "keepalive": 5, "probes": 3, "log": "warn", "restart": 1},
    "low_ping": {"nofile": 524288, "timeout": 3, "keepalive": 5, "probes": 3, "log": "warn", "restart": 1},
    "turbo": {"nofile": 1048576, "timeout": 4, "keepalive": 10, "probes": 3, "log": "error", "restart": 1},
}


def _run(command: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return result.returncode, (result.stdout + result.stderr)[-200_000:]
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
    except Exception as exc:
        return 1, str(exc)


def _atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    temporary.replace(path)


def _download(url: str, destination: Path, max_bytes: int = 128 * 1024 * 1024) -> str:
    if not url.startswith("https://"):
        raise RuntimeError("DARK Realm downloads require HTTPS")
    digest = hashlib.sha256()
    received = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=240) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    raise RuntimeError("DARK Realm download exceeded the safety limit")
                digest.update(chunk)
                handle.write(chunk)
    return digest.hexdigest()


def _host(value: Any, label: str) -> str:
    text = str(value or "").strip().rstrip(".")
    if not text or len(text) > 253 or any(ord(char) < 33 for char in text):
        raise ValueError(f"Invalid Realm {label}")
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        labels = text.split(".")
        if any(
            not part
            or len(part) > 63
            or part.startswith("-")
            or part.endswith("-")
            or not part.replace("-", "a").isalnum()
            for part in labels
        ):
            raise ValueError(f"Invalid Realm {label}")
        return text.casefold()


def _domain(value: Any, label: str) -> str:
    text = str(value or "").strip().casefold().rstrip(".")
    if not DOMAIN_RE.fullmatch(text):
        raise ValueError(f"Invalid Realm {label}")
    return text


def _port(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Realm {label} port") from exc
    if not 1 <= number <= 65535:
        raise ValueError(f"Realm {label} port is outside 1-65535")
    return number


def parse_mappings(payload: dict[str, Any]) -> list[tuple[int, int, int]]:
    raw = payload.get("port_mappings") or str(payload.get("maps") or "").split(",")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Realm mappings must be a list")
    result: list[tuple[int, int, int]] = []
    public_seen: set[int] = set()
    backbone_seen: set[int] = set()
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        match = MAP_RE.fullmatch(text)
        if not match:
            raise ValueError(f"Invalid Realm mapping: {text}")
        public, target, backbone = (_port(match.group(i), "mapping") for i in range(1, 4))
        if public in public_seen or backbone in backbone_seen:
            raise ValueError("Realm public and backbone ports must be unique")
        public_seen.add(public)
        backbone_seen.add(backbone)
        result.append((public, target, backbone))
    if not result or len(result) > 256:
        raise ValueError("Realm requires 1-256 mappings")
    return result


def _toml(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _address(host: str, port: int) -> str:
    try:
        parsed = ipaddress.ip_address(host)
        return f"[{parsed.compressed}]:{port}" if parsed.version == 6 else f"{parsed.compressed}:{port}"
    except ValueError:
        return f"{host}:{port}"


def _profile(value: Any) -> dict[str, Any]:
    key = str(value or "balanced").casefold()
    if key not in PROFILE_VALUES:
        raise ValueError("Unsupported Realm performance profile")
    return PROFILE_VALUES[key]


def _edge_transport(payload: dict[str, Any]) -> str:
    transport = str(payload.get("transport") or "tcp")
    if transport == "tcp":
        return ""
    if transport == "tls":
        result = f"tls;sni={_domain(payload.get('sni') or payload.get('tls_domain'), 'SNI')}"
    elif transport == "ws":
        result = (
            f"ws;host={_domain(payload.get('ws_host'), 'WebSocket Host')};"
            f"path={_ws_path(payload.get('ws_path'))};mask={_ws_mask(payload.get('ws_mask'))}"
        )
    elif transport == "wss":
        result = (
            f"ws;host={_domain(payload.get('ws_host'), 'WebSocket Host')};"
            f"path={_ws_path(payload.get('ws_path'))};mask={_ws_mask(payload.get('ws_mask'))};"
            f"tls;sni={_domain(payload.get('sni') or payload.get('tls_domain'), 'SNI')}"
        )
    else:
        raise ValueError("DARK Realm supports TCP, TLS, WS and WSS")
    alpn = str(payload.get("alpn") or "").strip()
    if alpn:
        if not re.fullmatch(r"[A-Za-z0-9./_-]{1,32}(?:,[A-Za-z0-9./_-]{1,32})*", alpn):
            raise ValueError("Invalid Realm ALPN list")
        result += f";alpn={alpn}"
    if transport in {"tls", "wss"} and bool(payload.get("tls_insecure", False)):
        result += ";insecure"
    return result


def _gateway_transport(payload: dict[str, Any]) -> str:
    transport = str(payload.get("transport") or "tcp")
    if transport == "tcp":
        return ""
    if transport == "ws":
        return f"ws;host={_domain(payload.get('ws_host'), 'WebSocket Host')};path={_ws_path(payload.get('ws_path'))}"
    if transport not in {"tls", "wss"}:
        raise ValueError("DARK Realm supports TCP, TLS, WS and WSS")
    cert = Path(str(payload.get("certificate_path") or ""))
    key = Path(str(payload.get("certificate_key_path") or ""))
    if not cert.is_file() or not key.is_file():
        raise RuntimeError("The selected Gateway TLS certificate files are missing")
    if transport == "tls":
        return f"tls;cert={cert};key={key}"
    return (
        f"ws;host={_domain(payload.get('ws_host'), 'WebSocket Host')};"
        f"path={_ws_path(payload.get('ws_path'))};tls;cert={cert};key={key}"
    )


def _ws_path(value: Any) -> str:
    text = str(value or "").strip()
    if not WS_PATH_RE.fullmatch(text) or ".." in text:
        raise ValueError("Invalid Realm WebSocket Path")
    return text


def _ws_mask(value: Any) -> str:
    text = str(value or "skipped").casefold()
    if text not in {"skipped", "fixed", "standard"}:
        raise ValueError("Invalid Realm WebSocket mask")
    return text


def render_config(payload: dict[str, Any], role: str) -> str:
    if role not in {"edge", "gateway"}:
        raise ValueError("Realm role must be edge or gateway")
    gateway = _host(payload.get("gateway_host") or payload.get("endpoint"), "Gateway host")
    target_host = _host(payload.get("target_host") or "127.0.0.1", "target host")
    mappings = parse_mappings(payload)
    profile = _profile(payload.get("profile"))
    transport = _edge_transport(payload) if role == "edge" else _gateway_transport(payload)
    lines = [
        "[log]",
        f"level = {_toml(profile['log'])}",
        'output = "stdout"',
        "",
        "[network]",
        "no_tcp = false",
        "use_udp = false",
        f"tcp_timeout = {profile['timeout']}",
        f"tcp_keepalive = {profile['keepalive']}",
        f"tcp_keepalive_probe = {profile['probes']}",
        "",
    ]
    for public, target, backbone in mappings:
        lines.append("[[endpoints]]")
        if role == "edge":
            lines.append(f'listen = "0.0.0.0:{public}"')
            lines.append(f"remote = {_toml(_address(gateway, backbone))}")
            if transport:
                lines.append(f"remote_transport = {_toml(transport)}")
        else:
            lines.append(f'listen = "0.0.0.0:{backbone}"')
            lines.append(f"remote = {_toml(_address(target_host, target))}")
            if transport:
                lines.append(f"listen_transport = {_toml(transport)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _meta_text(payload: dict[str, Any], role: str) -> str:
    mappings = parse_mappings(payload)
    profile_name = str(payload.get("profile") or "balanced")
    values = {
        "SCHEMA": "1",
        "NAME": str(payload["name"]),
        "ROLE": role,
        "GATEWAY_HOST": _host(payload.get("gateway_host") or payload.get("endpoint"), "Gateway host"),
        "TARGET_HOST": _host(payload.get("target_host") or "127.0.0.1", "target host"),
        "BIND_ADDR": "0.0.0.0",
        "TRANSPORT": str(payload.get("transport") or "tcp"),
        "PROFILE": "low_ping" if profile_name == "lowping" else profile_name,
        "MAPS": ",".join(f"{a}>{b}@{c}" for a, b, c in mappings),
        "TLS_DOMAIN": str(payload.get("tls_domain") or ""),
        "TLS_CERT": str(payload.get("certificate_path") or "") if role == "gateway" else "",
        "TLS_KEY": str(payload.get("certificate_key_path") or "") if role == "gateway" else "",
        "TLS_INSECURE": "1" if payload.get("tls_insecure") else "0",
        "SNI": str(payload.get("sni") or ""),
        "ALPN": str(payload.get("alpn") or ""),
        "WS_HOST": str(payload.get("ws_host") or ""),
        "WS_PATH": str(payload.get("ws_path") or ""),
        "WS_MASK": str(payload.get("ws_mask") or "skipped"),
        "SCHEDULE": str(payload.get("restart_every") or "off"),
        "CREATED_AT": str(int(payload.get("created_at") or time.time())),
    }
    for value in values.values():
        if any(ord(char) < 32 for char in value):
            raise ValueError("Realm metadata contains control characters")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _validate_toml(path: Path, expected: int) -> None:
    import tomllib

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) != expected:
        raise RuntimeError("Realm TOML endpoint count mismatch")
    for endpoint in endpoints:
        if not endpoint.get("listen") or not endpoint.get("remote"):
            raise RuntimeError("Realm TOML endpoint is incomplete")


def _safe_extract(package: Path, destination: Path) -> Path:
    with tarfile.open(package, "r:gz") as archive:
        members = archive.getmembers()
        if any(
            (not member.isfile() and not member.isdir())
            or member.name.startswith("/")
            or ".." in Path(member.name).parts
            for member in members
        ):
            raise RuntimeError("Unsafe path, link or special file in Realm archive")
        archive.extractall(destination, members=members)
    candidates = [item for item in destination.rglob("realm") if item.is_file()]
    if not candidates:
        raise RuntimeError("Realm binary was not found in the official archive")
    return candidates[0]


def install_manager() -> str:
    with tempfile.TemporaryDirectory(prefix="darkrealm-manager-") as tmp_name:
        source = Path(tmp_name) / "dark-realm.sh"
        _download(DARK_REALM_MANAGER_URL, source, max_bytes=4 * 1024 * 1024)
        text = source.read_text(encoding="utf-8")
        if "DARKVPN-REALM-SCRIPT" not in text or 'SCRIPT_VER="1.0.0"' not in text:
            raise RuntimeError("Downloaded DARK Realm manager failed identity/version verification")
        code, output = _run(["bash", "-n", str(source)], timeout=15)
        if code != 0:
            raise RuntimeError(f"DARK Realm manager syntax verification failed: {output[-500:]}")
        os.chmod(source, 0o755)
        temporary = Path("/usr/local/bin/.darkrealm.new")
        shutil.copy2(source, temporary)
        os.chmod(temporary, 0o755)
        temporary.replace(MANAGER_PATH)
    return "1.0.0"


def install_core() -> str:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    architecture = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }.get(os.uname().machine)
    if not architecture:
        raise RuntimeError(f"Unsupported Realm architecture: {os.uname().machine}")
    if CORE_PATH.is_file() and os.access(CORE_PATH, os.X_OK):
        code, output = _run([str(CORE_PATH), "--version"], timeout=10)
        if code == 0 and REALM_CORE_TAG.lstrip("v") in output:
            install_manager()
            return output.strip() or REALM_CORE_TAG

    response = httpx.get(REALM_RELEASE_API, follow_redirects=True, timeout=40)
    response.raise_for_status()
    release = response.json()
    asset_name = f"realm-{architecture}-unknown-linux-musl.tar.gz"
    asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
    if not asset:
        raise RuntimeError(f"Official Realm asset is missing: {asset_name}")
    expected = str(asset.get("digest") or "")
    if not expected.startswith("sha256:"):
        raise RuntimeError("Official Realm release asset has no SHA-256 digest")

    with tempfile.TemporaryDirectory(prefix="darkrealm-core-") as tmp_name:
        temporary_dir = Path(tmp_name)
        package = temporary_dir / asset_name
        actual = _download(str(asset["browser_download_url"]), package)
        if not hmac.compare_digest(expected.removeprefix("sha256:"), actual):
            raise RuntimeError("Realm release SHA-256 mismatch")
        unpacked = temporary_dir / "unpacked"
        unpacked.mkdir()
        candidate = _safe_extract(package, unpacked)
        os.chmod(candidate, 0o755)
        code, output = _run([str(candidate), "--version"], timeout=10)
        if code != 0 or REALM_CORE_TAG.lstrip("v") not in output:
            raise RuntimeError(f"Realm binary version verification failed: {output[-500:]}")
        replacement = Path("/usr/local/bin/.realm.new")
        shutil.copy2(candidate, replacement)
        os.chmod(replacement, 0o755)
        if CORE_PATH.exists():
            shutil.copy2(CORE_PATH, Path("/usr/local/bin/realm.bak"))
        replacement.replace(CORE_PATH)
        _atomic_text(CORE_VERSION_PATH, REALM_CORE_TAG + "\n", 0o600)
    install_manager()
    return output.strip() or REALM_CORE_TAG


def _ensure_units() -> None:
    unit = """[Unit]
Description=DARK Realm Tunnel (%i)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
ExecStart=/usr/local/bin/realm -c /etc/dark-realm/tunnels/%i/config.toml
Restart=always
RestartSec=2
TimeoutStopSec=15
KillSignal=SIGTERM
UMask=0077
LimitNOFILE=524288
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true

[Install]
WantedBy=multi-user.target
"""
    restart_unit = """[Unit]
Description=DARK Realm scheduled restart (%i)

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart dark-realm@%i.service
"""
    restart_timer = """[Unit]
Description=DARK Realm scheduled restart timer (%i)

[Timer]
OnBootSec=15min
OnUnitActiveSec=24h
Persistent=true
Unit=dark-realm-restart@%i.service

[Install]
WantedBy=timers.target
"""
    _atomic_text(UNIT_PATH, unit, 0o644)
    _atomic_text(RESTART_UNIT_PATH, restart_unit, 0o644)
    _atomic_text(RESTART_TIMER_PATH, restart_timer, 0o644)
    _run(["systemctl", "daemon-reload"], timeout=20)


def _configure_timer(name: str, schedule: str) -> None:
    if schedule not in {"off", "1h", "6h", "12h", "24h"}:
        raise ValueError("Invalid Realm restart schedule")
    timer = f"dark-realm-restart@{name}.timer"
    _run(["systemctl", "disable", "--now", timer], timeout=20)
    dropin = Path("/etc/systemd/system") / f"{timer}.d"
    shutil.rmtree(dropin, ignore_errors=True)
    if schedule == "off":
        _run(["systemctl", "daemon-reload"], timeout=20)
        return
    dropin.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        dropin / "schedule.conf",
        f"[Timer]\nOnUnitActiveSec=\nOnBootSec=\nOnUnitActiveSec={schedule}\nOnBootSec={schedule}\n",
        0o644,
    )
    _run(["systemctl", "daemon-reload"], timeout=20)
    code, output = _run(["systemctl", "enable", "--now", timer], timeout=20)
    if code != 0:
        raise RuntimeError(f"Could not enable Realm restart timer: {output[-500:]}")


def _port_listening(port: int) -> bool:
    code, output = _run(["ss", "-H", "-lnt"], timeout=10)
    return code == 0 and re.search(fr":{port}\b", output) is not None


def _configure_ufw(directory: Path, role: str, mappings: list[tuple[int, int, int]]) -> list[str]:
    if shutil.which("ufw") is None:
        return []
    code, status = _run(["ufw", "status"], timeout=15)
    if code != 0 or "Status: active" not in status:
        return []
    ports = [public for public, _, _ in mappings] if role == "edge" else [backbone for _, _, backbone in mappings]
    created: list[str] = []
    for port in ports:
        rule = f"{port}/tcp"
        code, output = _run(["ufw", "allow", rule, "comment", "DARK-NOC-REALM"], timeout=20)
        if code != 0:
            _remove_ufw_rules(created)
            raise RuntimeError(f"Could not open Realm UFW rule {rule}: {output[-500:]}")
        if "Rule added" in output:
            created.append(rule)
    _atomic_text(directory / "ufw-created.json", json.dumps(created), 0o600)
    return created


def _remove_ufw_rules(rules: list[str]) -> None:
    if not rules or shutil.which("ufw") is None:
        return
    for rule in rules:
        if re.fullmatch(r"[0-9]{1,5}/tcp", str(rule)):
            _run(["ufw", "--force", "delete", "allow", str(rule)], timeout=20)


def _save_agent_config(config: dict[str, Any], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(config_path)


def deploy(payload: dict[str, Any], config: dict[str, Any], config_path: Path) -> str:
    if payload.get("plugin_id") != "dark-realm":
        raise ValueError("Plugin is not allowlisted")
    name = str(payload.get("name") or "")
    role = str(payload.get("role") or "")
    transport = str(payload.get("transport") or "tcp")
    if not NAME_RE.fullmatch(name) or role not in {"edge", "gateway"} or transport not in {"tcp", "tls", "ws", "wss"}:
        raise ValueError("Invalid DARK Realm name, role or transport")
    mappings = parse_mappings(payload)
    profile = _profile(payload.get("profile"))
    gateway = _host(payload.get("gateway_host") or payload.get("endpoint"), "Gateway host")
    target_host = _host(payload.get("target_host") or "127.0.0.1", "target host")
    if transport in {"tls", "wss"}:
        _domain(payload.get("tls_domain"), "TLS domain")
        _domain(payload.get("sni") or payload.get("tls_domain"), "SNI")
    if transport in {"ws", "wss"}:
        _domain(payload.get("ws_host"), "WebSocket Host")
        _ws_path(payload.get("ws_path"))
        _ws_mask(payload.get("ws_mask"))

    version = install_core()
    _ensure_units()
    directory = TUNNEL_DIR / name
    directory.parent.mkdir(parents=True, exist_ok=True)
    existed = directory.exists()
    backup_root = Path(tempfile.mkdtemp(prefix=f"darkrealm-{name}-"))
    service = f"dark-realm@{name}.service"
    if existed:
        shutil.copytree(directory, backup_root / "tunnel", dirs_exist_ok=True)
        _run(["systemctl", "stop", service], timeout=30)
    directory.mkdir(parents=True, exist_ok=True)

    listeners = [public for public, _, _ in mappings] if role == "edge" else [backbone for _, _, backbone in mappings]
    conflicts = [port for port in listeners if _port_listening(port)]
    if conflicts:
        if existed:
            _run(["systemctl", "start", service], timeout=30)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise RuntimeError(f"Realm port conflict detected: {', '.join(map(str, conflicts))}")

    firewall_rules: list[str] = []
    try:
        config_text = render_config(payload, role)
        _atomic_text(directory / "config.toml", config_text, 0o600)
        _atomic_text(directory / "meta.conf", _meta_text(payload, role), 0o600)
        _atomic_text(
            directory / "ports.list",
            "".join(f"{public}\t{target}\t{backbone}\n" for public, target, backbone in mappings),
            0o600,
        )
        _validate_toml(directory / "config.toml", len(mappings))
        dropin = Path("/etc/systemd/system") / f"{service}.d"
        dropin.mkdir(parents=True, exist_ok=True)
        _atomic_text(
            dropin / "override.conf",
            f"[Service]\nLimitNOFILE={profile['nofile']}\nRestartSec={profile['restart']}\n",
            0o644,
        )
        _run(["systemctl", "daemon-reload"], timeout=20)
        _configure_timer(name, str(payload.get("restart_every") or "off"))
        firewall_rules = _configure_ufw(directory, role, mappings)
        code, output = _run(["systemctl", "enable", "--now", service], timeout=40)
        if code != 0:
            raise RuntimeError(f"Realm service failed to start: {output[-1000:]}")
        time.sleep(1)
        if _run(["systemctl", "is-active", "--quiet", service], timeout=10)[0] != 0:
            raise RuntimeError("Realm service is not active after deployment")
        missing = [port for port in listeners if not _port_listening(port)]
        if missing:
            raise RuntimeError(f"Realm expected listener(s) missing: {', '.join(map(str, missing))}")
    except Exception:
        _run(["systemctl", "disable", "--now", service], timeout=30)
        _remove_ufw_rules(firewall_rules)
        if existed and (backup_root / "tunnel").exists():
            shutil.rmtree(directory, ignore_errors=True)
            shutil.copytree(backup_root / "tunnel", directory)
            _run(["systemctl", "daemon-reload"], timeout=20)
            _run(["systemctl", "enable", "--now", service], timeout=30)
        else:
            shutil.rmtree(directory, ignore_errors=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    shutil.rmtree(backup_root, ignore_errors=True)

    public_ports = [public for public, _, _ in mappings]
    target_ports = [target for _, target, _ in mappings]
    backbone_ports = [backbone for _, _, backbone in mappings]
    monitor = {
        "name": name,
        "method": "DARK Realm Pro",
        "role": role,
        "service": service,
        "listen_port": public_ports[0] if role == "edge" else backbone_ports[0],
        "user_ports": public_ports,
        "backbone_ports": backbone_ports,
        "target_ports": target_ports,
        "target_host": gateway if role == "edge" else target_host,
        "target_port": backbone_ports[0] if role == "edge" else target_ports[0],
        "transport": transport,
        "profile": str(payload.get("profile") or "balanced"),
        "restart_every": str(payload.get("restart_every") or "off"),
        "tls_domain": str(payload.get("tls_domain") or ""),
        "port_mappings": [f"{a}>{b}@{c}" for a, b, c in mappings],
    }
    config["tunnels"] = [
        item
        for item in config.setdefault("tunnels", [])
        if not (item.get("name") == name and item.get("method") == "DARK Realm Pro")
    ] + [monitor]
    for key in ("services", "managed_services"):
        if service not in config.setdefault(key, []):
            config[key].append(service)
    _save_agent_config(config, config_path)
    return (
        "DARK Realm Pro deployed\n"
        f"role={role}\n"
        f"tunnel={name}\n"
        f"gateway={gateway}\n"
        f"transport={transport}\n"
        f"mappings={','.join(monitor['port_mappings'])}\n"
        f"core={version}\n"
        f"service={service} active\n"
        f"ufw_rules={','.join(firewall_rules) if firewall_rules else 'not-needed'}"
    )


def remove(payload: dict[str, Any], config: dict[str, Any], config_path: Path) -> str:
    name = str(payload.get("name") or "")
    if not NAME_RE.fullmatch(name):
        raise ValueError("Invalid DARK Realm tunnel name")
    service = f"dark-realm@{name}.service"
    timer = f"dark-realm-restart@{name}.timer"
    _run(["systemctl", "disable", "--now", timer], timeout=20)
    _run(["systemctl", "disable", "--now", service], timeout=30)
    directory = TUNNEL_DIR / name
    firewall_file = directory / "ufw-created.json"
    if firewall_file.is_file():
        try:
            _remove_ufw_rules(json.loads(firewall_file.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            pass
    if directory.parent == TUNNEL_DIR:
        shutil.rmtree(directory, ignore_errors=True)
    shutil.rmtree(Path("/etc/systemd/system") / f"{service}.d", ignore_errors=True)
    shutil.rmtree(Path("/etc/systemd/system") / f"{timer}.d", ignore_errors=True)
    _run(["systemctl", "daemon-reload"], timeout=20)
    config["tunnels"] = [
        item
        for item in config.get("tunnels", [])
        if not (item.get("name") == name and item.get("method") == "DARK Realm Pro")
    ]
    for key in ("services", "managed_services"):
        config[key] = [item for item in config.get(key, []) if item != service]
    _save_agent_config(config, config_path)
    return f"DARK Realm Pro tunnel {name} removed; shared Realm core and manager retained"


def inventory() -> dict[str, Any]:
    installed = CORE_PATH.is_file() and os.access(CORE_PATH, os.X_OK)
    version = None
    if installed:
        code, output = _run([str(CORE_PATH), "--version"], timeout=8)
        version = (output.strip().splitlines() or ["unknown"])[-1][:120] if code == 0 else "unknown"
    manager = None
    if MANAGER_PATH.is_file():
        try:
            match = re.search(r'^SCRIPT_VER="([^"]+)"', MANAGER_PATH.read_text(encoding="utf-8"), re.M)
            manager = match.group(1) if match else "unknown"
        except OSError:
            manager = "unknown"
    return {"installed": installed, "version": version, "manager_version": manager}
