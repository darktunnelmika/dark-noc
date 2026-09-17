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
import re
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
import importlib.util as _realm_support_importlib_util

_REALM_SUPPORT_ERROR: str | None = None
try:
    _realm_support_path = Path(__file__).resolve().with_name("realm_support.py")
    if not _realm_support_path.is_file():
        raise FileNotFoundError(f"Realm Hub support is missing: {_realm_support_path}")
    _realm_support_spec = _realm_support_importlib_util.spec_from_file_location("dark_noc_realm_support", _realm_support_path)
    if _realm_support_spec is None or _realm_support_spec.loader is None:
        raise ImportError(f"Could not load Realm Hub support: {_realm_support_path}")
    _realm_support_module = _realm_support_importlib_util.module_from_spec(_realm_support_spec)
    _realm_support_spec.loader.exec_module(_realm_support_module)
    prepare_realm_settings = _realm_support_module.prepare_realm_settings
    realm_pair_code = _realm_support_module.realm_pair_code
except Exception as exc:
    _REALM_SUPPORT_ERROR = f"{type(exc).__name__}: {exc}"[:500]

    def prepare_realm_settings(*_: Any, **__: Any) -> dict[str, Any]:
        raise HTTPException(503, f"DARK Realm support is unavailable: {_REALM_SUPPORT_ERROR}")

    def realm_pair_code(*_: Any, **__: Any) -> str:
        raise HTTPException(503, f"DARK Realm support is unavailable: {_REALM_SUPPORT_ERROR}")
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().with_name('runtime_config.py')
_runtime_config_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_runtime_config', _RUNTIME_CONFIG_PATH)
if _runtime_config_spec is None or _runtime_config_spec.loader is None:
    raise ImportError(f'Could not load runtime configuration: {_RUNTIME_CONFIG_PATH}')
_runtime_config_module = _realm_support_importlib_util.module_from_spec(_runtime_config_spec)
_runtime_config_spec.loader.exec_module(_runtime_config_module)
for _runtime_name in ['SSH_UPLOAD_LIMIT', 'SSH_RELAY_LIMIT', 'SSH_TRANSFER_CONCURRENCY', 'SSH_TRANSFER_TIMEOUT', 'SSH_TRANSFER_IDLE_TIMEOUT', 'SSH_KEEPALIVE_INTERVAL', 'SSH_KEEPALIVE_COUNT_MAX', 'SSH_UPLOAD_QUEUE_TIMEOUT', 'NODE_PROVISION_TIMEOUT', 'NODE_PROVISION_CONCURRENCY', 'MONITOR_RESULT_RETENTION_DAYS', 'METRIC_RAW_RETENTION_DAYS', 'METRIC_ROLLUP_RETENTION_DAYS', 'TUNNEL_SAMPLE_RETENTION_DAYS', 'HUB_LEASE_SECONDS', 'SSH_EDITOR_LIMIT', 'bounded_env_int', 'configured_public_hub_url']:
    globals()[_runtime_name] = getattr(_runtime_config_module, _runtime_name)
del _runtime_name

_SECURITY_RUNTIME_PATH = Path(__file__).resolve().with_name("security_runtime.py")
_security_runtime_spec = _realm_support_importlib_util.spec_from_file_location("dark_noc_security_runtime", _SECURITY_RUNTIME_PATH)
if _security_runtime_spec is None or _security_runtime_spec.loader is None:
    raise ImportError(f"Could not load runtime security helpers: {_SECURITY_RUNTIME_PATH}")
_security_runtime_module = _realm_support_importlib_util.module_from_spec(_security_runtime_spec)
_security_runtime_spec.loader.exec_module(_security_runtime_module)
browser_origin_allowed = _security_runtime_module.browser_origin_allowed
enforce_http_origin = _security_runtime_module.enforce_http_origin

_SCHEMAS_PATH = Path(__file__).resolve().with_name('schemas.py')
_schemas_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_schemas', _SCHEMAS_PATH)
if _schemas_spec is None or _schemas_spec.loader is None:
    raise ImportError(f'Could not load request schemas: {_SCHEMAS_PATH}')
_schemas_module = _realm_support_importlib_util.module_from_spec(_schemas_spec)
_schemas_spec.loader.exec_module(_schemas_module)
for _schema_name in ['LoginBody', 'NodeBody', 'SSHRelayBody', 'IncidentActionBody', 'IncidentNoteBody', 'MonitorBody', 'FleetOperationBody', 'SSHFileActionBody', 'SSHFileWriteBody', 'JobBody', 'PluginDeployBody', 'PairCodeDeployBody', 'TunnelActionBody', 'TunnelReconfigureBody', 'CertificateBody', 'AgentPulse', 'AgentReport', 'JobResult']:
    globals()[_schema_name] = getattr(_schemas_module, _schema_name)
del _schema_name

_DATABASE_MODULE_PATH = Path(__file__).resolve().with_name('database.py')
_database_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_database', _DATABASE_MODULE_PATH)
if _database_spec is None or _database_spec.loader is None:
    raise ImportError(f'Could not load database core: {_DATABASE_MODULE_PATH}')
_database_module = _realm_support_importlib_util.module_from_spec(_database_spec)
_database_spec.loader.exec_module(_database_module)
for _database_name in ['DATA_DIR', 'DB_PATH', 'SCHEMA', 'db']:
    globals()[_database_name] = getattr(_database_module, _database_name)
del _database_name

_DATABASE_BOOTSTRAP_PATH = Path(__file__).resolve().with_name('database_bootstrap.py')
_database_bootstrap_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_database_bootstrap', _DATABASE_BOOTSTRAP_PATH)
if _database_bootstrap_spec is None or _database_bootstrap_spec.loader is None:
    raise ImportError(f'Could not load database bootstrap: {_DATABASE_BOOTSTRAP_PATH}')
_database_bootstrap_module = _realm_support_importlib_util.module_from_spec(_database_bootstrap_spec)
_database_bootstrap_spec.loader.exec_module(_database_bootstrap_module)
bootstrap_database = _database_bootstrap_module.bootstrap_database


