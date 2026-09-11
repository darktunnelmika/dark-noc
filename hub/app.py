from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import posixpath
import secrets
import shlex
import socket
import sqlite3
import stat as statmod
import threading
import time
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncssh
import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DARK_NOC_DATA", "/var/lib/dark-noc"))
DB_PATH = DATA_DIR / "dark-noc.db"
KEY_PATH = DATA_DIR / "master.key"
STATIC_DIR = ROOT / "static"
SESSION_TTL = 12 * 60 * 60
NODE_STALE_AFTER = 120
LOGIN_FAILURES: dict[str, list[int]] = {}
LOGIN_LOCK = threading.Lock()
VERSION = "2.7.0"
LIVE_CLIENTS: set[WebSocket] = set()
LOGGER = logging.getLogger("dark-noc")


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def configured_public_hub_url() -> str:
    host = os.getenv("DARK_NOC_PUBLIC_HOST", "").strip()
    if not host:
        raise RuntimeError("Hub public address is not configured")
    try:
        port = int(os.getenv("DARK_NOC_PUBLIC_PORT", "443"))
    except ValueError as exc:
        raise RuntimeError("Hub public port is invalid") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("Hub public port is invalid")
    url_host = f"[{host}]" if ":" in host else host
    suffix = "" if port == 443 else f":{port}"
    return f"https://{url_host}{suffix}"


SSH_UPLOAD_LIMIT = bounded_env_int("DARK_NOC_SSH_UPLOAD_LIMIT_MB", 1024, 1, 102_400) * 1024 * 1024
SSH_RELAY_LIMIT = bounded_env_int("DARK_NOC_SSH_RELAY_LIMIT_MB", 20_480, 1, 102_400) * 1024 * 1024
SSH_FILE_CHUNK = 1024 * 1024
SSH_MULTIPART_OVERHEAD = 1024 * 1024
SSH_TRANSFER_CONCURRENCY = bounded_env_int("DARK_NOC_SSH_TRANSFER_CONCURRENCY", 4, 1, 32)
SSH_TRANSFER_TIMEOUT = bounded_env_int("DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS", 86_400, 30, 86_400)
SSH_TRANSFER_IDLE_TIMEOUT = bounded_env_int("DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS", 60, 5, 3600)
SSH_KEEPALIVE_INTERVAL = bounded_env_int("DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS", 15, 1, 300)
SSH_KEEPALIVE_COUNT_MAX = bounded_env_int("DARK_NOC_SSH_KEEPALIVE_COUNT_MAX", 3, 1, 20)
SSH_UPLOAD_QUEUE_TIMEOUT = bounded_env_int("DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS", 30, 1, 300)
NODE_PROVISION_TIMEOUT = bounded_env_int("DARK_NOC_PROVISION_TIMEOUT_SECONDS", 1800, 60, 7200)
NODE_PROVISION_CONCURRENCY = bounded_env_int("DARK_NOC_PROVISION_CONCURRENCY", 4, 1, 32)
MONITOR_RESULT_RETENTION_DAYS = bounded_env_int("DARK_NOC_MONITOR_RETENTION_DAYS", 90, 7, 730)
METRIC_RAW_RETENTION_DAYS = bounded_env_int("DARK_NOC_METRIC_RETENTION_DAYS", 31, 1, 365)
METRIC_ROLLUP_RETENTION_DAYS = bounded_env_int("DARK_NOC_ROLLUP_RETENTION_DAYS", 730, 30, 3650)
HUB_LEASE_SECONDS = bounded_env_int("DARK_NOC_HUB_LEASE_SECONDS", 75, 30, 300)
HUB_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
SSH_EDITOR_LIMIT = bounded_env_int("DARK_NOC_SSH_EDITOR_LIMIT_KB", 1024, 16, 16_384) * 1024
SSH_TRANSFER_SEMAPHORE = asyncio.Semaphore(SSH_TRANSFER_CONCURRENCY)
SSH_UPLOAD_REQUEST_SEMAPHORE = asyncio.Semaphore(SSH_TRANSFER_CONCURRENCY)
NODE_PROVISION_SEMAPHORE = asyncio.Semaphore(NODE_PROVISION_CONCURRENCY)
PROVISION_ENDPOINT_LOCKS: weakref.WeakValueDictionary[tuple[str, int], asyncio.Lock] = weakref.WeakValueDictionary()
SSH_DESTINATION_LOCKS: weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock] = weakref.WeakValueDictionary()


@asynccontextmanager
async def disconnect_aware_semaphore(request: Request, semaphore: asyncio.Lock | asyncio.Semaphore):
    """Acquire a request slot without retaining it after the client leaves."""
    waiter = asyncio.create_task(semaphore.acquire())
    acquired = False
    try:
        while not waiter.done():
            done, _ = await asyncio.wait({waiter}, timeout=0.25)
            if waiter in done:
                break
            if await request.is_disconnected():
                raise HTTPException(499, "Client disconnected")
        await waiter
        acquired = True
        yield
    finally:
        if acquired:
            semaphore.release()
        else:
            if not waiter.done():
                waiter.cancel()
            outcome = await asyncio.gather(waiter, return_exceptions=True)
            # The acquire may have won the race with cancellation. Return that
            # slot exactly once instead of leaking capacity permanently.
            if outcome and outcome[0] is True:
                semaphore.release()


@asynccontextmanager
async def bounded_semaphore(semaphore: asyncio.Semaphore):
    """Bound pre-body work without probing and consuming the ASGI receive channel."""
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=SSH_UPLOAD_QUEUE_TIMEOUT)
    except TimeoutError as exc:
        raise HTTPException(503, "Secure upload queue is busy; retry shortly") from exc
    try:
        yield
    finally:
        semaphore.release()


def utc_ts() -> int:
    return int(time.time())


def normalize_ip(value: Any) -> str:
    text = str(value or "").strip()
    try:
        address = ipaddress.ip_address(text)
        return str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address)
    except ValueError:
        return text.removeprefix("::ffff:")


def canonical_node_host(value: Any) -> str:
    text = str(value or "").strip()
    try:
        address = ipaddress.ip_address(text)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return address.compressed.lower()
    except ValueError:
        return text.rstrip(".").lower()


def node_endpoint_conflict(conn: sqlite3.Connection, host: str, ssh_port: int, exclude_id: int | None = None) -> sqlite3.Row | None:
    canonical_host = canonical_node_host(host)
    rows = conn.execute("SELECT id,name,host FROM nodes WHERE ssh_port=?", (ssh_port,)).fetchall()
    return next(
        (
            row for row in rows
            if row["id"] != exclude_id and canonical_node_host(row["host"]) == canonical_host
        ),
        None,
    )


def provision_endpoint_lock(host: str, ssh_port: int) -> asyncio.Lock:
    key = (canonical_node_host(host), int(ssh_port))
    lock = PROVISION_ENDPOINT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        PROVISION_ENDPOINT_LOCKS[key] = lock
    return lock


def ssh_destination_lock(node_id: int, destination_path: str) -> asyncio.Lock:
    key = (int(node_id), clean_remote_path(destination_path))
    lock = SSH_DESTINATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        SSH_DESTINATION_LOCKS[key] = lock
    return lock


def clean_remote_path(value: str, *, filename: str | None = None) -> str:
    raw = str(value or "")
    if raw != raw.strip() or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in raw):
        raise ValueError("Remote path cannot contain control or leading/trailing whitespace")
    if not raw or len(raw.encode("utf-8")) > 4096 or not raw.startswith("/"):
        raise ValueError("Remote path must be an absolute Linux path")
    if raw.endswith("/"):
        safe_name = posixpath.basename(str(filename or "").replace("\\", "/"))
        if (
            not safe_name
            or safe_name in {".", ".."}
            or safe_name != safe_name.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in safe_name)
        ):
            raise ValueError("A valid file name is required when destination is a directory")
        raw = posixpath.join(raw, safe_name)
    if len(raw.encode("utf-8")) > 4096 or raw != raw.strip() or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in raw):
        raise ValueError("Remote path cannot contain control or leading/trailing whitespace")
    normalized = posixpath.normpath(raw)
    if normalized == "/":
        raise ValueError("Remote path must point to a file")
    return normalized


def clean_remote_directory(value: str) -> str:
    raw = str(value or "")
    if raw != raw.strip() or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in raw):
        raise ValueError("Remote directory cannot contain control or leading/trailing whitespace")
    if not raw or len(raw.encode("utf-8")) > 4096 or not raw.startswith("/"):
        raise ValueError("Remote directory must be an absolute Linux path")
    return posixpath.normpath(raw)


def destructive_remote_path_allowed(path: str) -> bool:
    protected_roots = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/opt", "/proc", "/root", "/run", "/sbin", "/sys", "/usr", "/var"}
    protected_trees = (
        "/bin", "/boot", "/dev", "/lib", "/lib64", "/proc", "/run", "/sbin", "/sys", "/usr",
        "/opt/dark-noc", "/var/lib/dark-noc", "/etc/dark-noc",
    )
    protected_files = {
        "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow", "/etc/sudoers",
        "/etc/fstab", "/etc/crypttab", "/etc/hostname", "/etc/hosts",
    }
    if path in protected_roots or path in protected_files:
        return False
    return not any(path == prefix or path.startswith(f"{prefix}/") for prefix in protected_trees)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$", 2)
        actual = hash_password(password, base64.urlsafe_b64decode(salt)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return app.state.fernet.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return app.state.fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(500, "Stored credential cannot be decrypted")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL, ip TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, region TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'edge', host TEXT NOT NULL, ssh_port INTEGER NOT NULL DEFAULT 22,
  ssh_user TEXT NOT NULL DEFAULT 'root', ssh_password_enc TEXT, ssh_key_enc TEXT,
  agent_token_hash TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'pending', agent_version TEXT,
  observed_ip TEXT, plugin_inventory TEXT NOT NULL DEFAULT '{}',
  provision_status TEXT, provision_output TEXT,
  ssh_host_fingerprint TEXT, autoheal_enabled INTEGER NOT NULL DEFAULT 0,
  autoheal_cooldown INTEGER NOT NULL DEFAULT 300, autoheal_max_restarts INTEGER NOT NULL DEFAULT 3,
  last_seen INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, cpu REAL, ram REAL, swap REAL, disk REAL, load1 REAL,
  rx_bps REAL, tx_bps REAL, uptime INTEGER, connections INTEGER, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_node_ts ON metrics(node_id, ts DESC);
CREATE TABLE IF NOT EXISTS node_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, status TEXT NOT NULL, last_check INTEGER NOT NULL, UNIQUE(node_id,name)
);
CREATE TABLE IF NOT EXISTS tunnels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, method TEXT, target TEXT, service TEXT, listen_port INTEGER,
  status TEXT, latency_ms REAL, packet_loss REAL, sessions INTEGER, rx_bps REAL, tx_bps REAL,
  last_check INTEGER, details TEXT, failure_streak INTEGER NOT NULL DEFAULT 0, UNIQUE(node_id, name)
);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
  tunnel_id INTEGER REFERENCES tunnels(id) ON DELETE SET NULL, severity TEXT NOT NULL,
  title TEXT NOT NULL, detail TEXT, status TEXT NOT NULL DEFAULT 'open', opened_at INTEGER NOT NULL,
  acknowledged_at INTEGER, resolved_at INTEGER, last_changed_at INTEGER,
  root_cause TEXT, resolution TEXT, autoheal_stage INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  output TEXT, created_by INTEGER, created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS plugin_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plugin_id TEXT NOT NULL, name TEXT NOT NULL,
  iran_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kharej_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  iran_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  kharej_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  settings TEXT NOT NULL, pair_token_enc TEXT, lifecycle TEXT NOT NULL DEFAULT 'active', created_by INTEGER, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plugin_id TEXT NOT NULL, name TEXT NOT NULL,
  iran_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  iran_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  iran_endpoint TEXT NOT NULL, remote_label TEXT NOT NULL DEFAULT 'KHAREJ',
  settings TEXT NOT NULL, pair_token_enc TEXT NOT NULL, pair_code_hash TEXT NOT NULL,
  lifecycle TEXT NOT NULL DEFAULT 'active', created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS certificates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', issuer TEXT NOT NULL DEFAULT 'letsencrypt',
  cert_path TEXT, key_path TEXT, expires_at INTEGER, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  last_error TEXT, created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(node_id,domain)
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
  target TEXT, detail TEXT, ip TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS monitors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, port INTEGER,
  secret_enc TEXT, snmp_oid TEXT,
  interval_seconds INTEGER NOT NULL DEFAULT 60, timeout_seconds INTEGER NOT NULL DEFAULT 5,
  expected_status INTEGER, enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
  latency_ms REAL, detail TEXT, failure_streak INTEGER NOT NULL DEFAULT 0,
  next_run_at INTEGER NOT NULL, last_run_at INTEGER, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(node_id,name)
);
CREATE TABLE IF NOT EXISTS monitor_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, status TEXT NOT NULL, latency_ms REAL, detail TEXT
);
CREATE TABLE IF NOT EXISTS fleet_operations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'scheduled',
  scheduled_at INTEGER NOT NULL, created_by INTEGER, created_at INTEGER NOT NULL,
  started_at INTEGER, finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS fleet_operation_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id INTEGER NOT NULL REFERENCES fleet_operations(id) ON DELETE CASCADE,
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL, status TEXT NOT NULL DEFAULT 'scheduled',
  UNIQUE(operation_id,node_id)
);
CREATE TABLE IF NOT EXISTS hub_leases (
  name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_rollups (
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, bucket INTEGER NOT NULL,
  samples INTEGER NOT NULL, cpu_avg REAL, ram_avg REAL, disk_max REAL, load_avg REAL,
  rx_avg REAL, tx_avg REAL, connections_max INTEGER,
  PRIMARY KEY(node_id,bucket)
);
CREATE INDEX IF NOT EXISTS idx_jobs_node_status ON jobs(node_id,status,id);
CREATE INDEX IF NOT EXISTS idx_incidents_status_opened ON incidents(status,opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_tunnels_node ON tunnels(node_id);
CREATE INDEX IF NOT EXISTS idx_hybrid_node ON hybrid_deployments(iran_node_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_certificates_node ON certificates(node_id,status);
CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id,created_at,id);
CREATE INDEX IF NOT EXISTS idx_monitors_due ON monitors(enabled,next_run_at);
CREATE INDEX IF NOT EXISTS idx_monitor_results_monitor_ts ON monitor_results(monitor_id,ts DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_operations_due ON fleet_operations(status,scheduled_at);
CREATE INDEX IF NOT EXISTS idx_metric_rollups_bucket ON metric_rollups(bucket);
"""


def bootstrap() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        os.chmod(KEY_PATH, 0o600)
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        deployment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(plugin_deployments)").fetchall()}
        if "pair_token_enc" not in deployment_columns:
            conn.execute("ALTER TABLE plugin_deployments ADD COLUMN pair_token_enc TEXT")
        if "lifecycle" not in deployment_columns:
            conn.execute("ALTER TABLE plugin_deployments ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'active'")
        tunnel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tunnels)").fetchall()}
        if "failure_streak" not in tunnel_columns:
            conn.execute("ALTER TABLE tunnels ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0")
        node_columns = {row["name"] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "ssh_host_fingerprint" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN ssh_host_fingerprint TEXT")
        if "observed_ip" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN observed_ip TEXT")
        if "plugin_inventory" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN plugin_inventory TEXT NOT NULL DEFAULT '{}'")
        if "provision_status" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN provision_status TEXT")
        if "provision_output" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN provision_output TEXT")
        if "autoheal_enabled" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_enabled INTEGER NOT NULL DEFAULT 0")
        if "autoheal_cooldown" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_cooldown INTEGER NOT NULL DEFAULT 300")
        if "autoheal_max_restarts" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_max_restarts INTEGER NOT NULL DEFAULT 3")
        incident_columns = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)").fetchall()}
        for name, definition in {
            "acknowledged_at": "INTEGER",
            "last_changed_at": "INTEGER",
            "root_cause": "TEXT",
            "resolution": "TEXT",
        }.items():
            if name not in incident_columns:
                conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
        conn.execute("UPDATE incidents SET last_changed_at=COALESCE(last_changed_at,resolved_at,opened_at)")
        conn.execute(
            """INSERT INTO incident_events(incident_id,event_type,message,data,created_at)
               SELECT incidents.id,'detected','Incident detected',COALESCE(incidents.detail,'{}'),incidents.opened_at
               FROM incidents
               WHERE NOT EXISTS (SELECT 1 FROM incident_events WHERE incident_events.incident_id=incidents.id)"""
        )
        interrupted_message = "Provisioning was interrupted by a Hub restart before completion. Retry INSTALL/SYNC AGENT."
        conn.execute(
            """UPDATE nodes
               SET provision_status='failed',
                   provision_output=CASE
                     WHEN TRIM(COALESCE(provision_output,''))=''
                       THEN ?
                     ELSE SUBSTR(provision_output || CHAR(10) || ?, -12000)
                   END,
                   updated_at=?
               WHERE provision_status='provisioning'""",
            (interrupted_message, interrupted_message, utc_ts()),
        )
        conn.execute(
            """UPDATE fleet_operation_items SET status='failed'
               WHERE status='running' AND job_id IS NULL"""
        )
        interrupted_operations = conn.execute(
            "SELECT id FROM fleet_operations WHERE status='running'"
        ).fetchall()
        for operation in interrupted_operations:
            finalize_fleet_operation(conn, operation["id"])
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password = os.getenv("DARK_NOC_ADMIN_PASSWORD")
            if not password or len(password) < 10:
                raise RuntimeError("DARK_NOC_ADMIN_PASSWORD must contain at least 10 characters")
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                (os.getenv("DARK_NOC_ADMIN_USER", "admin"), hash_password(password), "owner", utc_ts()),
            )


def append_incident_event(
    conn: sqlite3.Connection,
    incident_id: int,
    event_type: str,
    message: str,
    data: dict[str, Any] | None = None,
    actor_id: int | None = None,
    created_at: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO incident_events(incident_id,event_type,message,data,actor_id,created_at) VALUES(?,?,?,?,?,?)",
        (incident_id, event_type, message[:4000], json.dumps(data or {}), actor_id, created_at or utc_ts()),
    )


def create_incident(
    conn: sqlite3.Connection,
    *,
    node_id: int | None,
    tunnel_id: int | None,
    severity: str,
    title: str,
    detail: dict[str, Any],
    opened_at: int | None = None,
) -> int:
    now = opened_at or utc_ts()
    incident_id = int(
        conn.execute(
            """INSERT INTO incidents(node_id,tunnel_id,severity,title,detail,status,opened_at,last_changed_at)
               VALUES(?,?,?,?,?,'open',?,?)""",
            (node_id, tunnel_id, severity, title, json.dumps(detail), now, now),
        ).lastrowid
    )
    append_incident_event(conn, incident_id, "detected", title, detail, created_at=now)
    return incident_id


def resolve_incident(
    conn: sqlite3.Connection,
    incident_id: int,
    message: str,
    *,
    actor_id: int | None = None,
    resolution: str | None = None,
) -> bool:
    now = utc_ts()
    changed = conn.execute(
        """UPDATE incidents SET status='resolved',resolved_at=?,last_changed_at=?,
                   resolution=COALESCE(?,resolution)
           WHERE id=? AND status IN ('open','acknowledged')""",
        (now, now, resolution, incident_id),
    ).rowcount
    if changed:
        append_incident_event(conn, incident_id, "resolved", message, actor_id=actor_id, created_at=now)
    return bool(changed)


def acquire_hub_lease(name: str, now: int | None = None) -> bool:
    current = now or utc_ts()
    expiry = current + HUB_LEASE_SECONDS
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO hub_leases(name,holder,expires_at,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at,updated_at=excluded.updated_at
               WHERE hub_leases.holder=excluded.holder OR hub_leases.expires_at<?""",
            (name, HUB_INSTANCE_ID, expiry, current, current),
        )
        row = conn.execute("SELECT holder,expires_at FROM hub_leases WHERE name=?", (name,)).fetchone()
    return bool(row and row["holder"] == HUB_INSTANCE_ID and int(row["expires_at"]) >= expiry)


def queue_due_monitors(conn: sqlite3.Connection, now: int, online_cutoff: int) -> None:
    due = conn.execute(
        """SELECT monitors.* FROM monitors JOIN nodes ON nodes.id=monitors.node_id
           WHERE monitors.enabled=1 AND monitors.next_run_at<=? AND nodes.last_seen>=?
           ORDER BY monitors.next_run_at,monitors.id LIMIT 100""",
        (now, online_cutoff),
    ).fetchall()
    for monitor in due:
        active = conn.execute(
            "SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')",
            (monitor["job_id"],),
        ).fetchone() if monitor["job_id"] else None
        if active:
            continue
        payload = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        try:
            if monitor["kind"] == "snmp":
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
        except Exception:
            LOGGER.exception("Could not decrypt synthetic monitor secret for monitor %s", monitor["id"])
            failure_streak = int(monitor["failure_streak"] or 0) + 1
            detail = json.dumps({"error": "Encrypted monitor secret cannot be read; edit and save the monitor again"})
            conn.execute(
                """UPDATE monitors SET status='down',detail=?,failure_streak=?,last_run_at=?,next_run_at=?,updated_at=?
                   WHERE id=?""",
                (detail, failure_streak, now, now + int(monitor["interval_seconds"]), now, monitor["id"]),
            )
            conn.execute(
                "INSERT INTO monitor_results(monitor_id,ts,status,detail) VALUES(?,?,'down',?)",
                (monitor["id"], now, detail),
            )
            title = f"Monitor {monitor['name']} is down"
            active_incident = conn.execute(
                "SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (monitor["node_id"], title),
            ).fetchone()
            if failure_streak >= 2 and not active_incident:
                create_incident(
                    conn, node_id=monitor["node_id"], tunnel_id=None, severity="critical", title=title,
                    detail={"monitor_id": monitor["id"], "reason": "encrypted_secret_unreadable"}, opened_at=now,
                )
            continue
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), now),
        ).lastrowid
        conn.execute(
            "UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?",
            (job_id, now + int(monitor["interval_seconds"]), now, monitor["id"]),
        )


