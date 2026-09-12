from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


hub = text("hub/app.py")
agent = text("agent/agent.py")
installer = text("install-hub.sh")
node_installer = text("install-node.sh")
upgrader = text("upgrade.sh")
cli = text("darknoc")
ci = text(".github/workflows/ci.yml")
release = text(".github/workflows/release.yml")
index = text("hub/static/index.html")

assert 'VERSION = "2.9.3"' in hub
assert 'VERSION = "2.9.3"' in agent
assert 'VERSION="2.9.3"' in cli
assert "Nightfall Command v2.9.3" in installer
assert "Zero-touch Node v2.9.3" in node_installer
assert "app.js?v=2.9.3" in index
assert "WatchdogSec=90s" in text("deploy/dark-noc-agent.service")
assert "NotifyAccess=main" in text("deploy/dark-noc-agent.service")
assert 'AGENT_PAYLOAD_DIR = Path(' in hub
assert 'AGENT_PAYLOAD_DIR / "realm_plugin.py"' in hub
assert "/opt/dark-noc/agent-payload/realm_plugin.py" in installer
assert "/opt/dark-noc-agent/realm_plugin.py" in installer
assert "X-Dark-Noc-Existing-Agent" in installer
assert "LOCAL_AGENT_BASELINE" in installer
assert "fresh v2.9.3 heartbeat" in installer
assert "/etc/dark-realm" in installer and "/etc/dark-realm" in node_installer and "/etc/dark-realm" in upgrader
assert 'install -m 0644 "$SCRIPT_DIR/agent/realm_plugin.py" /opt/dark-noc-agent/realm_plugin.py' in upgrader
assert "had_realm_adapter=0" in upgrader and "had_agent_payload=0" in upgrader
for workflow in (ci, release):
    assert "python tests/realm_integration.py" in workflow
    assert "python tests/local_hub_recovery.py" in workflow
    assert "python tests/installer_contracts.py" in workflow
assert "python tests/agent_heartbeat_resilience.py" in ci
assert "python tests/topology_inventory_regression.py" in ci

with tempfile.TemporaryDirectory(prefix="dark-noc-agent-without-realm-") as tmp_name:
    copied = Path(tmp_name) / "agent.py"
    shutil.copy2(ROOT / "agent" / "agent.py", copied)
    spec = importlib.util.spec_from_file_location("dark_noc_agent_without_realm", copied)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._REALM_ADAPTER_ERROR
    inventory = module.realm_inventory()
    assert inventory["installed"] is False and inventory["adapter_ready"] is False
    try:
        module.install_dark_realm()
        raise AssertionError("missing Realm adapter did not reject Realm operation")
    except RuntimeError as exc:
        assert "adapter is unavailable" in str(exc)

print("Installer and optional-adapter contracts passed")
