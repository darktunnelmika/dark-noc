#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import psutil

import importlib.util as _realm_importlib_util

_REALM_ADAPTER_ERROR: str | None = None
try:
    _realm_adapter_path = Path(__file__).resolve().with_name("realm_plugin.py")
    if not _realm_adapter_path.is_file():
        raise FileNotFoundError(f"Realm adapter is missing: {_realm_adapter_path}")
    _realm_adapter_spec = _realm_importlib_util.spec_from_file_location("dark_noc_realm_plugin", _realm_adapter_path)
    if _realm_adapter_spec is None or _realm_adapter_spec.loader is None:
        raise ImportError(f"Could not load Realm adapter spec: {_realm_adapter_path}")
    _realm_adapter_module = _realm_importlib_util.module_from_spec(_realm_adapter_spec)
    _realm_adapter_spec.loader.exec_module(_realm_adapter_module)
    deploy_dark_realm = _realm_adapter_module.deploy
    install_dark_realm = _realm_adapter_module.install_core
    realm_inventory = _realm_adapter_module.inventory
    remove_dark_realm = _realm_adapter_module.remove
except Exception as exc:
    _REALM_ADAPTER_ERROR = f"{type(exc).__name__}: {exc}"[:500]

    def _realm_adapter_unavailable(*_: Any, **__: Any) -> str:
        raise RuntimeError(f"DARK Realm adapter is unavailable: {_REALM_ADAPTER_ERROR}")

    def _realm_inventory_unavailable() -> dict[str, Any]:
        return {
            "installed": False,
            "version": None,
            "manager_version": None,
            "adapter_ready": False,
            "adapter_error": _REALM_ADAPTER_ERROR,
        }

    deploy_dark_realm = _realm_adapter_unavailable
    install_dark_realm = _realm_adapter_unavailable
    remove_dark_realm = _realm_adapter_unavailable
    realm_inventory = _realm_inventory_unavailable

VERSION = "2.9.28"
CONFIG_PATH = Path(os.getenv("DARK_NOC_AGENT_CONFIG", "/etc/dark-noc-agent/config.json"))
STATE_PATH = Path(os.getenv("DARK_NOC_AGENT_STATE", "/var/lib/dark-noc-agent/state.json"))
LOCAL_HUB_ENV_PATH = Path(os.getenv("DARK_NOC_LOCAL_HUB_ENV", "/etc/dark-noc/hub.env"))
LOGGER = logging.getLogger("dark-noc-agent")
ALLOWED_JOB_KINDS = {"diagnostics", "tunnel_test", "restart_service", "service_status", "speed_test", "logs", "plugin_deploy", "plugin_remove", "plugin_install", "tunnel_control", "configure_autoheal", "certificate_issue", "monitor_run"}

DARKBH_RELEASE_API = "https://api.github.com/repos/Musixal/Backhaul/releases/latest"
GOST_RELEASE_API = "https://api.github.com/repos/go-gost/gost/releases/latest"
PAQET_REPO = "hanselime/paqet"
PAQET_CORE_TAG = "v1.0.0-alpha.21"
_CONNECTION_SNAPSHOT: list[Any] = []
_TUNNEL_TRAFFIC_CACHE: dict[str, tuple[float, int, int]] = {}


def refresh_connection_snapshot() -> list[Any]:
    global _CONNECTION_SNAPSHOT
    try:
        _CONNECTION_SNAPSHOT = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        _CONNECTION_SNAPSHOT = []
    return _CONNECTION_SNAPSHOT


def connections_snapshot() -> list[Any]:
    return _CONNECTION_SNAPSHOT


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def clone_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return default


def systemd_notify(message: str) -> bool:
    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(message.encode())
        return True
    except OSError:
        return False


def read_root_env_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for line in lines:
        if not line.startswith(prefix):
            continue
        try:
            parts = shlex.split(line[len(prefix):], posix=True)
        except ValueError:
            return ""
        return parts[0] if len(parts) == 1 else ""
    return ""


