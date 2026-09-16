from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
module_path = root / "hub/plugin_registry.py"
spec = importlib.util.spec_from_file_location("dark_noc_plugin_registry_contract", module_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

catalog = module.load_plugin_catalog(root / "hub/plugins")
assert [item["id"] for item in catalog] == [
    "dark-backhaul", "dark-ghostpro", "dark-packetpro", "dark-realm",
]
assert len({item["ui"]["method"] for item in catalog}) == len(catalog)
assert len({item["ui"]["inventory_key"] for item in catalog}) == len(catalog)
for item in catalog:
    assert item["schema_version"] == 1
    assert item["roles"]["iran"] and item["roles"]["kharej"]
    assert item["transports"] and item["profiles"]
    assert item["ui"]["icon"] and item["ui"]["method"] and item["ui"]["inventory_key"]
    assert item["runtime"]["settings_profile"]
    assert item["runtime"]["pair_codec"]
    assert isinstance(item["runtime"]["pair_code"], bool)

# A new standard plugin can join the Hub catalog by adding only a manifest.
example = {
    "schema_version": 1,
    "order": 1,
    "id": "dark-example",
    "name": "DARK Example",
    "version": "1.0.0",
    "publisher": "@mikakhadm",
    "repository": "https://github.com/darktunnelmika/dark-example",
    "description": "Registry contract example.",
    "roles": {"iran": "server", "kharej": "client"},
    "transports": ["tcp"],
    "profiles": ["stable"],
    "automated": True,
    "ui": {
        "icon": "DX", "method": "DARK Example", "inventory_key": "dark_example",
        "form_profile": "standard", "tls_transports": [],
        "endpoint_default_side": "iran", "iran_role_label": "Server",
    },
    "runtime": {
        "settings_profile": "standard", "pair_codec": "generic-v1", "pair_code": True,
        "pair_endpoint": "iran", "pair_endpoint_ipv4": False, "managed_endpoint": "iran",
        "certificate_side": "none", "certificate_role": "",
        "allow_tunnel_port_overlap": False, "iran_endpoint_must_match_node": True,
        "pair_hides_certificate": False,
    },
}
with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    (directory / "dark-example.json").write_text(json.dumps(example), encoding="utf-8")
    loaded = module.load_plugin_catalog(directory)
    assert len(loaded) == 1 and loaded[0]["id"] == "dark-example"
    (directory / "duplicate.json").write_text(json.dumps(example), encoding="utf-8")
    try:
        module.load_plugin_catalog(directory)
    except module.PluginRegistryError as exc:
        assert "Duplicate plugin id" in str(exc)
    else:
        raise AssertionError("Duplicate plugin IDs must fail closed")

app = (root / "hub/app.py").read_text()
service = (root / "hub/plugin_deployment_service.py").read_text()
frontend = (root / "hub/static/app.js").read_text()
actions = (root / "hub/static/plugin-actions.js").read_text()
agent = (root / "agent/agent.py").read_text()
router = (root / "hub/plugin_deployments_router.py").read_text()

assert "load_plugin_catalog" in app
assert "PLUGIN_CATALOG = [{" not in app
assert 'settings_profile == "realm"' in service
assert 'settings_profile == "packet"' in service
assert 'runtime.get("pair_codec")' in service
assert "function pluginSpec(" in frontend
assert "state.selectedPlugin==='dark-realm'" not in frontend
assert "state.selectedPlugin==='dark-packetpro'" not in frontend
assert "state.selectedPlugin==='dark-realm'" not in actions
assert "plugin?.profiles||[]" in actions
assert "plugin?.runtime?.pair_code===false" in actions
assert "def plugin_adapters(" in agent
assert "def plugin_adapter(" in agent
assert '"inventory_key": "dark_backhaul"' in agent
assert '"inventory_key": "dark_ghostpro"' in agent
assert '"inventory_key": "dark_packetpro"' in agent
assert '"inventory_key": "dark_realm"' in agent
assert "for adapter in plugin_adapters().values()" in agent
assert 'if kind in {"plugin_deploy", "plugin_install", "plugin_remove"}' in agent
assert "service in managed_services" in agent
assert 'method in {"DARK Backhaul", "DARK Ghost Pro", "DARK Packet Pro", "DARK Realm Pro"}' in agent
assert '"plugin_name":' in router
assert 'VERSION = "2.9.45"' in app
assert 'VERSION = "2.9.45"' in agent

# Hub manifests and privileged Agent adapters must move together. This prevents
# a Plugin Store entry from shipping without an executable Agent capability or
# with an inventory key that can never report as installed.
agent_module_path = root / "agent/agent.py"
agent_spec = importlib.util.spec_from_file_location("dark_noc_plugin_registry_agent", agent_module_path)
assert agent_spec is not None and agent_spec.loader is not None
agent_module = importlib.util.module_from_spec(agent_spec)
agent_spec.loader.exec_module(agent_module)
adapters = agent_module.plugin_adapters()
assert set(adapters) == {item["id"] for item in catalog}
for item in catalog:
    adapter = adapters[item["id"]]
    assert adapter["name"] == item["name"]
    assert adapter["inventory_key"] == item["ui"]["inventory_key"]
    for capability in ("inventory", "install", "deploy", "remove"):
        assert callable(adapter[capability]), (item["id"], capability)

print("Plugin Registry v1 contract passed")
