from __future__ import annotations

import os

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
TUNNEL_SAMPLE_RETENTION_DAYS = bounded_env_int("DARK_NOC_TUNNEL_RETENTION_DAYS", 31, 1, 365)
HUB_LEASE_SECONDS = bounded_env_int("DARK_NOC_HUB_LEASE_SECONDS", 75, 30, 300)
SSH_EDITOR_LIMIT = bounded_env_int("DARK_NOC_SSH_EDITOR_LIMIT_KB", 1024, 16, 16_384) * 1024