def minimal_heartbeat_payload(config: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    try:
        disk_percent = psutil.disk_usage("/").percent
    except OSError:
        disk_percent = 0.0
    try:
        load1 = max(0.0, float(os.getloadavg()[0]))
    except (AttributeError, OSError):
        load1 = 0.0
    try:
        uptime = max(0, int(now - psutil.boot_time()))
    except (OSError, ValueError):
        uptime = 0
    metrics = {
        "hostname": socket.gethostname(),
        "cpu": max(0.0, float(psutil.cpu_percent(interval=None))),
        "ram": max(0.0, float(psutil.virtual_memory().percent)),
        "swap": max(0.0, float(psutil.swap_memory().percent)),
        "disk": max(0.0, float(disk_percent)),
        "load1": load1,
        "rx_bps": 0.0,
        "tx_bps": 0.0,
        "uptime": uptime,
        "connections": 0,
        "telemetry_status": "starting",
        "telemetry_age_seconds": 0,
        "inventory_complete": False,
        "inventory_snapshot_at": 0,
        "agent_loop_ts": int(now),
    }
    return {
        "metrics": metrics,
        "services": [],
        "tunnels": [],
        "plugins": {},
        "agent_version": VERSION,
        "autoheal": clone_json(config.get("autoheal", {}), {}),
    }


async def recover_local_enrollment(client: httpx.AsyncClient, hub: str, config: dict[str, Any]) -> bool:
    parsed = urlsplit(hub)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    secret = read_root_env_value(LOCAL_HUB_ENV_PATH, "DARK_NOC_LOCAL_ENROLL_SECRET")
    if not secret:
        return False
    current_token = str(config.get("agent_token") or "")
    headers = {"X-Dark-Noc-Bootstrap": secret}
    if current_token:
        headers["X-Dark-Noc-Existing-Agent"] = current_token
    response = await client.post(f"{hub}/api/agent/local-enroll", headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("agent_token") or "")
    if len(token) < 8 or any(ord(char) < 33 for char in token):
        raise RuntimeError("Local Agent recovery returned an invalid enrollment token")
    config["agent_token"] = token
    save_json(CONFIG_PATH, config)
    client.headers["Authorization"] = f"Bearer {token}"
    LOGGER.warning("Recovered local Hub Agent enrollment (reused=%s)", bool(payload.get("reused")))
    return True


async def collect_reports_for_payload(config: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    reports = await _collect_tunnel_reports(monitored_tunnels(config))
    await maybe_autoheal(config, reports, state)
    return reports


def collect_payload_blocking(config: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    refresh_connection_snapshot()
    metrics = base_metrics(state.get("net", {}))
    state["net"] = {
        key: metrics[key]
        for key in (
            "bytes_recv", "bytes_sent", "disk_read_bytes", "disk_write_bytes",
            "network_errors", "network_drops", "ts",
        )
    }
    metrics["inventory"] = system_inventory(state)
    reports = asyncio.run(collect_reports_for_payload(config, state))
    inventory_complete = True if not config.get("auto_discovery", True) else discovery_inventory_complete()
    metrics["inventory_complete"] = inventory_complete
    metrics["inventory_snapshot_at"] = int(time.time()) if inventory_complete else 0
    payload = {
        "metrics": metrics,
        "services": service_reports(config),
        "tunnels": reports,
        "plugins": plugin_inventory(),
        "agent_version": VERSION,
        "autoheal": clone_json(config.get("autoheal", {}), {}),
    }
    state_updates = {
        key: clone_json(state[key], state[key])
        for key in ("net", "inventory", "inventory_checked_at", "restart_history", "last_restart")
        if key in state
    }
    return payload, state_updates


def run(command: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = (result.stdout + result.stderr)[-200_000:]
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
    except Exception as exc:
        return 1, str(exc)


def systemd_status(service: str) -> str:
    code, _ = run(["systemctl", "is-active", service], timeout=8)
    return "active" if code == 0 else "inactive"


def service_uptime_seconds(service: str) -> int:
    if not service:
        return 0
    code, output = run(
        ["systemctl", "show", service, "--property=ActiveEnterTimestampMonotonic", "--value"],
        timeout=8,
    )
    try:
        entered_us = int(output.strip()) if code == 0 else 0
        boot_seconds = time.clock_gettime(time.CLOCK_BOOTTIME)
        return max(0, int(boot_seconds - entered_us / 1_000_000)) if entered_us else 0
    except (AttributeError, TypeError, ValueError):
        return 0


def socket_byte_snapshot() -> list[tuple[set[int], int, int]]:
    """Read cumulative TCP byte counters once, then attribute them by endpoint port."""
    code, output = run(["ss", "-tinH"], timeout=12)
    if code != 0:
        return []
    records: list[tuple[set[int], int, int]] = []
    ports: set[int] = set()
    received = sent = 0

    def flush() -> None:
        nonlocal ports, received, sent
        if ports:
            records.append((ports, received, sent))
        ports, received, sent = set(), 0, 0

    for line in output.splitlines():
        if line and not line[0].isspace():
            flush()
            for match in re.finditer(r"(?:\[[^\]]+\]|[^\s:]+):(\d+)(?:\s|$)", line):
                port = int(match.group(1))
                if 0 < port <= 65535:
                    ports.add(port)
        received_match = re.search(r"\bbytes_received:(\d+)", line)
        sent_match = re.search(r"\bbytes_sent:(\d+)", line)
        if received_match:
            received = int(received_match.group(1))
        if sent_match:
            sent = int(sent_match.group(1))
    flush()
    return records


def tunnel_traffic_bps(key: str, tunnel_ports: set[int], snapshot: list[tuple[set[int], int, int]]) -> tuple[float, float]:
    now = time.monotonic()
    received = sum(rx for ports, rx, _ in snapshot if ports & tunnel_ports)
    sent = sum(tx for ports, _, tx in snapshot if ports & tunnel_ports)
    previous = _TUNNEL_TRAFFIC_CACHE.get(key)
    _TUNNEL_TRAFFIC_CACHE[key] = (now, received, sent)
    if not previous:
        return 0.0, 0.0
    seconds = max(now - previous[0], 0.1)
    return (
        round(max(0, received - previous[1]) * 8 / seconds, 2),
        round(max(0, sent - previous[2]) * 8 / seconds, 2),
    )


async def tcp_probe(host: str, port: int, timeout: float = 4.0) -> tuple[bool, float | None]:
    started = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        await writer.wait_closed()
        return True, round((time.perf_counter() - started) * 1000, 2)
    except Exception:
        return False, None


async def tcp_sample(host: str, port: int, attempts: int = 3) -> tuple[str, float | None, float]:
    samples = [await tcp_probe(host, port) for _ in range(attempts)]
    latencies = [latency for ok, latency in samples if ok and latency is not None]
    loss = round((attempts - len(latencies)) * 100 / attempts, 1)
    status = "healthy" if loss == 0 else "degraded" if latencies else "down"
    return status, round(sum(latencies) / len(latencies), 2) if latencies else None, loss


def port_sessions(port: int) -> int:
    if not port:
        return 0
    try:
        return sum(
            1 for conn in connections_snapshot()
            if conn.status == psutil.CONN_ESTABLISHED and (
                (conn.laddr and conn.laddr.port == port) or
                (conn.raddr and conn.raddr.port == port)
            )
        )
    except (psutil.AccessDenied, OSError):
        return 0


def socket_state(port: int, remote_host: str | None = None) -> tuple[bool, int]:
    try:
        connections = connections_snapshot()
        if remote_host is None:
            listening = any(conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port for conn in connections)
            sessions = sum(1 for conn in connections if conn.status == psutil.CONN_ESTABLISHED and conn.laddr and conn.laddr.port == port)
            return listening, sessions
        targets = {remote_host}
        try:
            targets.add(socket.gethostbyname(remote_host))
        except OSError:
            pass
        sessions = sum(1 for conn in connections if conn.status == psutil.CONN_ESTABLISHED and conn.raddr and conn.raddr.port == port and conn.raddr.ip in targets)
        return sessions > 0, sessions
    except (psutil.AccessDenied, OSError):
        return False, 0


def socket_peer_ips(port: int) -> list[str]:
    """Return real TCP peers connected to a local Backhaul listener."""
    if not port:
        return []
    try:
        peers = {
            str(conn.raddr.ip) for conn in connections_snapshot()
            if conn.status == psutil.CONN_ESTABLISHED and conn.laddr and conn.laddr.port == port and conn.raddr and conn.raddr.ip
        }
        normalized = set()
        for peer in peers:
            try:
                address = ipaddress.ip_address(peer)
                normalized.add(str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address))
            except ValueError:
                normalized.add(peer.removeprefix("::ffff:"))
        return sorted(normalized)[:32]
    except (psutil.AccessDenied, OSError):
        return []


def _port_is_listening(port: int) -> bool:
    try:
        connections = connections_snapshot() or refresh_connection_snapshot()
        return any(conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port for conn in connections)
    except (psutil.AccessDenied, OSError):
        code, output = run(["ss", "-H", "-lntup"], timeout=10)
        return code == 0 and re.search(fr":{port}\b", output) is not None


def _darkbh_preflight(name: str, role: str, endpoint: str, tunnel_port: int, user_ports: list[int]) -> None:
    required = ["systemctl", "tar"]
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"Missing required commands: {', '.join(missing)}")
    if shutil.disk_usage("/usr/local").free < 64 * 1024 * 1024:
        raise RuntimeError("Less than 64 MiB free space is available")
    service = f"backhaul@{name}.service"
    if systemd_status(service) == "active" or (Path("/etc/dark-backhaul/tunnels") / name).exists():
        raise RuntimeError(f"Tunnel {name} already exists on this node")
    if role == "server":
        conflicts = [port for port in [tunnel_port, *user_ports] if _port_is_listening(port)]
        if conflicts:
            raise RuntimeError(f"Port conflict detected: {', '.join(map(str, conflicts))}")
    else:
        try:
            socket.getaddrinfo(endpoint, tunnel_port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RuntimeError(f"Endpoint DNS/IP resolution failed: {exc}") from exc
        missing = [port for port in user_ports if not _port_is_listening(port)]
        if missing:
            raise RuntimeError(f"Kharej destination port(s) are not listening locally: {', '.join(map(str, missing))}")


def connection_count() -> int:
    try:
        return len(connections_snapshot())
    except Exception:
        return 0


def base_metrics(previous: dict[str, Any]) -> dict[str, Any]:
    net = psutil.net_io_counters()
    disk_io = psutil.disk_io_counters()
    now = time.time()
    prior_ts = float(previous.get("ts", now))
    seconds = max(now - prior_ts, 1)
    rx_bps = max(0, (net.bytes_recv - int(previous.get("bytes_recv", net.bytes_recv))) * 8 / seconds)
    tx_bps = max(0, (net.bytes_sent - int(previous.get("bytes_sent", net.bytes_sent))) * 8 / seconds)
    disk = psutil.disk_usage("/")
    try:
        inode = os.statvfs("/")
        inode_percent = round((1 - inode.f_favail / max(inode.f_files, 1)) * 100, 2)
    except OSError:
        inode_percent = None
    read_bytes = int(getattr(disk_io, "read_bytes", 0) or 0)
    write_bytes = int(getattr(disk_io, "write_bytes", 0) or 0)
    disk_read_bps = max(0, (read_bytes - int(previous.get("disk_read_bytes", read_bytes))) / seconds)
    disk_write_bps = max(0, (write_bytes - int(previous.get("disk_write_bytes", write_bytes))) / seconds)
    network_errors = int(net.errin + net.errout)
    network_drops = int(net.dropin + net.dropout)
    network_errors_delta = max(0, network_errors - int(previous.get("network_errors", network_errors)))
    network_drops_delta = max(0, network_drops - int(previous.get("network_drops", network_drops)))
    interface_stats: dict[str, Any] = {}
    for name, counters in psutil.net_io_counters(pernic=True).items():
        if name == "lo":
            continue
        interface_stats[name] = {
            "rx_bytes": int(counters.bytes_recv), "tx_bytes": int(counters.bytes_sent),
            "rx_packets": int(counters.packets_recv), "tx_packets": int(counters.packets_sent),
            "rx_errors": int(counters.errin), "tx_errors": int(counters.errout),
            "rx_drops": int(counters.dropin), "tx_drops": int(counters.dropout),
        }
    temperatures: list[float] = []
    try:
        temperatures = [float(item.current) for group in psutil.sensors_temperatures().values() for item in group if item.current is not None]
    except (AttributeError, OSError):
        pass
    boot = psutil.boot_time()
    return {
        "hostname": socket.gethostname(),
        "cpu": psutil.cpu_percent(interval=0.4),
        "ram": psutil.virtual_memory().percent,
        "swap": psutil.swap_memory().percent,
        "disk": disk.percent,
        "load1": os.getloadavg()[0],
        "rx_bps": round(rx_bps, 2),
        "tx_bps": round(tx_bps, 2),
        "uptime": int(now - boot),
        "connections": connection_count(),
        "inode_percent": inode_percent,
        "disk_read_bps": round(disk_read_bps, 2),
        "disk_write_bps": round(disk_write_bps, 2),
        "disk_read_bytes": read_bytes,
        "disk_write_bytes": write_bytes,
        "temperature_c": round(max(temperatures), 1) if temperatures else None,
        "network_errors": network_errors,
        "network_drops": network_drops,
        "network_errors_delta": network_errors_delta,
        "network_drops_delta": network_drops_delta,
        "interfaces": interface_stats,
        "bytes_recv": net.bytes_recv,
        "bytes_sent": net.bytes_sent,
        "ts": now,
    }


def system_inventory(state: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    cached = state.get("inventory")
    if isinstance(cached, dict) and now - float(state.get("inventory_checked_at", 0)) < 1800:
        return cached
    os_name = "Linux"
    try:
        values = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
        os_name = values.get("PRETTY_NAME", os_name)
    except OSError:
        pass
    update_count = None
    if shutil.which("apt"):
        code, output = run(["apt", "list", "--upgradable"], timeout=60)
        if code == 0:
            update_count = len([line for line in output.splitlines() if "/" in line and not line.startswith("Listing")])
    docker = {"available": False, "running": 0, "total": 0, "unhealthy": 0}
    if shutil.which("docker"):
        code, output = run(["docker", "ps", "-a", "--format", "{{.State}}|{{.Status}}"], timeout=20)
        if code == 0:
            rows = [line for line in output.splitlines() if line.strip()]
            docker = {
                "available": True,
                "running": sum(line.startswith("running|") for line in rows),
                "total": len(rows),
                "unhealthy": sum("unhealthy" in line.lower() for line in rows),
            }
    inventory = {
        "os": os_name,
        "kernel": os.uname().release,
        "updates": update_count,
        "reboot_required": Path("/var/run/reboot-required").exists(),
        "docker": docker,
    }
    state["inventory"] = inventory
    state["inventory_checked_at"] = now
    return inventory


def execute_monitor(payload: dict[str, Any]) -> dict[str, Any]:
    monitor_id = int(payload.get("monitor_id", 0))
    kind = str(payload.get("kind", ""))
    target = str(payload.get("target", "")).strip()
    timeout = min(max(int(payload.get("timeout_seconds", 5)), 1), 60)
    port = int(payload.get("port") or (443 if kind in {"https", "tls"} else 80 if kind == "http" else 161 if kind == "snmp" else 0))
    if not monitor_id or kind not in {"icmp", "tcp", "http", "https", "dns", "tls", "snmp"} or not target:
        raise ValueError("Invalid monitor request")
    started = time.perf_counter()
    detail: dict[str, Any] = {}
    ok = False
    if kind == "icmp":
        if not shutil.which("ping"):
            raise RuntimeError("ping is not installed on this Agent")
        code, output = run(["ping", "-n", "-c", "1", "-W", str(timeout), target], timeout=timeout + 2)
        ok = code == 0
        match = re.search(r"time[=<]([0-9.]+)\s*ms", output)
        detail = {"reply": output.strip()[-1000:]}
        latency = float(match.group(1)) if match else None
    elif kind == "tcp":
        if not port:
            raise ValueError("TCP monitor requires a port")
        with socket.create_connection((target, port), timeout=timeout):
            ok = True
        latency = round((time.perf_counter() - started) * 1000, 2)
        detail = {"port": port}
    elif kind in {"http", "https"}:
        url = target if re.match(r"^https?://", target, re.I) else f"{kind}://{target}{f':{port}' if port not in {80,443} else ''}/"
        if not url.lower().startswith(f"{kind}://"):
            raise ValueError(f"{kind.upper()} monitor URL must use {kind}://")
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        expected = int(payload.get("expected_status") or 0)
        ok = response.status_code == expected if expected else response.status_code < 500
        latency = round((time.perf_counter() - started) * 1000, 2)
        detail = {"url": str(response.url), "status_code": response.status_code, "expected_status": expected or None}
    elif kind == "dns":
        records = sorted({item[4][0] for item in socket.getaddrinfo(target, port or None, type=socket.SOCK_STREAM)})
        ok = bool(records)
        latency = round((time.perf_counter() - started) * 1000, 2)
        detail = {"records": records[:16]}
    elif kind == "tls":
        context = ssl.create_default_context()
        with socket.create_connection((target, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=target) as secured:
                cert = secured.getpeercert()
                expires_at = int(ssl.cert_time_to_seconds(cert["notAfter"]))
                ok = expires_at > int(time.time())
                detail = {"port": port, "expires_at": expires_at, "days_left": (expires_at - int(time.time())) // 86400}
        latency = round((time.perf_counter() - started) * 1000, 2)
    else:
        community = str(payload.get("snmp_community") or "")
        oid = str(payload.get("snmp_oid") or ".1.3.6.1.2.1.1.3.0")
        if not community or not re.fullmatch(r"\.?[0-9]+(?:\.[0-9]+)+", oid):
            raise ValueError("SNMP monitor requires a community and numeric OID")
        if not shutil.which("snmpget"):
            raise RuntimeError("snmpget is not installed on this Agent")
        snmp_target = target if port == 161 else f"{target}:{port}"
        code, output = run(["snmpget", "-v2c", "-c", community, "-t", str(timeout), "-r", "0", snmp_target, oid], timeout=timeout + 2)
        ok = code == 0
        latency = round((time.perf_counter() - started) * 1000, 2) if ok else None
        detail = {"oid": oid, "value": output.strip()[-1000:] if ok else "SNMP request failed"}
    if kind == "icmp" and 'latency' not in locals():
        latency = round((time.perf_counter() - started) * 1000, 2) if ok else None
    return {"monitor_id": monitor_id, "status": "up" if ok else "down", "latency_ms": latency, "detail": detail, "checked_at": int(time.time())}


async def tunnel_report(
    tunnel: dict[str, Any],
    socket_bytes: list[tuple[set[int], int, int]] | None = None,
) -> dict[str, Any]:
    service = str(tunnel.get("service", ""))
    target_host = str(tunnel.get("target_host", "127.0.0.1"))
    target_port = int(tunnel.get("target_port", 0))
    service_ok = not service or systemd_status(service) == "active"
    method = str(tunnel.get("method", "custom"))
    role = str(tunnel.get("role", ""))
    if method in {"DARK Backhaul", "DARK Ghost Pro", "DARK Packet Pro"} and not role:
        role = "server" if tunnel.get("listen_port") else "client"
    user_ports = [int(port) for port in tunnel.get("user_ports", []) if str(port).isdigit()]
    backbone_ports = [int(port) for port in tunnel.get("backbone_ports", []) if str(port).isdigit()]
    target_ports = [int(port) for port in tunnel.get("target_ports", []) if str(port).isdigit()]
    if method == "DARK Realm Pro":
        listening_ports = user_ports if role == "edge" else backbone_ports
        listeners_ok = bool(listening_ports) and all(_port_is_listening(port) for port in listening_ports)
        probe_ok, latency = await tcp_probe(target_host, target_port, timeout=2.5) if target_port else (False, None)
        sessions = sum(port_sessions(port) for port in listening_ports)
        peer_ips = [target_host] if role == "edge" and target_host not in {"", "127.0.0.1", "localhost", "::1"} else []
        if role == "gateway" and backbone_ports:
            peer_ips = socket_peer_ips(backbone_ports[0])
        path_status = "healthy" if listeners_ok and probe_ok else "degraded" if listeners_ok or probe_ok else "down"
        packet_loss = 0 if probe_ok else 100
    elif method == "DARK Packet Pro":
        sessions = sum(port_sessions(port) for port in user_ports) if role == "client" else port_sessions(target_port)
        peer_ips = [target_host] if role == "client" and target_host not in {"", "127.0.0.1", "localhost", "::1"} else socket_peer_ips(target_port)
        path_status, latency, packet_loss = ("healthy" if service_ok else "down"), None, (0 if service_ok else 100)
    elif method in {"DARK Backhaul", "DARK Ghost Pro"} and role == "server":
        listening, sessions = socket_state(target_port)
        peer_ips = socket_peer_ips(target_port)
        connected = listening and sessions > 0
        if user_ports:
            sessions = sum(port_sessions(port) for port in user_ports)
        path_status, latency, packet_loss = ("healthy" if connected else "down"), None, (0 if connected else 100)
    elif method in {"DARK Backhaul", "DARK Ghost Pro"} and role == "client":
        connected, sessions = socket_state(target_port, target_host)
        peer_ips = [target_host] if target_host not in {"", "127.0.0.1", "localhost", "::1"} else []
        probe_ok, latency = await tcp_probe(target_host, target_port, timeout=2.5) if target_port else (False, None)
        path_status = "healthy" if connected else "degraded" if probe_ok else "down"
        packet_loss = 0 if connected or probe_ok else 100
    elif target_port:
        path_status, latency, packet_loss = await tcp_sample(target_host, target_port)
        sessions = port_sessions(int(tunnel.get("listen_port") or target_port or 0))
        peer_ips = []
    else:
        path_status, latency, packet_loss = ("healthy" if service_ok else "down"), None, (0 if service_ok else 100)
        sessions = 0
        peer_ips = []
    status = path_status if service_ok else "down"
    traffic_ports = {
        port
        for port in [target_port, int(tunnel.get("listen_port") or 0), *user_ports, *backbone_ports, *target_ports]
        if port
    }
    traffic_key = f"{service}|{tunnel.get('name', '')}|{role}"
    rx_bps, tx_bps = tunnel_traffic_bps(traffic_key, traffic_ports, socket_bytes or [])
    return {
        "name": str(tunnel.get("name", service or f"tcp-{target_port}")),
        "method": method,
        "target": f"{target_host}:{target_port}" if target_port else "local",
        "service": service,
        "listen_port": tunnel.get("listen_port"),
        "status": status,
        "latency_ms": latency,
        "packet_loss": packet_loss,
        "sessions": sessions,
        "peer_ips": peer_ips,
        "role": role,
        "target_host": target_host,
        "target_port": target_port,
        "user_ports": user_ports,
        "backbone_ports": backbone_ports,
        "target_ports": target_ports,
        "port_mappings": tunnel.get("port_mappings", []),
        "tls_domain": tunnel.get("tls_domain", ""),
        "transport": tunnel.get("transport"),
        "profile": tunnel.get("profile"),
        "restart_every": tunnel.get("restart_every"),
        "rx_bps": rx_bps,
        "tx_bps": tx_bps,
        "service_uptime": service_uptime_seconds(service),
        "checks": {"process": service_ok, "path": path_status != "down"},
    }


async def _collect_tunnel_reports(tunnels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    socket_bytes = socket_byte_snapshot()
    return list(await asyncio.gather(*(tunnel_report(item, socket_bytes) for item in tunnels)))


DARK_BACKHAUL_SERVICE_PATTERN = re.compile(r"^backhaul@[A-Za-z0-9_-]{1,64}\.service$")
GHOSTPRO_SERVICE_PATTERN = re.compile(r"^ghostpro@[A-Za-z0-9_-]{1,64}\.service$")
PACKETPRO_SERVICE_PATTERN = re.compile(r"^paqetpro@[A-Za-z0-9_-]{1,64}\.service$")
REALM_SERVICE_PATTERN = re.compile(r"^dark-realm@[A-Za-z0-9_-]{1,64}\.service$")
_DISCOVERY_CACHE: tuple[float, list[dict[str, Any]], bool] = (0.0, [], False)


def discover_tunnels_snapshot() -> tuple[list[dict[str, Any]], bool]:
    """Return DARK tunnel instances plus whether the inventory is authoritative.

    Local tunnel directories are scanned in addition to loaded systemd units so
    stopped/disabled Hub tunnels remain visible. Transient systemd failures reuse
    the last known entry and mark the snapshot incomplete instead of publishing a
    destructive empty inventory.
    """
    global _DISCOVERY_CACHE
    now = time.time()
    if now - _DISCOVERY_CACHE[0] < 60:
        return [dict(item) for item in _DISCOVERY_CACHE[1]], bool(_DISCOVERY_CACHE[2])

    previous_by_service = {
        str(item.get("service", "")): dict(item)
        for item in _DISCOVERY_CACHE[1]
        if item.get("service")
    }
    candidates: set[str] = set()
    complete = True
    service_commands = (
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"],
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--plain"],
    )
    systemd_scan_succeeded = False
    for command in service_commands:
        code, output = run(command, timeout=15)
        if code != 0:
            LOGGER.warning(
                "systemd tunnel discovery command failed (%s): %s",
                " ".join(command), output[-500:],
            )
            continue
        systemd_scan_succeeded = True
        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue
            service = parts[0]
            if (
                DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(service)
                or GHOSTPRO_SERVICE_PATTERN.fullmatch(service)
                or PACKETPRO_SERVICE_PATTERN.fullmatch(service)
                or REALM_SERVICE_PATTERN.fullmatch(service)
            ):
                candidates.add(service)

    filesystem_specs = (
        (Path("/etc/dark-backhaul/tunnels"), ("config.toml", "config.conf", "config.ini"), "backhaul@"),
        (Path("/etc/dark-ghostpro/tunnels"), ("config.yaml", "config.yml", "config.json"), "ghostpro@"),
        (Path("/etc/dark-packetpro/tunnels"), ("config.yaml", "config.yml", "config.json"), "paqetpro@"),
        (Path("/etc/dark-realm/tunnels"), ("config.toml", "config.conf"), "dark-realm@"),
    )
    scanned_root = False
    for base, config_names, prefix in filesystem_specs:
        try:
            if base.is_dir():
                scanned_root = True
                directories = list(base.iterdir())
            else:
                directories = []
        except OSError:
            complete = False
            directories = []
        for directory in directories:
            if not (
                directory.is_dir()
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", directory.name)
            ):
                continue
            has_owned_config = any((directory / name).is_file() for name in config_names)
            if has_owned_config or (directory / "meta.conf").is_file():
                candidates.add(f"{prefix}{directory.name}.service")

    if not systemd_scan_succeeded and not scanned_root:
        complete = False

    if not candidates and not complete and previous_by_service:
        return [dict(item) for item in previous_by_service.values()], False

    try:
        connections = connections_snapshot()
    except (psutil.AccessDenied, OSError):
        connections = []
        complete = False

    discovered: list[dict[str, Any]] = []
    for service in sorted(candidates)[:256]:
        ghost = bool(GHOSTPRO_SERVICE_PATTERN.fullmatch(service))
        packet = bool(PACKETPRO_SERVICE_PATTERN.fullmatch(service))
        realm = bool(REALM_SERVICE_PATTERN.fullmatch(service))
        prefix = "ghostpro@" if ghost else "paqetpro@" if packet else "dark-realm@" if realm else "backhaul@"
        instance = service.removeprefix(prefix).removesuffix(".service")
        tunnel_dir = Path(
            "/etc/dark-ghostpro/tunnels"
            if ghost else "/etc/dark-packetpro/tunnels"
            if packet else "/etc/dark-realm/tunnels"
            if realm else "/etc/dark-backhaul/tunnels"
        ) / instance
        config_candidates = (
            ("config.yaml", "config.yml", "config.json")
            if ghost or packet
            else ("config.toml", "config.conf", "config.ini")
        )
        expected_config = next(
            (tunnel_dir / name for name in config_candidates if (tunnel_dir / name).is_file()),
            tunnel_dir / config_candidates[0],
        )
        filesystem_owned = expected_config.is_file() or (tunnel_dir / "meta.conf").is_file()
        show_code, properties = run(
            ["systemctl", "show", service, "--property=ExecStart", "--property=MainPID"],
            timeout=8,
        )
        systemd_values: dict[str, str] = {}
        if show_code == 0:
            for line in properties.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    systemd_values[key] = value
        elif not filesystem_owned:
            complete = False
            cached = previous_by_service.get(service)
            if cached:
                discovered.append(cached)
            continue
        if not filesystem_owned and str(expected_config) not in systemd_values.get("ExecStart", ""):
            # A matching loaded unit without an owned DARK config path is not ours.
            continue

        meta: dict[str, str] = {}
        try:
            for raw_line in (tunnel_dir / "meta.conf").read_text(encoding="utf-8").splitlines():
                if "=" in raw_line:
                    key, value = raw_line.split("=", 1)
                    meta[key.strip()] = value.strip().strip('"')
        except OSError:
            pass
        try:
            root_pid = int(systemd_values.get("MainPID", "0").strip())
        except ValueError:
            root_pid = 0
        pids = {root_pid} if root_pid > 0 else set()
        if root_pid > 0:
            try:
                pids.update(child.pid for child in psutil.Process(root_pid).children(recursive=True))
            except (psutil.Error, OSError):
                pass
        owned = [conn for conn in connections if conn.pid in pids]
        listeners = sorted({conn.laddr.port for conn in owned if conn.status == psutil.CONN_LISTEN and conn.laddr})
        remotes = [conn.raddr for conn in owned if conn.status == psutil.CONN_ESTABLISHED and conn.raddr]
        method = "DARK Ghost Pro" if ghost else "DARK Packet Pro" if packet else "DARK Realm Pro" if realm else "DARK Backhaul"
        configured_role = meta.get("ROLE", "")
        configured_port = int(meta.get("PORT", "0")) if meta.get("PORT", "").isdigit() else 0
        remote = remotes[0] if remotes else None
        user_ports: list[int] = []
        backbone_ports: list[int] = []
        target_ports: list[int] = []
        port_mappings: list[str] = []
        if realm:
            for item in meta.get("MAPS", "").split(","):
                match = re.fullmatch(r"([0-9]{1,5})>([0-9]{1,5})@([0-9]{1,5})", item.strip())
                if match:
                    public, target, backbone = map(int, match.groups())
                    user_ports.append(public)
                    target_ports.append(target)
                    backbone_ports.append(backbone)
                    port_mappings.append(item.strip())
            role = configured_role if configured_role in {"edge", "gateway"} else (
                "gateway" if listeners and backbone_ports and listeners[0] in backbone_ports else "edge"
            )
            listen_port = user_ports[0] if role == "edge" and user_ports else backbone_ports[0] if backbone_ports else None
            target_host = meta.get("GATEWAY_HOST", "127.0.0.1") if role == "edge" else meta.get("TARGET_HOST", "127.0.0.1")
            target_port = backbone_ports[0] if role == "edge" and backbone_ports else target_ports[0] if target_ports else 0
        else:
            listen_port = configured_port if configured_role == "server" else None
            role = configured_role if configured_role in {"server", "client"} else ("server" if listeners else "client")
            target_host = "127.0.0.1" if role == "server" else (meta.get("PEER_IP") or (remote.ip if remote else "127.0.0.1"))
            target_port = configured_port or (remote.port if remote else 0)
            try:
                user_ports = [
                    int(line.split()[0])
                    for line in (tunnel_dir / "ports.list").read_text(encoding="utf-8").splitlines()
                    if line.split() and line.split()[0].isdigit()
                ]
            except OSError:
                pass
        discovered.append({
            "name": instance,
            "method": method,
            "role": role,
            "service": service,
            "listen_port": listen_port,
            "target_host": target_host,
            "target_port": target_port,
            "user_ports": user_ports,
            "backbone_ports": backbone_ports,
            "target_ports": target_ports,
            "port_mappings": port_mappings,
            "transport": meta.get("TRANSPORT", "unknown"),
            "profile": meta.get("PROFILE", "unknown"),
            "restart_every": meta.get("SCHEDULE", meta.get("RESTART_EVERY", "off")),
            "tls_domain": meta.get("TLS_DOMAIN", ""),
            "auto_discovered": True,
        })

    _DISCOVERY_CACHE = (now, discovered, complete)
    return [dict(item) for item in discovered], complete


def discover_tunnels() -> list[dict[str, Any]]:
    return discover_tunnels_snapshot()[0]


def discovery_inventory_complete() -> bool:
    return bool(_DISCOVERY_CACHE[2])


def monitored_tunnels(config: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [
        item for item in config.get("tunnels", [])
        if str(item.get("method", "")) in {"DARK Backhaul", "DARK Ghost Pro", "DARK Packet Pro", "DARK Realm Pro"}
        and (DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or GHOSTPRO_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or PACKETPRO_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or REALM_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))))
    ]
    if not config.get("auto_discovery", True):
        return explicit
    known_instances = {(str(item.get("name", "")), str(item.get("service", ""))) for item in explicit}
    automatic = [
        item for item in discover_tunnels()
        if (str(item.get("name", "")), str(item.get("service", ""))) not in known_instances
    ]
    return explicit + automatic


def service_reports(config: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    discovered_services = [item["service"] for item in discover_tunnels()] if config.get("auto_discovery", True) else []
    for service in dict.fromkeys([*config.get("services", []), *discovered_services]):
        service = str(service)
        result.append({"name": service, "status": systemd_status(service)})
    return result


def plugin_inventory() -> dict[str, Any]:
    core = Path("/usr/local/bin/backhaul")
    installed = core.is_file() and os.access(core, os.X_OK)
    version = None
    if installed:
        code, output = run([str(core), "-v"], timeout=8)
        version = (output.strip().splitlines() or ["unknown"])[-1][:120] if code == 0 else "unknown"
    gost = Path("/usr/local/bin/gost")
    gost_installed = gost.is_file() and os.access(gost, os.X_OK)
    gost_version = None
    if gost_installed:
        code, output = run([str(gost), "-V"], timeout=8)
        gost_version = (output.strip().splitlines() or ["unknown"])[-1][:120] if code == 0 else "unknown"
    paqet = Path("/usr/local/bin/paqet")
    paqet_installed = paqet.is_file() and os.access(paqet, os.X_OK)
    paqet_version = None
    if paqet_installed:
        code, output = run([str(paqet), "version"], timeout=8)
        paqet_version = (output.strip().splitlines() or ["unknown"])[-1][:120] if code == 0 else "unknown"
    tunnels = discover_tunnels()
    return {
        "dark_backhaul": {"installed": installed, "version": version, "instances": sum(item["method"] == "DARK Backhaul" for item in tunnels)},
        "dark_ghostpro": {"installed": gost_installed, "version": gost_version, "instances": sum(item["method"] == "DARK Ghost Pro" for item in tunnels)},
        "dark_packetpro": {"installed": paqet_installed, "version": paqet_version, "instances": sum(item["method"] == "DARK Packet Pro" for item in tunnels)},
        "dark_realm": {**realm_inventory(), "instances": sum(item["method"] == "DARK Realm Pro" for item in tunnels)},
    }


def restart_allowed(service: str, config: dict[str, Any]) -> bool:
    if service in {str(item) for item in config.get("managed_services", [])}:
        return True
    if DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(service):
        name = service.removeprefix("backhaul@").removesuffix(".service")
        return (Path("/etc/dark-backhaul/tunnels") / name / "config.toml").is_file()
    if GHOSTPRO_SERVICE_PATTERN.fullmatch(service):
        name = service.removeprefix("ghostpro@").removesuffix(".service")
        return (Path("/etc/dark-ghostpro/tunnels") / name / "config.yaml").is_file()
    if PACKETPRO_SERVICE_PATTERN.fullmatch(service):
        name = service.removeprefix("paqetpro@").removesuffix(".service")
        return (Path("/etc/dark-packetpro/tunnels") / name / "config.yaml").is_file()
    if REALM_SERVICE_PATTERN.fullmatch(service):
        name = service.removeprefix("dark-realm@").removesuffix(".service")
        return (Path("/etc/dark-realm/tunnels") / name / "config.toml").is_file()
    return False


def _download(url: str, destination: Path, timeout: int = 240, max_bytes: int = 128 * 1024 * 1024) -> str:
    if not url.startswith("https://"):
        raise RuntimeError("Plugin downloads require HTTPS")
    digest = hashlib.sha256()
    received = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    raise RuntimeError("Plugin download exceeded the 128 MiB safety limit")
                digest.update(chunk)
                handle.write(chunk)
    return digest.hexdigest()


def _install_darkbh_core() -> str:
    Path("/etc/dark-backhaul").mkdir(parents=True, exist_ok=True)

    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm", "i386": "386", "i686": "386"}.get(os.uname().machine)
    if not arch:
        raise RuntimeError(f"Unsupported CPU architecture: {os.uname().machine}")
    core_path = Path("/usr/local/bin/backhaul")
    if core_path.exists() and os.access(core_path, os.X_OK):
        code, version = run([str(core_path), "-v"], timeout=10)
        if code == 0:
            return version.strip() or "installed (existing core preserved)"
    release = httpx.get(DARKBH_RELEASE_API, follow_redirects=True, timeout=40)
    release.raise_for_status()
    release_data = release.json()
    release_tag = str(release_data.get("tag_name", "")).lstrip("v")
    candidates = [asset for asset in release_data.get("assets", []) if re.search(fr"linux[_-]{arch}", asset.get("name", ""), re.I) and not re.search(r"\.(sha256|asc|sig|txt|md5)$", asset.get("name", ""), re.I)]
    if not candidates:
        raise RuntimeError(f"No official Backhaul linux_{arch} release asset found")
    asset = next((item for item in candidates if re.search(r"\.(tar\.gz|tgz)$", item["name"], re.I)), candidates[0])
    with tempfile.TemporaryDirectory(prefix="darkbh-") as tmp_name:
        tmp = Path(tmp_name)
        package = tmp / asset["name"]
        actual_digest = _download(asset["browser_download_url"], package)
        expected_digest = str(asset.get("digest") or "")
        if not expected_digest.startswith("sha256:") or not hmac.compare_digest(expected_digest.removeprefix("sha256:"), actual_digest):
            raise RuntimeError("Backhaul release asset has no valid matching SHA-256 digest")
        unpacked = tmp / "unpacked"
        unpacked.mkdir()
        if re.search(r"\.(tar\.gz|tgz)$", asset["name"], re.I):
            try:
                with tarfile.open(package, "r:gz") as archive:
                    members = archive.getmembers()
                    if any((not member.isfile() and not member.isdir()) or member.name.startswith("/") or ".." in Path(member.name).parts for member in members):
                        raise RuntimeError("Unsafe path, link or special file found in Backhaul release archive")
                    archive.extractall(unpacked, members=members)
            except (tarfile.TarError, OSError) as exc:
                raise RuntimeError(f"Backhaul core extraction failed: {exc}") from exc
            binaries = [p for p in unpacked.rglob("backhaul*") if p.is_file() and not p.name.endswith((".md", ".toml"))]
        else:
            binaries = [package]
        if not binaries:
            raise RuntimeError("Backhaul binary was not found in official release")
        candidate = binaries[0]
        os.chmod(candidate, 0o755)
        code, output = run([str(candidate), "-v"], timeout=10)
        if code != 0:
            raise RuntimeError(f"Backhaul binary verification failed: {output[-500:]}")
        core_tmp = Path("/usr/local/bin/.backhaul.new")
        shutil.copy2(candidate, core_tmp)
        os.chmod(core_tmp, 0o755)
        core_tmp.replace(core_path)
        Path("/etc/dark-backhaul/core.version").write_text(str(release_data.get("tag_name", "latest")) + "\n")
        return output.strip() or str(release_data.get("tag_name", "installed"))


def _profile_values(profile: str) -> dict[str, Any]:
    values = {
        "stable": (4, 1024, 30, 20, 4, "false", 3, 10),
        "balanced": (8, 2048, 40, 75, 8, "false", 3, 10),
        "lowping": (16, 2048, 20, 20, 8, "true", 1, 5),
        "turbo": (24, 4096, 40, 60, 16, "true", 2, 10),
    }[profile]
    return dict(zip(("pool", "channel", "heartbeat", "keepalive", "muxcon", "aggressive", "retry", "dial"), values))


def _install_gost_core() -> str:
    core = Path("/usr/local/bin/gost")
    if core.is_file() and os.access(core, os.X_OK):
        code, output = run([str(core), "-V"], timeout=10)
        if code == 0:
            return output.strip() or "installed"
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm32", "i386": "386", "i686": "386"}.get(os.uname().machine)
    if not arch:
        raise RuntimeError(f"Unsupported CPU architecture: {os.uname().machine}")
    release = httpx.get(GOST_RELEASE_API, follow_redirects=True, timeout=40)
    release.raise_for_status(); data = release.json()
    assets = [a for a in data.get("assets", []) if re.search(fr"linux[_-]{arch}", a.get("name", ""), re.I) and re.search(r"\.(tar\.gz|tgz)$", a.get("name", ""), re.I)]
    if not assets:
        raise RuntimeError(f"No official GOST linux_{arch} release asset found")
    with tempfile.TemporaryDirectory(prefix="darkghost-") as tmp_name:
        tmp = Path(tmp_name); package = tmp / assets[0]["name"]
        actual = _download(assets[0]["browser_download_url"], package)
        expected = str(assets[0].get("digest") or "")
        if expected.startswith("sha256:") and not hmac.compare_digest(expected.removeprefix("sha256:"), actual):
            raise RuntimeError("GOST release SHA-256 mismatch")
        with tarfile.open(package, "r:gz") as archive:
            members = archive.getmembers()
            if any((not m.isfile() and not m.isdir()) or m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
                raise RuntimeError("Unsafe GOST archive")
            (tmp / "unpacked").mkdir()
            archive.extractall(tmp / "unpacked", members=members)
        binaries = [p for p in (tmp / "unpacked").rglob("gost") if p.is_file()]
        if not binaries:
            raise RuntimeError("GOST binary not found")
        os.chmod(binaries[0], 0o755)
        code, output = run([str(binaries[0]), "-V"], timeout=10)
        if code != 0:
            raise RuntimeError("GOST binary verification failed")
        shutil.copy2(binaries[0], Path("/usr/local/bin/.gost.new")); os.chmod("/usr/local/bin/.gost.new", 0o755)
        Path("/usr/local/bin/.gost.new").replace(core)
        Path("/etc/dark-ghostpro").mkdir(parents=True, exist_ok=True)
        Path("/etc/dark-ghostpro/core.version").write_text(str(data.get("tag_name", "latest")) + "\n")
        return output.strip() or str(data.get("tag_name", "installed"))


def _deploy_dark_ghost(payload: dict[str, Any], config: dict[str, Any]) -> str:
    name, role = str(payload.get("name", "")), str(payload.get("role", ""))
    endpoint, token = str(payload.get("endpoint", "")), str(payload.get("token", ""))
    transport, profile = str(payload.get("transport", "")), str(payload.get("profile", ""))
    port = int(payload.get("tunnel_port", 0)); ports = sorted(set(int(p) for p in payload.get("user_ports", [])))
    restart = str(payload.get("restart_every", "off"))
    supported = {"relay+tls", "relay+wss", "relay+h2", "relay+grpc", "relay+quic", "relay+ws", "relay", "tls", "wss", "h2", "grpc", "quic", "ws", "tcp", "h2c", "dtls", "icmp"}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name) or role not in {"server", "client"} or transport not in supported:
        raise ValueError("Invalid Ghost Pro tunnel identity, role or transport")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", endpoint) or not re.fullmatch(r"[A-Za-z0-9]{8,128}", token):
        raise ValueError("Invalid endpoint or token")
    if profile not in {"stable", "balanced", "lowping", "turbo"} or restart not in {"off", "1h", "6h", "12h", "24h"} or not 1 <= port <= 65535 or not ports or any(p == port or not 1 <= p <= 65535 for p in ports):
        raise ValueError("Invalid Ghost Pro profile, restart or ports")
    version = _install_gost_core()
    directory = Path("/etc/dark-ghostpro/tunnels") / name; directory.mkdir(parents=True, exist_ok=True)
    managed_cert = str(payload.get("certificate_path", "")); managed_key = str(payload.get("certificate_key_path", ""))
    cert = Path(managed_cert) if managed_cert else directory / "server.crt"; key = Path(managed_key) if managed_key else directory / "server.key"
    tls = transport in {"relay+tls", "relay+wss", "relay+h2", "relay+grpc", "tls", "wss", "h2", "grpc"}
    if role == "server" and tls and managed_cert and (not cert.is_file() or not key.is_file()):
        raise RuntimeError("Selected TLS certificate files are missing")
    if role == "server" and tls and not managed_cert and (not cert.exists() or not key.exists()):
        code, output = run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={endpoint}"], timeout=30)
        if code != 0: raise RuntimeError(f"Certificate generation failed: {output[-500:]}")
    listener = {"relay+tls":"tls", "relay+wss":"wss", "relay+h2":"h2", "relay+grpc":"grpc", "relay+quic":"quic", "relay+ws":"ws", "relay":"tcp", "tls":"tls", "wss":"wss", "h2":"h2", "grpc":"grpc", "quic":"quic", "ws":"ws", "tcp":"tcp", "h2c":"h2c", "dtls":"dtls", "icmp":"icmp"}[transport]
    if role == "server":
        yaml = f'''services:\n  - name: ghost-relay\n    addr: ":{port}"\n    handler:\n      type: relay\n      auth:\n        username: ghost\n        password: "{token}"\n    listener:\n      type: {listener}\n'''
        if tls: yaml += f'      tls:\n        certFile: "{cert}"\n        keyFile: "{key}"\n'
    else:
        insecure = "\n              tls:\n                insecure: false" if tls and managed_cert else ("\n              tls:\n                insecure: true" if tls else "")
        yaml = f'''chains:\n  - name: iran-chain\n    hops:\n      - name: iran-hop\n        nodes:\n          - name: iran-node\n            addr: "{endpoint}:{port}"\n            connector:\n              type: relay\n              auth:\n                username: ghost\n                password: "{token}"\n            dialer:\n              type: {listener}{insecure}\nservices:\n'''
        for index, user_port in enumerate(ports, 1):
            yaml += f'''  - name: rtcp-{user_port}\n    addr: ":{user_port}"\n    handler:\n      type: rtcp\n      chain: iran-chain\n    listener:\n      type: rtcp\n    forwarder:\n      nodes:\n        - name: local-{index}\n          addr: 127.0.0.1:{user_port}\n'''
    yaml += "log:\n  level: info\n"; (directory / "config.yaml").write_text(yaml)
    (directory / "ports.list").write_text("".join(f"{p}\t-\n" for p in ports))
    (directory / "meta.conf").write_text(f'NAME="{name}"\nROLE="{role}"\nPORT="{port}"\nTOKEN="{token}"\nTRANSPORT="{transport}"\nPROFILE="{profile}"\nTLS_CERT="{cert if tls and role == "server" else ""}"\nTLS_KEY="{key if tls and role == "server" else ""}"\nPEER_IP="{endpoint if role == "client" else ""}"\nPUB_IP="{endpoint if role == "server" else ""}"\nLOGLEVEL="info"\nRESTART_EVERY="{restart}"\n')
    for path in (directory / "config.yaml", directory / "meta.conf", key):
        if path.exists(): os.chmod(path, 0o600)
    unit = f"[Unit]\nDescription=DARK VPN Ghost Pro Tunnel (%i)\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nExecStart=/usr/local/bin/gost -C /etc/dark-ghostpro/tunnels/%i/config.yaml\nRestart=always\nRestartSec=5\nLimitNOFILE=1048576\n\n[Install]\nWantedBy=multi-user.target\n"
    Path("/etc/systemd/system/ghostpro@.service").write_text(unit)
    Path("/etc/systemd/system/ghostpro-restart@.service").write_text("[Unit]\nDescription=DARK Ghost Pro scheduled restart (%i)\n\n[Service]\nType=oneshot\nExecStart=/bin/systemctl restart ghostpro@%i.service\n")
    Path("/etc/systemd/system/ghostpro-restart@.timer").write_text("[Unit]\nDescription=DARK Ghost Pro scheduled restart timer (%i)\n\n[Timer]\nOnUnitActiveSec=1h\nOnBootSec=1h\nAccuracySec=1min\nUnit=ghostpro-restart@%i.service\n\n[Install]\nWantedBy=timers.target\n")
    timer = f"ghostpro-restart@{name}.timer"; dropin = Path("/etc/systemd/system") / f"{timer}.d"
    if restart == "off":
        run(["systemctl", "disable", "--now", timer], timeout=20); shutil.rmtree(dropin, ignore_errors=True)
    else:
        dropin.mkdir(parents=True, exist_ok=True); (dropin / "schedule.conf").write_text(f"[Timer]\nOnUnitActiveSec=\nOnBootSec=\nOnUnitActiveSec={restart}\nOnBootSec={restart}\n")
    firewall_rules = _configure_ufw(directory, name, role, transport, port, ports)
    run(["systemctl", "daemon-reload"])
    code, output = run(["systemctl", "enable", "--now", f"ghostpro@{name}.service"], timeout=30)
    if code != 0: raise RuntimeError(f"Ghost Pro service failed: {output[-1000:]}")
    if restart != "off": run(["systemctl", "enable", "--now", timer], timeout=20)
    service = f"ghostpro@{name}.service"; monitor = {"name":name,"method":"DARK Ghost Pro","role":role,"service":service,"listen_port":port if role=="server" else None,"user_ports":ports,"target_host":"127.0.0.1" if role=="server" else endpoint,"target_port":port,"transport":transport,"profile":profile,"restart_every":restart}
    config["tunnels"] = [i for i in config.setdefault("tunnels", []) if not (i.get("name") == name and i.get("method") == "DARK Ghost Pro")] + [monitor]
    for k in ("services", "managed_services"):
        if service not in config.setdefault(k, []): config[k].append(service)
    save_json(CONFIG_PATH, config)
    return f"DARK Ghost Pro deployed\nrole={role}\ntunnel={name}\ntransport={transport}\ncore={version}\nservice={service} active\nufw_rules={','.join(firewall_rules) if firewall_rules else 'not-needed'}"


def _configure_ufw(tunnel_dir: Path, name: str, role: str, transport: str, tunnel_port: int, user_ports: list[int]) -> list[str]:
    if role != "server" or shutil.which("ufw") is None:
        return []
    code, status = run(["ufw", "status"], timeout=15)
    if code != 0 or "Status: active" not in status:
        return []
    tunnel_rules = [] if transport == "icmp" else [(tunnel_port, "udp" if transport in {"udp", "quic", "dtls", "relay+quic"} else "tcp")]
    requested = [*tunnel_rules, *[(port, "tcp") for port in user_ports]]
    created: list[str] = []
    for port, protocol in requested:
        rule = f"{port}/{protocol}"
        code, output = run(["ufw", "allow", rule, "comment", f"DARK-NOC-{name}"], timeout=20)
        if code != 0:
            raise RuntimeError(f"Could not open UFW rule {rule}: {output[-500:]}")
        if "Rule added" in output:
            created.append(rule)
    (tunnel_dir / "ufw-created.json").write_text(json.dumps(created))
    return created


def _issue_certificate(payload: dict[str, Any]) -> str:
    domain = str(payload.get("domain", "")).casefold().rstrip(".")
    if not re.fullmatch(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        raise ValueError("Invalid certificate domain")
    nginx_active = run(["systemctl", "is-active", "nginx"], timeout=10)[0] == 0
    if not shutil.which("certbot"):
        raise RuntimeError("certbot is not installed; rerun the DARK NOC Node installer or Agent upgrade")
    command = ["certbot", "certonly", "--non-interactive", "--agree-tos", "--register-unsafely-without-email", "--keep-until-expiring", "-d", domain]
    if nginx_active:
        webroot = Path("/var/lib/dark-noc-acme"); (webroot / ".well-known/acme-challenge").mkdir(parents=True, exist_ok=True)
        conf = Path("/etc/nginx/conf.d") / f"dark-noc-acme-{hashlib.sha256(domain.encode()).hexdigest()[:12]}.conf"
        conf.write_text(f"server {{\n  listen 80;\n  listen [::]:80;\n  server_name {domain};\n  location ^~ /.well-known/acme-challenge/ {{ root {webroot}; }}\n  location / {{ return 301 https://$host$request_uri; }}\n}}\n")
        code, output = run(["nginx", "-t"], timeout=20)
        if code != 0: conf.unlink(missing_ok=True); raise RuntimeError(f"Nginx ACME configuration failed: {output[-700:]}")
        run(["systemctl", "reload", "nginx"], timeout=20)
        command += ["--webroot", "-w", str(webroot)]
    else:
        command += ["--standalone"]
    code, output = run(command, timeout=300)
    if code != 0: raise RuntimeError(f"certificate issuance failed: {output[-1200:]}")
    live = Path("/etc/letsencrypt/live") / domain; cert = live / "fullchain.pem"; key = live / "privkey.pem"
    if not cert.is_file() or not key.is_file(): raise RuntimeError("certbot completed but certificate files are missing")
    code, expiry = run(["openssl", "x509", "-enddate", "-noout", "-in", str(cert)], timeout=10)
    if code != 0 or "=" not in expiry: raise RuntimeError("could not read certificate expiry")
    expires_at = int(time.mktime(time.strptime(expiry.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z")))
    return json.dumps({"domain": domain, "cert_path": str(cert), "key_path": str(key), "expires_at": expires_at, "issuer": "letsencrypt"})


def _install_paqet_core(required_tag: str = PAQET_CORE_TAG) -> str:
    if not re.fullmatch(r"v?[0-9A-Za-z._-]{1,40}", required_tag):
        raise ValueError("Invalid PAQET core tag")
    core = Path("/usr/local/bin/paqet")
    version_file = Path("/etc/dark-packetpro/core.version")
    if core.is_file() and os.access(core, os.X_OK) and version_file.exists() and version_file.read_text().strip() == required_tag:
        code, output = run([str(core), "version"], timeout=10)
        if code == 0:
            return output.strip() or required_tag
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "armv7", "i386": "386", "i686": "386"}.get(os.uname().machine)
    if not arch:
        raise RuntimeError(f"Unsupported CPU architecture: {os.uname().machine}")
    asset_name = f"paqet-linux-{arch}-{required_tag}.tar.gz"
    release = httpx.get(f"https://api.github.com/repos/{PAQET_REPO}/releases/tags/{required_tag}", follow_redirects=True, timeout=40)
    release.raise_for_status(); release_data = release.json()
    asset = next((item for item in release_data.get("assets", []) if item.get("name") == asset_name), None)
    if not asset:
        raise RuntimeError(f"Official PAQET asset not found: {asset_name}")
    expected_digest = str(asset.get("digest") or "")
    if not expected_digest.startswith("sha256:"):
        raise RuntimeError("Official PAQET asset has no SHA-256 digest")
    with tempfile.TemporaryDirectory(prefix="darkpacket-") as tmp_name:
        tmp = Path(tmp_name); package = tmp / "paqet.tar.gz"
        actual_digest = _download(str(asset["browser_download_url"]), package)
        if not hmac.compare_digest(expected_digest.removeprefix("sha256:"), actual_digest):
            raise RuntimeError("PAQET release SHA-256 mismatch")
        unpacked = tmp / "unpacked"; unpacked.mkdir()
        try:
            with tarfile.open(package, "r:gz") as archive:
                members = archive.getmembers()
                if any((not m.isfile() and not m.isdir()) or m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
                    raise RuntimeError("Unsafe PAQET release archive")
                archive.extractall(unpacked, members=members)
        except (tarfile.TarError, OSError) as exc:
            raise RuntimeError(f"PAQET extraction failed: {exc}") from exc
        binaries = [p for p in unpacked.rglob(f"paqet_linux_{arch}") if p.is_file()] or [p for p in unpacked.rglob("paqet*") if p.is_file() and p.stat().st_size > 1024 * 1024]
        if not binaries:
            raise RuntimeError("PAQET binary not found in official release")
        os.chmod(binaries[0], 0o755)
        code, output = run([str(binaries[0]), "version"], timeout=10)
        if code != 0:
            raise RuntimeError(f"PAQET binary verification failed: {output[-500:]}")
        target = Path("/usr/local/bin/.paqet.new"); shutil.copy2(binaries[0], target); os.chmod(target, 0o755); target.replace(core)
    version_file.parent.mkdir(parents=True, exist_ok=True); version_file.write_text(required_tag + "\n")
    return output.strip() or required_tag


def _paqet_network(endpoint: str) -> tuple[str, str, str]:
    code, route = run(["ip", "-4", "route", "show", "default"], timeout=10)
    if code != 0:
        raise RuntimeError(f"Could not resolve route to {endpoint}: {route[-500:]}")
    iface_match = re.search(r"\bdev\s+(\S+)", route); gateway_match = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)", route)
    if not iface_match or not gateway_match:
        raise RuntimeError("Could not detect PAQET default interface/gateway")
    iface, gateway = iface_match.group(1), gateway_match.group(1)
    _, addresses = run(["ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"], timeout=10)
    source_match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", addresses)
    if not source_match:
        raise RuntimeError("Could not detect PAQET local IPv4")
    local_ip = source_match.group(1)
    run(["ping", "-c", "1", "-W", "1", gateway], timeout=3)
    _, neigh = run(["ip", "neigh", "show", gateway, "dev", iface], timeout=8)
    mac_match = re.search(r"\blladdr\s+([0-9a-fA-F:]{17})", neigh)
    if not mac_match:
        raise RuntimeError(f"Gateway MAC detection failed for {gateway} on {iface}")
    return iface, local_ip, mac_match.group(1).lower()


def _packet_profile(profile: str) -> tuple[str, int, int]:
    return {"stable": ("normal", 2, 1150), "balanced": ("fast", 4, 1150), "lowping": ("fast2", 4, 1150), "turbo": ("fast3", 8, 1250)}[profile]


def _write_paqet_firewall_helper() -> Path:
    helper = Path("/usr/local/bin/darknoc-paqet-fw")
    helper.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
ACT="${1:-}"; NAME="${2:-}"
[[ "$NAME" =~ ^[A-Za-z0-9_-]{1,24}$ ]] || exit 2
DIR="/etc/dark-packetpro/tunnels/$NAME"; META="$DIR/meta.conf"; [[ -f "$META" ]] || exit 2
read_value(){ awk -F= -v k="$1" '$1==k{v=substr($0,index($0,"=")+1);sub(/^"/,"",v);sub(/"$/,"",v);print v;exit}' "$META"; }
PORT="$(read_value PORT)"; ROLE="$(read_value ROLE)"; [[ "$PORT" =~ ^[0-9]+$ ]] || exit 2
tag="darkpp-$NAME"
one(){ local op="$1" proto="$2" port="$3" table chain; for table in raw mangle; do chain=PREROUTING; if [[ "$op" == add ]]; then iptables -t "$table" -C "$chain" -p "$proto" --dport "$port" -m comment --comment "$tag" -j ACCEPT 2>/dev/null || iptables -t "$table" -I "$chain" -p "$proto" --dport "$port" -m comment --comment "$tag" -j ACCEPT; else while iptables -t "$table" -C "$chain" -p "$proto" --dport "$port" -m comment --comment "$tag" -j ACCEPT 2>/dev/null; do iptables -t "$table" -D "$chain" -p "$proto" --dport "$port" -m comment --comment "$tag" -j ACCEPT; done; fi; done; }
run_rules(){ local op="$1" p target; one "$op" tcp "$PORT"; [[ "$ROLE" == client ]] || return 0; [[ -f "$DIR/ports.list" ]] || return 0; while IFS=$'\t' read -r p target; do [[ "$p" =~ ^[0-9]+$ ]] && one "$op" tcp "$p"; done < "$DIR/ports.list"; }
case "$ACT" in apply) run_rules add;; clear) run_rules del;; *) exit 2;; esac
''')
    os.chmod(helper, 0o755)
    return helper


def _deploy_dark_packet(payload: dict[str, Any], config: dict[str, Any]) -> str:
    if payload.get("plugin_id") != "dark-packetpro":
        raise ValueError("Plugin is not allowlisted")
    name, role = str(payload.get("name", "")), str(payload.get("role", ""))
    endpoint, key = str(payload.get("endpoint", "")), str(payload.get("token", ""))
    profile, restart = str(payload.get("profile", "")), str(payload.get("restart_every", "off"))
    port = int(payload.get("tunnel_port", 0)); ports = sorted(set(int(p) for p in payload.get("user_ports", [])))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name) or role not in {"server", "client"}:
        raise ValueError("Invalid Packet Pro tunnel identity or role")
    try:
        if ipaddress.ip_address(endpoint).version != 4: raise ValueError
    except ValueError:
        raise ValueError("DARK Packet Pro requires the KHAREJ public IPv4")
    if not re.fullmatch(r"[A-Za-z0-9]{16,128}", key) or profile not in {"stable", "balanced", "lowping", "turbo"}:
        raise ValueError("Invalid Packet Pro key or performance profile")
    if restart not in {"off", "1h", "6h", "12h", "24h"} or not 1 <= port <= 65535 or not ports or any(not 1 <= p <= 65535 or p == port for p in ports):
        raise ValueError("Invalid Packet Pro restart schedule or ports")
    core_tag = str(payload.get("core_tag") or PAQET_CORE_TAG)
    version = _install_paqet_core(core_tag)
    iface, local_ip, router_mac = _paqet_network(endpoint)
    mode, conn, mtu = _packet_profile(profile)
    directory = Path("/etc/dark-packetpro/tunnels") / name; directory.mkdir(parents=True, exist_ok=True)
    if role == "server":
        yaml = f'''role: "server"\nlog:\n  level: "info"\nlisten:\n  addr: ":{port}"\nnetwork:\n  interface: "{iface}"\n  ipv4:\n    addr: "{local_ip}:{port}"\n    router_mac: "{router_mac}"\n  tcp:\n    local_flag: ["PA"]\ntransport:\n  protocol: "kcp"\n  conn: {conn}\n  kcp:\n    key: "{key}"\n    mode: "{mode}"\n    block: "aes-128-gcm"\n    mtu: {mtu}\n'''
    else:
        forwards = "".join(f'  - listen: "0.0.0.0:{p}"\n    target: "127.0.0.1:{p}"\n    protocol: "tcp"\n' for p in ports)
        yaml = f'''role: "client"\nlog:\n  level: "info"\nforward:\n{forwards}network:\n  interface: "{iface}"\n  ipv4:\n    addr: "{local_ip}:0"\n    router_mac: "{router_mac}"\n  tcp:\n    local_flag: ["PA"]\n    remote_flag: ["PA"]\nserver:\n  addr: "{endpoint}:{port}"\ntransport:\n  protocol: "kcp"\n  conn: {conn}\n  kcp:\n    key: "{key}"\n    mode: "{mode}"\n    block: "aes-128-gcm"\n    mtu: {mtu}\n'''
    (directory / "config.yaml").write_text(yaml)
    (directory / "ports.list").write_text("".join(f"{p}\t127.0.0.1:{p}\n" for p in ports))
    meta = {"NAME": name, "ROLE": role, "PORT": port, "KEY": key, "MODE": mode, "BLOCK": "aes-128-gcm", "CONN": conn, "MTU": mtu, "IFACE": iface, "LOCAL_IP": local_ip, "ROUTER_MAC": router_mac, "PEER_IP": endpoint if role == "client" else "", "PUB_IP": endpoint if role == "server" else "", "TRANSPORT": "kcp", "PROFILE": profile, "RESTART_EVERY": restart}
    (directory / "meta.conf").write_text("".join(f'{k}="{v}"\n' for k, v in meta.items()))
    for path in (directory / "config.yaml", directory / "meta.conf"): os.chmod(path, 0o600)
    fw_helper = _write_paqet_firewall_helper()
    Path("/etc/systemd/system/paqetpro@.service").write_text(f"[Unit]\nDescription=DARK Packet Pro Tunnel (%i)\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nExecStartPre={fw_helper} apply %i\nExecStart=/usr/local/bin/paqet run -c /etc/dark-packetpro/tunnels/%i/config.yaml\nExecStopPost={fw_helper} clear %i\nRestart=always\nRestartSec=3\nLimitNOFILE=1048576\n\n[Install]\nWantedBy=multi-user.target\n")
    Path("/etc/systemd/system/paqetpro-restart@.service").write_text("[Unit]\nDescription=DARK Packet Pro scheduled restart (%i)\n\n[Service]\nType=oneshot\nExecStart=/bin/systemctl restart paqetpro@%i.service\n")
    Path("/etc/systemd/system/paqetpro-restart@.timer").write_text("[Unit]\nDescription=DARK Packet Pro restart timer (%i)\n\n[Timer]\nOnUnitActiveSec=1h\nOnBootSec=1h\nUnit=paqetpro-restart@%i.service\n\n[Install]\nWantedBy=timers.target\n")
    timer = f"paqetpro-restart@{name}.timer"; dropin = Path("/etc/systemd/system") / f"{timer}.d"
    if restart == "off": run(["systemctl", "disable", "--now", timer], timeout=15); shutil.rmtree(dropin, ignore_errors=True)
    else:
        dropin.mkdir(parents=True, exist_ok=True); (dropin / "schedule.conf").write_text(f"[Timer]\nOnUnitActiveSec=\nOnBootSec=\nOnUnitActiveSec={restart}\nOnBootSec={restart}\n")
    requested = [(port, "tcp")] if role == "server" else [(p, "tcp") for p in ports]
    firewall_rules: list[str] = []
    if shutil.which("ufw") and run(["ufw", "status"], timeout=15)[0] == 0:
        for value, protocol in requested:
            rule = f"{value}/{protocol}"; code, output = run(["ufw", "allow", rule, "comment", f"DARK-NOC-{name}"], timeout=20)
            if code != 0: raise RuntimeError(f"Could not open UFW rule {rule}: {output[-500:]}")
            if "Rule added" in output: firewall_rules.append(rule)
    (directory / "ufw-created.json").write_text(json.dumps(firewall_rules))
    run(["systemctl", "daemon-reload"], timeout=20)
    service = f"paqetpro@{name}.service"
    try:
        code, output = run(["systemctl", "enable", "--now", service], timeout=30)
        if code != 0: raise RuntimeError(f"Packet Pro service failed: {output[-1200:]}")
        if restart != "off":
            code, output = run(["systemctl", "enable", "--now", timer], timeout=20)
            if code != 0: raise RuntimeError(f"Packet Pro restart timer failed: {output[-700:]}")
    except Exception:
        run(["systemctl", "disable", "--now", timer], timeout=15); run(["systemctl", "disable", "--now", service], timeout=20)
        if shutil.which("ufw"):
            for rule in firewall_rules: run(["ufw", "--force", "delete", "allow", rule], timeout=20)
        shutil.rmtree(directory, ignore_errors=True); raise
    monitor = {"name": name, "method": "DARK Packet Pro", "role": role, "service": service, "listen_port": port if role == "server" else None, "user_ports": ports if role == "client" else [], "target_host": endpoint if role == "client" else "127.0.0.1", "target_port": port, "transport": "kcp", "profile": profile, "restart_every": restart}
    config["tunnels"] = [i for i in config.setdefault("tunnels", []) if not (i.get("name") == name and i.get("method") == "DARK Packet Pro")] + [monitor]
    for k in ("services", "managed_services"):
        if service not in config.setdefault(k, []): config[k].append(service)
    save_json(CONFIG_PATH, config)
    return f"DARK Packet Pro deployed\nrole={role}\ntunnel={name}\nendpoint={endpoint}:{port}\nprofile={profile}\ncore={version}\nservice={service} active"


def _remove_dark_packet(payload: dict[str, Any], config: dict[str, Any]) -> str:
    name = str(payload.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name): raise ValueError("Invalid tunnel name")
    service = f"paqetpro@{name}.service"; timer = f"paqetpro-restart@{name}.timer"; directory = Path("/etc/dark-packetpro/tunnels") / name
    run(["systemctl", "disable", "--now", timer], timeout=20); run(["systemctl", "disable", "--now", service], timeout=30)
    rules = load_json(directory / "ufw-created.json", [])
    if shutil.which("ufw"):
        for rule in rules:
            if re.fullmatch(r"[0-9]{1,5}/(?:tcp|udp)", str(rule)): run(["ufw", "--force", "delete", "allow", str(rule)], timeout=20)
    shutil.rmtree(directory, ignore_errors=True); shutil.rmtree(Path("/etc/systemd/system") / f"{timer}.d", ignore_errors=True); run(["systemctl", "daemon-reload"], timeout=20)
    config["tunnels"] = [i for i in config.get("tunnels", []) if not (i.get("name") == name and i.get("method") == "DARK Packet Pro")]
    for k in ("services", "managed_services"): config[k] = [i for i in config.get(k, []) if i != service]
    save_json(CONFIG_PATH, config); return f"DARK Packet Pro tunnel {name} removed; shared PAQET core retained"


def _deploy_dark_backhaul(payload: dict[str, Any], config: dict[str, Any]) -> str:
    if payload.get("plugin_id") != "dark-backhaul":
        raise ValueError("Plugin is not allowlisted")
    name, role = str(payload.get("name", "")), str(payload.get("role", ""))
    endpoint, token = str(payload.get("endpoint", "")), str(payload.get("token", ""))
    transport, profile = str(payload.get("transport", "")), str(payload.get("profile", ""))
    tunnel_port, ports = int(payload.get("tunnel_port", 0)), [int(p) for p in payload.get("user_ports", [])]
    restart_every = str(payload.get("restart_every", "off"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name) or role not in {"server", "client"}:
        raise ValueError("Invalid tunnel identity or role")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", endpoint) or not re.fullmatch(r"[A-Za-z0-9]{8,128}", token):
        raise ValueError("Invalid endpoint or pairing token")
    if transport not in {"tcp", "tcpmux", "ws", "wsmux", "wss", "wssmux", "udp"} or profile not in {"stable", "balanced", "lowping", "turbo"}:
        raise ValueError("Unsupported transport or profile")
    if not 1 <= tunnel_port <= 65535 or not ports or any(not 1 <= p <= 65535 or p == tunnel_port for p in ports):
        raise ValueError("Invalid tunnel/user ports")
    if restart_every not in {"off", "1h", "6h", "12h", "24h"}:
        raise ValueError("Invalid restart interval")

    tunnel_dir = Path("/etc/dark-backhaul/tunnels") / name
    existing_meta = load_json(tunnel_dir / "deployment.json", {})
    tls_transport = transport in {"wss", "wssmux"}
    cert_path = str(payload.get("certificate_path", ""))
    key_path = str(payload.get("certificate_key_path", ""))
    cert_domain = str(payload.get("certificate_domain", ""))
    if tls_transport and role == "server":
        if not cert_path.startswith("/") or not key_path.startswith("/"):
            raise ValueError("WSS/WSSMUX requires certificate and private-key paths on the IRAN server")
        if not Path(cert_path).is_file() or not Path(key_path).is_file():
            raise ValueError("The selected TLS certificate files do not exist on this Agent")
    identity = {"role": role, "endpoint": endpoint, "tunnel_port": tunnel_port, "user_ports": sorted(set(ports)), "transport": transport, "profile": profile, "restart_every": restart_every, "certificate_domain": cert_domain if tls_transport else "", "certificate_path": cert_path if tls_transport and role == "server" else "", "certificate_key_path": key_path if tls_transport and role == "server" else ""}
    if existing_meta == identity and systemd_status(f"backhaul@{name}.service") == "active":
        return f"DARK Backhaul tunnel {name} is already deployed and active"
    _darkbh_preflight(name, role, endpoint, tunnel_port, ports)
    core_version = _install_darkbh_core()
    base = Path("/etc/dark-backhaul")
    tunnel_dir = base / "tunnels" / name
    tunnel_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    (tunnel_dir / "ports.list").write_text("".join(f"{port}\t-\n" for port in ports) if role == "server" else "")
    p = _profile_values(profile)
    common = [
        f'transport = "{transport}"', f'token = "{token}"', f'keepalive_period = {p["keepalive"]}',
        'nodelay = true', 'sniffer = false', 'web_port = 0', f'sniffer_log = "{tunnel_dir}/sniffer.json"', 'log_level = "info"',
    ]
    if role == "server":
        lines = ["[server]", f'bind_addr = "0.0.0.0:{tunnel_port}"', *common, f'heartbeat = {p["heartbeat"]}', f'channel_size = {p["channel"]}']
        if tls_transport:
            lines += [f'tls_cert = "{cert_path}"', f'tls_key = "{key_path}"']
        if transport.endswith("mux"):
            lines += [f'mux_con = {p["muxcon"]}', 'mux_version = 1', 'mux_framesize = 32768', 'mux_recievebuffer = 4194304', 'mux_streambuffer = 65536']
        lines += ["ports = [", *[f'  "{port}",' for port in ports], "]"]
    else:
        lines = ["[client]", f'remote_addr = "{endpoint}:{tunnel_port}"', *common, f'connection_pool = {p["pool"]}', f'aggressive_pool = {p["aggressive"]}', f'dial_timeout = {p["dial"]}', f'retry_interval = {p["retry"]}']
        if transport.endswith("mux"):
            lines += ['mux_version = 1', 'mux_framesize = 32768', 'mux_recievebuffer = 4194304', 'mux_streambuffer = 65536']
    (tunnel_dir / "config.toml").write_text("\n".join(lines) + "\n")
    meta = {"NAME": name, "ROLE": role, "PORT": tunnel_port, "TOKEN": token, "TRANSPORT": transport, "PROFILE": profile, "PEER_IP": endpoint if role == "client" else "", "PUB_IP": endpoint if role == "server" else "", "TLS_CERT": cert_path if tls_transport and role == "server" else "", "TLS_KEY": key_path if tls_transport and role == "server" else "", "RESTART_EVERY": restart_every}
    (tunnel_dir / "meta.conf").write_text("".join(f'{key}="{value}"\n' for key, value in meta.items()))
    for path in (tunnel_dir / "config.toml", tunnel_dir / "meta.conf"):
        os.chmod(path, 0o600)
    save_json(tunnel_dir / "deployment.json", identity)
    unit = """[Unit]\nDescription=DARK VPN Backhaul Tunnel (%i)\nAfter=network-online.target\nWants=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nType=simple\nExecStart=/usr/local/bin/backhaul -c /etc/dark-backhaul/tunnels/%i/config.toml\nRestart=always\nTimeoutStopSec=10\nKillMode=mixed\nRestartSec=5\nLimitNOFILE=1048576\nStandardOutput=journal\nStandardError=journal\n\n[Install]\nWantedBy=multi-user.target\n"""
    Path("/etc/systemd/system/backhaul@.service").write_text(unit)
    timer_service = "[Unit]\nDescription=DARK Backhaul scheduled restart (%i)\n\n[Service]\nType=oneshot\nExecStart=/bin/systemctl restart backhaul@%i.service\n"
    timer_unit = "[Unit]\nDescription=DARK Backhaul scheduled restart timer (%i)\n\n[Timer]\nOnUnitActiveSec=1h\nOnBootSec=1h\nAccuracySec=1min\nUnit=backhaul-restart@%i.service\n\n[Install]\nWantedBy=timers.target\n"
    Path("/etc/systemd/system/backhaul-restart@.service").write_text(timer_service)
    Path("/etc/systemd/system/backhaul-restart@.timer").write_text(timer_unit)
    override = Path(f"/etc/systemd/system/backhaul-restart@{name}.timer.d")
    if restart_every == "off":
        run(["systemctl", "disable", "--now", f"backhaul-restart@{name}.timer"], timeout=15)
        shutil.rmtree(override, ignore_errors=True)
    else:
        override.mkdir(parents=True, exist_ok=True)
        (override / "interval.conf").write_text(f"[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec={restart_every}\nOnBootSec=\nOnBootSec={restart_every}\n")
    run(["systemctl", "daemon-reload"], timeout=20)
    firewall_rules: list[str] = []
    try:
        firewall_rules = _configure_ufw(tunnel_dir, name, role, transport, tunnel_port, ports)
        code, output = run(["systemctl", "enable", "--now", f"backhaul@{name}.service"], timeout=30)
        if code != 0:
            raise RuntimeError(f"Service start failed: {output[-1500:]}")
        if restart_every != "off":
            code, output = run(["systemctl", "enable", "--now", f"backhaul-restart@{name}.timer"], timeout=20)
            if code != 0:
                raise RuntimeError(f"Restart timer failed: {output[-1000:]}")
    except Exception:
        run(["systemctl", "disable", "--now", f"backhaul-restart@{name}.timer"], timeout=15)
        run(["systemctl", "disable", "--now", f"backhaul@{name}.service"], timeout=20)
        if shutil.which("ufw"):
            for rule in firewall_rules:
                run(["ufw", "--force", "delete", "allow", rule], timeout=20)
        shutil.rmtree(tunnel_dir, ignore_errors=True)
        raise
    service = f"backhaul@{name}.service"
    monitor = {"name": name, "method": "DARK Backhaul", "role": role, "service": service, "listen_port": tunnel_port if role == "server" else None, "user_ports": ports if role == "server" else [], "target_host": "127.0.0.1" if role == "server" else endpoint, "target_port": tunnel_port}
    config["tunnels"] = [item for item in config.setdefault("tunnels", []) if item.get("name") != name] + [monitor]
    for key in ("services", "managed_services"):
        if service not in config.setdefault(key, []):
            config[key].append(service)
    save_json(CONFIG_PATH, config)
    return f"DARK Backhaul deployed\nrole={role}\ntunnel={name}\nendpoint={endpoint}:{tunnel_port}\ntransport={transport}\nprofile={profile}\ncore={core_version}\nservice={service} active\nufw_rules={','.join(firewall_rules) if firewall_rules else 'not-needed'}"


def _remove_dark_backhaul(payload: dict[str, Any], config: dict[str, Any]) -> str:
    if payload.get("plugin_id") != "dark-backhaul":
        raise ValueError("Plugin is not allowlisted")
    name = str(payload.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name):
        raise ValueError("Invalid tunnel name")
    service = f"backhaul@{name}.service"
    timer = f"backhaul-restart@{name}.timer"
    run(["systemctl", "disable", "--now", timer], timeout=20)
    run(["systemctl", "disable", "--now", service], timeout=30)
    run(["systemctl", "disable", "--now", f"ghostpro-restart@{name}.timer"], timeout=20)
    tunnel_dir = Path("/etc/dark-backhaul/tunnels") / name
    firewall_file = tunnel_dir / "ufw-created.json"
    if firewall_file.exists() and shutil.which("ufw"):
        try:
            for rule in json.loads(firewall_file.read_text()):
                if re.fullmatch(r"[0-9]{1,5}/(?:tcp|udp)", str(rule)):
                    run(["ufw", "--force", "delete", "allow", str(rule)], timeout=20)
        except (OSError, ValueError, TypeError):
            pass
    if tunnel_dir.parent == Path("/etc/dark-backhaul/tunnels"):
        shutil.rmtree(tunnel_dir, ignore_errors=True)
    shutil.rmtree(Path("/etc/systemd/system") / f"backhaul-restart@{name}.timer.d", ignore_errors=True)
    run(["systemctl", "daemon-reload"], timeout=20)
    config["tunnels"] = [item for item in config.get("tunnels", []) if item.get("name") != name]
    for key in ("services", "managed_services"):
        config[key] = [item for item in config.get(key, []) if item != service]
    save_json(CONFIG_PATH, config)
    return f"DARK Backhaul tunnel {name} removed from this node; shared core retained"


def _remove_dark_ghost(payload: dict[str, Any], config: dict[str, Any]) -> str:
    name = str(payload.get("name", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name): raise ValueError("Invalid tunnel name")
    service = f"ghostpro@{name}.service"
    run(["systemctl", "disable", "--now", service], timeout=30)
    directory = Path("/etc/dark-ghostpro/tunnels") / name
    if directory.parent == Path("/etc/dark-ghostpro/tunnels"): shutil.rmtree(directory, ignore_errors=True)
    shutil.rmtree(Path("/etc/systemd/system") / f"ghostpro-restart@{name}.timer.d", ignore_errors=True)
    config["tunnels"] = [i for i in config.get("tunnels", []) if not (i.get("name") == name and i.get("method") == "DARK Ghost Pro")]
    for key in ("services", "managed_services"): config[key] = [i for i in config.get(key, []) if i != service]
    save_json(CONFIG_PATH, config); return f"DARK Ghost Pro tunnel {name} removed; shared GOST core retained"


def execute_job(job: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    kind = str(job.get("kind", ""))
    payload = json.loads(job.get("payload") or "{}")
    if kind not in ALLOWED_JOB_KINDS:
        return "failed", "Job kind is not allowed"
    if kind == "diagnostics":
        commands = [
            ["systemctl", "--failed", "--no-pager"],
            ["ss", "-s"],
            ["ip", "route"],
            ["df", "-h", "/"],
        ]
        output = []
        for command in commands:
            code, text = run(command)
            output.append(f"$ {shlex.join(command)}\n{text.strip()}\n[exit={code}]")
        return "completed", "\n\n".join(output)
    if kind == "tunnel_test":
        tunnels = monitored_tunnels(config)
        if not tunnels:
            return "completed", "No tunnels are configured on this Agent"
        reports = asyncio.run(_collect_tunnel_reports(tunnels))
        return "completed", json.dumps(reports, indent=2)
    if kind == "certificate_issue":
        try: return "completed", _issue_certificate(payload)
        except Exception as exc: return "failed", str(exc)
    if kind == "monitor_run":
        try:
            return "completed", json.dumps(execute_monitor(payload), separators=(",", ":"))
        except Exception as exc:
            return "failed", json.dumps({
                "monitor_id": int(payload.get("monitor_id", 0) or 0),
                "status": "down", "latency_ms": None,
                "detail": {"error": str(exc)[:1000]}, "checked_at": int(time.time()),
            }, separators=(",", ":"))
    if kind == "logs":
        service = str(payload.get("service") or "dark-noc-agent.service")
        if service != "dark-noc-agent.service" and not restart_allowed(service, config):
            return "failed", "Service is not in managed_services allowlist"
        code, output = run(["journalctl", "-u", service, "-n", "200", "--no-pager", "-o", "short-iso"], timeout=15)
        return ("completed" if code == 0 else "failed"), output
    if kind == "plugin_deploy":
        try:
            if payload.get("plugin_id") == "dark-realm": return "completed", deploy_dark_realm(payload, config, CONFIG_PATH)
            if payload.get("plugin_id") == "dark-ghostpro": return "completed", _deploy_dark_ghost(payload, config)
            if payload.get("plugin_id") == "dark-packetpro": return "completed", _deploy_dark_packet(payload, config)
            return "completed", _deploy_dark_backhaul(payload, config)
        except Exception as exc:
            return "failed", f"Plugin deployment failed: {exc}"
    if kind == "plugin_install":
        try:
            if payload.get("plugin_id") == "dark-realm": return "completed", f"DARK Realm Pro core ready: {install_dark_realm()}"
            if payload.get("plugin_id") == "dark-ghostpro": return "completed", f"DARK Ghost Pro core ready: {_install_gost_core()}"
            if payload.get("plugin_id") == "dark-packetpro": return "completed", f"DARK Packet Pro core ready: {_install_paqet_core()}"
            return "completed", f"DARK Backhaul core ready: {_install_darkbh_core()}"
        except Exception as exc:
            return "failed", f"Plugin installation failed: {exc}"
    if kind == "plugin_remove":
        try:
            if payload.get("plugin_id") == "dark-realm": return "completed", remove_dark_realm(payload, config, CONFIG_PATH)
            if payload.get("plugin_id") == "dark-ghostpro": return "completed", _remove_dark_ghost(payload, config)
            if payload.get("plugin_id") == "dark-packetpro": return "completed", _remove_dark_packet(payload, config)
            return "completed", _remove_dark_backhaul(payload, config)
        except Exception as exc:
            return "failed", f"Plugin removal failed: {exc}"
    if kind == "tunnel_control":
        name, action = str(payload.get("name", "")), str(payload.get("action", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name) or action not in {"start", "stop", "restart"}:
            return "failed", "Invalid tunnel control request"
        plugin_id = str(payload.get("plugin_id", "dark-backhaul"))
        service = f"dark-realm@{name}.service" if plugin_id == "dark-realm" else f"ghostpro@{name}.service" if plugin_id == "dark-ghostpro" else f"paqetpro@{name}.service" if plugin_id == "dark-packetpro" else f"backhaul@{name}.service"
        if not restart_allowed(service, config):
            return "failed", "Tunnel is not managed by DARK NOC"
        code, output = run(["systemctl", action, service], timeout=30)
        return ("completed" if code == 0 else "failed"), output or f"{service} {action} completed"
    if kind == "speed_test":
        target = config.get("speedtest", {})
        host, port = str(target.get("host", "")), int(target.get("port", 5201))
        if not host:
            return "failed", "Configure speedtest.host and speedtest.port in the Agent config"
        code, output = run(["iperf3", "-c", host, "-p", str(port), "-J", "-t", "5"], timeout=15)
        return ("completed" if code == 0 else "failed"), output
    if kind == "configure_autoheal":
        try:
            enabled = bool(payload.get("enabled"))
            cooldown = int(payload.get("cooldown_seconds", 300))
            maximum = int(payload.get("max_restarts_per_hour", 3))
            if not 60 <= cooldown <= 86400 or not 1 <= maximum <= 12:
                raise ValueError
        except (TypeError, ValueError):
            return "failed", "Invalid Auto-Heal policy"
        config["autoheal"] = {"enabled": enabled, "cooldown_seconds": cooldown, "max_restarts_per_hour": maximum}
        save_json(CONFIG_PATH, config)
        return "completed", f"Auto-Heal {'enabled' if enabled else 'disabled'}; cooldown={cooldown}s; hourly_limit={maximum}"
    service = str(payload.get("service", ""))
    if not service or not restart_allowed(service, config):
        return "failed", "Service is not in managed_services allowlist"
    if kind == "restart_service":
        code, output = run(["systemctl", "restart", service])
        return ("completed" if code == 0 else "failed"), output or f"{service} restarted"
    if kind == "service_status":
        code, output = run(["systemctl", "status", service, "--no-pager", "-l"])
        return ("completed" if code in {0, 3} else "failed"), output
    return "failed", "Unsupported job"


async def maybe_autoheal(config: dict[str, Any], reports: list[dict[str, Any]], state: dict[str, Any]) -> None:
    policy = config.get("autoheal", {})
    if not policy.get("enabled", False):
        return
    cooldown = max(int(policy.get("cooldown_seconds", 300)), 60)
    maximum = min(max(int(policy.get("max_restarts_per_hour", 3)), 1), 6)
    history = [float(ts) for ts in state.get("restart_history", []) if time.time() - float(ts) < 3600]
    for report in reports:
        if report["status"] != "down" or not report.get("service"):
            continue
        service = report["service"]
        last = float(state.get("last_restart", {}).get(service, 0))
        if not restart_allowed(service, config) or time.time() - last < cooldown or len(history) >= maximum:
            continue
        code, _ = run(["systemctl", "restart", service])
        if code == 0:
            now = time.time()
            state.setdefault("last_restart", {})[service] = now
            history.append(now)
    state["restart_history"] = history


async def process_jobs(jobs: list[dict[str, Any]], config: dict[str, Any], client: httpx.AsyncClient, hub: str) -> None:
    for job in jobs:
        execution = asyncio.create_task(asyncio.to_thread(execute_job, job, config))
        while not execution.done():
            done, _ = await asyncio.wait({execution}, timeout=60)
            if done:
                break
            lease = await client.post(f"{hub}/api/agent/jobs/{job['id']}/lease")
            lease.raise_for_status()
        status, output = await execution
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = await client.post(f"{hub}/api/agent/jobs/result", json={"job_id": job["id"], "status": status, "output": output})
                response.raise_for_status()
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(min(2 ** attempt, 15))
        if last_error:
            raise last_error


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_json(CONFIG_PATH, {})
    if not isinstance(config, dict):
        raise SystemExit(f"Invalid JSON object in {CONFIG_PATH}")
    required = ["hub_url", "agent_token"]
    if any(not config.get(key) for key in required):
        raise SystemExit(f"Missing required keys in {CONFIG_PATH}: {', '.join(required)}")
    hub = str(config["hub_url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {config['agent_token']}"}
    verify_value = config.get("verify_tls", True)
    verify_tls: bool | str = verify_value if isinstance(verify_value, (bool, str)) else True
    interval = min(max(int(config.get("interval_seconds", 15)), 5), 60)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    timeout = httpx.Timeout(12, connect=5)
    cached_payload = clone_json(state.get("last_payload"), None)
    cached_metrics = cached_payload.get("metrics", {}) if isinstance(cached_payload, dict) else {}
    if (
        isinstance(cached_payload, dict)
        and isinstance(cached_metrics, dict)
        and cached_metrics.get("inventory_complete") is True
        and isinstance(cached_payload.get("services"), list)
        and isinstance(cached_payload.get("tunnels"), list)
        and isinstance(cached_payload.get("plugins"), dict)
    ):
        latest_payload = cached_payload
        latest_telemetry_at = float(
            state.get("last_telemetry_success")
            or cached_metrics.get("inventory_snapshot_at")
            or 0
        )
    else:
        latest_payload = minimal_heartbeat_payload(config)
        latest_telemetry_at = 0.0
    telemetry_task: asyncio.Task | None = None
    telemetry_started_at = 0.0
    job_task: asyncio.Task | None = None
    last_recovery_attempt = 0.0
    consecutive_failures = 0
    pulse_supported = True

    async with httpx.AsyncClient(headers=headers, verify=verify_tls, timeout=timeout) as client:
        systemd_notify("READY=1\nSTATUS=DARK NOC Agent heartbeat loop started")
        while True:
            loop_started = time.monotonic()

            if telemetry_task is None:
                telemetry_started_at = time.monotonic()
                telemetry_task = asyncio.create_task(
                    asyncio.to_thread(
                        collect_payload_blocking,
                        clone_json(config, {}),
                        clone_json(state, {}),
                    )
                )
            elif telemetry_task.done():
                completed_task = telemetry_task
                telemetry_task = None
                try:
                    candidate_payload, state_updates = completed_task.result()
                    state.update(state_updates)
                    latest_payload = candidate_payload
                    state["last_telemetry_attempt"] = time.time()
                    candidate_metrics = latest_payload.setdefault("metrics", {})
                    if candidate_metrics.get("inventory_complete") is True:
                        latest_telemetry_at = time.time()
                        candidate_metrics["inventory_snapshot_at"] = int(latest_telemetry_at)
                        state["last_telemetry_success"] = latest_telemetry_at
                        state["last_payload"] = clone_json(latest_payload, {})
                        state.pop("last_telemetry_incomplete", None)
                    else:
                        state["last_telemetry_incomplete"] = state["last_telemetry_attempt"]
                    state.pop("last_telemetry_error", None)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"[:1000]
                    state["last_telemetry_error"] = message
                    LOGGER.exception("Full telemetry collection failed; liveness heartbeat continues")

            heartbeat_payload = clone_json(latest_payload, minimal_heartbeat_payload(config))
            metrics = heartbeat_payload.setdefault("metrics", {})
            inventory_complete = metrics.get("inventory_complete") is True
            snapshot_at = float(metrics.get("inventory_snapshot_at") or latest_telemetry_at or 0)
            telemetry_age = int(max(0, time.time() - snapshot_at)) if snapshot_at else 0
            metrics["inventory_complete"] = inventory_complete
            metrics["inventory_snapshot_at"] = int(snapshot_at) if inventory_complete else 0
            metrics["telemetry_age_seconds"] = telemetry_age
            metrics["telemetry_status"] = (
                "fresh" if inventory_complete and snapshot_at and telemetry_age <= 90
                else "collecting" if telemetry_task is not None and time.monotonic() - telemetry_started_at <= 120
                else "stale"
            )
            metrics["agent_loop_ts"] = int(time.time())
            if state.get("last_telemetry_error"):
                metrics["telemetry_error"] = str(state["last_telemetry_error"])[:500]

            pulse_ok = False
            heartbeat_ok = False
            pulse_error: str | None = None
            inventory_error: str | None = None
            pulse_payload = {
                "agent_version": VERSION,
                "agent_loop_ts": int(metrics.get("agent_loop_ts") or time.time()),
                "telemetry_status": str(metrics.get("telemetry_status") or "unknown")[:32],
                "telemetry_age_seconds": max(0, int(metrics.get("telemetry_age_seconds") or 0)),
                "inventory_error": str(state.get("last_inventory_error") or "")[:1000] or None,
            }

            if pulse_supported:
                try:
                    pulse_response = await client.post(
                        f"{hub}/api/agent/pulse", json=pulse_payload, timeout=6
                    )
                    if pulse_response.status_code == 404:
                        # Rolling upgrades may briefly run a new Agent against an
                        # older Hub. Fall back to the compatible heartbeat route.
                        pulse_supported = False
                    else:
                        if (
                            pulse_response.status_code == 401
                            and time.monotonic() - last_recovery_attempt >= 30
                        ):
                            last_recovery_attempt = time.monotonic()
                            if await recover_local_enrollment(client, hub, config):
                                pulse_response = await client.post(
                                    f"{hub}/api/agent/pulse", json=pulse_payload, timeout=6
                                )
                        pulse_response.raise_for_status()
                        pulse_ok = True
                        state["last_pulse_success"] = time.time()
                        state["last_pulse_status"] = pulse_response.status_code
                        state.pop("last_pulse_error", None)
                except Exception as exc:
                    pulse_error = f"{type(exc).__name__}: {exc}"[:1000]
                    state["last_pulse_error"] = pulse_error

            try:
                response = await client.post(
                    f"{hub}/api/agent/heartbeat", json=heartbeat_payload, timeout=10
                )
                if (
                    response.status_code == 401
                    and time.monotonic() - last_recovery_attempt >= 30
                ):
                    last_recovery_attempt = time.monotonic()
                    if await recover_local_enrollment(client, hub, config):
                        response = await client.post(
                            f"{hub}/api/agent/heartbeat", json=heartbeat_payload, timeout=10
                        )
                response.raise_for_status()
                heartbeat_ok = True
                state["last_inventory_success"] = time.time()
                state["last_heartbeat_status"] = response.status_code
                state.pop("last_inventory_error", None)
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[-700:] if exc.response is not None else ""
                status_code = exc.response.status_code if exc.response is not None else 0
                inventory_error = (f"HTTP {status_code}: {body or str(exc)}")[:1000]
                state["last_inventory_error"] = inventory_error
                if pulse_ok and status_code in {400, 413, 422}:
                    # A cached report from an older schema must not poison every
                    # future heartbeat. Keep the pulse alive and rebuild inventory.
                    state.pop("last_payload", None)
                    state["last_payload_quarantined_at"] = time.time()
                    latest_payload = minimal_heartbeat_payload(config)
                    latest_telemetry_at = 0.0
                    LOGGER.warning(
                        "Inventory report rejected by Hub; cached snapshot quarantined while pulse remains online: %s",
                        inventory_error,
                    )
            except Exception as exc:
                inventory_error = f"{type(exc).__name__}: {exc}"[:1000]
                state["last_inventory_error"] = inventory_error

            liveness_ok = pulse_ok or heartbeat_ok
            if liveness_ok:
                consecutive_failures = 0
                state["last_success"] = time.time()
                state.pop("last_error", None)
            else:
                consecutive_failures += 1
                message = pulse_error or inventory_error or "Agent liveness request failed"
                state["last_error"] = message
                state["consecutive_heartbeat_failures"] = consecutive_failures
                if consecutive_failures == 1 or consecutive_failures % 4 == 0:
                    LOGGER.warning(
                        "Agent liveness failed (%s consecutive): %s",
                        consecutive_failures,
                        message,
                    )

            try:
                if job_task is None or job_task.done():
                    if job_task is not None:
                        try:
                            job_task.result()
                            state.pop("last_job_error", None)
                        except Exception as exc:
                            state["last_job_error"] = f"{type(exc).__name__}: {exc}"[:1000]
                            LOGGER.exception("Agent job worker failed")
                    jobs_response = await client.get(f"{hub}/api/agent/jobs", timeout=10)
                    jobs_response.raise_for_status()
                    jobs = jobs_response.json()
                    if jobs:
                        job_task = asyncio.create_task(process_jobs(jobs, config, client, hub))
            except Exception as exc:
                state["last_job_error"] = f"{type(exc).__name__}: {exc}"[:1000]
                LOGGER.warning("Job polling failed while heartbeat remained independent: %s", exc)

            try:
                save_json(STATE_PATH, state)
            except Exception:
                LOGGER.exception("Could not persist Agent state; heartbeat loop continues")

            status = (
                "Pulse OK" if pulse_ok
                else "Heartbeat OK" if heartbeat_ok
                else f"Liveness failures: {consecutive_failures}"
            )
            if liveness_ok and inventory_error:
                status += " · inventory retrying"
            systemd_notify(f"WATCHDOG=1\nSTATUS={status}")
            elapsed = time.monotonic() - loop_started
            await asyncio.sleep(max(1.0, interval - elapsed))

if __name__ == "__main__":
    asyncio.run(main())