def queue_due_fleet_operations(conn: sqlite3.Connection, now: int) -> None:
    operations = conn.execute(
        "SELECT * FROM fleet_operations WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at,id LIMIT 20",
        (now,),
    ).fetchall()
    for operation in operations:
        payload = json.loads(operation["payload"] or "{}")
        items = conn.execute(
            "SELECT * FROM fleet_operation_items WHERE operation_id=? ORDER BY id",
            (operation["id"],),
        ).fetchall()
        for item in items:
            if item["job_id"]:
                continue
            job_id = conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (item["node_id"], operation["kind"], json.dumps(payload), operation["created_by"], now),
            ).lastrowid
            conn.execute(
                "UPDATE fleet_operation_items SET job_id=?,status='queued' WHERE id=?",
                (job_id, item["id"]),
            )
        conn.execute("UPDATE fleet_operations SET status='running',started_at=? WHERE id=?", (now, operation["id"]))


def rollup_previous_hour(conn: sqlite3.Connection, now: int) -> None:
    bucket = ((now // 3600) - 1) * 3600
    if bucket < 0:
        return
    conn.execute(
        """INSERT INTO metric_rollups(node_id,bucket,samples,cpu_avg,ram_avg,disk_max,load_avg,rx_avg,tx_avg,connections_max)
           SELECT node_id,?,COUNT(*),AVG(cpu),AVG(ram),MAX(disk),AVG(load1),AVG(rx_bps),AVG(tx_bps),MAX(connections)
           FROM metrics WHERE ts>=? AND ts<? GROUP BY node_id
           ON CONFLICT(node_id,bucket) DO UPDATE SET
             samples=excluded.samples,cpu_avg=excluded.cpu_avg,ram_avg=excluded.ram_avg,
             disk_max=excluded.disk_max,load_avg=excluded.load_avg,rx_avg=excluded.rx_avg,
             tx_avg=excluded.tx_avg,connections_max=excluded.connections_max""",
        (bucket, bucket, bucket + 3600),
    )


async def maintenance_loop() -> None:
    while True:
        try:
            if not acquire_hub_lease("maintenance"):
                await asyncio.sleep(15)
                continue
            cutoff = utc_ts() - NODE_STALE_AFTER
            with db() as conn:
                stale_nodes = conn.execute("SELECT id,name FROM nodes WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,)).fetchall()
                for stale in stale_nodes:
                    title = f"Node {stale['name']} is offline"
                    if not conn.execute("SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')", (stale["id"], title)).fetchone():
                        create_incident(conn, node_id=stale["id"], tunnel_id=None, severity="critical", title=title, detail={"reason": "heartbeat_timeout"})
                online_nodes = conn.execute("SELECT id FROM nodes WHERE last_seen>=?", (cutoff,)).fetchall()
                for online in online_nodes:
                    active = conn.execute("SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')", (online["id"],)).fetchall()
                    for incident in active:
                        resolve_incident(conn, incident["id"], "Agent heartbeat recovered")
                conn.execute("UPDATE nodes SET status='offline' WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,))
                conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utc_ts(),))
                rollup_previous_hour(conn, utc_ts())
                conn.execute("DELETE FROM metrics WHERE ts < ?", (utc_ts() - METRIC_RAW_RETENTION_DAYS * 86400,))
                conn.execute("DELETE FROM metric_rollups WHERE bucket < ?", (utc_ts() - METRIC_ROLLUP_RETENTION_DAYS * 86400,))
                conn.execute("DELETE FROM monitor_results WHERE ts < ?", (utc_ts() - MONITOR_RESULT_RETENTION_DAYS * 86400,))
                conn.execute("UPDATE jobs SET status='queued',started_at=NULL WHERE status='running' AND started_at<?", (utc_ts() - 5 * 60,))
                conn.execute("DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at<?", (utc_ts() - 90 * 86400,))
                conn.execute("DELETE FROM audit_logs WHERE created_at<?", (utc_ts() - 180 * 86400,))
                conn.execute("UPDATE certificates SET status='expired',updated_at=? WHERE expires_at IS NOT NULL AND expires_at<=?", (utc_ts(), utc_ts()))
                conn.execute("UPDATE certificates SET status='expiring',updated_at=? WHERE status='valid' AND expires_at BETWEEN ? AND ?", (utc_ts(), utc_ts(), utc_ts() + 14 * 86400))
                renewable = conn.execute("SELECT c.* FROM certificates c JOIN nodes n ON n.id=c.node_id WHERE c.status IN ('valid','expiring') AND c.expires_at BETWEEN ? AND ? AND n.last_seen>=?", (utc_ts(), utc_ts() + 30 * 86400, cutoff)).fetchall()
                for cert in renewable:
                    pending = conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (cert["job_id"],)).fetchone() if cert["job_id"] else None
                    if pending: continue
                    job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)", (cert["node_id"], "certificate_issue", json.dumps({"certificate_id": cert["id"], "domain": cert["domain"], "renew": True}), utc_ts())).lastrowid
                    conn.execute("UPDATE certificates SET status='renewing',job_id=?,updated_at=? WHERE id=?", (job_id, utc_ts(), cert["id"]))
                queue_due_monitors(conn, utc_ts(), cutoff)
                queue_due_fleet_operations(conn, utc_ts())
        except Exception:
            LOGGER.exception("maintenance loop failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    app.state.fernet = Fernet(KEY_PATH.read_bytes())
    task = asyncio.create_task(maintenance_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            with db() as conn:
                conn.execute("DELETE FROM hub_leases WHERE holder=?", (HUB_INSTANCE_ID,))
        except Exception:
            LOGGER.exception("Could not release Hub controller leases")


app = FastAPI(title="DARK NOC Hub", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    is_ssh_upload = request.method == "POST" and request.url.path.startswith("/api/ssh/upload/") and request.url.path.removeprefix("/api/ssh/upload/").isdigit()

    async def forward_request():
        # Multipart parsing happens before the endpoint body runs. Bound that
        # work separately so authenticated clients cannot spool unlimited
        # concurrent upload bodies while waiting for an SSH transfer slot.
        if is_ssh_upload:
            # Do not call Request.is_disconnected() before Starlette parses the
            # multipart body: doing so can consume an ASGI http.request frame.
            async with bounded_semaphore(SSH_UPLOAD_REQUEST_SEMAPHORE):
                return await call_next(request)
        return await call_next(request)

    if is_ssh_upload:
        try:
            current_user(request, request.cookies.get("dark_noc_session"))
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        else:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                    if declared_length < 0:
                        raise ValueError
                except ValueError:
                    response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})
                else:
                    if declared_length > SSH_UPLOAD_LIMIT + SSH_MULTIPART_OVERHEAD:
                        response = JSONResponse(
                            status_code=413,
                            content={"detail": f"Upload exceeds the {SSH_UPLOAD_LIMIT // 1024 // 1024} MB limit"},
                        )
                    else:
                        try:
                            response = await forward_request()
                        except HTTPException as exc:
                            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            else:
                try:
                    response = await forward_request()
                except HTTPException as exc:
                    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    else:
        response = await forward_request()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path in {"/", "/static/app.js", "/static/styles.css"}:
        response.headers["Cache-Control"] = "no-store"
    return response

PLUGIN_CATALOG = [{
    "id": "dark-backhaul", "name": "DARK Backhaul", "version": "1.9.2",
    "publisher": "DARK Tunnel Mika", "repository": "https://github.com/darktunnelmika/dark-backhaul",
    "description": "Reverse tunnel with coordinated IRAN server and KHAREJ client deployment.",
    "roles": {"iran": "server", "kharej": "client"},
    "transports": ["tcp", "tcpmux", "ws", "wsmux", "wss", "wssmux", "udp"],
    "profiles": ["stable", "balanced", "lowping", "turbo"],
    "automated": True,
}, {
    "id": "dark-ghostpro", "name": "DARK Ghost Pro", "version": "1.0.0",
    "publisher": "DARK Tunnel Mika", "repository": "https://github.com/darktunnelmika/dark-ghostpro",
    "description": "GOST v3 reverse tunnel with native DGP Pair Code and encrypted relay transports.",
    "roles": {"iran": "server", "kharej": "client"},
    "transports": ["relay+tls", "relay+wss", "relay+h2", "relay+grpc", "relay+quic", "relay+ws", "relay", "tls", "wss", "h2", "grpc", "quic", "ws", "tcp", "h2c", "dtls", "icmp"],
    "profiles": ["stable", "balanced", "lowping", "turbo"], "automated": True,
}, {
    "id": "dark-packetpro", "name": "DARK Packet Pro", "version": "5.6.0",
    "publisher": "@mikakhadm", "repository": "https://github.com/darktunnelmika/dark-packetpro",
    "description": "PAQET/KCP tunnel with managed deployment or offline KHAREJ pairing.",
    "roles": {"iran": "client", "kharej": "server"},
    "transports": ["kcp"],
    "profiles": ["stable", "balanced", "lowping", "turbo"], "automated": True,
}]


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NodeBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.() -]{2,48}$")
    region: str = Field(min_length=2, max_length=64)
    role: str = Field(default="edge", pattern=r"^(edge|exit|hub)$")
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=64)
    ssh_password: str | None = Field(default=None, max_length=512)
    ssh_private_key: str | None = Field(default=None, max_length=32768)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        try:
            ipaddress.ip_address(value)
            return canonical_node_host(value)
        except ValueError:
            if not value or len(value) > 253 or not all(part and len(part) <= 63 and part.replace("-", "a").isalnum() and not part.startswith("-") and not part.endswith("-") for part in value.rstrip(".").split(".")):
                raise ValueError("Invalid host or IP address")
            return canonical_node_host(value)


class NodeUpdateBody(NodeBody):
    pass


class SSHRelayBody(BaseModel):
    source_node_id: int = Field(gt=0)
    destination_node_id: int = Field(gt=0)
    source_path: str = Field(min_length=2, max_length=4096)
    destination_path: str = Field(min_length=2, max_length=4096)
    overwrite: bool = False

    @field_validator("source_path", "destination_path")
    @classmethod
    def validate_remote_path(cls, value: str) -> str:
        return clean_remote_path(value)


class IncidentActionBody(BaseModel):
    action: str = Field(pattern=r"^(acknowledge|resolve|reopen)$")
    note: str | None = Field(default=None, max_length=4000)
    root_cause: str | None = Field(default=None, max_length=4000)
    resolution: str | None = Field(default=None, max_length=4000)


class IncidentNoteBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    event_type: str = Field(default="note", pattern=r"^(note|diagnostic|action)$")


class MonitorBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.() -]{2,64}$")
    node_id: int = Field(gt=0)
    kind: str = Field(pattern=r"^(icmp|tcp|http|https|dns|tls|snmp)$")
    target: str = Field(min_length=1, max_length=512)
    port: int | None = Field(default=None, ge=1, le=65535)
    interval_seconds: int = Field(default=60, ge=15, le=86400)
    timeout_seconds: int = Field(default=5, ge=1, le=60)
    expected_status: int | None = Field(default=None, ge=100, le=599)
    snmp_community: str | None = Field(default=None, min_length=1, max_length=256)
    snmp_oid: str | None = Field(default=None, pattern=r"^\.?[0-9]+(?:\.[0-9]+)+$")
    enabled: bool = True

    @field_validator("target")
    @classmethod
    def validate_monitor_target(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid monitor target")
        return value


class FleetOperationBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.() -]{2,80}$")
    node_ids: list[int] = Field(min_length=1, max_length=200)
    kind: str = Field(pattern=r"^(diagnostics|tunnel_test|restart_service|service_status|logs|configure_autoheal|sync_agent)$")
    service: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.@-]{1,128}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: int | None = Field(default=None, ge=0)


class SSHFileActionBody(BaseModel):
    action: str = Field(pattern=r"^(mkdir|rename|delete|chmod)$")
    path: str = Field(min_length=2, max_length=4096)
    destination: str | None = Field(default=None, min_length=2, max_length=4096)
    mode: str | None = Field(default=None, pattern=r"^[0-7]{3,4}$")

    @field_validator("path", "destination")
    @classmethod
    def validate_file_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return clean_remote_path(value)


class SSHFileWriteBody(BaseModel):
    path: str = Field(min_length=2, max_length=4096)
    content: str = Field(max_length=16_777_216)
    overwrite: bool = False

    @field_validator("path")
    @classmethod
    def validate_write_path(cls, value: str) -> str:
        return clean_remote_path(value)


class JobBody(BaseModel):
    kind: str = Field(pattern=r"^(diagnostics|tunnel_test|restart_service|service_status|speed_test|logs|plugin_deploy|plugin_remove|plugin_install|tunnel_control|configure_autoheal)$")
    service: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.@-]{1,128}$")
    payload: dict[str, Any] = Field(default_factory=dict)


class PluginDeployBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,24}$")
    iran_node_id: int = Field(gt=0)
    kharej_node_id: int = Field(gt=0)
    iran_endpoint: str = Field(min_length=1, max_length=253)
    tunnel_port: int = Field(default=3080, ge=1, le=65535)
    user_ports: list[int] = Field(min_length=1, max_length=32)
    transport: str = Field(default="tcpmux", pattern=r"^(tcp|tcpmux|ws|wsmux|wss|wssmux|udp|kcp|relay|tls|h2|h2c|grpc|quic|dtls|icmp|relay\+(?:tls|wss|h2|grpc|quic|ws))$")
    profile: str = Field(default="balanced", pattern=r"^(stable|balanced|lowping|turbo)$")
    restart_every: str = Field(default="off", pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)

    @field_validator("iran_endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = NodeBody.validate_host(value)
        try:
            if ipaddress.ip_address(normalized).version != 4:
                raise ValueError("DARK Backhaul automation currently requires IPv4 or a hostname")
        except ValueError as exc:
            if "currently requires" in str(exc):
                raise
        return normalized


class PairCodeDeployBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,24}$")
    iran_node_id: int = Field(gt=0)
    iran_endpoint: str = Field(min_length=1, max_length=253)
    remote_label: str = Field(default="KHAREJ", pattern=r"^[A-Za-z0-9_.() -]{2,48}$")
    kharej_endpoint: str | None = Field(default=None, min_length=1, max_length=253)
    tunnel_port: int = Field(default=3080, ge=1, le=65535)
    user_ports: list[int] = Field(min_length=1, max_length=32)
    transport: str = Field(default="tcpmux", pattern=r"^(tcp|tcpmux|ws|wsmux|wss|wssmux|udp|kcp|relay|tls|h2|h2c|grpc|quic|dtls|icmp|relay\+(?:tls|wss|h2|grpc|quic|ws))$")
    profile: str = Field(default="balanced", pattern=r"^(stable|balanced|lowping|turbo)$")
    restart_every: str = Field(default="off", pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)

    @field_validator("iran_endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return PluginDeployBody.validate_endpoint(value)

    @field_validator("kharej_endpoint")
    @classmethod
    def validate_kharej_endpoint(cls, value: str | None) -> str | None:
        return PluginDeployBody.validate_endpoint(value) if value else None


class TunnelActionBody(BaseModel):
    action: str = Field(pattern=r"^(start|stop|restart|logs|status|test|install)$")


class TunnelReconfigureBody(BaseModel):
    user_ports: list[int] = Field(min_length=1, max_length=32)
    transport: str = Field(pattern=r"^(tcp|tcpmux|ws|wsmux|wss|wssmux|udp|kcp|relay|tls|h2|h2c|grpc|quic|dtls|icmp|relay\+(?:tls|wss|h2|grpc|quic|ws))$")
    profile: str = Field(pattern=r"^(stable|balanced|lowping|turbo)$")
    restart_every: str = Field(pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)


class CertificateBody(BaseModel):
    node_id: int = Field(gt=0)
    domain: str = Field(min_length=4, max_length=253, pattern=r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


class AgentReport(BaseModel):
    metrics: dict[str, Any]
    services: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    tunnels: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    agent_version: str = "unknown"
    autoheal: dict[str, Any] = Field(default_factory=dict)
    plugins: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > 65_536:
            raise ValueError("Metrics payload is too large")
        for key in ("cpu", "ram", "swap", "disk", "load1", "rx_bps", "tx_bps", "uptime", "connections"):
            if key not in value or value[key] is None:
                continue
            try:
                number = float(value[key])
            except (TypeError, ValueError):
                raise ValueError(f"Invalid numeric metric: {key}")
            if not math.isfinite(number) or number < 0 or (key in {"cpu", "ram", "swap", "disk"} and number > 100):
                raise ValueError(f"Metric out of range: {key}")
        return value

    @field_validator("services", "tunnels")
    @classmethod
    def validate_report_lists(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(json.dumps(value, default=str)) > 512_000:
            raise ValueError("Telemetry list is too large")
        return value


class JobResult(BaseModel):
    job_id: int
    status: str = Field(pattern=r"^(completed|failed)$")
    output: str = Field(max_length=200_000)


@app.post("/api/agent/local-enroll")
def local_agent_enroll(request: Request, x_dark_noc_bootstrap: str | None = Header(default=None)):
    expected = os.getenv("DARK_NOC_LOCAL_ENROLL_SECRET", "")
    if not expected or not x_dark_noc_bootstrap or not secrets.compare_digest(expected, x_dark_noc_bootstrap):
        raise HTTPException(403, "Local enrollment denied")
    enrollment = secrets.token_urlsafe(36)
    now = utc_ts()
    node_name = f"{socket.gethostname()} (Hub)"[:128]
    # The loopback address is correct for the local Agent transport, but it is
    # misleading in the inventory and unusable as a remote SSH destination.
    # Store the operator-facing host selected by the installer instead.
    node_host = os.getenv("DARK_NOC_PUBLIC_HOST", "").strip() or "127.0.0.1"
    with db() as conn:
        row = conn.execute("SELECT id FROM nodes WHERE role='hub' ORDER BY id LIMIT 1").fetchone()
        if row:
            node_id = row["id"]
            conn.execute("UPDATE nodes SET name=?,host=?,agent_token_hash=?,status='pending',last_seen=NULL,updated_at=? WHERE id=?", (node_name, node_host, token_hash(enrollment), now, node_id))
        else:
            if conn.execute("SELECT 1 FROM nodes WHERE name=?", (node_name,)).fetchone():
                node_name = f"{socket.gethostname()} Hub-{secrets.token_hex(2)}"[:128]
            cursor = conn.execute("INSERT INTO nodes(name,region,role,host,ssh_port,ssh_user,agent_token_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (node_name, "NOC Hub", "hub", node_host, 22, "root", token_hash(enrollment), "pending", now, now))
            node_id = cursor.lastrowid
    return {"id": node_id, "name": node_name, "agent_token": enrollment}


def current_user(request: Request, dark_noc_session: str | None = Cookie(default=None)) -> sqlite3.Row:
    if not dark_noc_session:
        raise HTTPException(401, "Authentication required")
    with db() as conn:
        row = conn.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?",
            (token_hash(dark_noc_session), utc_ts()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Session expired")
    return row


def agent_node(authorization: str | None = Header(default=None)) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Agent token required")
    with db() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE agent_token_hash=?", (token_hash(authorization[7:]),)).fetchone()
    if not row:
        raise HTTPException(401, "Invalid agent token")
    return row


def audit(user_id: int | None, action: str, target: str, detail: str, ip: str | None = None) -> None:
    with db() as conn:
        conn.execute("INSERT INTO audit_logs(user_id,action,target,detail,ip,created_at) VALUES(?,?,?,?,?,?)", (user_id, action, target, detail[:4000], ip, utc_ts()))


def public_job(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        payload = json.loads(item.get("payload") or "{}")
        for key in list(payload):
            if any(word in key.casefold() for word in ("token", "password", "secret", "community", "private_key")):
                payload[key] = "[REDACTED]"
        item["payload"] = json.dumps(payload)
    except (TypeError, ValueError):
        item["payload"] = "{}"
    return item


def telegram_notify(message: str) -> None:
    bot_token = os.getenv("DARK_NOC_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("DARK_NOC_TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=8,
        ).raise_for_status()
    except Exception:
        pass


def public_node(row: sqlite3.Row, metric: sqlite3.Row | None = None) -> dict[str, Any]:
    data = {k: row[k] for k in ("id", "name", "region", "role", "host", "observed_ip", "ssh_port", "ssh_user", "status", "agent_version", "provision_status", "provision_output", "autoheal_enabled", "autoheal_cooldown", "autoheal_max_restarts", "last_seen", "created_at")}
    data["ssh_configured"] = bool(row["ssh_password_enc"] or row["ssh_key_enc"])
    data["ssh_host_fingerprint"] = row["ssh_host_fingerprint"]
    data["metric"] = dict(metric) if metric else None
    if data["metric"]:
        try:
            data["metric"]["detail"] = json.loads(data["metric"].pop("payload", "{}") or "{}")
        except (TypeError, ValueError):
            data["metric"]["detail"] = {}
    return data


def public_monitor(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item.pop("secret_enc", None)
    if "node_last_seen" in item:
        item["runner_status"] = "online" if item["node_last_seen"] and int(item["node_last_seen"]) >= utc_ts() - NODE_STALE_AFTER else "offline"
    try:
        item["detail"] = json.loads(item.get("detail") or "{}")
    except (TypeError, ValueError):
        item["detail"] = {"message": str(item.get("detail") or "")[:1000]}
    return item


def monitor_values(body: MonitorBody, existing_secret: str | None = None) -> tuple[str, int | None, str | None, str | None]:
    target = body.target.strip()
    port = body.port
    if body.kind in {"icmp", "tcp", "dns", "tls", "snmp"}:
        if "://" in target or "/" in target:
            raise HTTPException(422, f"{body.kind.upper()} target must be a hostname or IP address")
        target = NodeBody.validate_host(target)
    if body.kind == "tcp" and not port:
        raise HTTPException(422, "TCP monitor requires a port")
    if body.kind == "tls":
        port = port or 443
    if body.kind == "snmp":
        port = port or 161
        if not body.snmp_community and not existing_secret:
            raise HTTPException(422, "SNMP monitor requires a community string")
        if not body.snmp_oid:
            raise HTTPException(422, "SNMP monitor requires an OID")
    if body.kind in {"http", "https"}:
        lowered = target.casefold()
        if "://" in target and not lowered.startswith(f"{body.kind}://"):
            raise HTTPException(422, f"{body.kind.upper()} monitor must use {body.kind}://")
    secret = encrypt(body.snmp_community) if body.snmp_community else existing_secret
    oid = body.snmp_oid
    if body.kind != "snmp":
        secret, oid = None, None
    expected_status = body.expected_status if body.kind in {"http", "https"} else None
    body.expected_status = expected_status
    return target, port, secret, oid


def ssh_connection_options(node: sqlite3.Row, password: str | None, key: str | None) -> dict[str, Any]:
    node_id, expected_fingerprint = node["id"], node["ssh_host_fingerprint"]

    class PinnedSSHClient(asyncssh.SSHClient):
        def validate_host_public_key(self, host: str, addr: str, port: int, host_key: Any) -> bool:
            fingerprint = host_key.get_fingerprint("sha256")
            if expected_fingerprint:
                return hmac.compare_digest(expected_fingerprint, fingerprint)
            with db() as pin_conn:
                current = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                if not current:
                    return False
                if current["ssh_host_fingerprint"]:
                    return hmac.compare_digest(current["ssh_host_fingerprint"], fingerprint)
                changed = pin_conn.execute("UPDATE nodes SET ssh_host_fingerprint=?,updated_at=? WHERE id=? AND ssh_host_fingerprint IS NULL", (fingerprint, utc_ts(), node_id)).rowcount
                if changed == 1:
                    return True
                saved = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                return bool(saved and saved["ssh_host_fingerprint"] and hmac.compare_digest(saved["ssh_host_fingerprint"], fingerprint))

    options: dict[str, Any] = {
        "host": node["host"], "port": node["ssh_port"], "username": node["ssh_user"],
        "known_hosts": ([], [], [], [], [], [], []), "client_factory": PinnedSSHClient,
        "connect_timeout": 15, "keepalive_interval": SSH_KEEPALIVE_INTERVAL,
        "keepalive_count_max": SSH_KEEPALIVE_COUNT_MAX,
    }
    if key:
        options["client_keys"] = [asyncssh.import_private_key(key)]
    if password:
        options["password"] = password
    return options


async def _provision_node_impl(node_id: int, enrollment: str) -> None:
    log: list[str] = []
    old_token_hash: str | None = None
    activated_token_hash: str | None = None
    token_hash_changed = False
    try:
        with db() as conn_db:
            node = conn_db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            return
        if node["provision_status"] != "provisioning":
            return
        old_token_hash = node["agent_token_hash"]
        password, key = decrypt(node["ssh_password_enc"]), decrypt(node["ssh_key_enc"])
        if not password and not key:
            raise RuntimeError("SSH password or private key is required for automatic installation")
        if node["ssh_user"] != "root":
            raise RuntimeError("Automatic Node installation currently requires SSH user root")

        log.append(f"Connecting to {node['host']}:{node['ssh_port']} as {node['ssh_user']}")
        async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
            prep = await ssh.run(
                "export DEBIAN_FRONTEND=noninteractive; "
                "apt-get update -qq && apt-get install -y python3 python3-venv python3-pip ca-certificates certbot curl tar openssl iproute2 iputils-ping iptables iperf3 snmp && "
                "install -d -m 0755 /opt/dark-noc-agent && "
                "install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul /etc/dark-ghostpro /etc/dark-packetpro && "
                "install -d -m 0755 /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /etc/nginx/conf.d /var/lib/dark-noc-acme /var/lib/dark-noc-acme/.well-known /var/lib/dark-noc-acme/.well-known/acme-challenge",
                check=False, timeout=600,
            )
            log.append((prep.stdout + prep.stderr).strip()[-6000:])
            if prep.exit_status != 0:
                raise RuntimeError(f"Prerequisite installation failed (exit {prep.exit_status})")

            agent_file = Path("/opt/dark-noc-agent/agent.py")
            requirements_file = Path("/opt/dark-noc-agent/requirements.txt")
            service_file = Path("/etc/systemd/system/dark-noc-agent.service")
            for required in (agent_file, requirements_file, service_file):
                if not required.is_file():
                    raise RuntimeError(f"Hub Node payload is missing: {required}")

            public_host = os.getenv("DARK_NOC_PUBLIC_HOST", "").strip()
            hub_url = configured_public_hub_url()
            tls_verify: bool | str = True
            cert_file = Path("/etc/dark-noc/tls/panel.crt")
            if os.getenv("DARK_NOC_PANEL_CERT_MODE", "selfsigned") == "selfsigned":
                tls_verify = "/etc/dark-noc-agent/hub-ca.crt"

            config = {
                "hub_url": hub_url, "agent_token": enrollment, "verify_tls": tls_verify,
                "interval_seconds": 15, "services": [], "managed_services": [], "tunnels": [],
                "speedtest": {"host": "", "port": 5201}, "auto_discovery": True,
                "autoheal": {"enabled": False, "cooldown_seconds": 300, "max_restarts_per_hour": 3},
            }
            operation_id = secrets.token_hex(8)
            staged_files: list[dict[str, Any]] = []
            activated_files: list[dict[str, Any]] = []
            venv_target = "/opt/dark-noc-agent/venv"
            venv_stage = f"/opt/dark-noc-agent/.venv.darknoc-{operation_id}.part"
            venv_backup = f"/opt/dark-noc-agent/.venv.darknoc-{operation_id}.bak"
            venv_installed = False
            venv_backup_created = False
            restart_attempted = False
            service_was_active = False
            service_was_enabled = False

            async with ssh.start_sftp_client() as sftp:
                try:
                    async with sftp.open("/etc/dark-noc-agent/config.json", "r") as old_remote_config:
                        old_content = await old_remote_config.read()
                except asyncssh.SFTPNoSuchFile:
                    old_config: dict[str, Any] | None = None
                    log.append("No existing Agent configuration found; preparing a fresh enrollment")
                else:
                    if isinstance(old_content, bytes):
                        old_content = old_content.decode("utf-8")
                    old_config = json.loads(old_content)
                    if not isinstance(old_config, dict):
                        raise RuntimeError("Existing Agent configuration is not a JSON object; refusing to overwrite it")
                    remote_token = old_config.get("agent_token")
                    if remote_token is not None and not isinstance(remote_token, str):
                        raise RuntimeError("Existing Agent token is malformed; refusing to overwrite its configuration")
                    remote_hub_url = old_config.get("hub_url")
                    if remote_hub_url is not None and not isinstance(remote_hub_url, str):
                        raise RuntimeError("Existing Agent Hub URL is malformed; refusing to overwrite its configuration")
                    if remote_token:
                        remote_token_hash = token_hash(remote_token)
                        if old_token_hash and hmac.compare_digest(remote_token_hash, old_token_hash):
                            enrollment = remote_token
                            log.append("Existing Agent enrollment token verified and reused")
                        else:
                            with db() as ownership_db:
                                owner = ownership_db.execute(
                                    "SELECT id,name FROM nodes WHERE id<>? AND agent_token_hash=?",
                                    (node_id, remote_token_hash),
                                ).fetchone()
                            if owner:
                                raise RuntimeError(f"Remote Agent belongs to another DARK NOC Node ({owner['name']}); refusing takeover")
                            if not remote_hub_url or remote_hub_url.rstrip("/").casefold() != hub_url.rstrip("/").casefold():
                                raise RuntimeError("Remote Agent points to a different Hub; refusing takeover")
                            enrollment = remote_token
                            log.append("Recovered the unowned Agent enrollment token for this Hub")
                    old_config.update({"hub_url": hub_url, "agent_token": enrollment, "verify_tls": tls_verify})
                    old_config.setdefault("interval_seconds", 15)
                    old_config.setdefault("services", [])
                    old_config.setdefault("managed_services", [])
                    old_config.setdefault("tunnels", [])
                    old_config.setdefault("speedtest", {"host": "", "port": 5201})
                    old_config.setdefault("auto_discovery", True)
                    old_config.setdefault("autoheal", {"enabled": False, "cooldown_seconds": 300, "max_restarts_per_hour": 3})
                    config = old_config
                    log.append("Existing DARK NOC Agent rules preserved")

                activated_token_hash = token_hash(enrollment)
                payloads: list[tuple[str, bytes, int]] = [
                    ("/opt/dark-noc-agent/agent.py", agent_file.read_bytes(), 0o755),
                    ("/opt/dark-noc-agent/requirements.txt", requirements_file.read_bytes(), 0o644),
                    ("/etc/systemd/system/dark-noc-agent.service", service_file.read_bytes(), 0o644),
                    ("/etc/dark-noc-agent/config.json", (json.dumps(config, indent=2) + "\n").encode(), 0o600),
                ]
                if tls_verify is not True:
                    if not cert_file.is_file():
                        raise RuntimeError("Hub self-signed certificate is missing")
                    payloads.append(("/etc/dark-noc-agent/hub-ca.crt", cert_file.read_bytes(), 0o600))

                async def remove_remote_tree(path: str) -> None:
                    result = await ssh.run(f"rm -rf -- {shlex.quote(path)}", check=False, timeout=120)
                    if result.exit_status != 0:
                        raise RuntimeError(f"Could not remove staged directory {path}")

                async def backup_remote_file(source: str, backup: str) -> None:
                    result = await ssh.run(
                        f"test ! -e {shlex.quote(backup)} && "
                        f"cp --reflink=auto --preserve=all -- {shlex.quote(source)} {shlex.quote(backup)}",
                        check=False,
                        timeout=60,
                    )
                    if result.exit_status != 0:
                        raise RuntimeError(f"Could not stage rollback copy for {source}")

                try:
                    for target, payload, final_mode in payloads:
                        entry = {
                            "target": target,
                            "temporary": f"{target}.darknoc-{operation_id}.part",
                            "backup": f"{target}.darknoc-{operation_id}.bak",
                            "mode": final_mode,
                            "stage_created": False,
                            "backup_created": False,
                            "installed": False,
                        }
                        staged_files.append(entry)
                        async with sftp.open(
                            entry["temporary"],
                            "xb",
                            attrs=asyncssh.SFTPAttrs(permissions=0o600),
                        ) as remote_stage:
                            entry["stage_created"] = True
                            await remote_stage.write(payload)
                        staged_attrs = await sftp.stat(entry["temporary"])
                        if staged_attrs.size is not None and int(staged_attrs.size) != len(payload):
                            raise RuntimeError(f"Staged Agent payload size mismatch for {target}")

                    requirements_stage = next(entry["temporary"] for entry in staged_files if entry["target"].endswith("requirements.txt"))
                    venv_result = await ssh.run(
                        f"test ! -e {shlex.quote(venv_stage)} && "
                        f"python3 -m venv {shlex.quote(venv_stage)} && "
                        f"{shlex.quote(venv_stage + '/bin/pip')} install --disable-pip-version-check -r {shlex.quote(requirements_stage)}",
                        check=False,
                        timeout=600,
                    )
                    log.append((venv_result.stdout + venv_result.stderr).strip()[-6000:])
                    if venv_result.exit_status != 0:
                        raise RuntimeError(f"Agent dependency staging failed (exit {venv_result.exit_status})")

                    active_result = await ssh.run("systemctl is-active dark-noc-agent.service", check=False, timeout=15)
                    enabled_result = await ssh.run("systemctl is-enabled dark-noc-agent.service", check=False, timeout=15)
                    service_was_active = active_result.stdout.strip() == "active"
                    service_was_enabled = enabled_result.stdout.strip() == "enabled"
                except BaseException:
                    for entry in staged_files:
                        if entry["stage_created"]:
                            await asyncio.shield(cleanup_sftp_path(sftp, entry["temporary"]))
                    try:
                        await asyncio.shield(remove_remote_tree(venv_stage))
                    except BaseException:
                        LOGGER.warning("Could not clean failed Agent staging directory %s", venv_stage, exc_info=True)
                    raise

                async def rollback_activation() -> None:
                    nonlocal token_hash_changed
                    rollback_errors: list[str] = []
                    if restart_attempted:
                        try:
                            await ssh.run("systemctl stop dark-noc-agent.service", check=False, timeout=60)
                        except BaseException as rollback_exc:
                            rollback_errors.append(f"service stop: {rollback_exc}")
                    try:
                        if venv_installed and await sftp_path_exists(sftp, venv_target):
                            await remove_remote_tree(venv_target)
                        if venv_backup_created and await sftp_path_exists(sftp, venv_backup):
                            await sftp.rename(venv_backup, venv_target)
                    except BaseException as rollback_exc:
                        rollback_errors.append(f"venv: {rollback_exc}")
                    for entry in reversed(activated_files):
                        try:
                            if entry["backup_created"] and await sftp_path_exists(sftp, entry["backup"]):
                                if entry["installed"]:
                                    await sftp.posix_rename(entry["backup"], entry["target"])
                                else:
                                    await sftp.remove(entry["backup"])
                            elif entry["installed"] and await sftp_path_exists(sftp, entry["target"]):
                                await sftp.remove(entry["target"])
                        except BaseException as rollback_exc:
                            rollback_errors.append(f"{entry['target']}: {rollback_exc}")
                    if token_hash_changed and activated_token_hash:
                        try:
                            with db() as rollback_db:
                                restored = rollback_db.execute(
                                    "UPDATE nodes SET agent_token_hash=?,updated_at=? WHERE id=? AND agent_token_hash=?",
                                    (old_token_hash, utc_ts(), node_id, activated_token_hash),
                                ).rowcount
                            if restored != 1:
                                rollback_errors.append("database enrollment token changed concurrently")
                            else:
                                token_hash_changed = False
                        except BaseException as rollback_exc:
                            rollback_errors.append(f"database token: {rollback_exc}")
                    if restart_attempted:
                        restore_parts = ["systemctl daemon-reload"]
                        restore_parts.append("systemctl enable dark-noc-agent.service" if service_was_enabled else "systemctl disable dark-noc-agent.service")
                        restore_parts.append("systemctl restart dark-noc-agent.service" if service_was_active else "systemctl stop dark-noc-agent.service")
                        try:
                            restored_service = await ssh.run("; ".join(restore_parts), check=False, timeout=120)
                            if restored_service.exit_status != 0 and service_was_active:
                                rollback_errors.append("previous Agent service could not be restarted")
                        except BaseException as rollback_exc:
                            rollback_errors.append(f"service restore: {rollback_exc}")
                    if rollback_errors:
                        log.append("ROLLBACK WARNINGS: " + "; ".join(rollback_errors))

                try:
                    for entry in staged_files:
                        await sftp.chmod(entry["temporary"], entry["mode"])
                        activated_files.append(entry)
                        if await sftp_path_exists(sftp, entry["target"]):
                            await backup_remote_file(entry["target"], entry["backup"])
                            entry["backup_created"] = True
                            await sftp.posix_rename(entry["temporary"], entry["target"])
                        else:
                            await sftp.rename(entry["temporary"], entry["target"])
                        entry["installed"] = True

                    if await sftp_path_exists(sftp, venv_target):
                        await sftp.rename(venv_target, venv_backup)
                        venv_backup_created = True
                    await sftp.rename(venv_stage, venv_target)
                    venv_installed = True

                    health_command = ["curl", "-fsS", "--max-time", "15"]
                    if tls_verify is not True:
                        health_command.extend(["--cacert", str(tls_verify)])
                    health_command.append(f"{hub_url}/healthz")
                    health_result = await ssh.run(" ".join(shlex.quote(part) for part in health_command), check=False, timeout=30)
                    if health_result.exit_status != 0:
                        raise RuntimeError(f"Node cannot reach the Hub HTTPS endpoint; check DNS, firewall and port {os.getenv('DARK_NOC_PUBLIC_PORT', '443')}")
                    log.append("Hub HTTPS health check passed from Node")

                    with db() as activation_db:
                        changed = activation_db.execute(
                            """UPDATE nodes SET agent_token_hash=?,updated_at=?
                               WHERE id=? AND provision_status='provisioning' AND agent_token_hash IS ?""",
                            (activated_token_hash, utc_ts(), node_id, old_token_hash),
                        ).rowcount
                    if changed != 1:
                        raise RuntimeError("Node enrollment changed while Agent activation was staged")
                    token_hash_changed = activated_token_hash != old_token_hash

                    restart_attempted = True
                    install_result = await ssh.run(
                        "systemctl daemon-reload && systemctl enable dark-noc-agent.service && "
                        "systemctl restart dark-noc-agent.service && systemctl is-active dark-noc-agent.service",
                        check=False,
                        timeout=120,
                    )
                    log.append((install_result.stdout + install_result.stderr).strip()[-6000:])
                    if install_result.exit_status != 0 or install_result.stdout.strip().splitlines()[-1:] != ["active"]:
                        raise RuntimeError(f"Agent service activation failed (exit {install_result.exit_status})")

                    with db() as heartbeat_db:
                        baseline_row = heartbeat_db.execute("SELECT last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
                    baseline_last_seen = int(baseline_row["last_seen"] or 0) if baseline_row else 0
                    enrolled = False
                    for _ in range(12):
                        await asyncio.sleep(2.5)
                        with db() as conn_db:
                            heartbeat = conn_db.execute("SELECT last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
                        if heartbeat and int(heartbeat["last_seen"] or 0) > baseline_last_seen:
                            enrolled = True
                            break
                    if not enrolled:
                        raise RuntimeError("Agent service started but no new heartbeat reached the Hub within 30 seconds; open INSTALL LOG and check TLS/network")
                    log.append("Agent heartbeat received; Node is online")
                    with db() as completion_db:
                        completed = completion_db.execute(
                            """UPDATE nodes SET provision_status='completed',provision_output=?,updated_at=?
                               WHERE id=? AND provision_status='provisioning' AND agent_token_hash IS ?""",
                            ("\n".join(log)[-12000:], utc_ts(), node_id, activated_token_hash),
                        ).rowcount
                    if completed != 1:
                        raise RuntimeError("Node provisioning state changed before activation completed")
                except BaseException:
                    await asyncio.shield(rollback_activation())
                    raise
                finally:
                    for entry in staged_files:
                        await asyncio.shield(cleanup_sftp_path(sftp, entry["temporary"]))
                    try:
                        if await sftp_path_exists(sftp, venv_stage):
                            await asyncio.shield(remove_remote_tree(venv_stage))
                    except BaseException:
                        LOGGER.warning("Could not remove staged Agent virtualenv %s", venv_stage, exc_info=True)

                for entry in activated_files:
                    if entry["backup_created"]:
                        try:
                            await sftp.remove(entry["backup"])
                        except Exception:
                            LOGGER.warning("Could not remove Agent backup %s", entry["backup"], exc_info=True)
                if venv_backup_created:
                    try:
                        await remove_remote_tree(venv_backup)
                    except Exception:
                        LOGGER.warning("Could not remove Agent virtualenv backup %s", venv_backup, exc_info=True)

    except BaseException as exc:
        log.append(f"ERROR: {str(exc).strip() or exc.__class__.__name__}")
        LOGGER.warning("Automatic provisioning failed for node %s: %s", node_id, exc)
        with db() as conn_db:
            conn_db.execute(
                """UPDATE nodes SET provision_status='failed',provision_output=?,updated_at=?
                   WHERE id=? AND provision_status='provisioning'""",
                ("\n".join(log)[-12000:], utc_ts(), node_id),
            )
        if isinstance(exc, asyncio.CancelledError):
            raise


async def provision_node(node_id: int, enrollment: str) -> None:
    with db() as conn_db:
        node = conn_db.execute("SELECT id,host,ssh_port,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node or node["provision_status"] != "provisioning":
        return
    endpoint_lock = provision_endpoint_lock(node["host"], node["ssh_port"])
    try:
        async with asyncio.timeout(NODE_PROVISION_TIMEOUT):
            async with NODE_PROVISION_SEMAPHORE:
                async with endpoint_lock:
                    with db() as conn_db:
                        current = conn_db.execute("SELECT id,host,ssh_port,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
                        if not current or current["provision_status"] != "provisioning":
                            return
                        conflict = node_endpoint_conflict(conn_db, current["host"], current["ssh_port"], node_id)
                    if conflict:
                        message = f"Provisioning refused: SSH endpoint is already registered as {conflict['name']}."
                        with db() as conn_db:
                            conn_db.execute(
                                "UPDATE nodes SET provision_status='failed',provision_output=?,updated_at=? WHERE id=? AND provision_status='provisioning'",
                                (message, utc_ts(), node_id),
                            )
                        return
                    await _provision_node_impl(node_id, enrollment)
    except TimeoutError:
        message = f"Provisioning exceeded the {NODE_PROVISION_TIMEOUT // 60} minute safety timeout and was stopped. Retry INSTALL/SYNC AGENT."
        with db() as conn_db:
            conn_db.execute(
                """UPDATE nodes SET provision_status='failed',provision_output=?,updated_at=?
                   WHERE id=? AND provision_status IN ('provisioning','failed')""",
                (message, utc_ts(), node_id),
            )
        LOGGER.warning("Automatic provisioning timed out for node %s", node_id)


def finalize_fleet_operation(conn: sqlite3.Connection, operation_id: int) -> None:
    counts = conn.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN status IN ('completed','failed','cancelled') THEN 1 ELSE 0 END) terminal,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                  SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled
           FROM fleet_operation_items WHERE operation_id=?""",
        (operation_id,),
    ).fetchone()
    if counts and counts["total"] and counts["terminal"] == counts["total"]:
        status = "failed" if counts["failed"] else "cancelled" if counts["cancelled"] else "completed"
        conn.execute("UPDATE fleet_operations SET status=?,finished_at=? WHERE id=?", (status, utc_ts(), operation_id))


async def provision_node_for_fleet(item_id: int, node_id: int, enrollment: str) -> None:
    try:
        await provision_node(node_id, enrollment)
    finally:
        with db() as conn:
            item = conn.execute("SELECT operation_id FROM fleet_operation_items WHERE id=?", (item_id,)).fetchone()
            node = conn.execute("SELECT provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not item:
                return
            status = "completed" if node and node["provision_status"] == "completed" else "failed"
            conn.execute("UPDATE fleet_operation_items SET status=? WHERE id=?", (status, item_id))
            finalize_fleet_operation(conn, item["operation_id"])


def login_rate_check(ip: str) -> None:
    now = utc_ts()
    with LOGIN_LOCK:
        attempts = [stamp for stamp in LOGIN_FAILURES.get(ip, []) if stamp > now - 15 * 60]
        LOGIN_FAILURES[ip] = attempts
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many login attempts; try again in 15 minutes")


def login_rate_record(ip: str, success: bool) -> None:
    with LOGIN_LOCK:
        if success:
            LOGIN_FAILURES.pop(ip, None)
        else:
            LOGIN_FAILURES.setdefault(ip, []).append(utc_ts())


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    login_rate_check(client_ip)
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
        if not user or not verify_password(body.password, user["password_hash"]):
            login_rate_record(client_ip, False)
            audit(user["id"] if user else None, "login_failed", body.username, "Invalid credentials", client_ip)
            raise HTTPException(401, "Invalid username or password")
        token = secrets.token_urlsafe(42)
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at,ip,created_at) VALUES(?,?,?,?,?)", (token_hash(token), user["id"], utc_ts() + SESSION_TTL, request.client.host if request.client else None, utc_ts()))
    response = JSONResponse({"ok": True, "username": user["username"], "role": user["role"]})
    response.set_cookie("dark_noc_session", token, httponly=True, secure=os.getenv("DARK_NOC_COOKIE_SECURE", "0") == "1", samesite="strict", max_age=SESSION_TTL)
    login_rate_record(client_ip, True)
    audit(user["id"], "login", user["username"], "Session created", client_ip)
    return response


@app.post("/api/auth/logout")
def logout(user: sqlite3.Row = Depends(current_user), dark_noc_session: str | None = Cookie(default=None)):
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(dark_noc_session or ""),))
    response = JSONResponse({"ok": True})
    response.delete_cookie("dark_noc_session")
    return response


@app.get("/api/auth/me")
def me(user: sqlite3.Row = Depends(current_user)):
    return {"username": user["username"], "role": user["role"]}


@app.put("/api/auth/account")
def update_account(_: sqlite3.Row = Depends(current_user)):
    raise HTTPException(403, "Account changes are server-only. Run sudo darknoc on the Hub server.")


@app.get("/api/dashboard")
def dashboard(_: sqlite3.Row = Depends(current_user)):
    cutoff = utc_ts() - NODE_STALE_AFTER
    with db() as conn:
        nodes = conn.execute("SELECT CASE WHEN last_seen>=? THEN 'online' WHEN last_seen IS NULL THEN 'pending' ELSE 'offline' END status,COUNT(*) count FROM nodes GROUP BY 1", (cutoff,)).fetchall()
        tunnel_rows = conn.execute("SELECT CASE WHEN nodes.last_seen>=? THEN tunnels.status ELSE 'stale' END status,COUNT(*) count FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id GROUP BY 1", (cutoff,)).fetchall()
        totals = conn.execute("SELECT COALESCE(SUM(rx_bps),0) rx,COALESCE(SUM(tx_bps),0) tx,COALESCE(SUM(connections),0) connections FROM (SELECT m.* FROM metrics m JOIN (SELECT node_id,MAX(ts) ts FROM metrics WHERE ts>=? GROUP BY node_id) x ON x.node_id=m.node_id AND x.ts=m.ts)", (cutoff,)).fetchone()
        incidents = conn.execute("SELECT COUNT(*) count FROM incidents WHERE status IN ('open','acknowledged')").fetchone()["count"]
    node_counts = {r["status"]: r["count"] for r in nodes}
    tunnel_counts = {r["status"]: r["count"] for r in tunnel_rows}
    component_total = sum(node_counts.values()) + sum(tunnel_counts.values())
    health_percent = round(100 * (node_counts.get("online", 0) + tunnel_counts.get("healthy", 0)) / component_total, 1) if component_total else 0.0
    return {"nodes": node_counts, "tunnels": tunnel_counts, "health_percent": health_percent, "rx_bps": totals["rx"], "tx_bps": totals["tx"], "throughput_bps": totals["rx"] + totals["tx"], "connections": totals["connections"], "open_incidents": incidents, "server_time": utc_ts(), "limits": {"ssh_upload_bytes": SSH_UPLOAD_LIMIT, "ssh_relay_bytes": SSH_RELAY_LIMIT, "ssh_upload_queue_seconds": SSH_UPLOAD_QUEUE_TIMEOUT}}


@app.get("/api/dashboard/traffic")
def dashboard_traffic(minutes: int = 60, _: sqlite3.Row = Depends(current_user)):
    minutes = min(max(minutes, 10), 1440)
    bucket = 60 if minutes <= 180 else 300
    with db() as conn:
        rows = conn.execute(
            """WITH ranked AS (
                 SELECT (ts / ?) * ? bucket,node_id,rx_bps,tx_bps,
                        ROW_NUMBER() OVER (PARTITION BY node_id,(ts / ?) ORDER BY ts DESC) rn
                 FROM metrics WHERE ts>=?
               )
               SELECT bucket ts,COALESCE(SUM(rx_bps),0) rx_bps,COALESCE(SUM(tx_bps),0) tx_bps
               FROM ranked WHERE rn=1 GROUP BY bucket ORDER BY bucket""",
            (bucket, bucket, bucket, utc_ts() - minutes * 60),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/nodes")
def list_nodes(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute("""SELECT * FROM nodes
            ORDER BY CASE role WHEN 'hub' THEN 0 WHEN 'edge' THEN 1 WHEN 'exit' THEN 2 ELSE 3 END,
                     CASE WHEN last_seen>=? THEN 0 WHEN last_seen IS NULL THEN 2 ELSE 1 END,
                     name COLLATE NOCASE""", (utc_ts() - NODE_STALE_AFTER,)).fetchall()
        result = []
        for row in rows:
            metric = conn.execute("SELECT * FROM metrics WHERE node_id=? ORDER BY ts DESC LIMIT 1", (row["id"],)).fetchone()
            item = public_node(row, metric)
            try:
                item["plugins"] = json.loads(row["plugin_inventory"] or "{}")
            except (TypeError, ValueError):
                item["plugins"] = {}
            item["services"] = [dict(service) for service in conn.execute("SELECT name,status,last_check FROM node_services WHERE node_id=? ORDER BY name", (row["id"],)).fetchall()]
            if not row["last_seen"] or row["last_seen"] < utc_ts() - NODE_STALE_AFTER:
                item["status"] = "pending" if not row["last_seen"] else "offline"
            result.append(item)
    return result


@app.post("/api/nodes", status_code=202)
def create_node(body: NodeBody, background: BackgroundTasks, request: Request, user: sqlite3.Row = Depends(current_user)):
    if body.role == "hub":
        raise HTTPException(400, "The Hub node is registered automatically; add this server as Iran Edge or Global Exit")
    if not body.ssh_password and not body.ssh_private_key:
        raise HTTPException(400, "SSH password or private key is required for automatic Node installation")
    if body.ssh_user != "root":
        raise HTTPException(400, "Automatic Node installation currently requires SSH user root")
    enrollment = secrets.token_urlsafe(36)
    now = utc_ts()
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conflict = node_endpoint_conflict(conn, body.host, body.ssh_port)
            if conflict:
                raise HTTPException(409, f"SSH endpoint is already registered as {conflict['name']}")
            cursor = conn.execute("INSERT INTO nodes(name,region,role,host,ssh_port,ssh_user,ssh_password_enc,ssh_key_enc,agent_token_hash,status,provision_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (body.name, body.region, body.role, body.host, body.ssh_port, body.ssh_user, encrypt(body.ssh_password), encrypt(body.ssh_private_key), token_hash(enrollment), "pending", "provisioning", now, now))
            node_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Node name already exists")
    background.add_task(provision_node, node_id, enrollment)
    try:
        audit(user["id"], "node_create", body.name, f"Node {body.host} registered", request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record Node creation audit event")
    return {"id": node_id, "name": body.name, "provisioning": True, "message": "Secure SSH installation started"}


@app.post("/api/nodes/{node_id}/provision", status_code=202)
def retry_node_provision(node_id: int, background: BackgroundTasks, request: Request, user: sqlite3.Row = Depends(current_user)):
    enrollment = secrets.token_urlsafe(36)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        if node["role"] == "hub":
            raise HTTPException(400, "The local Hub Agent is managed by the Hub installer")
        if not node["ssh_password_enc"] and not node["ssh_key_enc"]:
            raise HTTPException(400, "Configure SSH credentials before retrying installation")
        conflict = node_endpoint_conflict(conn, node["host"], node["ssh_port"], node_id)
        if conflict:
            raise HTTPException(409, f"SSH endpoint is already registered as {conflict['name']}")
        changed = conn.execute(
            """UPDATE nodes SET provision_status='provisioning',provision_output='',updated_at=?
               WHERE id=? AND COALESCE(provision_status,'')<>'provisioning'""",
            (utc_ts(), node_id),
        ).rowcount
        if changed != 1:
            raise HTTPException(409, "Agent installation or synchronization is already running")
    background.add_task(provision_node, node_id, enrollment)
    try:
        audit(user["id"], "node_provision_retry", node["name"], "Automatic Agent installation retried", request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record Agent synchronization audit event")
    return {"ok": True, "provisioning": True}


@app.delete("/api/nodes/{node_id}")
def delete_node(node_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT name,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Node not found")
        if row["provision_status"] == "provisioning":
            raise HTTPException(409, "Wait for Agent installation or synchronization to finish before deleting this Node")
        deployment = conn.execute("SELECT id FROM plugin_deployments WHERE lifecycle IN ('active','removing','rolling_back') AND (iran_node_id=? OR kharej_node_id=?) LIMIT 1", (node_id, node_id)).fetchone()
        if deployment:
            raise HTTPException(409, "Remove active DARK NOC tunnel deployments before deleting this node")
        conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    audit(user["id"], "node_delete", row["name"], "Node and telemetry removed", request.client.host if request.client else None)
    return {"ok": True}


@app.put("/api/nodes/{node_id}")
def update_node(node_id: int, body: NodeUpdateBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not current:
            raise HTTPException(404, "Node not found")
        if current["provision_status"] == "provisioning":
            raise HTTPException(409, "Wait for Agent installation or synchronization to finish before editing this Node")
        conflict = node_endpoint_conflict(conn, body.host, body.ssh_port, node_id)
        if conflict:
            raise HTTPException(409, f"SSH endpoint is already registered as {conflict['name']}")
        password_enc = encrypt(body.ssh_password) if body.ssh_password else current["ssh_password_enc"]
        key_enc = encrypt(body.ssh_private_key) if body.ssh_private_key else current["ssh_key_enc"]
        reset_pin = current["host"] != body.host or current["ssh_port"] != body.ssh_port
        try:
            conn.execute("""UPDATE nodes SET name=?,region=?,role=?,host=?,ssh_port=?,ssh_user=?,
              ssh_password_enc=?,ssh_key_enc=?,ssh_host_fingerprint=?,updated_at=? WHERE id=?""",
              (body.name, body.region, body.role, body.host, body.ssh_port, body.ssh_user,
               password_enc, key_enc, None if reset_pin else current["ssh_host_fingerprint"], utc_ts(), node_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Node name already exists")
    audit(user["id"], "node_update", body.name, "Node connection settings updated", request.client.host if request.client else None)
    return {"ok": True, "ssh_pin_reset": reset_pin}


@app.delete("/api/nodes/{node_id}/ssh-fingerprint")
def reset_ssh_fingerprint(node_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT name,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Node not found")
        if row["provision_status"] == "provisioning":
            raise HTTPException(409, "Wait for Agent installation or synchronization to finish before resetting its SSH fingerprint")
        conn.execute("UPDATE nodes SET ssh_host_fingerprint=NULL,updated_at=? WHERE id=?", (utc_ts(), node_id))
    audit(user["id"], "ssh_pin_reset", row["name"], "SSH host fingerprint reset", request.client.host if request.client else None)
    return {"ok": True}


def ssh_file_node(node_id: int) -> tuple[sqlite3.Row, str | None, str | None]:
    with db() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        raise HTTPException(404, "Node not found")
    password, key = decrypt(node["ssh_password_enc"]), decrypt(node["ssh_key_enc"])
    if not password and not key:
        raise HTTPException(400, f"SSH credentials are not configured for {node['name']}")
    return node, password, key


async def sftp_path_exists(sftp: Any, path: str) -> bool:
    try:
        await sftp.stat(path)
        return True
    except asyncssh.SFTPNoSuchFile:
        return False


async def cleanup_sftp_path(sftp: Any, path: str) -> None:
    try:
        if await sftp_path_exists(sftp, path):
            await sftp.remove(path)
    except BaseException:
        LOGGER.warning("Could not clean up temporary SSH file %s", path, exc_info=True)


async def finalize_sftp_file(sftp: Any, temporary_path: str, destination_path: str, overwrite: bool) -> None:
    if not await sftp_path_exists(sftp, destination_path):
        await sftp.rename(temporary_path, destination_path)
        return
    if not overwrite:
        raise HTTPException(409, "Destination already exists; enable overwrite to replace it")
    attrs = await sftp.stat(destination_path)
    if attrs.permissions is not None and statmod.S_ISDIR(attrs.permissions):
        raise HTTPException(409, "Destination points to a directory")
    if attrs.permissions is not None:
        await sftp.chmod(temporary_path, statmod.S_IMODE(attrs.permissions))

    try:
        # OpenSSH's POSIX rename extension replaces the destination in one
        # atomic filesystem operation, so readers never observe a gap.
        await sftp.posix_rename(temporary_path, destination_path)
        return
    except asyncssh.SFTPOpUnsupported:
        # Older/non-OpenSSH servers get the recoverable backup/swap path.
        pass

    backup_path = f"{destination_path}.darknoc-{secrets.token_hex(8)}.bak"
    failed_path = f"{temporary_path}.failed-{secrets.token_hex(4)}"
    try:
        await sftp.rename(destination_path, backup_path)
        await sftp.rename(temporary_path, destination_path)
    except BaseException:
        async def rollback() -> None:
            if not await sftp_path_exists(sftp, backup_path):
                return
            if await sftp_path_exists(sftp, destination_path):
                await sftp.rename(destination_path, failed_path)
            await sftp.rename(backup_path, destination_path)
            await cleanup_sftp_path(sftp, failed_path)

        try:
            await asyncio.shield(rollback())
        except BaseException:
            LOGGER.exception("SSH overwrite rollback failed; original is retained at %s", backup_path)
        raise

    try:
        await sftp.remove(backup_path)
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.warning("Could not remove SSH overwrite backup %s", backup_path, exc_info=True)


def ssh_file_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "Secure file operation timed out")
    detail = str(exc).strip().replace("\n", " ")[:260] or exc.__class__.__name__
    LOGGER.warning("SSH file operation failed: %s", detail)
    return HTTPException(502, f"Secure file operation failed: {detail}")


@app.post("/api/ssh/upload/{node_id}")
async def ssh_upload_file(
    node_id: int,
    request: Request,
    remote_path: str = Form(...),
    overwrite: bool = Form(False),
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
):
    node, password, key = ssh_file_node(node_id)
    if file.size is not None and file.size > SSH_UPLOAD_LIMIT:
        await file.close()
        raise HTTPException(413, f"Upload exceeds the {SSH_UPLOAD_LIMIT // 1024 // 1024} MB limit")
    try:
        destination = clean_remote_path(remote_path, filename=file.filename)
    except ValueError as exc:
        await file.close()
        raise HTTPException(400, str(exc)) from exc
    if not destructive_remote_path_allowed(destination):
        await file.close()
        raise HTTPException(400, "This protected system path cannot be replaced through file upload")
    temporary = f"{destination}.darknoc-{secrets.token_hex(8)}.part"
    transferred = 0
    try:
        async with asyncio.timeout(SSH_TRANSFER_TIMEOUT):
            async with disconnect_aware_semaphore(request, SSH_TRANSFER_SEMAPHORE):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        try:
                            parent = posixpath.dirname(destination)
                            parent_attrs = await sftp.stat(parent)
                            if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                raise HTTPException(400, "Destination parent is not a directory")
                            if await sftp_path_exists(sftp, destination) and not overwrite:
                                raise HTTPException(409, "Destination already exists; enable overwrite to replace it")
                            async with sftp.open(
                                temporary,
                                "xb",
                                attrs=asyncssh.SFTPAttrs(permissions=0o600),
                            ) as remote:
                                while True:
                                    if await request.is_disconnected():
                                        raise HTTPException(499, "Client disconnected")
                                    chunk = await asyncio.wait_for(file.read(SSH_FILE_CHUNK), SSH_TRANSFER_IDLE_TIMEOUT)
                                    if not chunk:
                                        break
                                    transferred += len(chunk)
                                    if transferred > SSH_UPLOAD_LIMIT:
                                        raise HTTPException(413, f"Upload exceeds the {SSH_UPLOAD_LIMIT // 1024 // 1024} MB limit")
                                    await asyncio.wait_for(remote.write(chunk), SSH_TRANSFER_IDLE_TIMEOUT)
                            async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], destination)):
                                if await request.is_disconnected():
                                    raise HTTPException(499, "Client disconnected")
                                await finalize_sftp_file(sftp, temporary, destination, overwrite)
                        except BaseException:
                            await asyncio.shield(cleanup_sftp_path(sftp, temporary))
                            raise
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc
    finally:
        try:
            await asyncio.shield(file.close())
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("Could not close uploaded file", exc_info=True)
    try:
        audit(user["id"], "ssh_file_upload", node["name"], f"Uploaded {transferred} bytes to {destination}", request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record successful SSH upload audit event")
    return {"ok": True, "node": node["name"], "path": destination, "bytes": transferred}


@app.post("/api/ssh/relay")
async def ssh_relay_file(body: SSHRelayBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    source, source_password, source_key = ssh_file_node(body.source_node_id)
    destination, destination_password, destination_key = ssh_file_node(body.destination_node_id)
    if source["id"] == destination["id"] and body.source_path == body.destination_path:
        raise HTTPException(400, "Source and destination are the same file")
    if not destructive_remote_path_allowed(body.destination_path):
        raise HTTPException(400, "This protected system path cannot be replaced through file relay")
    temporary = f"{body.destination_path}.darknoc-{secrets.token_hex(8)}.part"
    transferred = 0
    try:
        async with asyncio.timeout(SSH_TRANSFER_TIMEOUT):
            async with disconnect_aware_semaphore(request, SSH_TRANSFER_SEMAPHORE):
                async with asyncssh.connect(**ssh_connection_options(source, source_password, source_key)) as source_ssh:
                    async with asyncssh.connect(**ssh_connection_options(destination, destination_password, destination_key)) as destination_ssh:
                        async with source_ssh.start_sftp_client() as source_sftp, destination_ssh.start_sftp_client() as destination_sftp:
                            try:
                                source_attrs = await source_sftp.stat(body.source_path)
                                if source_attrs.permissions is not None and not statmod.S_ISREG(source_attrs.permissions):
                                    raise HTTPException(400, "Source path must be a regular file")
                                source_size = int(source_attrs.size or 0)
                                if source_size > SSH_RELAY_LIMIT:
                                    raise HTTPException(413, f"File exceeds the {SSH_RELAY_LIMIT // 1024 // 1024} MB relay limit")
                                parent_attrs = await destination_sftp.stat(posixpath.dirname(body.destination_path))
                                if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                if await sftp_path_exists(destination_sftp, body.destination_path) and not body.overwrite:
                                    raise HTTPException(409, "Destination already exists; enable overwrite to replace it")
                                source_mode = statmod.S_IMODE(source_attrs.permissions) if source_attrs.permissions is not None else 0o600
                                async with source_sftp.open(body.source_path, "rb") as reader, destination_sftp.open(
                                    temporary,
                                    "xb",
                                    attrs=asyncssh.SFTPAttrs(permissions=source_mode),
                                ) as writer:
                                    while True:
                                        if await request.is_disconnected():
                                            raise HTTPException(499, "Client disconnected")
                                        chunk = await asyncio.wait_for(reader.read(SSH_FILE_CHUNK), SSH_TRANSFER_IDLE_TIMEOUT)
                                        if not chunk:
                                            break
                                        transferred += len(chunk)
                                        if transferred > SSH_RELAY_LIMIT:
                                            raise HTTPException(413, f"File exceeds the {SSH_RELAY_LIMIT // 1024 // 1024} MB relay limit")
                                        await asyncio.wait_for(writer.write(chunk), SSH_TRANSFER_IDLE_TIMEOUT)
                                async with disconnect_aware_semaphore(
                                    request, ssh_destination_lock(destination["id"], body.destination_path)
                                ):
                                    if await request.is_disconnected():
                                        raise HTTPException(499, "Client disconnected")
                                    await finalize_sftp_file(destination_sftp, temporary, body.destination_path, body.overwrite)
                            except BaseException:
                                await asyncio.shield(cleanup_sftp_path(destination_sftp, temporary))
                                raise
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc
    try:
        audit(user["id"], "ssh_file_relay", f"{source['name']} → {destination['name']}", f"Relayed {transferred} bytes: {body.source_path} → {body.destination_path}", request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record successful SSH relay audit event")
    return {"ok": True, "source_node": source["name"], "destination_node": destination["name"], "source_path": body.source_path, "destination_path": body.destination_path, "bytes": transferred}


def sftp_entry(name: str, directory: str, attrs: Any) -> dict[str, Any]:
    permissions = int(getattr(attrs, "permissions", 0) or 0)
    item_type = "directory" if statmod.S_ISDIR(permissions) else "symlink" if statmod.S_ISLNK(permissions) else "file"
    return {
        "name": name,
        "path": posixpath.join(directory, name) if directory != "/" else f"/{name}",
        "type": item_type,
        "size": int(getattr(attrs, "size", 0) or 0),
        "mode": format(statmod.S_IMODE(permissions), "04o"),
        "modified_at": int(getattr(attrs, "mtime", 0) or 0),
    }


@app.get("/api/ssh/files/{node_id}")
async def list_ssh_files(node_id: int, path: str = "/root", _: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    try:
        directory = clean_remote_directory(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        async with asyncio.timeout(min(SSH_TRANSFER_TIMEOUT, 120)):
            async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                async with ssh.start_sftp_client() as sftp:
                    attrs = await sftp.stat(directory)
                    if attrs.permissions is not None and not statmod.S_ISDIR(attrs.permissions):
                        raise HTTPException(400, "Remote path is not a directory")
                    entries = []
                    async for entry in sftp.scandir(directory):
                        name = str(entry.filename)
                        if name in {".", ".."}:
                            continue
                        entries.append(sftp_entry(name, directory, entry.attrs))
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
        parent = None if directory == "/" else posixpath.dirname(directory) or "/"
        return {"node": node["name"], "path": directory, "parent": parent, "entries": entries[:5000]}
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc


@app.get("/api/ssh/files/{node_id}/read")
async def read_ssh_file(node_id: int, path: str, _: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    try:
        remote_path = clean_remote_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        async with asyncio.timeout(min(SSH_TRANSFER_TIMEOUT, 120)):
            async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                async with ssh.start_sftp_client() as sftp:
                    attrs = await sftp.stat(remote_path)
                    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                        raise HTTPException(400, "Only regular files can be opened in the editor")
                    if int(attrs.size or 0) > SSH_EDITOR_LIMIT:
                        raise HTTPException(413, f"Editor limit is {SSH_EDITOR_LIMIT // 1024} KB")
                    async with sftp.open(remote_path, "rb") as remote:
                        payload = await asyncio.wait_for(remote.read(SSH_EDITOR_LIMIT + 1), SSH_TRANSFER_IDLE_TIMEOUT)
        if len(payload) > SSH_EDITOR_LIMIT:
            raise HTTPException(413, f"Editor limit is {SSH_EDITOR_LIMIT // 1024} KB")
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, "File is not valid UTF-8 text") from exc
        return {"node": node["name"], "path": remote_path, "content": content, "bytes": len(payload)}
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc


@app.put("/api/ssh/files/{node_id}/write")
async def write_ssh_file(node_id: int, body: SSHFileWriteBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    if not destructive_remote_path_allowed(body.path):
        raise HTTPException(400, "This protected system path cannot be edited from File Manager")
    payload = body.content.encode("utf-8")
    if len(payload) > SSH_EDITOR_LIMIT:
        raise HTTPException(413, f"Editor limit is {SSH_EDITOR_LIMIT // 1024} KB")
    temporary = f"{body.path}.darknoc-{secrets.token_hex(8)}.part"
    try:
        async with asyncio.timeout(min(SSH_TRANSFER_TIMEOUT, 300)):
            async with disconnect_aware_semaphore(request, SSH_TRANSFER_SEMAPHORE):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        try:
                            parent = await sftp.stat(posixpath.dirname(body.path))
                            if parent.permissions is not None and not statmod.S_ISDIR(parent.permissions):
                                raise HTTPException(400, "Destination parent is not a directory")
                            if await sftp_path_exists(sftp, body.path) and not body.overwrite:
                                raise HTTPException(409, "File already exists; enable overwrite to replace it")
                            async with sftp.open(temporary, "xb", attrs=asyncssh.SFTPAttrs(permissions=0o600)) as remote:
                                await asyncio.wait_for(remote.write(payload), SSH_TRANSFER_IDLE_TIMEOUT)
                            async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.path)):
                                await finalize_sftp_file(sftp, temporary, body.path, body.overwrite)
                        except BaseException:
                            await asyncio.shield(cleanup_sftp_path(sftp, temporary))
                            raise
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc
    try:
        audit(user["id"], "ssh_file_write", node["name"], f"Saved {len(payload)} bytes to {body.path}", request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record SSH editor audit event")
    return {"ok": True, "path": body.path, "bytes": len(payload)}


@app.post("/api/ssh/files/{node_id}/action")
async def ssh_file_action(node_id: int, body: SSHFileActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    if not destructive_remote_path_allowed(body.path):
        raise HTTPException(400, "This protected system path cannot be changed from File Manager")
    if body.action == "rename":
        if not body.destination:
            raise HTTPException(422, "Rename requires a destination path")
        if not destructive_remote_path_allowed(body.destination):
            raise HTTPException(400, "This protected destination cannot be changed from File Manager")
    if body.action == "chmod" and not body.mode:
        raise HTTPException(422, "CHMOD requires an octal mode")
    try:
        async with asyncio.timeout(min(SSH_TRANSFER_TIMEOUT, 120)):
            async with disconnect_aware_semaphore(request, SSH_TRANSFER_SEMAPHORE):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.destination or body.path)):
                            if body.action == "mkdir":
                                await sftp.mkdir(body.path, attrs=asyncssh.SFTPAttrs(permissions=0o750))
                            elif body.action == "rename":
                                if await sftp_path_exists(sftp, body.destination or ""):
                                    raise HTTPException(409, "Destination already exists")
                                await sftp.rename(body.path, body.destination)
                            elif body.action == "delete":
                                attrs = await sftp.lstat(body.path)
                                if attrs.permissions is not None and statmod.S_ISDIR(attrs.permissions):
                                    await sftp.rmdir(body.path)
                                else:
                                    await sftp.remove(body.path)
                            else:
                                await sftp.chmod(body.path, int(body.mode or "600", 8))
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc
    target = f"{body.path} -> {body.destination}" if body.destination else body.path
    try:
        audit(user["id"], f"ssh_file_{body.action}", node["name"], target, request.client.host if request.client else None)
    except Exception:
        LOGGER.exception("Could not record SSH File Manager action")
    return {"ok": True, "action": body.action, "path": body.path, "destination": body.destination}


@app.get("/api/ssh/files/{node_id}/checksum")
async def checksum_ssh_file(node_id: int, path: str, _: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    try:
        remote_path = clean_remote_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    digest = hashlib.sha256()
    size = 0
    try:
        async with asyncio.timeout(SSH_TRANSFER_TIMEOUT):
            async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                async with ssh.start_sftp_client() as sftp:
                    attrs = await sftp.stat(remote_path)
                    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                        raise HTTPException(400, "Checksum requires a regular file")
                    async with sftp.open(remote_path, "rb") as remote:
                        while True:
                            chunk = await asyncio.wait_for(remote.read(SSH_FILE_CHUNK), SSH_TRANSFER_IDLE_TIMEOUT)
                            if not chunk:
                                break
                            size += len(chunk)
                            digest.update(chunk)
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc
    return {"node": node["name"], "path": remote_path, "bytes": size, "sha256": digest.hexdigest()}


@app.get("/api/ssh/files/{node_id}/download")
async def download_ssh_file(node_id: int, path: str, request: Request, user: sqlite3.Row = Depends(current_user)):
    node, password, key = ssh_file_node(node_id)
    try:
        remote_path = clean_remote_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
            async with ssh.start_sftp_client() as sftp:
                attrs = await sftp.stat(remote_path)
                if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                    raise HTTPException(400, "Only regular files can be downloaded")
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise ssh_file_error(exc) from exc

    async def stream_remote_file():
        async with asyncio.timeout(SSH_TRANSFER_TIMEOUT):
            async with disconnect_aware_semaphore(request, SSH_TRANSFER_SEMAPHORE):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        async with sftp.open(remote_path, "rb") as remote:
                            while True:
                                if await request.is_disconnected():
                                    break
                                chunk = await asyncio.wait_for(remote.read(SSH_FILE_CHUNK), SSH_TRANSFER_IDLE_TIMEOUT)
                                if not chunk:
                                    break
                                yield chunk

    safe_name = posixpath.basename(remote_path).replace('"', "_").replace("\\", "_") or "download.bin"
    try:
        audit(user["id"], "ssh_file_download", node["name"], remote_path)
    except Exception:
        LOGGER.exception("Could not record SSH download audit event")
    return StreamingResponse(
        stream_remote_file(), media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "Cache-Control": "no-store"},
    )


@app.get("/api/nodes/{node_id}/metrics")
def metrics(node_id: int, hours: int = 24, resolution: str = "auto", _: sqlite3.Row = Depends(current_user)):
    hours = min(max(hours, 1), METRIC_ROLLUP_RETENTION_DAYS * 24)
    if resolution not in {"auto", "raw", "hour"}:
        raise HTTPException(422, "resolution must be auto, raw or hour")
    use_hourly = resolution == "hour" or (resolution == "auto" and hours > 48)
    cutoff = utc_ts() - hours * 3600
    with db() as conn:
        if not conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
            raise HTTPException(404, "Node not found")
        if not use_hourly:
            rows = conn.execute(
                "SELECT ts,cpu,ram,swap,disk,load1,rx_bps,tx_bps,uptime,connections FROM metrics WHERE node_id=? AND ts>? ORDER BY ts LIMIT 20000",
                (node_id, cutoff),
            ).fetchall()
            return [{**dict(row), "resolution": "raw"} for row in rows]
        current_bucket = (utc_ts() // 3600) * 3600
        rolled = conn.execute(
            """SELECT bucket ts,cpu_avg cpu,ram_avg ram,NULL swap,disk_max disk,load_avg load1,
                      rx_avg rx_bps,tx_avg tx_bps,NULL uptime,connections_max connections,samples
               FROM metric_rollups WHERE node_id=? AND bucket>=? AND bucket<? ORDER BY bucket""",
            (node_id, (cutoff // 3600) * 3600, current_bucket),
        ).fetchall()
        raw_hourly = conn.execute(
            """SELECT (ts/3600)*3600 ts,AVG(cpu) cpu,AVG(ram) ram,AVG(swap) swap,MAX(disk) disk,AVG(load1) load1,
                      AVG(rx_bps) rx_bps,AVG(tx_bps) tx_bps,MAX(uptime) uptime,MAX(connections) connections,
                      COUNT(*) samples
               FROM metrics WHERE node_id=? AND ts>=? GROUP BY (ts/3600)*3600 ORDER BY ts""",
            (node_id, cutoff),
        ).fetchall()
    rollup_buckets = {int(row["ts"]) for row in rolled}
    combined = [*rolled, *(row for row in raw_hourly if int(row["ts"]) not in rollup_buckets)]
    combined.sort(key=lambda row: int(row["ts"]))
    return [{**dict(row), "resolution": "hour"} for row in combined]


@app.get("/api/tunnels")
def list_tunnels(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute("""SELECT tunnels.*,nodes.name node_name,nodes.host node_host,
            nodes.observed_ip node_observed_ip,nodes.region,nodes.role node_role,
            nodes.last_seen node_last_seen,nodes.status node_agent_status,
            CASE WHEN nodes.ssh_password_enc IS NOT NULL OR nodes.ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END node_ssh_configured
            FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id ORDER BY tunnels.status DESC,tunnels.name""").fetchall()
        nodes = conn.execute("""SELECT id,name,host,observed_ip,region,role,status,last_seen,
            CASE WHEN ssh_password_enc IS NOT NULL OR ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END ssh_configured FROM nodes""").fetchall()
        deployments = conn.execute("SELECT name,iran_node_id,kharej_node_id FROM plugin_deployments WHERE lifecycle!='removed'").fetchall()
    now = utc_ts()
    node_index = {row["id"]: dict(row) for row in nodes}
    deployment_index = {row["name"]: dict(row) for row in deployments}
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            details = json.loads(item.get("details") or "{}")
        except (TypeError, ValueError):
            details = {}
        item["tunnel_role"] = str(details.get("role") or "")
        item["target_host"] = str(details.get("target_host") or "")
        item["target_port"] = details.get("target_port")
        item["user_ports"] = details.get("user_ports") if isinstance(details.get("user_ports"), list) else []
        item["transport"] = str(details.get("transport") or "unknown")
        item["profile"] = str(details.get("profile") or "unknown")
        item["restart_every"] = str(details.get("restart_every") or "off")
        item["peer_ips"] = [normalize_ip(value) for value in details.get("peer_ips", []) if value] if isinstance(details.get("peer_ips"), list) else []
        item["node_agent_online"] = bool(item["node_last_seen"] and item["node_last_seen"] >= now - NODE_STALE_AFTER)
        if not item["node_agent_online"]:
            item["status"] = "stale"
        item.pop("details", None)
        result.append(item)

    for item in result:
        peer_id: int | None = None
        deployment = deployment_index.get(item["name"])
        if deployment and item["node_id"] in {deployment["iran_node_id"], deployment["kharej_node_id"]}:
            peer_id = deployment["kharej_node_id"] if item["node_id"] == deployment["iran_node_id"] else deployment["iran_node_id"]
        if not peer_id:
            opposite = next((candidate for candidate in result if candidate["node_id"] != item["node_id"] and candidate["name"] == item["name"] and (not item["tunnel_role"] or not candidate["tunnel_role"] or candidate["tunnel_role"] != item["tunnel_role"])), None)
            if opposite:
                peer_id = opposite["node_id"]
        remote_addresses = {normalize_ip(value) for value in item["peer_ips"]}
        if item["target_host"] not in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            remote_addresses.add(item["target_host"])
        if not peer_id and remote_addresses:
            peer = next((node for node in node_index.values() if remote_addresses.intersection({normalize_ip(node["host"]), normalize_ip(node.get("observed_ip"))})), None)
            if peer:
                peer_id = peer["id"]
        peer = node_index.get(peer_id) if peer_id else None
        item["peer_node_id"] = peer_id
        item["peer_name"] = peer["name"] if peer else None
        item["peer_host"] = (peer["host"] or peer.get("observed_ip")) if peer else (item["peer_ips"][0] if item["peer_ips"] else (item["target_host"] if item["target_host"] not in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"} else None))
        item["peer_role"] = peer["role"] if peer else None
        item["peer_agent_online"] = bool(peer and peer.get("last_seen") and peer["last_seen"] >= now - NODE_STALE_AFTER)
        item["peer_ssh_configured"] = bool(peer and peer.get("ssh_configured"))
    return result


@app.post("/api/tunnels/{tunnel_id}/action", status_code=202)
def tunnel_action(tunnel_id: int, body: TunnelActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        tunnel = conn.execute("SELECT tunnels.*,nodes.last_seen FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id WHERE tunnels.id=?", (tunnel_id,)).fetchone()
        if not tunnel:
            raise HTTPException(404, "Tunnel not found")
        if not tunnel["last_seen"] or tunnel["last_seen"] < utc_ts() - NODE_STALE_AFTER:
            raise HTTPException(409, "The tunnel Agent is offline")
        service = str(tunnel["service"] or "")
        plugin_id = "dark-ghostpro" if service.startswith("ghostpro@") else "dark-packetpro" if service.startswith("paqetpro@") else "dark-backhaul" if service.startswith("backhaul@") else None
        if not plugin_id:
            raise HTTPException(422, "This tunnel is not managed by a DARK NOC plugin")
        kind, payload = {
            "start": ("tunnel_control", {"name": tunnel["name"], "action": "start", "plugin_id": plugin_id}),
            "stop": ("tunnel_control", {"name": tunnel["name"], "action": "stop", "plugin_id": plugin_id}),
            "restart": ("tunnel_control", {"name": tunnel["name"], "action": "restart", "plugin_id": plugin_id}),
            "logs": ("logs", {"service": service}), "status": ("service_status", {"service": service}),
            "test": ("tunnel_test", {}), "install": ("plugin_install", {"plugin_id": plugin_id}),
        }[body.action]
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (tunnel["node_id"], kind, json.dumps(payload), user["id"], utc_ts())).lastrowid
    audit(user["id"], f"tunnel_{body.action}", tunnel["name"], str(tunnel_id), request.client.host if request.client else None)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/nodes/{node_id}/plugins/{plugin_id}/install", status_code=202)
def install_plugin(node_id: int, plugin_id: str, request: Request, user: sqlite3.Row = Depends(current_user)):
    if plugin_id not in {item["id"] for item in PLUGIN_CATALOG}:
        raise HTTPException(404, "Plugin not found")
    with db() as conn:
        node = conn.execute("SELECT id,name,last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        if not node["last_seen"] or node["last_seen"] < utc_ts() - NODE_STALE_AFTER:
            raise HTTPException(409, "Agent must be online")
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, "plugin_install", json.dumps({"plugin_id": plugin_id}), user["id"], utc_ts())).lastrowid
    audit(user["id"], "plugin_install", node["name"], plugin_id, request.client.host if request.client else None)
    return {"job_id": job_id, "status": "queued"}


@app.put("/api/tunnels/{tunnel_id}", status_code=202)
def reconfigure_tunnel(tunnel_id: int, body: TunnelReconfigureBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    ports = sorted(set(body.user_ports))
    with db() as conn:
        tunnel = conn.execute("SELECT * FROM tunnels WHERE id=?", (tunnel_id,)).fetchone()
        if not tunnel:
            raise HTTPException(404, "Tunnel not found")
        managed = conn.execute("SELECT * FROM plugin_deployments WHERE name=? AND lifecycle='active' AND (iran_node_id=? OR kharej_node_id=?)", (tunnel["name"], tunnel["node_id"], tunnel["node_id"])).fetchone()
        hybrid = conn.execute("SELECT * FROM hybrid_deployments WHERE name=? AND lifecycle='active' AND iran_node_id=?", (tunnel["name"], tunnel["node_id"])).fetchone()
        deployment = managed or hybrid
        if not deployment:
            raise HTTPException(409, "This discovered tunnel has no recoverable DARK NOC pairing token; recreate it from the panel to edit configuration")
        settings = json.loads(deployment["settings"])
        plugin = next((item for item in PLUGIN_CATALOG if item["id"] == settings.get("plugin_id")), None)
        if not plugin or body.transport not in plugin["transports"]:
            raise HTTPException(422, "Transport is not supported by this plugin")
        if any(port < 1 or port > 65535 or port == int(settings["tunnel_port"]) for port in ports):
            raise HTTPException(422, "Invalid user port or collision with the tunnel port")
        settings.update({"user_ports": ports, "transport": body.transport, "profile": body.profile, "restart_every": body.restart_every})
        selected_certificate_id = body.certificate_id or settings.get("certificate_id")
        cert = certificate_for_deployment(conn, selected_certificate_id, tunnel["node_id"], body.transport)
        for key in ("certificate_id", "certificate_domain", "certificate_path", "certificate_key_path"):
            settings.pop(key, None)
        if cert:
            settings.update(cert)
            settings["endpoint"] = cert["certificate_domain"]
        token = decrypt(deployment["pair_token_enc"])
        jobs: dict[str, int] = {}
        roles = plugin["roles"]
        if managed:
            for side, node_id, role in (("iran", managed["iran_node_id"], roles["iran"]), ("kharej", managed["kharej_node_id"], roles["kharej"])):
                jobs[side] = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, "plugin_deploy", json.dumps(plugin_job_payload(settings, token, role)), user["id"], utc_ts())).lastrowid
            conn.execute("UPDATE plugin_deployments SET settings=?,iran_job_id=?,kharej_job_id=? WHERE id=?", (json.dumps(settings), jobs["iran"], jobs["kharej"], managed["id"]))
        else:
            jobs["iran"] = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (hybrid["iran_node_id"], "plugin_deploy", json.dumps(plugin_job_payload(settings, token, roles["iran"])), user["id"], utc_ts())).lastrowid
            pair_code = plugin_pair_code(settings, token)
            conn.execute("UPDATE hybrid_deployments SET settings=?,iran_job_id=?,pair_code_hash=?,updated_at=? WHERE id=?", (json.dumps(settings), jobs["iran"], token_hash(pair_code), utc_ts(), hybrid["id"]))
    audit(user["id"], "tunnel_reconfigure", tunnel["name"], json.dumps({"ports": ports, "transport": body.transport, "profile": body.profile}), request.client.host if request.client else None)
    result: dict[str, Any] = {"status": "queued", "jobs": jobs, "mode": "managed" if managed else "pair_code"}
    if hybrid:
        result["pair_code"] = pair_code
        result["foreign_action_required"] = True
    return result


@app.delete("/api/tunnels/{tunnel_id}", status_code=202)
def remove_tunnel_from_manager(tunnel_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        tunnel = conn.execute("SELECT * FROM tunnels WHERE id=?", (tunnel_id,)).fetchone()
        if not tunnel:
            raise HTTPException(404, "Tunnel not found")
        managed = conn.execute("SELECT * FROM plugin_deployments WHERE name=? AND lifecycle='active' AND (iran_node_id=? OR kharej_node_id=?)", (tunnel["name"], tunnel["node_id"], tunnel["node_id"])).fetchone()
        hybrid = conn.execute("SELECT * FROM hybrid_deployments WHERE name=? AND lifecycle='active' AND iran_node_id=?", (tunnel["name"], tunnel["node_id"])).fetchone()
        jobs: dict[str, int] = {}
        if managed:
            for side, node_id in (("iran", managed["iran_node_id"]), ("kharej", managed["kharej_node_id"])):
                settings = json.loads(managed["settings"])
                jobs[side] = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, "plugin_remove", json.dumps({"plugin_id": settings.get("plugin_id", "dark-backhaul"), "name": tunnel["name"]}), user["id"], now)).lastrowid
            conn.execute("UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing' WHERE id=?", (jobs["iran"], jobs["kharej"], managed["id"]))
        else:
            plugin_id = json.loads(hybrid["settings"]).get("plugin_id", "dark-backhaul") if hybrid else ("dark-ghostpro" if str(tunnel["service"]).startswith("ghostpro@") else "dark-backhaul")
            jobs["iran"] = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (tunnel["node_id"], "plugin_remove", json.dumps({"plugin_id": plugin_id, "name": tunnel["name"]}), user["id"], now)).lastrowid
            if hybrid:
                conn.execute("UPDATE hybrid_deployments SET iran_job_id=?,lifecycle='removing',updated_at=? WHERE id=?", (jobs["iran"], now, hybrid["id"]))
    audit(user["id"], "tunnel_remove", tunnel["name"], "managed pair" if managed else "selected node", request.client.host if request.client else None)
    return {"status": "queued", "jobs": jobs, "foreign_action_required": bool(hybrid)}


@app.get("/api/plugins")
def list_plugins(_: sqlite3.Row = Depends(current_user)):
    return PLUGIN_CATALOG


def transport_requires_certificate(transport: str) -> bool:
    return transport.casefold() in {"tls", "wss", "wssmux", "h2", "http2", "grpc", "relay+tls", "relay+wss", "relay+h2", "relay+grpc"}


def certificate_for_deployment(conn: sqlite3.Connection, certificate_id: int | None, node_id: int, transport: str) -> dict[str, Any] | None:
    if not transport_requires_certificate(transport):
        return None
    if not certificate_id:
        raise HTTPException(422, "This transport requires a valid TLS certificate")
    row = conn.execute("SELECT * FROM certificates WHERE id=? AND node_id=?", (certificate_id, node_id)).fetchone()
    if not row:
        raise HTTPException(404, "Certificate was not found on the selected Iran node")
    if row["status"] != "valid" or not row["expires_at"] or row["expires_at"] <= utc_ts() + 86400:
        raise HTTPException(409, "Certificate is not valid or expires in less than 24 hours")
    return {"certificate_id": row["id"], "certificate_domain": row["domain"], "certificate_path": row["cert_path"], "certificate_key_path": row["key_path"]}


@app.get("/api/certificates")
def list_certificates(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute("SELECT c.*,n.name node_name,n.host node_host FROM certificates c JOIN nodes n ON n.id=c.node_id ORDER BY c.domain").fetchall()
    return [{key: row[key] for key in row.keys() if key not in {"key_path"}} for row in rows]


@app.post("/api/certificates", status_code=202)
def issue_certificate(body: CertificateBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    domain = body.domain.casefold().rstrip(".")
    with db() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (body.node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        if not node["last_seen"] or node["last_seen"] < utc_ts() - NODE_STALE_AFTER:
            raise HTTPException(409, "The selected Agent must be online")
        try:
            resolved = {normalize_ip(item[4][0]) for item in socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise HTTPException(422, "Domain DNS does not resolve yet")
        expected = {normalize_ip(node["host"]), normalize_ip(node["observed_ip"])} - {""}
        if not resolved.intersection(expected):
            raise HTTPException(422, f"DNS mismatch: domain resolves to {', '.join(sorted(resolved))}, not the selected node")
        now = utc_ts()
        existing = conn.execute("SELECT id FROM certificates WHERE node_id=? AND domain=?", (body.node_id, domain)).fetchone()
        if existing:
            cert_id = existing["id"]
            conn.execute("UPDATE certificates SET status='pending',last_error=NULL,updated_at=? WHERE id=?", (now, cert_id))
        else:
            cert_id = conn.execute("INSERT INTO certificates(node_id,domain,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)", (body.node_id, domain, "pending", user["id"], now, now)).lastrowid
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (body.node_id, "certificate_issue", json.dumps({"certificate_id": cert_id, "domain": domain}), user["id"], now)).lastrowid
        conn.execute("UPDATE certificates SET job_id=? WHERE id=?", (job_id, cert_id))
    audit(user["id"], "certificate_issue", domain, node["name"], request.client.host if request.client else None)
    return {"certificate_id": cert_id, "job_id": job_id, "status": "pending"}


@app.post("/api/certificates/{certificate_id}/renew", status_code=202)
def renew_certificate(certificate_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        cert = conn.execute("SELECT c.*,n.last_seen FROM certificates c JOIN nodes n ON n.id=c.node_id WHERE c.id=?", (certificate_id,)).fetchone()
        if not cert: raise HTTPException(404, "Certificate not found")
        if not cert["last_seen"] or cert["last_seen"] < utc_ts() - NODE_STALE_AFTER: raise HTTPException(409, "Agent is offline")
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (cert["node_id"], "certificate_issue", json.dumps({"certificate_id": cert["id"], "domain": cert["domain"], "renew": True}), user["id"], utc_ts())).lastrowid
        conn.execute("UPDATE certificates SET status='renewing',job_id=?,last_error=NULL,updated_at=? WHERE id=?", (job_id, utc_ts(), certificate_id))
    audit(user["id"], "certificate_renew", cert["domain"], str(cert["node_id"]), request.client.host if request.client else None)
    return {"job_id": job_id, "status": "renewing"}


PAIR_TRANSPORT_INDEX = {"tcp": 1, "tcpmux": 2, "ws": 3, "wsmux": 4, "wss": 5, "wssmux": 6, "udp": 7}
PAQET_CORE_TAG = "v1.0.0-alpha.21"
PACKET_PROFILES = {
    "stable": ("normal", 2, 1150), "balanced": ("fast", 4, 1150),
    "lowping": ("fast2", 4, 1150), "turbo": ("fast3", 8, 1250),
}


def dark_backhaul_pair_code(settings: dict[str, Any], token: str) -> str:
    """Emit the exact B2 code consumed by dark-backhaul.sh's normal KHAREJ flow."""
    ports = ",".join(str(port) for port in settings["user_ports"])
    raw = "|".join((
        "B2", settings["endpoint"], str(settings["tunnel_port"]), token,
        str(PAIR_TRANSPORT_INDEX[settings["transport"]]), settings["profile"],
        settings["restart_every"], ports,
    ))
    return "DBH-" + base64.b64encode(raw.encode()).decode()


def plugin_pair_code(settings: dict[str, Any], token: str) -> str:
    if settings["plugin_id"] == "dark-backhaul":
        return dark_backhaul_pair_code(settings, token)
    if settings["plugin_id"] == "dark-ghostpro":
        ports = ",".join(str(port) for port in settings["user_ports"])
        raw = "|".join(("GPC1", settings["endpoint"], str(settings["tunnel_port"]), token, settings["transport"], settings["profile"], settings["restart_every"], ports))
        return "DGP-" + base64.b64encode(raw.encode()).decode()
    if settings["plugin_id"] == "dark-packetpro":
        mode, conn, mtu = PACKET_PROFILES[settings["profile"]]
        mode_index = {"normal": 0, "fast": 1, "fast2": 2, "fast3": 3}[mode]
        ports = ",".join(f"t{port}" for port in settings["user_ports"])
        raw = "|".join((
            "N1", settings["name"], settings["endpoint"], str(settings["tunnel_port"]), token,
            "1", str(mode_index), str(conn), str(mtu), settings["restart_every"], PAQET_CORE_TAG,
            settings["pair_id"], str(settings["pair_created"]), ports,
        ))
        return "DPP-N1-" + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    raise HTTPException(404, "Plugin not found")


def plugin_job_payload(settings: dict[str, Any], token: str, role: str) -> dict[str, Any]:
    """Keep server-local certificate paths off KHAREJ job payloads."""
    payload = {**settings, "token": token, "role": role}
    if role == "client":
        payload.pop("certificate_path", None)
        payload.pop("certificate_key_path", None)
    return payload


@app.post("/api/plugins/{plugin_id}/pair-code", status_code=202)
def deploy_plugin_pair_code(plugin_id: str, body: PairCodeDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    if plugin_id not in {item["id"] for item in PLUGIN_CATALOG}:
        raise HTTPException(404, "Plugin not found")
    catalog = next(item for item in PLUGIN_CATALOG if item["id"] == plugin_id)
    if body.transport not in catalog["transports"]:
        raise HTTPException(422, f"Unsupported {catalog['name']} transport")
    ports = sorted(set(body.user_ports))
    if any(port < 1 or port > 65535 or port == body.tunnel_port for port in ports):
        raise HTTPException(422, "User ports must be unique valid ports and cannot equal the tunnel port")
    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    if plugin_id == "dark-packetpro" and not body.kharej_endpoint:
        raise HTTPException(422, "DARK Packet Pro requires the KHAREJ public IPv4")
    if plugin_id == "dark-packetpro":
        try:
            if ipaddress.ip_address(body.kharej_endpoint).version != 4:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "DARK Packet Pro KHAREJ endpoint must be an IPv4 address")
    endpoint = body.kharej_endpoint if plugin_id == "dark-packetpro" else body.iran_endpoint
    settings = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every,
    }
    if plugin_id == "dark-packetpro":
        settings.update({"pair_id": secrets.token_hex(8), "pair_created": utc_ts(), "core_tag": PAQET_CORE_TAG})
    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,role,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        if not iran:
            raise HTTPException(404, "Iran node was not found")
        if iran["role"] not in {"edge", "hub"}:
            raise HTTPException(422, "Select an Iran Edge or Hub node for the IRAN side")
        cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
        if cert:
            settings.update(cert)
            if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                raise HTTPException(422, "TLS endpoint must match the selected certificate domain")
        elif plugin_id != "dark-packetpro" and body.iran_endpoint.casefold() != iran["host"].casefold():
            raise HTTPException(422, "Iran endpoint must match the selected Iran node host/IP")
        if not iran["last_seen"] or iran["last_seen"] < now - NODE_STALE_AFTER:
            raise HTTPException(409, "The Iran Agent must be online before deployment")
        managed_active = conn.execute("SELECT 1 FROM plugin_deployments WHERE name=? AND (iran_node_id=? OR kharej_node_id=?) AND lifecycle IN ('active','removing','rolling_back','rollback_failed') LIMIT 1", (body.name, iran["id"], iran["id"])).fetchone()
        hybrid_active = conn.execute("SELECT 1 FROM hybrid_deployments WHERE name=? AND iran_node_id=? AND lifecycle IN ('active','removing') LIMIT 1", (body.name, iran["id"])).fetchone()
        if managed_active or hybrid_active:
            raise HTTPException(409, "A deployment with this name is already active on the selected Iran node")
        pair_code = plugin_pair_code(settings, token)
        iran_job = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (iran["id"], "plugin_deploy", json.dumps(plugin_job_payload(settings, token, catalog["roles"]["iran"])), user["id"], now),
        ).lastrowid
        deployment_id = conn.execute(
            "INSERT INTO hybrid_deployments(plugin_id,name,iran_node_id,iran_job_id,iran_endpoint,remote_label,settings,pair_token_enc,pair_code_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (plugin_id, body.name, iran["id"], iran_job, endpoint, body.remote_label, json.dumps(settings), encrypt(token), token_hash(pair_code), user["id"], now, now),
        ).lastrowid
    audit(user["id"], "plugin_pair_create", body.name, f"{iran['name']} -> pair-code:{body.remote_label}", request.client.host if request.client else None)
    return {
        "deployment_id": deployment_id, "mode": "pair_code", "status": "queued", "iran_job_id": iran_job,
        "pair_code": pair_code,
        "instructions": ["Wait until the IRAN side shows READY.", f"Run {catalog['name']} on the foreign server.", "Select KHAREJ, then choose Connect with DARK NOC Pair Code.", "Paste the Pair Code exactly as shown."],
    }


@app.post("/api/plugins/{plugin_id}/deploy", status_code=202)
def deploy_plugin(plugin_id: str, body: PluginDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    if plugin_id not in {item["id"] for item in PLUGIN_CATALOG}:
        raise HTTPException(404, "Plugin not found")
    catalog = next(item for item in PLUGIN_CATALOG if item["id"] == plugin_id)
    if body.transport not in catalog["transports"]:
        raise HTTPException(422, f"Unsupported {catalog['name']} transport")
    if body.iran_node_id == body.kharej_node_id:
        raise HTTPException(422, "IRAN and KHAREJ must be different nodes")
    ports = sorted(set(body.user_ports))
    if any(port < 1 or port > 65535 or port == body.tunnel_port for port in ports):
        raise HTTPException(422, "User ports must be unique valid ports and cannot equal the tunnel port")
    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    common = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": body.iran_endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every, "token": token,
    }
    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,role,status,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        kharej = conn.execute("SELECT id,name,host,observed_ip,role,status,last_seen FROM nodes WHERE id=?", (body.kharej_node_id,)).fetchone()
        if not iran or not kharej:
            raise HTTPException(404, "One or both nodes were not found")
        if iran["role"] != "edge" or kharej["role"] != "exit":
            raise HTTPException(422, "Select an Iran Edge node for IRAN and a Global Exit node for KHAREJ")
        if plugin_id == "dark-packetpro":
            packet_endpoint = normalize_ip(kharej["observed_ip"] or kharej["host"])
            try:
                if ipaddress.ip_address(packet_endpoint).version != 4: raise ValueError
            except ValueError:
                raise HTTPException(422, "The KHAREJ node needs a detected public IPv4 for Packet Pro")
            common["endpoint"] = packet_endpoint
        cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
        if cert:
            common.update(cert)
            if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                raise HTTPException(422, "TLS endpoint must match the selected certificate domain")
        elif plugin_id != "dark-packetpro" and body.iran_endpoint.casefold() != iran["host"].casefold():
            raise HTTPException(422, "Iran endpoint must match the selected Iran node host/IP")
        if not iran["last_seen"] or iran["last_seen"] < now - NODE_STALE_AFTER or not kharej["last_seen"] or kharej["last_seen"] < now - NODE_STALE_AFTER:
            raise HTTPException(409, "Both nodes must be online before deployment")
        active = conn.execute("""SELECT 1 FROM plugin_deployments d
          WHERE d.name=? AND (d.iran_node_id IN (?,?) OR d.kharej_node_id IN (?,?))
            AND d.lifecycle IN ('active','removing','rolling_back','rollback_failed') LIMIT 1""",
          (body.name, iran["id"], kharej["id"], iran["id"], kharej["id"])).fetchone()
        if active:
            raise HTTPException(409, "A deployment with this name is already running on one of the selected nodes")
        iran_job = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (iran["id"], "plugin_deploy", json.dumps(plugin_job_payload(common, token, catalog["roles"]["iran"])), user["id"], now),
        ).lastrowid
        kharej_job = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (kharej["id"], "plugin_deploy", json.dumps(plugin_job_payload(common, token, catalog["roles"]["kharej"])), user["id"], now),
        ).lastrowid
        deployment_id = conn.execute(
            "INSERT INTO plugin_deployments(plugin_id,name,iran_node_id,kharej_node_id,iran_job_id,kharej_job_id,settings,pair_token_enc,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (plugin_id, body.name, iran["id"], kharej["id"], iran_job, kharej_job, json.dumps({k: v for k, v in common.items() if k != "token"}), encrypt(token), user["id"], now),
        ).lastrowid
    audit(user["id"], "plugin_deploy", body.name, f"{plugin_id}: {iran['name']} -> {kharej['name']}", request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "jobs": {"iran": iran_job, "kharej": kharej_job}}


@app.get("/api/plugin-deployments")
def list_plugin_deployments(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute("""
          SELECT d.*, i.name iran_node, k.name kharej_node,
                 ji.status iran_status, ji.output iran_output,
                 jk.status kharej_status, jk.output kharej_output
          FROM plugin_deployments d
          JOIN nodes i ON i.id=d.iran_node_id JOIN nodes k ON k.id=d.kharej_node_id
          LEFT JOIN jobs ji ON ji.id=d.iran_job_id LEFT JOIN jobs jk ON jk.id=d.kharej_job_id
          ORDER BY d.id DESC LIMIT 100
        """).fetchall()
        hybrid_rows = conn.execute("""
          SELECT d.*,i.name iran_node,j.status iran_status,j.output iran_output
          FROM hybrid_deployments d JOIN nodes i ON i.id=d.iran_node_id
          LEFT JOIN jobs j ON j.id=d.iran_job_id ORDER BY d.id DESC LIMIT 100
        """).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item.pop("pair_token_enc", None)
        statuses = {item.get("iran_status"), item.get("kharej_status")}
        lifecycle = item.get("lifecycle")
        if lifecycle in {"rolling_back", "rolled_back", "rollback_failed"}:
            item["status"] = lifecycle
        elif "failed" in statuses:
            item["status"] = "failed"
        elif statuses == {"completed"}:
            item["status"] = "removed" if lifecycle == "removing" else "completed"
        else:
            item["status"] = "running" if "running" in statuses or "completed" in statuses else "queued"
        item["settings"] = json.loads(item["settings"])
        item["mode"] = "managed"
        result.append(item)
    for row in hybrid_rows:
        item = dict(row)
        item.pop("pair_token_enc", None)
        iran_status = item.get("iran_status") or "queued"
        lifecycle = item.get("lifecycle")
        if lifecycle == "removing" and iran_status == "completed":
            status = "removed"
        elif iran_status == "failed":
            status = "failed"
        elif iran_status == "completed":
            status = "awaiting_pair"
        else:
            status = iran_status if iran_status in {"queued", "running"} else "queued"
        item.update({"mode": "pair_code", "kharej_node": item["remote_label"], "kharej_status": "pair code", "status": status, "settings": json.loads(item["settings"])})
        result.append(item)
    return sorted(result, key=lambda item: (item["created_at"], item["mode"] == "pair_code", item["id"]), reverse=True)[:100]


@app.post("/api/hybrid-deployments/{deployment_id}/pair-code")
def recover_hybrid_pair_code(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM hybrid_deployments WHERE id=?", (deployment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Pair Code deployment not found")
    if row["lifecycle"] != "active":
        raise HTTPException(409, "This Pair Code deployment is no longer active")
    token = decrypt(row["pair_token_enc"])
    pair_code = plugin_pair_code(json.loads(row["settings"]), token)
    if not hmac.compare_digest(token_hash(pair_code), row["pair_code_hash"]):
        raise HTTPException(500, "Pair Code integrity check failed")
    audit(user["id"], "plugin_pair_reveal", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "pair_code": pair_code}


@app.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)
def retry_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        row = conn.execute("SELECT d.*,j.status iran_status FROM hybrid_deployments d LEFT JOIN jobs j ON j.id=d.iran_job_id WHERE d.id=?", (deployment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Pair Code deployment not found")
        if row["lifecycle"] != "active":
            raise HTTPException(409, "This Pair Code deployment is no longer active")
        if row["iran_status"] in {"queued", "running"}:
            raise HTTPException(409, "The Iran deployment is still running")
        online = conn.execute("SELECT 1 FROM nodes WHERE id=? AND last_seen>=?", (row["iran_node_id"], now - NODE_STALE_AFTER)).fetchone()
        if not online:
            raise HTTPException(409, "The Iran Agent must be online before retry")
        settings = json.loads(row["settings"])
        plugin = next((item for item in PLUGIN_CATALOG if item["id"] == row["plugin_id"]), None)
        if not plugin:
            raise HTTPException(409, "Plugin is no longer available")
        payload = {**settings, "token": decrypt(row["pair_token_enc"]), "role": plugin["roles"]["iran"]}
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (row["iran_node_id"], "plugin_deploy", json.dumps(payload), user["id"], now)).lastrowid
        conn.execute("UPDATE hybrid_deployments SET iran_job_id=?,updated_at=? WHERE id=?", (job_id, now, deployment_id))
    audit(user["id"], "plugin_pair_retry", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id}


@app.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)
def remove_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        row = conn.execute("SELECT d.*,j.status iran_status FROM hybrid_deployments d LEFT JOIN jobs j ON j.id=d.iran_job_id WHERE d.id=?", (deployment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Pair Code deployment not found")
        if row["lifecycle"] == "removing":
            raise HTTPException(409, "The Iran side is already being removed")
        if row["iran_status"] in {"queued", "running"}:
            raise HTTPException(409, "Wait for the current Iran job to finish")
        job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (row["iran_node_id"], "plugin_remove", json.dumps({"plugin_id": row["plugin_id"], "name": row["name"]}), user["id"], now)).lastrowid
        conn.execute("UPDATE hybrid_deployments SET iran_job_id=?,lifecycle='removing',updated_at=? WHERE id=?", (job_id, now, deployment_id))
    audit(user["id"], "plugin_pair_remove", row["name"], "Iran side only; foreign side is script-managed", request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id, "foreign_action_required": True}


@app.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)
def retry_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        row = conn.execute("""SELECT d.*,ji.status iran_status,jk.status kharej_status
          FROM plugin_deployments d LEFT JOIN jobs ji ON ji.id=d.iran_job_id LEFT JOIN jobs jk ON jk.id=d.kharej_job_id
          WHERE d.id=?""", (deployment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Deployment not found")
        if row["lifecycle"] == "removing" and row["iran_status"] == "completed" and row["kharej_status"] == "completed":
            raise HTTPException(409, "This deployment was removed; create a new deployment instead")
        if row["lifecycle"] == "rolling_back":
            raise HTTPException(409, "Automatic rollback is still running")
        if row["iran_status"] in {"queued", "running"} or row["kharej_status"] in {"queued", "running"}:
            raise HTTPException(409, "Deployment is still running")
        online_nodes = conn.execute("SELECT COUNT(*) count FROM nodes WHERE id IN (?,?) AND last_seen>=?", (row["iran_node_id"], row["kharej_node_id"], now - NODE_STALE_AFTER)).fetchone()["count"]
        if online_nodes != 2:
            raise HTTPException(409, "Both nodes must be online before retry")
        token = decrypt(row["pair_token_enc"])
        if not token:
            raise HTTPException(409, "This older deployment has no recoverable pair token; create it again")
        settings = json.loads(row["settings"])
        plugin = next((item for item in PLUGIN_CATALOG if item["id"] == row["plugin_id"]), None)
        if not plugin:
            raise HTTPException(409, "Plugin is no longer available")
        was_rolled_back = row["lifecycle"] == "rolled_back"
        conn.execute("UPDATE plugin_deployments SET lifecycle='active' WHERE id=?", (deployment_id,))
        new_jobs: dict[str, int] = {}
        for side, node_id, role, old_status in (("iran", row["iran_node_id"], plugin["roles"]["iran"], row["iran_status"]), ("kharej", row["kharej_node_id"], plugin["roles"]["kharej"], row["kharej_status"])):
            if old_status == "completed" and not was_rolled_back:
                continue
            job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, "plugin_deploy", json.dumps(plugin_job_payload(settings, token, role)), user["id"], now)).lastrowid
            conn.execute(f"UPDATE plugin_deployments SET {side}_job_id=? WHERE id=?", (job_id, deployment_id))
            new_jobs[side] = job_id
    audit(user["id"], "plugin_retry", str(deployment_id), json.dumps(new_jobs), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "jobs": new_jobs}


@app.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)
def remove_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        row = conn.execute("SELECT * FROM plugin_deployments WHERE id=?", (deployment_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Deployment not found")
        if row["lifecycle"] == "removing":
            raise HTTPException(409, "Deployment is already being removed or has been removed")
        active_jobs = conn.execute("SELECT COUNT(*) count FROM jobs WHERE id IN (?,?) AND status IN ('queued','running')", (row["iran_job_id"], row["kharej_job_id"])).fetchone()["count"]
        if active_jobs:
            raise HTTPException(409, "Wait for the current deployment action to finish")
        jobs = {}
        for side, node_id in (("iran", row["iran_node_id"]), ("kharej", row["kharej_node_id"])):
            jobs[side] = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, "plugin_remove", json.dumps({"plugin_id": row["plugin_id"], "name": row["name"]}), user["id"], now)).lastrowid
        conn.execute("UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing' WHERE id=?", (jobs["iran"], jobs["kharej"], deployment_id))
    audit(user["id"], "plugin_remove", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "jobs": jobs}


@app.get("/api/monitors")
def list_monitors(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            """SELECT monitors.*,nodes.name node_name,nodes.role node_role,nodes.host node_host,
                      nodes.last_seen node_last_seen
               FROM monitors JOIN nodes ON nodes.id=monitors.node_id
               ORDER BY CASE monitors.status WHEN 'down' THEN 0 WHEN 'degraded' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                        monitors.name COLLATE NOCASE"""
        ).fetchall()
    return [public_monitor(row) for row in rows]


@app.post("/api/monitors", status_code=201)
def create_monitor(body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    target, port, secret, oid = monitor_values(body)
    now = utc_ts()
    try:
        with db() as conn:
            node = conn.execute("SELECT name FROM nodes WHERE id=?", (body.node_id,)).fetchone()
            if not node:
                raise HTTPException(404, "Node not found")
            monitor_id = conn.execute(
                """INSERT INTO monitors(node_id,name,kind,target,port,secret_enc,snmp_oid,interval_seconds,
                       timeout_seconds,expected_status,enabled,status,next_run_at,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, "pending", now,
                 user["id"], now, now),
            ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A monitor with this name already exists on the selected Node") from exc
    audit(user["id"], "monitor_create", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
    return {"id": monitor_id, "status": "pending"}


@app.put("/api/monitors/{monitor_id}")
def update_monitor(monitor_id: int, body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    try:
        with db() as conn:
            current = conn.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
            if not current:
                raise HTTPException(404, "Monitor not found")
            if not conn.execute("SELECT 1 FROM nodes WHERE id=?", (body.node_id,)).fetchone():
                raise HTTPException(404, "Node not found")
            target, port, secret, oid = monitor_values(body, current["secret_enc"])
            old_title = f"Monitor {current['name']} is down"
            old_incidents = conn.execute(
                "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (current["node_id"], old_title),
            ).fetchall()
            for incident in old_incidents:
                resolve_incident(conn, incident["id"], "Monitor configuration changed", actor_id=user["id"])
            conn.execute(
                """UPDATE monitors SET node_id=?,name=?,kind=?,target=?,port=?,secret_enc=?,snmp_oid=?,
                       interval_seconds=?,timeout_seconds=?,expected_status=?,enabled=?,status='pending',
                       failure_streak=0,next_run_at=?,updated_at=? WHERE id=?""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, now, now, monitor_id),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "A monitor with this name already exists on the selected Node") from exc
    audit(user["id"], "monitor_update", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
    return {"ok": True}


@app.delete("/api/monitors/{monitor_id}")
def delete_monitor(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT node_id,name FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Monitor not found")
        title = f"Monitor {row['name']} is down"
        active = conn.execute(
            "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
            (row["node_id"], title),
        ).fetchall()
        for incident in active:
            resolve_incident(conn, incident["id"], "Monitor was removed by the operator", actor_id=user["id"])
        conn.execute("DELETE FROM monitors WHERE id=?", (monitor_id,))
    audit(user["id"], "monitor_delete", row["name"], str(monitor_id), request.client.host if request.client else None)
    return {"ok": True}


@app.post("/api/monitors/{monitor_id}/run", status_code=202)
def run_monitor_now(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    with db() as conn:
        monitor = conn.execute(
            "SELECT monitors.*,nodes.last_seen FROM monitors JOIN nodes ON nodes.id=monitors.node_id WHERE monitors.id=?",
            (monitor_id,),
        ).fetchone()
        if not monitor:
            raise HTTPException(404, "Monitor not found")
        if not monitor["last_seen"] or monitor["last_seen"] < now - NODE_STALE_AFTER:
            raise HTTPException(409, "Agent is offline")
        active = conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (monitor["job_id"],)).fetchone() if monitor["job_id"] else None
        if active:
            raise HTTPException(409, "Monitor check is already running")
        payload = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        if monitor["kind"] == "snmp":
            try:
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
            except Exception as exc:
                raise HTTPException(409, "Encrypted SNMP community cannot be read; edit and save the monitor again") from exc
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), user["id"], now),
        ).lastrowid
        conn.execute("UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?", (job_id, now + monitor["interval_seconds"], now, monitor_id))
    audit(user["id"], "monitor_run", monitor["name"], str(job_id), request.client.host if request.client else None)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/monitors/{monitor_id}/results")
def monitor_results(monitor_id: int, limit: int = 200, _: sqlite3.Row = Depends(current_user)):
    limit = min(max(limit, 1), 2000)
    with db() as conn:
        if not conn.execute("SELECT 1 FROM monitors WHERE id=?", (monitor_id,)).fetchone():
            raise HTTPException(404, "Monitor not found")
        rows = conn.execute(
            "SELECT ts,status,latency_ms,detail FROM monitor_results WHERE monitor_id=? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["detail"] = json.loads(item.get("detail") or "{}")
        except (TypeError, ValueError):
            item["detail"] = {"message": str(item.get("detail") or "")[:1000]}
        result.append(item)
    return result


@app.get("/api/fleet/operations")
def list_fleet_operations(limit: int = 100, _: sqlite3.Row = Depends(current_user)):
    limit = min(max(limit, 1), 500)
    with db() as conn:
        operations = conn.execute(
            "SELECT * FROM fleet_operations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for operation in operations:
            item = dict(operation)
            try:
                payload = json.loads(item.get("payload") or "{}")
                for key in list(payload):
                    if any(word in key.casefold() for word in ("token", "password", "secret", "community", "private_key")):
                        payload[key] = "[REDACTED]"
                item["payload"] = payload
            except (TypeError, ValueError):
                item["payload"] = {}
            rows = conn.execute(
                """SELECT fleet_operation_items.id,fleet_operation_items.node_id,fleet_operation_items.job_id,
                          fleet_operation_items.status,nodes.name node_name,jobs.output
                   FROM fleet_operation_items JOIN nodes ON nodes.id=fleet_operation_items.node_id
                   LEFT JOIN jobs ON jobs.id=fleet_operation_items.job_id
                   WHERE operation_id=? ORDER BY nodes.name COLLATE NOCASE""",
                (operation["id"],),
            ).fetchall()
            item["items"] = [dict(row) for row in rows]
            result.append(item)
    return result


@app.post("/api/fleet/operations", status_code=202)
def create_fleet_operation(
    body: FleetOperationBody,
    background: BackgroundTasks,
    request: Request,
    user: sqlite3.Row = Depends(current_user),
):
    node_ids = list(dict.fromkeys(body.node_ids))
    now = utc_ts()
    scheduled_at = int(body.scheduled_at or now)
    if scheduled_at > now + 366 * 86400:
        raise HTTPException(422, "Fleet operations can be scheduled at most one year ahead")
    if body.kind in {"restart_service", "service_status"} and not body.service:
        raise HTTPException(422, "This fleet operation requires a managed service name")
    if body.kind == "sync_agent" and scheduled_at > now + 5:
        raise HTTPException(422, "Agent synchronization must be started immediately")
    payload = dict(body.payload)
    if body.service:
        payload["service"] = body.service
    with db() as conn:
        placeholders = ",".join("?" for _ in node_ids)
        nodes = conn.execute(
            f"SELECT * FROM nodes WHERE id IN ({placeholders}) ORDER BY id",
            node_ids,
        ).fetchall()
        if len(nodes) != len(node_ids):
            raise HTTPException(404, "One or more selected Nodes do not exist")
        if body.kind == "sync_agent":
            for node in nodes:
                if node["role"] == "hub" or (not node["ssh_password_enc"] and not node["ssh_key_enc"]):
                    raise HTTPException(409, f"{node['name']} cannot be synchronized through remote SSH")
                if node["provision_status"] == "provisioning":
                    raise HTTPException(409, f"{node['name']} is already synchronizing")
        operation_id = conn.execute(
            """INSERT INTO fleet_operations(name,kind,payload,status,scheduled_at,created_by,created_at,started_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (body.name, body.kind, json.dumps(payload), "running" if body.kind == "sync_agent" else "scheduled",
             scheduled_at, user["id"], now, now if body.kind == "sync_agent" else None),
        ).lastrowid
        created_items: list[tuple[int, sqlite3.Row, str | None]] = []
        for node in nodes:
            enrollment = secrets.token_urlsafe(36) if body.kind == "sync_agent" else None
            if body.kind == "sync_agent":
                conn.execute(
                    "UPDATE nodes SET provision_status='provisioning',provision_output='',updated_at=? WHERE id=?",
                    (now, node["id"]),
                )
            item_id = conn.execute(
                "INSERT INTO fleet_operation_items(operation_id,node_id,status) VALUES(?,?,?)",
                (operation_id, node["id"], "running" if body.kind == "sync_agent" else "scheduled"),
            ).lastrowid
            created_items.append((item_id, node, enrollment))
        if body.kind != "sync_agent" and scheduled_at <= now:
            queue_due_fleet_operations(conn, now)
    if body.kind == "sync_agent":
        for item_id, node, enrollment in created_items:
            background.add_task(provision_node_for_fleet, item_id, node["id"], enrollment or secrets.token_urlsafe(36))
    audit(user["id"], "fleet_operation_create", body.name, f"{body.kind}:{len(node_ids)} nodes", request.client.host if request.client else None)
    return {"id": operation_id, "status": "running" if body.kind == "sync_agent" or scheduled_at <= now else "scheduled", "nodes": len(node_ids)}


@app.delete("/api/fleet/operations/{operation_id}")
def cancel_fleet_operation(operation_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        operation = conn.execute("SELECT * FROM fleet_operations WHERE id=?", (operation_id,)).fetchone()
        if not operation:
            raise HTTPException(404, "Fleet operation not found")
        if operation["status"] != "scheduled":
            raise HTTPException(409, "Only a scheduled operation can be cancelled")
        conn.execute("UPDATE fleet_operations SET status='cancelled',finished_at=? WHERE id=?", (utc_ts(), operation_id))
        conn.execute("UPDATE fleet_operation_items SET status='cancelled' WHERE operation_id=?", (operation_id,))
    audit(user["id"], "fleet_operation_cancel", operation["name"], str(operation_id), request.client.host if request.client else None)
    return {"ok": True}


@app.get("/api/incidents")
def list_incidents(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name,
                      (SELECT COUNT(*) FROM incident_events WHERE incident_id=incidents.id) event_count
               FROM incidents
               LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id
               ORDER BY incidents.status='open' DESC,incidents.status='acknowledged' DESC,incidents.opened_at DESC LIMIT 500"""
        ).fetchall()
    now = utc_ts()
    result = []
    for row in rows:
        item = dict(row)
        item["downtime_seconds"] = max(0, int(item.get("resolved_at") or now) - int(item["opened_at"]))
        result.append(item)
    return result


@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: int, _: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name
               FROM incidents LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id WHERE incidents.id=?""",
            (incident_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Incident not found")
        events = conn.execute(
            """SELECT incident_events.*,users.username actor
               FROM incident_events LEFT JOIN users ON users.id=incident_events.actor_id
               WHERE incident_id=? ORDER BY created_at,id""",
            (incident_id,),
        ).fetchall()
    item = dict(row)
    item["downtime_seconds"] = max(0, int(item.get("resolved_at") or utc_ts()) - int(item["opened_at"]))
    item["events"] = [dict(event) for event in events]
    return item


@app.post("/api/incidents/{incident_id}/notes", status_code=201)
def add_incident_note(incident_id: int, body: IncidentNoteBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT title FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Incident not found")
        append_incident_event(conn, incident_id, body.event_type, body.message, actor_id=user["id"])
        conn.execute("UPDATE incidents SET last_changed_at=? WHERE id=?", (utc_ts(), incident_id))
    audit(user["id"], "incident_note", str(incident_id), body.message, request.client.host if request.client else None)
    return {"ok": True}


@app.post("/api/incidents/{incident_id}/action")
def incident_action(incident_id: int, body: IncidentActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Incident not found")
        now = utc_ts()
        if body.action == "acknowledge":
            if row["status"] != "open":
                raise HTTPException(409, "Only an open incident can be acknowledged")
            conn.execute(
                "UPDATE incidents SET status='acknowledged',acknowledged_at=?,last_changed_at=?,root_cause=COALESCE(?,root_cause) WHERE id=?",
                (now, now, body.root_cause, incident_id),
            )
            append_incident_event(conn, incident_id, "acknowledged", body.note or "Operator acknowledged the incident", actor_id=user["id"], created_at=now)
        elif body.action == "resolve":
            if row["status"] == "resolved":
                raise HTTPException(409, "Incident is already resolved")
            conn.execute("UPDATE incidents SET root_cause=COALESCE(?,root_cause) WHERE id=?", (body.root_cause, incident_id))
            resolve_incident(
                conn, incident_id, body.note or "Operator resolved the incident",
                actor_id=user["id"], resolution=body.resolution,
            )
        else:
            if row["status"] != "resolved":
                raise HTTPException(409, "Only a resolved incident can be reopened")
            conn.execute(
                "UPDATE incidents SET status='open',resolved_at=NULL,last_changed_at=?,resolution=NULL WHERE id=?",
                (now, incident_id),
            )
            append_incident_event(conn, incident_id, "reopened", body.note or "Operator reopened the incident", actor_id=user["id"], created_at=now)
    audit(user["id"], f"incident_{body.action}", str(incident_id), row["title"], request.client.host if request.client else None)
    return {"ok": True, "action": body.action}


@app.post("/api/nodes/{node_id}/jobs", status_code=202)
def create_job(node_id: int, body: JobBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    payload = dict(body.payload)
    if body.service:
        payload["service"] = body.service
    with db() as conn:
        node = conn.execute("SELECT name FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        cursor = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, body.kind, json.dumps(payload), user["id"], utc_ts()))
    audit(user["id"], "job_create", node["name"], body.kind, request.client.host if request.client else None)
    return {"job_id": cursor.lastrowid, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, _: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return public_job(row)


@app.get("/api/jobs")
def list_jobs(limit: int = 100, _: sqlite3.Row = Depends(current_user)):
    limit = min(max(limit, 1), 500)
    with db() as conn:
        rows = conn.execute("SELECT jobs.*,nodes.name node_name FROM jobs JOIN nodes ON nodes.id=jobs.node_id ORDER BY jobs.id DESC LIMIT ?", (limit,)).fetchall()
    return [public_job(row) for row in rows]


@app.post("/api/agent/heartbeat")
async def agent_heartbeat(report: AgentReport, request: Request, node: sqlite3.Row = Depends(agent_node)):
    now = utc_ts()
    m = report.metrics
    notifications: list[str] = []
    with db() as conn:
        autoheal = report.autoheal or {}
        try:
            autoheal_cooldown = min(max(int(autoheal.get("cooldown_seconds", 300)), 60), 86400)
            autoheal_max = min(max(int(autoheal.get("max_restarts_per_hour", 3)), 1), 12)
        except (TypeError, ValueError):
            autoheal_cooldown, autoheal_max = 300, 3
        observed_ip = request.client.host if request.client else None
        if observed_ip in {"127.0.0.1", "::1"} and node["role"] == "hub":
            observed_ip = node["host"]
        conn.execute("UPDATE nodes SET status='online',agent_version=?,observed_ip=?,plugin_inventory=?,autoheal_enabled=?,autoheal_cooldown=?,autoheal_max_restarts=?,last_seen=?,updated_at=? WHERE id=?", (report.agent_version, observed_ip, json.dumps(report.plugins), 1 if autoheal.get("enabled") else 0, autoheal_cooldown, autoheal_max, now, now, node["id"]))
        conn.execute("INSERT INTO metrics(node_id,ts,cpu,ram,swap,disk,load1,rx_bps,tx_bps,uptime,connections,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (node["id"], now, m.get("cpu"), m.get("ram"), m.get("swap"), m.get("disk"), m.get("load1"), m.get("rx_bps"), m.get("tx_bps"), m.get("uptime"), m.get("connections"), json.dumps(m)))
        recovered_node_incidents = conn.execute(
            "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')",
            (node["id"],),
        ).fetchall()
        for incident in recovered_node_incidents:
            resolve_incident(conn, incident["id"], "Agent heartbeat recovered")
        inventory = m.get("inventory") if isinstance(m.get("inventory"), dict) else {}
        docker = inventory.get("docker") if isinstance(inventory.get("docker"), dict) else {}
        resource_limits = {
            "CPU": (m.get("cpu"), 90.0), "RAM": (m.get("ram"), 90.0),
            "DISK": (m.get("disk"), 90.0), "INODE": (m.get("inode_percent"), 90.0),
            "LOAD": (m.get("load1"), 8.0), "TEMPERATURE": (m.get("temperature_c"), 85.0),
            "NETWORK ERRORS": (m.get("network_errors_delta"), 10.0),
            "NETWORK DROPS": (m.get("network_drops_delta"), 100.0),
            "DOCKER UNHEALTHY": (docker.get("unhealthy"), 1.0),
        }
        for resource_name, (raw_value, limit) in resource_limits.items():
            if raw_value is None:
                continue
            value = float(raw_value)
            title = f"Node {node['name']} {resource_name} pressure"
            active_resource = conn.execute("SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged') LIMIT 1", (node["id"], title)).fetchone()
            if value >= limit and not active_resource:
                create_incident(
                    conn, node_id=node["id"], tunnel_id=None, severity="warning", title=title,
                    detail={"resource": resource_name.lower(), "value": value, "threshold": limit}, opened_at=now,
                )
                notifications.append(f"🟠 DARK NOC RESOURCE ALERT\nNode: {node['name']}\n{resource_name}: {value:.1f}\nThreshold: {limit:.1f}")
            elif value < limit * 0.9 and active_resource:
                resolve_incident(conn, active_resource["id"], f"{resource_name} returned below the recovery threshold")
        active_services = []
        for service in report.services:
            service_name = str(service.get("name", ""))[:128]
            if not service_name:
                continue
            active_services.append(service_name)
            service_status = str(service.get("status", "unknown"))[:32]
            conn.execute("INSERT INTO node_services(node_id,name,status,last_check) VALUES(?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET status=excluded.status,last_check=excluded.last_check", (node["id"], service_name, service_status, now))
        if active_services:
            conn.execute(f"DELETE FROM node_services WHERE node_id=? AND name NOT IN ({','.join('?' for _ in active_services)})", (node["id"], *active_services))
        else:
            conn.execute("DELETE FROM node_services WHERE node_id=?", (node["id"],))
        for tunnel in report.tunnels:
            method, service = str(tunnel.get("method", "")), str(tunnel.get("service", ""))
            if not ((method == "DARK Backhaul" and service.startswith("backhaul@")) or (method == "DARK Ghost Pro" and service.startswith("ghostpro@")) or (method == "DARK Packet Pro" and service.startswith("paqetpro@"))):
                continue
            name = str(tunnel.get("name", "unnamed"))[:128]
            old = conn.execute("SELECT * FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
            conn.execute("INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,last_check,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET method=excluded.method,target=excluded.target,service=excluded.service,listen_port=excluded.listen_port,status=excluded.status,latency_ms=excluded.latency_ms,packet_loss=excluded.packet_loss,sessions=excluded.sessions,rx_bps=excluded.rx_bps,tx_bps=excluded.tx_bps,last_check=excluded.last_check,details=excluded.details", (node["id"], name, tunnel.get("method"), tunnel.get("target"), tunnel.get("service"), tunnel.get("listen_port"), tunnel.get("status"), tunnel.get("latency_ms"), tunnel.get("packet_loss"), tunnel.get("sessions"), tunnel.get("rx_bps"), tunnel.get("tx_bps"), now, json.dumps(tunnel)))
            current = str(tunnel.get("status", "unknown"))
            previous = old["status"] if old else None
            tunnel_row = conn.execute("SELECT id FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
            failure_streak = (int(old["failure_streak"] or 0) + 1) if old and current in {"down", "degraded"} else (1 if current in {"down", "degraded"} else 0)
            conn.execute("UPDATE tunnels SET failure_streak=? WHERE id=?", (failure_streak, tunnel_row["id"]))
            open_incident = conn.execute("SELECT id FROM incidents WHERE tunnel_id=? AND status IN ('open','acknowledged') LIMIT 1", (tunnel_row["id"],)).fetchone()
            threshold = 2 if current == "down" else 3
            if current in {"down", "degraded"} and failure_streak >= threshold and not open_incident:
                severity = "critical" if current == "down" else "warning"
                create_incident(
                    conn, node_id=node["id"], tunnel_id=tunnel_row["id"], severity=severity,
                    title=f"Tunnel {name} is {current}", detail=tunnel, opened_at=now,
                )
                notifications.append(f"🔴 DARK NOC INCIDENT\nNode: {node['name']}\nTunnel: {name}\nStatus: {current.upper()}\nLatency: {tunnel.get('latency_ms', '—')} ms")
            elif current == "healthy" and open_incident:
                resolve_incident(conn, open_incident["id"], "Tunnel telemetry returned to healthy")
                notifications.append(f"🟢 DARK NOC RECOVERED\nNode: {node['name']}\nTunnel: {name}\nStatus: HEALTHY")
        active_names = [str(tunnel.get("name", "unnamed"))[:128] for tunnel in report.tunnels if (str(tunnel.get("method", "")) == "DARK Backhaul" and str(tunnel.get("service", "")).startswith("backhaul@")) or (str(tunnel.get("method", "")) == "DARK Ghost Pro" and str(tunnel.get("service", "")).startswith("ghostpro@")) or (str(tunnel.get("method", "")) == "DARK Packet Pro" and str(tunnel.get("service", "")).startswith("paqetpro@"))]
        stale_rows = conn.execute("SELECT id FROM tunnels WHERE node_id=?" + (f" AND name NOT IN ({','.join('?' for _ in active_names)})" if active_names else ""), (node["id"], *active_names)).fetchall()
        if stale_rows:
            stale_ids = [row["id"] for row in stale_rows]
            stale_incidents = conn.execute(
                f"SELECT id FROM incidents WHERE tunnel_id IN ({','.join('?' for _ in stale_ids)}) AND status IN ('open','acknowledged')",
                stale_ids,
            ).fetchall()
            for incident in stale_incidents:
                resolve_incident(conn, incident["id"], "Tunnel was removed from the active Agent inventory")
            conn.executemany("DELETE FROM tunnels WHERE id=?", [(tunnel_id,) for tunnel_id in stale_ids])
    for message in notifications:
        await asyncio.to_thread(telegram_notify, message)
    stale_clients: list[WebSocket] = []
    for live_socket in list(LIVE_CLIENTS):
        try:
            await live_socket.send_json({"type": "telemetry", "node_id": node["id"], "server_time": now})
        except Exception:
            stale_clients.append(live_socket)
    for live_socket in stale_clients:
        LIVE_CLIENTS.discard(live_socket)
    return {"ok": True, "server_time": now}


@app.get("/api/agent/jobs")
def agent_jobs(node: sqlite3.Row = Depends(agent_node)):
    with db() as conn:
        row = conn.execute("""UPDATE jobs SET status='running',started_at=?
          WHERE id=(SELECT id FROM jobs WHERE node_id=? AND status='queued' ORDER BY id LIMIT 1)
            AND status='queued' RETURNING id,kind,payload,created_at""", (utc_ts(), node["id"])).fetchone()
    return [dict(row)] if row else []


@app.post("/api/agent/jobs/result")
def agent_job_result(result: JobResult, node: sqlite3.Row = Depends(agent_node)):
    notification: str | None = None
    with db() as conn:
        row = conn.execute("SELECT id,status,kind,payload FROM jobs WHERE id=? AND node_id=?", (result.job_id, node["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        if row["status"] in {"completed", "failed"}:
            existing = conn.execute("SELECT status,output FROM jobs WHERE id=?", (result.job_id,)).fetchone()
            if existing["status"] == result.status and existing["output"] == result.output:
                return {"ok": True, "idempotent": True}
            raise HTTPException(409, "Job already has a different terminal result")
        if row["status"] != "running":
            raise HTTPException(409, "Only a running job can accept a result")
        conn.execute("UPDATE jobs SET status=?,output=?,finished_at=? WHERE id=?", (result.status, result.output, utc_ts(), result.job_id))
        if row["kind"] == "certificate_issue":
            cert_id = int(json.loads(row["payload"]).get("certificate_id", 0))
            if result.status == "completed":
                try:
                    cert = json.loads(result.output)
                    conn.execute("UPDATE certificates SET status='valid',issuer=?,cert_path=?,key_path=?,expires_at=?,last_error=NULL,updated_at=? WHERE id=? AND node_id=?", (cert.get("issuer", "letsencrypt"), cert["cert_path"], cert["key_path"], int(cert["expires_at"]), utc_ts(), cert_id, node["id"]))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    conn.execute("UPDATE certificates SET status='failed',last_error=?,updated_at=? WHERE id=?", ("Agent returned invalid certificate metadata", utc_ts(), cert_id))
            else:
                conn.execute("UPDATE certificates SET status='failed',last_error=?,updated_at=? WHERE id=?", (result.output[-2000:], utc_ts(), cert_id))
        if row["kind"] == "monitor_run":
            request_payload = json.loads(row["payload"] or "{}")
            monitor_id = int(request_payload.get("monitor_id", 0) or 0)
            monitor = conn.execute("SELECT * FROM monitors WHERE id=? AND node_id=?", (monitor_id, node["id"])).fetchone()
            if monitor:
                checked_at = utc_ts()
                try:
                    report = json.loads(result.output)
                    monitor_status = "up" if result.status == "completed" and report.get("status") == "up" else "down"
                    latency = float(report["latency_ms"]) if report.get("latency_ms") is not None else None
                    detail_value = report.get("detail") if isinstance(report.get("detail"), dict) else {"message": str(report.get("detail") or "")}
                except (TypeError, ValueError, json.JSONDecodeError):
                    monitor_status, latency = "down", None
                    detail_value = {"error": result.output[-2000:] or "Agent monitor execution failed"}
                failure_streak = 0 if monitor_status == "up" else int(monitor["failure_streak"] or 0) + 1
                detail_json = json.dumps(detail_value)[:8000]
                conn.execute(
                    """UPDATE monitors SET status=?,latency_ms=?,detail=?,failure_streak=?,last_run_at=?,updated_at=?
                       WHERE id=?""",
                    (monitor_status, latency, detail_json, failure_streak, checked_at, utc_ts(), monitor_id),
                )
                conn.execute(
                    "INSERT INTO monitor_results(monitor_id,ts,status,latency_ms,detail) VALUES(?,?,?,?,?)",
                    (monitor_id, checked_at, monitor_status, latency, detail_json),
                )
                title = f"Monitor {monitor['name']} is down"
                open_monitor_incident = conn.execute(
                    "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged') LIMIT 1",
                    (node["id"], title),
                ).fetchone()
                if monitor_status == "down" and failure_streak >= 2 and not open_monitor_incident:
                    create_incident(
                        conn, node_id=node["id"], tunnel_id=None, severity="critical", title=title,
                        detail={"monitor_id": monitor_id, "kind": monitor["kind"], "target": monitor["target"], **detail_value},
                        opened_at=checked_at,
                    )
                    notification = f"🔴 DARK NOC MONITOR\nNode: {node['name']}\nMonitor: {monitor['name']}\nTarget: {monitor['target']}\nStatus: DOWN"
                elif monitor_status == "up" and open_monitor_incident:
                    resolve_incident(conn, open_monitor_incident["id"], "Synthetic monitor recovered")
                    notification = f"🟢 DARK NOC MONITOR RECOVERED\nNode: {node['name']}\nMonitor: {monitor['name']}\nStatus: UP"
        fleet_item = conn.execute(
            "SELECT * FROM fleet_operation_items WHERE job_id=?",
            (result.job_id,),
        ).fetchone()
        if fleet_item:
            conn.execute("UPDATE fleet_operation_items SET status=? WHERE id=?", (result.status, fleet_item["id"]))
            finalize_fleet_operation(conn, fleet_item["operation_id"])
        deployment = conn.execute("SELECT * FROM plugin_deployments WHERE iran_job_id=? OR kharej_job_id=?", (result.job_id, result.job_id)).fetchone()
        if deployment:
            iran_status = conn.execute("SELECT status FROM jobs WHERE id=?", (deployment["iran_job_id"],)).fetchone()["status"]
            kharej_status = conn.execute("SELECT status FROM jobs WHERE id=?", (deployment["kharej_job_id"],)).fetchone()["status"]
            statuses = {iran_status, kharej_status}
            if deployment["lifecycle"] == "active" and statuses == {"completed", "failed"}:
                side = "iran" if iran_status == "completed" else "kharej"
                node_id = deployment[f"{side}_node_id"]
                rollback_job = conn.execute(
                    "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                    (node_id, "plugin_remove", json.dumps({"plugin_id": deployment["plugin_id"], "name": deployment["name"]}), deployment["created_by"], utc_ts()),
                ).lastrowid
                conn.execute(f"UPDATE plugin_deployments SET {side}_job_id=?,lifecycle='rolling_back' WHERE id=?", (rollback_job, deployment["id"]))
            elif deployment["lifecycle"] == "rolling_back" and result.status == "completed":
                conn.execute("UPDATE plugin_deployments SET lifecycle='rolled_back' WHERE id=?", (deployment["id"],))
            elif deployment["lifecycle"] == "rolling_back" and result.status == "failed":
                conn.execute("UPDATE plugin_deployments SET lifecycle='rollback_failed' WHERE id=?", (deployment["id"],))
    if notification:
        telegram_notify(notification)
    return {"ok": True}


@app.post("/api/agent/jobs/{job_id}/lease")
def renew_agent_job_lease(job_id: int, node: sqlite3.Row = Depends(agent_node)):
    with db() as conn:
        changed = conn.execute("UPDATE jobs SET started_at=? WHERE id=? AND node_id=? AND status='running'", (utc_ts(), job_id, node["id"])).rowcount
    if changed != 1:
        raise HTTPException(409, "Job is no longer running on this node")
    return {"ok": True}


async def websocket_user(websocket: WebSocket) -> sqlite3.Row | None:
    token = websocket.cookies.get("dark_noc_session")
    if not token:
        return None
    with db() as conn:
        return conn.execute(
            """SELECT users.*,sessions.created_at session_created_at,
                      sessions.expires_at session_expires_at
               FROM sessions JOIN users ON users.id=sessions.user_id
               WHERE sessions.token_hash=? AND sessions.expires_at>?
                 AND sessions.created_at>?""",
            (token_hash(token), utc_ts(), utc_ts() - SESSION_TTL),
        ).fetchone()


def websocket_session_valid(session_digest: str, user_id: int, lifetime_deadline: int) -> bool:
    now = utc_ts()
    if now >= lifetime_deadline:
        return False
    with db() as conn:
        session = conn.execute(
            "SELECT created_at,expires_at FROM sessions WHERE token_hash=? AND user_id=?",
            (session_digest, user_id),
        ).fetchone()
    if not session:
        return False
    effective_expiry = min(int(session["expires_at"]), int(session["created_at"]) + SESSION_TTL, lifetime_deadline)
    return effective_expiry > now


async def websocket_session_guard(session_digest: str, user_id: int, lifetime_deadline: int) -> bool:
    while websocket_session_valid(session_digest, user_id, lifetime_deadline):
        remaining = lifetime_deadline - utc_ts()
        await asyncio.sleep(min(5.0, max(0.1, float(remaining))))
    return False


@app.websocket("/ws/live")
async def live_updates(websocket: WebSocket):
    user = await websocket_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    session_digest = token_hash(websocket.cookies.get("dark_noc_session", ""))
    session_deadline = min(
        int(user["session_expires_at"]),
        int(user["session_created_at"]) + SESSION_TTL,
        utc_ts() + SESSION_TTL,
    )
    await websocket.accept()
    LIVE_CLIENTS.add(websocket)
    try:
        await websocket.send_json({"type": "ready", "server_time": utc_ts()})

        async def receive_live_messages() -> None:
            while True:
                raw_message = await websocket.receive_text()
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    message = {"type": raw_message}
                if isinstance(message, dict) and message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "server_time": utc_ts()})

        auth_task = asyncio.create_task(websocket_session_guard(session_digest, user["id"], session_deadline))
        tasks = {asyncio.create_task(receive_live_messages()), auth_task}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
        if auth_task in done and auth_task.result() is False:
            LIVE_CLIENTS.discard(websocket)
            await websocket.close(code=4401)
    except WebSocketDisconnect:
        pass
    finally:
        LIVE_CLIENTS.discard(websocket)


@app.websocket("/ws/ssh/{node_id}")
async def ssh_terminal(websocket: WebSocket, node_id: int):
    user = await websocket_user(websocket)
    if not user:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    with db() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        await websocket.send_json({"type": "error", "message": "Node not found"})
        await websocket.close()
        return
    password, key = decrypt(node["ssh_password_enc"]), decrypt(node["ssh_key_enc"])
    if not password and not key:
        await websocket.send_json({"type": "error", "message": "SSH credentials are not configured"})
        await websocket.close()
        return
    session_digest = token_hash(websocket.cookies.get("dark_noc_session", ""))
    session_deadline = min(
        int(user["session_expires_at"]),
        int(user["session_created_at"]) + SESSION_TTL,
        utc_ts() + SESSION_TTL,
    )
    audit(user["id"], "ssh_open", node["name"], "Interactive terminal opened", websocket.client.host if websocket.client else None)
    try:
        expected_fingerprint = node["ssh_host_fingerprint"]

        class PinnedSSHClient(asyncssh.SSHClient):
            def validate_host_public_key(self, host: str, addr: str, port: int, host_key: Any) -> bool:
                fingerprint = host_key.get_fingerprint("sha256")
                if expected_fingerprint:
                    return hmac.compare_digest(expected_fingerprint, fingerprint)
                with db() as pin_conn:
                    current = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                    if not current:
                        return False
                    if current["ssh_host_fingerprint"]:
                        return hmac.compare_digest(current["ssh_host_fingerprint"], fingerprint)
                    changed = pin_conn.execute("UPDATE nodes SET ssh_host_fingerprint=?,updated_at=? WHERE id=? AND ssh_host_fingerprint IS NULL", (fingerprint, utc_ts(), node_id)).rowcount
                    if changed == 1:
                        return True
                    saved = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                    return bool(saved and saved["ssh_host_fingerprint"] and hmac.compare_digest(saved["ssh_host_fingerprint"], fingerprint))

        options: dict[str, Any] = {
            "host": node["host"], "port": node["ssh_port"], "username": node["ssh_user"],
            "known_hosts": ([], [], [], [], [], [], []), "client_factory": PinnedSSHClient,
            "connect_timeout": 12, "keepalive_interval": SSH_KEEPALIVE_INTERVAL,
            "keepalive_count_max": SSH_KEEPALIVE_COUNT_MAX,
        }
        if key:
            options["client_keys"] = [asyncssh.import_private_key(key)]
        if password:
            options["password"] = password
        async with asyncssh.connect(**options) as conn:
            if not websocket_session_valid(session_digest, user["id"], session_deadline):
                await websocket.close(code=4401)
                return
            process = await conn.create_process(term_type="xterm-256color", term_size=(120, 32))

            async def ssh_to_ws():
                while not process.stdout.at_eof():
                    chunk = await process.stdout.read(4096)
                    if chunk:
                        await websocket.send_json({"type": "output", "data": chunk})

            async def ws_to_ssh():
                while True:
                    message = await websocket.receive_json()
                    if message.get("type") == "input":
                        process.stdin.write(str(message.get("data", "")))
                    elif message.get("type") == "resize":
                        process.change_terminal_size(int(message.get("cols", 120)), int(message.get("rows", 32)))
                    elif message.get("type") == "ping":
                        await websocket.send_json({"type": "pong", "server_time": utc_ts()})

            with db() as pin_conn:
                pinned = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
            await websocket.send_json({"type": "connected", "node": node["name"], "fingerprint": pinned["ssh_host_fingerprint"] if pinned else None})
            auth_task = asyncio.create_task(websocket_session_guard(session_digest, user["id"], session_deadline))
            tasks = {asyncio.create_task(ssh_to_ws()), asyncio.create_task(ws_to_ssh()), auth_task}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            if auth_task in done and auth_task.result() is False:
                await websocket.close(code=4401)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            LOGGER.warning("SSH session failed for node %s: %s", node_id, exc)
            detail = str(exc).strip().replace("\n", " ")[:240] or exc.__class__.__name__
            await websocket.send_json({"type": "error", "message": f"SSH connection failed: {detail}"})
        except Exception:
            pass
    finally:
        audit(user["id"], "ssh_close", node["name"], "Interactive terminal closed", websocket.client.host if websocket.client else None)
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": VERSION}


@app.get("/readyz")
def readyz():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        if not KEY_PATH.is_file() or not (STATIC_DIR / "index.html").is_file():
            raise RuntimeError("Hub runtime files are incomplete")
    except Exception as exc:
        raise HTTPException(503, f"Hub is not ready: {str(exc)[:160]}") from exc
    return {"status": "ready", "version": VERSION, "instance": HUB_INSTANCE_ID}


@app.get("/api/system/status")
def system_status(_: sqlite3.Row = Depends(current_user)):
    with db() as conn:
        leader = conn.execute("SELECT holder,expires_at,updated_at FROM hub_leases WHERE name='maintenance'").fetchone()
        counts = {
            "nodes": conn.execute("SELECT COUNT(*) count FROM nodes").fetchone()["count"],
            "monitors": conn.execute("SELECT COUNT(*) count FROM monitors").fetchone()["count"],
            "queued_jobs": conn.execute("SELECT COUNT(*) count FROM jobs WHERE status='queued'").fetchone()["count"],
            "metric_samples": conn.execute("SELECT COUNT(*) count FROM metrics").fetchone()["count"],
            "rollup_samples": conn.execute("SELECT COUNT(*) count FROM metric_rollups").fetchone()["count"],
        }
    return {
        "version": VERSION, "instance": HUB_INSTANCE_ID,
        "maintenance_leader": dict(leader) if leader else None,
        "retention": {
            "raw_metrics_days": METRIC_RAW_RETENTION_DAYS,
            "rollups_days": METRIC_ROLLUP_RETENTION_DAYS,
            "monitor_results_days": MONITOR_RESULT_RETENTION_DAYS,
        },
        "counts": counts,
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