_NODE_TUNNEL_REPOSITORY_PATH = Path(__file__).resolve().with_name('node_tunnel_repository.py')
_node_tunnel_repository_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_node_tunnel_repository', _NODE_TUNNEL_REPOSITORY_PATH)
if _node_tunnel_repository_spec is None or _node_tunnel_repository_spec.loader is None:
    raise ImportError(f'Could not load Node/Tunnel repository: {_NODE_TUNNEL_REPOSITORY_PATH}')
_node_tunnel_repository_module = _realm_support_importlib_util.module_from_spec(_node_tunnel_repository_spec)
_node_tunnel_repository_spec.loader.exec_module(_node_tunnel_repository_module)
for _repository_name in ['find_node_endpoint_conflict', 'fetch_node_inventory', 'fetch_tunnel_inventory', 'fetch_tunnel_operation_rows']:
    globals()[_repository_name] = getattr(_node_tunnel_repository_module, _repository_name)
del _repository_name

_MONITOR_INCIDENT_REPOSITORY_PATH = Path(__file__).resolve().with_name('monitor_incident_repository.py')
_monitor_incident_repository_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitor_incident_repository', _MONITOR_INCIDENT_REPOSITORY_PATH)
if _monitor_incident_repository_spec is None or _monitor_incident_repository_spec.loader is None:
    raise ImportError(f'Could not load Monitor/Incident repository: {_MONITOR_INCIDENT_REPOSITORY_PATH}')
_monitor_incident_repository_module = _realm_support_importlib_util.module_from_spec(_monitor_incident_repository_spec)
_monitor_incident_repository_spec.loader.exec_module(_monitor_incident_repository_module)
for _monitor_incident_repository_name in ['fetch_monitor_inventory', 'fetch_monitor_result_rows', 'fetch_incident_inventory', 'fetch_incident_detail_rows']:
    globals()[_monitor_incident_repository_name] = getattr(_monitor_incident_repository_module, _monitor_incident_repository_name)
del _monitor_incident_repository_name


_NODE_TUNNEL_SERVICE_PATH = Path(__file__).resolve().with_name('node_tunnel_service.py')
_node_tunnel_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_node_tunnel_service', _NODE_TUNNEL_SERVICE_PATH)
if _node_tunnel_service_spec is None or _node_tunnel_service_spec.loader is None:
    raise ImportError(f'Could not load Node/Tunnel service: {_NODE_TUNNEL_SERVICE_PATH}')
_node_tunnel_service_module = _realm_support_importlib_util.module_from_spec(_node_tunnel_service_spec)
_node_tunnel_service_spec.loader.exec_module(_node_tunnel_service_module)
for _node_tunnel_service_name in [
    'NodeTunnelServiceError', 'create_node_mutation', 'prepare_node_provision_mutation',
    'delete_node_mutation', 'update_node_mutation', 'reset_node_fingerprint_mutation',
    'queue_tunnel_action_mutation', 'queue_plugin_install_mutation',
    'reconfigure_tunnel_mutation', 'remove_tunnel_mutation',
]:
    globals()[_node_tunnel_service_name] = getattr(_node_tunnel_service_module, _node_tunnel_service_name)
del _node_tunnel_service_name


_PLUGIN_DEPLOYMENT_SERVICE_PATH = Path(__file__).resolve().with_name('plugin_deployment_service.py')
_plugin_deployment_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_plugin_deployment_service', _PLUGIN_DEPLOYMENT_SERVICE_PATH)
if _plugin_deployment_service_spec is None or _plugin_deployment_service_spec.loader is None:
    raise ImportError(f'Could not load Plugin Deployment service: {_PLUGIN_DEPLOYMENT_SERVICE_PATH}')
_plugin_deployment_service_module = _realm_support_importlib_util.module_from_spec(_plugin_deployment_service_spec)
_plugin_deployment_service_spec.loader.exec_module(_plugin_deployment_service_module)
for _plugin_deployment_service_name in [
    'PluginDeploymentServiceError', 'deploy_pair_code_mutation', 'deploy_managed_mutation',
    'recover_pair_code_mutation', 'retry_hybrid_mutation', 'remove_hybrid_mutation',
    'retry_managed_mutation', 'remove_managed_mutation',
]:
    globals()[_plugin_deployment_service_name] = getattr(_plugin_deployment_service_module, _plugin_deployment_service_name)
del _plugin_deployment_service_name


_MONITOR_INCIDENT_SERVICE_PATH = Path(__file__).resolve().with_name('monitor_incident_service.py')
_monitor_incident_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitor_incident_service', _MONITOR_INCIDENT_SERVICE_PATH)
if _monitor_incident_service_spec is None or _monitor_incident_service_spec.loader is None:
    raise ImportError(f'Could not load Monitor/Incident service: {_MONITOR_INCIDENT_SERVICE_PATH}')
_monitor_incident_service_module = _realm_support_importlib_util.module_from_spec(_monitor_incident_service_spec)
_monitor_incident_service_spec.loader.exec_module(_monitor_incident_service_module)
for _monitor_incident_service_name in [
    'MonitorIncidentServiceError', 'create_monitor_mutation', 'update_monitor_mutation',
    'delete_monitor_mutation', 'queue_monitor_run_mutation', 'add_incident_note_mutation',
    'incident_action_mutation',
]:
    globals()[_monitor_incident_service_name] = getattr(_monitor_incident_service_module, _monitor_incident_service_name)
del _monitor_incident_service_name


_CERTIFICATE_FLEET_SERVICE_PATH = Path(__file__).resolve().with_name('certificate_fleet_service.py')
_certificate_fleet_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_certificate_fleet_service', _CERTIFICATE_FLEET_SERVICE_PATH)
if _certificate_fleet_service_spec is None or _certificate_fleet_service_spec.loader is None:
    raise ImportError(f'Could not load Certificate/Fleet service: {_CERTIFICATE_FLEET_SERVICE_PATH}')
_certificate_fleet_service_module = _realm_support_importlib_util.module_from_spec(_certificate_fleet_service_spec)
_certificate_fleet_service_spec.loader.exec_module(_certificate_fleet_service_module)
for _certificate_fleet_service_name in [
    'CertificateFleetServiceError', 'issue_certificate_mutation', 'renew_certificate_mutation',
    'create_fleet_operation_mutation', 'cancel_fleet_operation_mutation',
]:
    globals()[_certificate_fleet_service_name] = getattr(_certificate_fleet_service_module, _certificate_fleet_service_name)
