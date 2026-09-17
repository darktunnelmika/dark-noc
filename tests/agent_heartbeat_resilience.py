from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_agent():
    spec = importlib.util.spec_from_file_location("dark_noc_agent_resilience", ROOT / "agent" / "agent.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load_agent()
assert agent.VERSION == "2.9.46"

payload = agent.minimal_heartbeat_payload({"autoheal": {"enabled": False}})
assert payload["agent_version"] == "2.9.46"
assert payload["metrics"]["telemetry_status"] == "starting"
assert payload["metrics"]["uptime"] >= 0

agent.refresh_connection_snapshot = lambda: []
agent.base_metrics = lambda previous: {
    "cpu": 1, "ram": 2, "swap": 0, "disk": 3, "load1": 0.1,
    "rx_bps": 10, "tx_bps": 20, "uptime": 30, "connections": 4,
    "bytes_recv": 1, "bytes_sent": 2, "disk_read_bytes": 3, "disk_write_bytes": 4,
    "network_errors": 0, "network_drops": 0, "ts": 5,
}
agent.system_inventory = lambda state: {"os": "Test Linux"}
agent.monitored_tunnels = lambda config: []

async def no_reports(_):
    return []

async def no_autoheal(config, reports, state):
    state["restart_history"] = []

agent._collect_tunnel_reports = no_reports
agent.maybe_autoheal = no_autoheal
agent.service_reports = lambda config: []
agent.plugin_inventory = lambda: {"dark_realm": {"installed": False}}
collected, updates = agent.collect_payload_blocking({"autoheal": {}}, {})
assert collected["metrics"]["inventory"]["os"] == "Test Linux"
assert collected["plugins"]["dark_realm"]["installed"] is False
assert updates["net"]["bytes_recv"] == 1

class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"agent_token": "RecoveredLocalToken123", "reused": False}


class FakeClient:
    def __init__(self):
        self.headers = {"Authorization": "Bearer OldToken123"}
        self.calls = []

    async def post(self, url, headers=None, timeout=None, json=None):
        self.calls.append((url, headers, timeout, json))
        return FakeResponse()


with tempfile.TemporaryDirectory(prefix="dark-noc-agent-recovery-") as tmp_name:
    root = Path(tmp_name)
    agent.LOCAL_HUB_ENV_PATH = root / "hub.env"
    agent.CONFIG_PATH = root / "config.json"
    agent.LOCAL_HUB_ENV_PATH.write_text("DARK_NOC_LOCAL_ENROLL_SECRET=BootstrapSecret123\n", encoding="utf-8")
    config = {"hub_url": "http://127.0.0.1:19090", "agent_token": "OldToken123"}
    client = FakeClient()
    assert asyncio.run(agent.recover_local_enrollment(client, config["hub_url"], config)) is True
    assert config["agent_token"] == "RecoveredLocalToken123"
    assert client.headers["Authorization"] == "Bearer RecoveredLocalToken123"
    assert json.loads(agent.CONFIG_PATH.read_text())["agent_token"] == "RecoveredLocalToken123"
    assert client.calls[0][1]["X-Dark-Noc-Existing-Agent"] == "OldToken123"

service = (ROOT / "deploy" / "dark-noc-agent.service").read_text(encoding="utf-8")
source = (ROOT / "agent" / "agent.py").read_text(encoding="utf-8")
hub = (ROOT / "hub" / "app.py").read_text(encoding="utf-8")
assert "WatchdogSec=90s" in service and "NotifyAccess=main" in service
assert "asyncio.to_thread(" in source and "collect_payload_blocking" in source
assert "recover_local_enrollment" in source and "WATCHDOG=1" in source
assert "/api/agent/pulse" in source and "last_payload_quarantined_at" in source
assert "NODE_STALE_AFTER = 180" in hub
assert 'method == "DARK Realm Pro"' in hub

print("Agent heartbeat resilience tests passed")
