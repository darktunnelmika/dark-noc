from __future__ import annotations

import base64
import hashlib
import importlib.util
import tempfile
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hub.realm_support import canonical_realm_mappings, prepare_realm_settings, realm_pair_code


ROOT = Path(__file__).resolve().parents[1]


def load_realm_agent_module():
    spec = importlib.util.spec_from_file_location("dark_noc_realm_plugin", ROOT / "agent" / "realm_plugin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decode_pair(code: str) -> str:
    prefix, encoded, checksum = code.split(".")
    assert prefix == "DR1"
    encoded += "=" * (-len(encoded) % 4)
    payload = base64.urlsafe_b64decode(encoded).decode()
    assert hashlib.sha256(payload.encode()).hexdigest() == checksum
    return payload


def main() -> None:
    mappings = canonical_realm_mappings([443, 8443], 3080, ["443>8000", "8443>8000@4000"])
    assert mappings == ["443>8000@3080", "8443>8000@4000"]

    settings = prepare_realm_settings(
        {
            "name": "realm-test",
            "transport": "wss",
            "profile": "lowping",
            "tunnel_port": 3080,
            "user_ports": [443],
            "port_mappings": ["443>8000@3080"],
            "target_host": "127.0.0.1",
            "tls_domain": "realm.example.com",
            "sni": "realm.example.com",
            "ws_host": "realm.example.com",
            "ws_path": "/dark-test",
            "ws_mask": "standard",
            "restart_every": "off",
            "created_at": 1700000000,
        },
        gateway_host="realm.example.com",
        pair_mode=True,
    )
    payload = decode_pair(realm_pair_code(settings))
    expected = {
        "schema=1",
        "name=realm-test",
        "gateway=realm.example.com",
        "target=127.0.0.1",
        "transport=wss",
        "profile=low_ping",
        "maps=443>8000@3080",
        "tls_domain=realm.example.com",
        "tls_insecure=0",
        "sni=realm.example.com",
        "ws_host=realm.example.com",
        "ws_path=/dark-test",
        "ws_mask=standard",
        "created=1700000000",
    }
    assert expected.issubset(set(payload.splitlines()))

    realm_agent = load_realm_agent_module()
    edge_config = realm_agent.render_config(settings, "edge")
    assert 'listen = "0.0.0.0:443"' in edge_config
    assert 'remote = "realm.example.com:3080"' in edge_config
    assert "remote_transport" in edge_config
    assert "cert=" not in edge_config and "key=" not in edge_config

    with tempfile.TemporaryDirectory() as tmp_name:
        cert = Path(tmp_name) / "fullchain.pem"
        key = Path(tmp_name) / "privkey.pem"
        cert.write_text("test")
        key.write_text("test")
        gateway_settings = {
            **settings,
            "certificate_path": str(cert),
            "certificate_key_path": str(key),
        }
        gateway_config = realm_agent.render_config(gateway_settings, "gateway")
    assert 'listen = "0.0.0.0:3080"' in gateway_config
    assert 'remote = "127.0.0.1:8000"' in gateway_config
    assert "listen_transport" in gateway_config and "cert=" in gateway_config and "key=" in gateway_config

    app_source = (ROOT / "hub" / "app.py").read_text()
    agent_source = (ROOT / "agent" / "agent.py").read_text()
    frontend = (ROOT / "hub" / "static" / "app.js").read_text()
    index = (ROOT / "hub" / "static" / "index.html").read_text()
    assert '"id": "dark-realm"' in app_source
    assert "DARK Realm Pro" in agent_source
    assert "dark-realm" in frontend
    assert "plugin-realm" in index
    assert 'VERSION = "2.9.13"' in app_source
    assert 'VERSION = "2.9.13"' in agent_source
    assert "_remove_ufw_rules(firewall_rules)" in (ROOT / "agent" / "realm_plugin.py").read_text()

    commands = []
    realm_agent.shutil.which = lambda command: "/usr/sbin/ufw" if command == "ufw" else None
    realm_agent._run = lambda command, timeout=30: (commands.append(command) or (0, ""))
    realm_agent._remove_ufw_rules(["443/tcp", "invalid", "8443/tcp"])
    assert commands == [
        ["ufw", "--force", "delete", "allow", "443/tcp"],
        ["ufw", "--force", "delete", "allow", "8443/tcp"],
    ]
    print("DARK Realm NOC integration tests passed")


if __name__ == "__main__":
    main()