del _certificate_fleet_service_name

_MAINTENANCE_RUNTIME_PATH = Path(__file__).resolve().with_name('maintenance_runtime.py')
_maintenance_runtime_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_maintenance_runtime', _MAINTENANCE_RUNTIME_PATH)
if _maintenance_runtime_spec is None or _maintenance_runtime_spec.loader is None:
    raise ImportError(f'Could not load maintenance runtime: {_MAINTENANCE_RUNTIME_PATH}')
_maintenance_runtime_module = _realm_support_importlib_util.module_from_spec(_maintenance_runtime_spec)
_maintenance_runtime_spec.loader.exec_module(_maintenance_runtime_module)
_maintenance_acquire_hub_lease = _maintenance_runtime_module.acquire_hub_lease
_maintenance_queue_due_monitors = _maintenance_runtime_module.queue_due_monitors
_maintenance_queue_due_fleet_operations = _maintenance_runtime_module.queue_due_fleet_operations
_maintenance_rollup_previous_hour = _maintenance_runtime_module.rollup_previous_hour
_maintenance_run_cycle = _maintenance_runtime_module.run_maintenance_cycle
_maintenance_loop_runner = _maintenance_runtime_module.maintenance_loop
_build_maintenance_lifespan = _maintenance_runtime_module.build_lifespan

_MONITORING_ROUTER_PATH = Path(__file__).resolve().with_name('monitoring_router.py')
_monitoring_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitoring_router', _MONITORING_ROUTER_PATH)
if _monitoring_router_spec is None or _monitoring_router_spec.loader is None:
    raise ImportError(f'Could not load Monitoring router: {_MONITORING_ROUTER_PATH}')
_monitoring_router_module = _realm_support_importlib_util.module_from_spec(_monitoring_router_spec)
_monitoring_router_spec.loader.exec_module(_monitoring_router_module)
register_monitoring_router = _monitoring_router_module.register_monitoring_router

_INCIDENTS_ROUTER_PATH = Path(__file__).resolve().with_name('incidents_router.py')
_incidents_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_incidents_router', _INCIDENTS_ROUTER_PATH)
if _incidents_router_spec is None or _incidents_router_spec.loader is None:
    raise ImportError(f'Could not load Incidents router: {_INCIDENTS_ROUTER_PATH}')
_incidents_router_module = _realm_support_importlib_util.module_from_spec(_incidents_router_spec)
_incidents_router_spec.loader.exec_module(_incidents_router_module)
register_incidents_router = _incidents_router_module.register_incidents_router

_NODES_ROUTER_PATH = Path(__file__).resolve().with_name('nodes_router.py')
_nodes_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_nodes_router', _NODES_ROUTER_PATH)
if _nodes_router_spec is None or _nodes_router_spec.loader is None:
    raise ImportError(f'Could not load Nodes router: {_NODES_ROUTER_PATH}')
_nodes_router_module = _realm_support_importlib_util.module_from_spec(_nodes_router_spec)
_nodes_router_spec.loader.exec_module(_nodes_router_module)
register_nodes_router = _nodes_router_module.register_nodes_router

_TUNNELS_ROUTER_PATH = Path(__file__).resolve().with_name('tunnels_router.py')
_tunnels_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_tunnels_router', _TUNNELS_ROUTER_PATH)
if _tunnels_router_spec is None or _tunnels_router_spec.loader is None:
    raise ImportError(f'Could not load Tunnels router: {_TUNNELS_ROUTER_PATH}')
_tunnels_router_module = _realm_support_importlib_util.module_from_spec(_tunnels_router_spec)
_tunnels_router_spec.loader.exec_module(_tunnels_router_module)
register_tunnels_router = _tunnels_router_module.register_tunnels_router

_PLUGIN_DEPLOYMENTS_ROUTER_PATH = Path(__file__).resolve().with_name('plugin_deployments_router.py')
_plugin_deployments_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_plugin_deployments_router', _PLUGIN_DEPLOYMENTS_ROUTER_PATH)
if _plugin_deployments_router_spec is None or _plugin_deployments_router_spec.loader is None:
    raise ImportError(f'Could not load Plugin Deployments router: {_PLUGIN_DEPLOYMENTS_ROUTER_PATH}')
_plugin_deployments_router_module = _realm_support_importlib_util.module_from_spec(_plugin_deployments_router_spec)
_plugin_deployments_router_spec.loader.exec_module(_plugin_deployments_router_module)
register_plugin_deployments_router = _plugin_deployments_router_module.register_plugin_deployments_router

_CERTIFICATES_ROUTER_PATH = Path(__file__).resolve().with_name('certificates_router.py')
_certificates_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_certificates_router', _CERTIFICATES_ROUTER_PATH)
if _certificates_router_spec is None or _certificates_router_spec.loader is None:
    raise ImportError(f'Could not load Certificates router: {_CERTIFICATES_ROUTER_PATH}')
_certificates_router_module = _realm_support_importlib_util.module_from_spec(_certificates_router_spec)
_certificates_router_spec.loader.exec_module(_certificates_router_module)
register_certificates_router = _certificates_router_module.register_certificates_router

_FLEET_ROUTER_PATH = Path(__file__).resolve().with_name('fleet_router.py')
_fleet_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_fleet_router', _FLEET_ROUTER_PATH)
if _fleet_router_spec is None or _fleet_router_spec.loader is None:
    raise ImportError(f'Could not load Fleet router: {_FLEET_ROUTER_PATH}')
_fleet_router_module = _realm_support_importlib_util.module_from_spec(_fleet_router_spec)
_fleet_router_spec.loader.exec_module(_fleet_router_module)
register_fleet_router = _fleet_router_module.register_fleet_router

_AUTH_ROUTER_PATH = Path(__file__).resolve().with_name('auth_router.py')
_auth_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_auth_router', _AUTH_ROUTER_PATH)
if _auth_router_spec is None or _auth_router_spec.loader is None:
    raise ImportError(f'Could not load Auth router: {_AUTH_ROUTER_PATH}')
_auth_router_module = _realm_support_importlib_util.module_from_spec(_auth_router_spec)
_auth_router_spec.loader.exec_module(_auth_router_module)
register_auth_router = _auth_router_module.register_auth_router

