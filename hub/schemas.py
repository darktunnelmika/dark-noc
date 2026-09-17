from __future__ import annotations

import ipaddress
import json
import math
import posixpath
import re
import secrets

from typing import Any

from pydantic import BaseModel, Field, field_validator

def canonical_node_host(value: Any) -> str:
    text = str(value or "").strip()
    try:
        address = ipaddress.ip_address(text)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return address.compressed.lower()
    except ValueError:
        return text.rstrip(".").lower()

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
    kind: str = Field(pattern=r"^(diagnostics|tunnel_test|restart_service|service_status|logs|configure_autoheal|sync_agent|upgrade_agents)$")
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
    user_ports: list[int] = Field(min_length=1, max_length=256)
    transport: str = Field(default="tcpmux", max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    profile: str = Field(default="balanced", max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    restart_every: str = Field(default="off", pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)
    target_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port_mappings: list[str] = Field(default_factory=list, max_length=256)
    tls_domain: str | None = Field(default=None, max_length=253)
    tls_insecure: bool = False
    sni: str | None = Field(default=None, max_length=253)
    alpn: str | None = Field(default=None, max_length=128)
    ws_host: str | None = Field(default=None, max_length=253)
    ws_path: str | None = Field(default=None, max_length=128)
    ws_mask: str = Field(default="skipped", pattern=r"^(skipped|fixed|standard)$")

    @field_validator("iran_endpoint", "target_host")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = NodeBody.validate_host(value)
        try:
            if ipaddress.ip_address(normalized).version != 4:
                raise ValueError("DARK tunnel automation currently requires IPv4 or a hostname")
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
    user_ports: list[int] = Field(min_length=1, max_length=256)
    transport: str = Field(default="tcpmux", max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    profile: str = Field(default="balanced", max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    restart_every: str = Field(default="off", pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)
    target_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port_mappings: list[str] = Field(default_factory=list, max_length=256)
    tls_domain: str | None = Field(default=None, max_length=253)
    tls_insecure: bool = False
    sni: str | None = Field(default=None, max_length=253)
    alpn: str | None = Field(default=None, max_length=128)
    ws_host: str | None = Field(default=None, max_length=253)
    ws_path: str | None = Field(default=None, max_length=128)
    ws_mask: str = Field(default="skipped", pattern=r"^(skipped|fixed|standard)$")

    @field_validator("iran_endpoint", "target_host")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return PluginDeployBody.validate_endpoint(value)

    @field_validator("kharej_endpoint", mode="before")
    @classmethod
    def validate_kharej_endpoint(cls, value: str | None) -> str | None:
        text = str(value or "").strip()
        return PluginDeployBody.validate_endpoint(text) if text else None

class TunnelActionBody(BaseModel):
    action: str = Field(pattern=r"^(start|stop|restart|logs|status|test|install)$")

class TunnelReconfigureBody(BaseModel):
    user_ports: list[int] = Field(min_length=1, max_length=256)
    transport: str = Field(max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    profile: str = Field(max_length=64, pattern=r"^[A-Za-z0-9+._-]+$")
    restart_every: str = Field(pattern=r"^(off|1h|6h|12h|24h)$")
    certificate_id: int | None = Field(default=None, gt=0)

class CertificateBody(BaseModel):
    node_id: int = Field(gt=0)
    domain: str = Field(min_length=4, max_length=253, pattern=r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")

class AgentPulse(BaseModel):
    agent_version: str = Field(default="unknown", min_length=1, max_length=64)
    agent_loop_ts: int | None = Field(default=None, ge=0)
    telemetry_status: str = Field(default="unknown", max_length=32)
    telemetry_age_seconds: int | None = Field(default=None, ge=0)
    inventory_error: str | None = Field(default=None, max_length=1000)

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

for _schema_model in [LoginBody, NodeBody, SSHRelayBody, IncidentActionBody, IncidentNoteBody, MonitorBody, FleetOperationBody, SSHFileActionBody, SSHFileWriteBody, JobBody, PluginDeployBody, PairCodeDeployBody, TunnelActionBody, TunnelReconfigureBody, CertificateBody, AgentPulse, AgentReport, JobResult]:
    _schema_model.model_rebuild()
del _schema_model