_DASHBOARD_ROUTER_PATH = Path(__file__).resolve().with_name('dashboard_router.py')
_dashboard_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_dashboard_router', _DASHBOARD_ROUTER_PATH)
if _dashboard_router_spec is None or _dashboard_router_spec.loader is None:
    raise ImportError(f'Could not load Dashboard router: {_DASHBOARD_ROUTER_PATH}')
_dashboard_router_module = _realm_support_importlib_util.module_from_spec(_dashboard_router_spec)
_dashboard_router_spec.loader.exec_module(_dashboard_router_module)
register_dashboard_router = _dashboard_router_module.register_dashboard_router

_SYSTEM_ROUTER_PATH = Path(__file__).resolve().with_name('system_router.py')
_system_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_system_router', _SYSTEM_ROUTER_PATH)
if _system_router_spec is None or _system_router_spec.loader is None:
    raise ImportError(f'Could not load System router: {_SYSTEM_ROUTER_PATH}')
_system_router_module = _realm_support_importlib_util.module_from_spec(_system_router_spec)
_system_router_spec.loader.exec_module(_system_router_module)
register_system_router = _system_router_module.register_system_router

_SSH_FILE_ROUTER_PATH = Path(__file__).resolve().with_name('ssh_file_router.py')
_ssh_file_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_ssh_file_router', _SSH_FILE_ROUTER_PATH)
if _ssh_file_router_spec is None or _ssh_file_router_spec.loader is None:
    raise ImportError(f'Could not load SSH File router: {_SSH_FILE_ROUTER_PATH}')
_ssh_file_router_module = _realm_support_importlib_util.module_from_spec(_ssh_file_router_spec)
_ssh_file_router_spec.loader.exec_module(_ssh_file_router_module)
register_ssh_file_router = _ssh_file_router_module.register_ssh_file_router

_SSH_TERMINAL_ROUTER_PATH = Path(__file__).resolve().with_name('ssh_terminal_router.py')
_ssh_terminal_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_ssh_terminal_router', _SSH_TERMINAL_ROUTER_PATH)
if _ssh_terminal_router_spec is None or _ssh_terminal_router_spec.loader is None:
    raise ImportError(f'Could not load SSH Terminal router: {_SSH_TERMINAL_ROUTER_PATH}')
_ssh_terminal_router_module = _realm_support_importlib_util.module_from_spec(_ssh_terminal_router_spec)
_ssh_terminal_router_spec.loader.exec_module(_ssh_terminal_router_module)
register_ssh_terminal_router = _ssh_terminal_router_module.register_ssh_terminal_router

_JOBS_ROUTER_PATH = Path(__file__).resolve().with_name('jobs_router.py')
_jobs_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_jobs_router', _JOBS_ROUTER_PATH)
if _jobs_router_spec is None or _jobs_router_spec.loader is None:
    raise ImportError(f'Could not load Jobs router: {_JOBS_ROUTER_PATH}')
_jobs_router_module = _realm_support_importlib_util.module_from_spec(_jobs_router_spec)
_jobs_router_spec.loader.exec_module(_jobs_router_module)
register_jobs_router = _jobs_router_module.register_jobs_router

_AGENT_CONTROL_ROUTER_PATH = Path(__file__).resolve().with_name('agent_control_router.py')
_agent_control_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_agent_control_router', _AGENT_CONTROL_ROUTER_PATH)
if _agent_control_router_spec is None or _agent_control_router_spec.loader is None:
    raise ImportError(f'Could not load Agent Control router: {_AGENT_CONTROL_ROUTER_PATH}')
_agent_control_router_module = _realm_support_importlib_util.module_from_spec(_agent_control_router_spec)
_agent_control_router_spec.loader.exec_module(_agent_control_router_module)
register_agent_control_router = _agent_control_router_module.register_agent_control_router

_LIVE_ROUTER_PATH = Path(__file__).resolve().with_name('live_router.py')
_live_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_live_router', _LIVE_ROUTER_PATH)
if _live_router_spec is None or _live_router_spec.loader is None:
    raise ImportError(f'Could not load Live router: {_LIVE_ROUTER_PATH}')
_live_router_module = _realm_support_importlib_util.module_from_spec(_live_router_spec)
_live_router_spec.loader.exec_module(_live_router_module)
register_live_router = _live_router_module.register_live_router

ROOT = Path(__file__).resolve().parent
KEY_PATH = DATA_DIR / "master.key"
STATIC_DIR = ROOT / "static"
AGENT_PAYLOAD_DIR = Path(os.getenv("DARK_NOC_AGENT_PAYLOAD", "/opt/dark-noc/agent-payload"))
SESSION_TTL = 12 * 60 * 60
NODE_STALE_AFTER = 180
LOGIN_FAILURES: dict[str, list[int]] = {}
LOGIN_LOCK = threading.Lock()
VERSION = "2.9.48"
LIVE_CLIENTS: set[WebSocket] = set()
LOGGER = logging.getLogger("dark-noc")


SSH_FILE_CHUNK = 1024 * 1024
SSH_MULTIPART_OVERHEAD = 1024 * 1024
HUB_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
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
    return find_node_endpoint_conflict(conn, host, ssh_port, canonical_node_host, exclude_id)


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




def bootstrap() -> None:
    bootstrap_database(
        DATA_DIR=DATA_DIR,
        KEY_PATH=KEY_PATH,
        db=db,
        SCHEMA=SCHEMA,
        utc_ts=utc_ts,
        hash_password=hash_password,
        finalize_fleet_operation=finalize_fleet_operation,
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
    return _maintenance_acquire_hub_lease(
        db=db, utc_ts=utc_ts, hub_lease_seconds=HUB_LEASE_SECONDS,
        hub_instance_id=HUB_INSTANCE_ID, name=name, now=now,
    )


def queue_due_monitors(conn: sqlite3.Connection, now: int, online_cutoff: int) -> None:
    _maintenance_queue_due_monitors(
        conn, now, online_cutoff, decrypt=decrypt, logger=LOGGER, create_incident=create_incident,
    )


def queue_due_fleet_operations(conn: sqlite3.Connection, now: int) -> None:
    _maintenance_queue_due_fleet_operations(conn, now)


def rollup_previous_hour(conn: sqlite3.Connection, now: int) -> None:
    _maintenance_rollup_previous_hour(conn, now)


def run_maintenance_cycle() -> None:
    _maintenance_run_cycle(
        db=db, utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER,
        metric_raw_retention_days=METRIC_RAW_RETENTION_DAYS,
        metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,
        tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,
        monitor_result_retention_days=MONITOR_RESULT_RETENTION_DAYS,
        create_incident=create_incident, resolve_incident=resolve_incident,
        rollup_previous_hour=rollup_previous_hour, queue_due_monitors=queue_due_monitors,
        queue_due_fleet_operations=queue_due_fleet_operations,
    )


async def maintenance_loop() -> None:
    await _maintenance_loop_runner(
        acquire_hub_lease=acquire_hub_lease, run_cycle=run_maintenance_cycle, logger=LOGGER,
    )


lifespan = _build_maintenance_lifespan(
    bootstrap=lambda: bootstrap(), fernet_cls=Fernet, key_path=KEY_PATH,
    maintenance_loop=lambda: maintenance_loop(), db=lambda: db(),
    hub_instance_id=HUB_INSTANCE_ID, logger=LOGGER,
)


app = FastAPI(title="DARK NOC Hub", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def browser_origin_guard(request: Request, call_next):
    try:
        enforce_http_origin(request.method, request.url.path, request.headers, request.url.scheme)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


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
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith("/api/") or request.url.path in {"/", "/static/app.js", "/static/styles.css"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

_PLUGIN_REGISTRY_PATH = Path(__file__).resolve().with_name("plugin_registry.py")
_plugin_registry_spec = _realm_support_importlib_util.spec_from_file_location("dark_noc_plugin_registry", _PLUGIN_REGISTRY_PATH)
if _plugin_registry_spec is None or _plugin_registry_spec.loader is None:
    raise ImportError(f"Could not load Plugin Registry: {_PLUGIN_REGISTRY_PATH}")
_plugin_registry_module = _realm_support_importlib_util.module_from_spec(_plugin_registry_spec)
_plugin_registry_spec.loader.exec_module(_plugin_registry_module)
load_plugin_catalog = _plugin_registry_module.load_plugin_catalog
PLUGIN_CATALOG = load_plugin_catalog(Path(__file__).resolve().with_name("plugins"))

class NodeUpdateBody(NodeBody):
    pass


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
                "apt-get update -qq && apt-get install -y python3 python3-venv python3-pip ca-certificates certbot curl tar openssl iproute2 iputils-ping iptables iperf3 snmp tmux && "
                "install -d -m 0755 /opt/dark-noc-agent && "
                "install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul /etc/dark-ghostpro /etc/dark-packetpro /etc/dark-realm && "
                "install -d -m 0755 /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /etc/nginx/conf.d /var/lib/dark-noc-acme /var/lib/dark-noc-acme/.well-known /var/lib/dark-noc-acme/.well-known/acme-challenge",
                check=False, timeout=600,
            )
            log.append((prep.stdout + prep.stderr).strip()[-6000:])
            if prep.exit_status != 0:
                raise RuntimeError(f"Prerequisite installation failed (exit {prep.exit_status})")

            agent_file = AGENT_PAYLOAD_DIR / "agent.py"
            realm_adapter_file = AGENT_PAYLOAD_DIR / "realm_plugin.py"
            requirements_file = AGENT_PAYLOAD_DIR / "requirements.txt"
            requirements_lock_file = AGENT_PAYLOAD_DIR / "requirements.lock"
            service_file = AGENT_PAYLOAD_DIR / "dark-noc-agent.service"
            for required in (agent_file, realm_adapter_file, requirements_file, requirements_lock_file, service_file):
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
                    ("/opt/dark-noc-agent/realm_plugin.py", realm_adapter_file.read_bytes(), 0o644),
                    ("/opt/dark-noc-agent/requirements.txt", requirements_file.read_bytes(), 0o644),
                    ("/opt/dark-noc-agent/requirements.lock", requirements_lock_file.read_bytes(), 0o644),
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
                    requirements_lock_stage = next(entry["temporary"] for entry in staged_files if entry["target"].endswith("requirements.lock"))
                    venv_result = await ssh.run(
                        f"test ! -e {shlex.quote(venv_stage)} && "
                        f"python3 -m venv {shlex.quote(venv_stage)} && "
                        f"{shlex.quote(venv_stage + '/bin/pip')} install --disable-pip-version-check --require-hashes -r {shlex.quote(requirements_lock_stage)}",
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
            node = conn.execute("SELECT provision_status,agent_version FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not item:
                return
            status = "completed" if node and node["provision_status"] == "completed" and node["agent_version"] == VERSION else "failed"
            if node and node["provision_status"] == "completed" and node["agent_version"] != VERSION:
                conn.execute(
                    "UPDATE nodes SET provision_status='failed',provision_output=COALESCE(provision_output,'') || ?,updated_at=? WHERE id=?",
                    (f"\nVERSION CHECK FAILED: expected Agent {VERSION}, received {node['agent_version'] or 'unknown'}", utc_ts(), node_id),
                )
            conn.execute("UPDATE fleet_operation_items SET status=? WHERE id=?", (status, item_id))
            finalize_fleet_operation(conn, item["operation_id"])


async def orchestrate_agent_upgrade(
    operation_id: int,
    rollout: list[dict[str, Any]],
    batch_size: int,
    pause_seconds: int,
    stop_on_failure: bool,
) -> None:
    """Roll out the current Agent build in a canary-first, bounded sequence."""
    batches: list[list[dict[str, Any]]] = []
    remaining = list(rollout)
    if remaining and remaining[0].get("canary"):
        batches.append([remaining.pop(0)])
    batches.extend(remaining[offset:offset + batch_size] for offset in range(0, len(remaining), batch_size))
    for batch_index, batch in enumerate(batches):
        with db() as conn:
            operation = conn.execute("SELECT status FROM fleet_operations WHERE id=?", (operation_id,)).fetchone()
            if not operation or operation["status"] not in {"running", "scheduled"}:
                return
            for entry in batch:
                conn.execute("UPDATE fleet_operation_items SET status='running' WHERE id=?", (entry["item_id"],))
                conn.execute(
                    "UPDATE nodes SET provision_status='provisioning',provision_output='',updated_at=? WHERE id=?",
                    (utc_ts(), entry["node_id"]),
                )
        await asyncio.gather(
            *(
                provision_node_for_fleet(entry["item_id"], entry["node_id"], entry["enrollment"])
                for entry in batch
            )
        )
        with db() as conn:
            failed = conn.execute(
                "SELECT COUNT(*) count FROM fleet_operation_items WHERE operation_id=? AND status='failed'",
                (operation_id,),
            ).fetchone()["count"]
            if failed and stop_on_failure:
                conn.execute(
                    "UPDATE fleet_operation_items SET status='cancelled' WHERE operation_id=? AND status='scheduled'",
                    (operation_id,),
                )
                finalize_fleet_operation(conn, operation_id)
                return
        if batch_index + 1 < len(batches) and pause_seconds:
            await asyncio.sleep(pause_seconds)


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


register_auth_router(
    app,
    LoginBody=LoginBody, current_user=current_user, db=db, login_rate_check=login_rate_check,
    login_rate_record=login_rate_record, verify_password=verify_password, audit=audit, token_hash=token_hash,
    utc_ts=utc_ts, session_ttl=SESSION_TTL,
)

register_dashboard_router(
    app,
    current_user=current_user, db=db, utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER, version=VERSION,
    ssh_upload_limit=SSH_UPLOAD_LIMIT, ssh_relay_limit=SSH_RELAY_LIMIT,
    ssh_upload_queue_timeout=SSH_UPLOAD_QUEUE_TIMEOUT,
)

register_system_router(
    app,
    db=db, current_user=current_user, version=VERSION, key_path=KEY_PATH, static_dir=STATIC_DIR,
    hub_instance_id=HUB_INSTANCE_ID, metric_raw_retention_days=METRIC_RAW_RETENTION_DAYS,
    metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,
    tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,
    monitor_result_retention_days=MONITOR_RESULT_RETENTION_DAYS,
)


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


_ssh_file_router, _ssh_file_handlers = register_ssh_file_router(
    app,
    current_user=current_user, ssh_file_node=ssh_file_node, clean_remote_path=clean_remote_path,
    destructive_remote_path_allowed=destructive_remote_path_allowed,
    disconnect_aware_semaphore=disconnect_aware_semaphore, ssh_connection_options=ssh_connection_options,
    sftp_path_exists=sftp_path_exists, ssh_destination_lock=ssh_destination_lock,
    finalize_sftp_file=finalize_sftp_file, cleanup_sftp_path=cleanup_sftp_path, ssh_file_error=ssh_file_error,
    audit=audit, LOGGER=LOGGER, SSHRelayBody=SSHRelayBody, clean_remote_directory=clean_remote_directory,
    sftp_entry=sftp_entry, SSHFileWriteBody=SSHFileWriteBody, SSHFileActionBody=SSHFileActionBody,
    get_ssh_upload_limit=lambda: SSH_UPLOAD_LIMIT, get_ssh_relay_limit=lambda: SSH_RELAY_LIMIT,
    get_ssh_transfer_timeout=lambda: SSH_TRANSFER_TIMEOUT,
    get_ssh_transfer_idle_timeout=lambda: SSH_TRANSFER_IDLE_TIMEOUT,
    get_ssh_transfer_semaphore=lambda: SSH_TRANSFER_SEMAPHORE, get_ssh_file_chunk=lambda: SSH_FILE_CHUNK,
    get_ssh_editor_limit=lambda: SSH_EDITOR_LIMIT,
)
for _ssh_file_name, _ssh_file_handler in _ssh_file_handlers.items():
    globals()[_ssh_file_name] = _ssh_file_handler
del _ssh_file_name, _ssh_file_handler, _ssh_file_handlers


def tunnel_topology_side(node_role: str, method: str, tunnel_role: str) -> str:
    node_role = str(node_role or "").casefold()
    tunnel_role = str(tunnel_role or "").casefold()
    if node_role in {"hub", "edge"}:
        return "iran"
    if node_role == "exit":
        return "kharej"
    if method in {"DARK Backhaul", "DARK Ghost Pro"}:
        return "iran" if tunnel_role == "server" else "kharej" if tunnel_role == "client" else "unknown"
    if method == "DARK Packet Pro":
        return "iran" if tunnel_role == "client" else "kharej" if tunnel_role == "server" else "unknown"
    if method == "DARK Realm Pro":
        return "iran" if tunnel_role == "edge" else "kharej" if tunnel_role == "gateway" else "unknown"
    return "unknown"


def tunnel_health_score(item: dict[str, Any]) -> int:
    status = str(item.get("status") or "unknown").casefold()
    if status in {"down", "offline"}:
        return 0
    score = 35 if status == "stale" else 65 if status == "degraded" else 100
    try:
        score -= min(45, round(float(item.get("packet_loss") or 0) * 2))
    except (TypeError, ValueError):
        pass
    try:
        latency = float(item.get("latency_ms"))
        if latency > 250:
            score -= 25
        elif latency > 120:
            score -= 12
        elif latency > 60:
            score -= 5
    except (TypeError, ValueError):
        pass
    checks = item.get("checks") if isinstance(item.get("checks"), dict) else {}
    if checks.get("process") is False:
        score -= 50
    if checks.get("path") is False:
        score -= 35
    return min(max(score, 0), 100)


def transport_requires_certificate(transport: str) -> bool:
    return transport.casefold() in {"tls", "wss", "wssmux", "h2", "http2", "grpc", "relay+tls", "relay+wss", "relay+h2", "relay+grpc"}


def certificate_for_deployment(conn: sqlite3.Connection, certificate_id: int | None, node_id: int, transport: str) -> dict[str, Any] | None:
    if not transport_requires_certificate(transport):
        return None
    if not certificate_id:
        raise HTTPException(422, "This transport requires a valid TLS certificate")
    row = conn.execute("SELECT * FROM certificates WHERE id=? AND node_id=?", (certificate_id, node_id)).fetchone()
    if not row:
        raise HTTPException(404, "Certificate was not found on the selected transport-listener node")
    if row["status"] != "valid" or not row["expires_at"] or row["expires_at"] <= utc_ts() + 86400:
        raise HTTPException(409, "Certificate is not valid or expires in less than 24 hours")
    return {"certificate_id": row["id"], "certificate_domain": row["domain"], "certificate_path": row["cert_path"], "certificate_key_path": row["key_path"]}


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


def generic_pair_code(settings: dict[str, Any], token: str) -> str:
    payload = {
        "schema": 1,
        "plugin_id": settings.get("plugin_id"),
        "name": settings.get("name"),
        "endpoint": settings.get("endpoint"),
        "tunnel_port": settings.get("tunnel_port"),
        "user_ports": settings.get("user_ports", []),
        "transport": settings.get("transport"),
        "profile": settings.get("profile"),
        "restart_every": settings.get("restart_every"),
        "token": token,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "DNP1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def plugin_pair_code(settings: dict[str, Any], token: str) -> str:
    plugin_id = str(settings.get("plugin_id") or "")
    legacy_codecs = {
        "dark-realm": "realm",
        "dark-backhaul": "backhaul",
        "dark-ghostpro": "ghostpro",
        "dark-packetpro": "packetpro",
    }
    codec = str(settings.get("pair_codec") or legacy_codecs.get(plugin_id, ""))
    if codec == "realm":
        return realm_pair_code(settings)
    if codec == "backhaul":
        return dark_backhaul_pair_code(settings, token)
    if codec == "ghostpro":
        ports = ",".join(str(port) for port in settings["user_ports"])
        raw = "|".join(("GPC1", settings["endpoint"], str(settings["tunnel_port"]), token, settings["transport"], settings["profile"], settings["restart_every"], ports))
        return "DGP-" + base64.b64encode(raw.encode()).decode()
    if codec == "packetpro":
        mode, conn, mtu = PACKET_PROFILES[settings["profile"]]
        mode_index = {"normal": 0, "fast": 1, "fast2": 2, "fast3": 3}[mode]
        ports = ",".join(f"t{port}" for port in settings["user_ports"])
        raw = "|".join((
            "N1", settings["name"], settings["endpoint"], str(settings["tunnel_port"]), token,
            "1", str(mode_index), str(conn), str(mtu), settings["restart_every"], PAQET_CORE_TAG,
            settings["pair_id"], str(settings["pair_created"]), ports,
        ))
        return "DPP-N1-" + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    if codec == "generic-v1":
        return generic_pair_code(settings, token)
    raise HTTPException(409, "Plugin Pair Code codec is not supported by this Hub build")

def plugin_job_payload(settings: dict[str, Any], token: str, role: str) -> dict[str, Any]:
    payload = {**settings, "token": token, "role": role}
    certificate_role = str(settings.get("certificate_role") or "")
    if not certificate_role:
        # Rows created before Plugin Contract v1 did not persist this metadata.
        certificate_role = "gateway" if settings.get("plugin_id") == "dark-realm" else "server"
    if role != certificate_role:
        payload.pop("certificate_path", None)
        payload.pop("certificate_key_path", None)
    return payload

register_plugin_deployments_router(
    app,
    current_user=current_user, db=db, PLUGIN_CATALOG=PLUGIN_CATALOG, PairCodeDeployBody=PairCodeDeployBody,
    PluginDeployBody=PluginDeployBody, deploy_pair_code_mutation=deploy_pair_code_mutation,
    deploy_managed_mutation=deploy_managed_mutation, recover_pair_code_mutation=recover_pair_code_mutation,
    retry_hybrid_mutation=retry_hybrid_mutation, remove_hybrid_mutation=remove_hybrid_mutation,
    retry_managed_mutation=retry_managed_mutation, remove_managed_mutation=remove_managed_mutation,
    PluginDeploymentServiceError=PluginDeploymentServiceError, utc_ts=utc_ts,
    prepare_realm_settings=prepare_realm_settings, certificate_for_deployment=certificate_for_deployment,
    plugin_pair_code=plugin_pair_code, plugin_job_payload=plugin_job_payload, encrypt=encrypt, token_hash=token_hash,
    normalize_ip=normalize_ip, decrypt=decrypt, audit=audit, node_stale_after=NODE_STALE_AFTER, paqet_core_tag=PAQET_CORE_TAG,
)

register_certificates_router(
    app,
    current_user=current_user, db=db, CertificateBody=CertificateBody, issue_certificate_mutation=issue_certificate_mutation,
    renew_certificate_mutation=renew_certificate_mutation, CertificateFleetServiceError=CertificateFleetServiceError,
    utc_ts=utc_ts, normalize_ip=normalize_ip, audit=audit, node_stale_after=NODE_STALE_AFTER,
)

register_fleet_router(
    app,
    current_user=current_user, db=db, FleetOperationBody=FleetOperationBody,
    create_fleet_operation_mutation=create_fleet_operation_mutation,
    cancel_fleet_operation_mutation=cancel_fleet_operation_mutation,
    CertificateFleetServiceError=CertificateFleetServiceError, utc_ts=utc_ts, audit=audit, version=VERSION,
    get_queue_due_fleet_operations=lambda: queue_due_fleet_operations,
    get_provision_node_for_fleet=lambda: provision_node_for_fleet,
    get_orchestrate_agent_upgrade=lambda: orchestrate_agent_upgrade,
)

register_nodes_router(
    app,
    current_user=current_user, db=db, NodeBody=NodeBody, NodeUpdateBody=NodeUpdateBody, JobBody=JobBody,
    fetch_node_inventory=fetch_node_inventory, public_node=public_node, utc_ts=utc_ts,
    create_node_mutation=create_node_mutation, encrypt=encrypt, token_hash=token_hash,
    node_endpoint_conflict=node_endpoint_conflict, NodeTunnelServiceError=NodeTunnelServiceError,
    get_provision_node=lambda: provision_node, audit=audit, LOGGER=LOGGER,
    prepare_node_provision_mutation=prepare_node_provision_mutation, delete_node_mutation=delete_node_mutation,
    update_node_mutation=update_node_mutation, reset_node_fingerprint_mutation=reset_node_fingerprint_mutation,
    queue_plugin_install_mutation=queue_plugin_install_mutation, PLUGIN_CATALOG=PLUGIN_CATALOG,
    node_stale_after=NODE_STALE_AFTER, metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,
)

register_tunnels_router(
    app,
    current_user=current_user, db=db, fetch_tunnel_inventory=fetch_tunnel_inventory, utc_ts=utc_ts,
    normalize_ip=normalize_ip, tunnel_topology_side=tunnel_topology_side, tunnel_health_score=tunnel_health_score,
    fetch_tunnel_operation_rows=fetch_tunnel_operation_rows, public_job=public_job,
    TunnelActionBody=TunnelActionBody, queue_tunnel_action_mutation=queue_tunnel_action_mutation,
    NodeTunnelServiceError=NodeTunnelServiceError, audit=audit, TunnelReconfigureBody=TunnelReconfigureBody,
    reconfigure_tunnel_mutation=reconfigure_tunnel_mutation, PLUGIN_CATALOG=PLUGIN_CATALOG,
    certificate_for_deployment=certificate_for_deployment, decrypt=decrypt, plugin_job_payload=plugin_job_payload,
    plugin_pair_code=plugin_pair_code, token_hash=token_hash, remove_tunnel_mutation=remove_tunnel_mutation,
    node_stale_after=NODE_STALE_AFTER, tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,
)


register_monitoring_router(
    app,
    current_user=current_user, db=db, MonitorBody=MonitorBody, fetch_monitor_inventory=fetch_monitor_inventory,
    public_monitor=public_monitor, create_monitor_mutation=create_monitor_mutation,
    update_monitor_mutation=update_monitor_mutation, delete_monitor_mutation=delete_monitor_mutation,
    queue_monitor_run_mutation=queue_monitor_run_mutation, MonitorIncidentServiceError=MonitorIncidentServiceError,
    monitor_values=monitor_values, resolve_incident=resolve_incident, utc_ts=utc_ts, decrypt=decrypt, audit=audit,
    fetch_monitor_result_rows=fetch_monitor_result_rows, node_stale_after=NODE_STALE_AFTER,
)


register_incidents_router(
    app,
    current_user=current_user, db=db, IncidentNoteBody=IncidentNoteBody, IncidentActionBody=IncidentActionBody,
    fetch_incident_inventory=fetch_incident_inventory, fetch_incident_detail_rows=fetch_incident_detail_rows,
    add_incident_note_mutation=add_incident_note_mutation, incident_action_mutation=incident_action_mutation,
    MonitorIncidentServiceError=MonitorIncidentServiceError, append_incident_event=append_incident_event,
    resolve_incident=resolve_incident, utc_ts=utc_ts, audit=audit,
)


_jobs_router, _job_handlers = register_jobs_router(
    app, current_user=current_user, db=db, public_job=public_job,
)
for _job_handler_name, _job_handler in _job_handlers.items():
    globals()[_job_handler_name] = _job_handler
del _job_handler_name, _job_handler, _job_handlers


def managed_tunnel_report(method: str, service: str) -> bool:
    return any(
        method == str(plugin.get("ui", {}).get("method", ""))
        and service.startswith(str(plugin.get("runtime", {}).get("service_prefix", "")))
        for plugin in PLUGIN_CATALOG
        if str(plugin.get("runtime", {}).get("service_prefix", ""))
    )



def agent_inventory_flags(metrics: dict[str, Any]) -> tuple[bool, bool]:
    """Return (complete, fresh) for Agent service/plugin/tunnel inventory.

    v2.9.2 lightweight heartbeats expose telemetry_status but no explicit marker;
    legacy Agents expose neither and remain authoritative for compatibility.
    """
    marker = metrics.get("inventory_complete")
    status = str(metrics.get("telemetry_status") or "").strip().casefold()
    if marker is None:
        complete = status not in {"starting", "collecting", "stale"}
    elif isinstance(marker, bool):
        complete = marker
    else:
        complete = str(marker).strip().casefold() in {"1", "true", "yes", "on"}
    fresh = complete and status in {"", "fresh"}
    return complete, fresh


_agent_control_router, _agent_control_handlers = register_agent_control_router(
    app,
    AgentPulse=AgentPulse, AgentReport=AgentReport, JobResult=JobResult, agent_node=agent_node,
    db=db, utc_ts=utc_ts, token_hash=token_hash,
    get_agent_inventory_flags=lambda: agent_inventory_flags,
    get_managed_tunnel_report=lambda: managed_tunnel_report,
    get_create_incident=lambda: create_incident, get_resolve_incident=lambda: resolve_incident,
    get_finalize_fleet_operation=lambda: finalize_fleet_operation,
    get_telegram_notify=lambda: telegram_notify, get_live_clients=lambda: LIVE_CLIENTS,
)
for _agent_control_name, _agent_control_handler in _agent_control_handlers.items():
    globals()[_agent_control_name] = _agent_control_handler
del _agent_control_name, _agent_control_handler, _agent_control_handlers


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


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    return browser_origin_allowed(websocket.headers, websocket.url.scheme)


_ssh_terminal_router, _ssh_terminal_handlers = register_ssh_terminal_router(
    app,
    websocket_user=websocket_user, db=db, decrypt=decrypt, token_hash=token_hash, utc_ts=utc_ts, audit=audit,
    websocket_session_valid=websocket_session_valid, websocket_session_guard=websocket_session_guard,
    websocket_origin_allowed=websocket_origin_allowed, LOGGER=LOGGER,
    get_session_ttl=lambda: SESSION_TTL, get_ssh_keepalive_interval=lambda: SSH_KEEPALIVE_INTERVAL,
    get_ssh_keepalive_count_max=lambda: SSH_KEEPALIVE_COUNT_MAX,
)
for _ssh_terminal_name, _ssh_terminal_handler in _ssh_terminal_handlers.items():
    globals()[_ssh_terminal_name] = _ssh_terminal_handler
del _ssh_terminal_name, _ssh_terminal_handler, _ssh_terminal_handlers


_live_router, _live_handlers = register_live_router(
    app,
    websocket_user=websocket_user, token_hash=token_hash, utc_ts=utc_ts,
    websocket_session_guard=websocket_session_guard, websocket_origin_allowed=websocket_origin_allowed,
    get_session_ttl=lambda: SESSION_TTL, get_live_clients=lambda: LIVE_CLIENTS,
)
for _live_name, _live_handler in _live_handlers.items():
    globals()[_live_name] = _live_handler
del _live_name, _live_handler, _live_handlers


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
