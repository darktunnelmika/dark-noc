import base64
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="dark-noc-test-") as data_dir:
    os.environ["DARK_NOC_DATA"] = data_dir
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "TestPassword-1234"
    os.environ["DARK_NOC_PUBLIC_HOST"] = "noc.example.test"
    os.environ["DARK_NOC_PUBLIC_PORT"] = "9090"
    hub_dir = Path(__file__).resolve().parents[1] / "hub"
    sys.path.insert(0, str(hub_dir))
    import app

    enrollment_tokens = {}

    async def fake_provision_node(node_id, enrollment):
        enrollment_tokens[node_id] = enrollment
        with app.db() as conn:
            conn.execute(
                "UPDATE nodes SET provision_status='completed',updated_at=? WHERE id=?",
                (app.utc_ts(), node_id),
            )

    app.provision_node = fake_provision_node

    assert app.clean_remote_path("/root/", filename="dark_core") == "/root/dark_core"
    assert app.clean_remote_path("/tmp/../root/file.bin") == "/root/file.bin"
    try:
        app.clean_remote_path("relative/file.bin")
        raise AssertionError("relative SSH file path was accepted")
    except ValueError:
        pass

    with TestClient(app.app, base_url="https://testserver") as client:
        health = client.get("/healthz")
        assert health.status_code == 200 and health.json() == {"status": "ok", "version": "2.9.17"}
        assert app.configured_public_hub_url() == "https://noc.example.test:9090"
        assert health.headers["x-content-type-options"] == "nosniff"
        backhaul = next(item for item in app.PLUGIN_CATALOG if item["id"] == "dark-backhaul")
        assert {"wss", "wssmux"}.issubset(backhaul["transports"])
        pair = app.dark_backhaul_pair_code({"endpoint": "tunnel.example.com", "tunnel_port": 3080, "user_ports": [443], "transport": "wssmux", "profile": "balanced", "restart_every": "off"}, "PairToken123")
        assert base64.b64decode(pair.removeprefix("DBH-")).decode() == "B2|tunnel.example.com|3080|PairToken123|6|balanced|off|443"
        client_payload = app.plugin_job_payload({"plugin_id": "dark-backhaul", "certificate_domain": "tunnel.example.com", "certificate_path": "/etc/cert.pem", "certificate_key_path": "/etc/key.pem"}, "PairToken123", "client")
        assert client_payload["certificate_domain"] == "tunnel.example.com" and "certificate_path" not in client_payload and "certificate_key_path" not in client_payload

        login = client.post("/api/auth/login", json={"username": "admin", "password": "TestPassword-1234"})
        assert login.status_code == 200
        assert client.post("/api/ssh/upload/999", data={"remote_path": "/root/"}, files={"file": ("test.txt", b"test")}).status_code == 404
        assert client.post("/api/ssh/relay", json={"source_node_id": 999, "destination_node_id": 998, "source_path": "/root/a", "destination_path": "/root/b"}).status_code == 404

        account = client.put("/api/auth/account", json={
            "current_password": "TestPassword-1234", "username": "noc-owner"
        })
        assert account.status_code == 403
        assert "server-only" in account.json()["detail"]

        node = client.post("/api/nodes", json={
            "name": "IR-TEST-01", "region": "Iran Edge", "role": "edge",
            "host": "127.0.0.1", "ssh_port": 22, "ssh_user": "root", "ssh_password": "test-root-password"
        })
        assert node.status_code == 202 and node.json()["provisioning"] is True
        token = enrollment_tokens[node.json()["id"]]

        report = client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {token}"}, json={
            "agent_version": "test", "metrics": {
                "cpu": 12, "ram": 34, "swap": 0, "disk": 20, "load1": 0.2,
                "rx_bps": 1000000, "tx_bps": 500000, "uptime": 5000, "connections": 42
            }, "services": [{"name": "backhaul@test-link.service", "status": "active"}], "plugins": {"dark_backhaul": {"installed": True, "version": "1.9.2", "instances": 1}}, "autoheal": {"enabled": True, "cooldown_seconds": 300, "max_restarts_per_hour": 3},
            "tunnels": [{
                "name": "test-link", "method": "DARK Backhaul", "target": "127.0.0.1:443",
                "service": "backhaul@test-link.service", "listen_port": 443, "status": "healthy",
                "latency_ms": 62, "packet_loss": 0, "sessions": 21
            }, {"name": "must-be-ignored", "method": "PAQET", "target": "127.0.0.1:9999", "service": "paqet.service", "status": "healthy"}]
        })
        assert report.status_code == 200

        dashboard = client.get("/api/dashboard").json()
        assert dashboard["version"] == "2.9.17"
        assert dashboard["nodes"]["online"] == 1
        assert dashboard["connections"] == 42
        assert dashboard["rx_bps"] == 1000000 and dashboard["tx_bps"] == 500000
        traffic = client.get("/api/dashboard/traffic?minutes=60")
        assert traffic.status_code == 200 and traffic.json()[-1]["rx_bps"] == 1000000
        tunnel_rows = client.get("/api/tunnels").json()
        assert len(tunnel_rows) == 1 and tunnel_rows[0]["node_host"] == "127.0.0.1"
        assert tunnel_rows[0]["node_ssh_configured"] == 1 and tunnel_rows[0]["node_agent_online"] is True
        live_node = client.get("/api/nodes").json()[0]
        assert live_node["services"][0]["status"] == "active" and live_node["autoheal_enabled"] == 1
        assert live_node["plugins"]["dark_backhaul"]["installed"] is True
        updated = client.put("/api/nodes/1", json={
            "name": "IR-TEST-01", "region": "Iran Edge", "role": "edge",
            "host": "127.0.0.1", "ssh_port": 2222, "ssh_user": "root"
        })
        assert updated.status_code == 200 and updated.json()["ssh_pin_reset"] is True

        job = client.post("/api/nodes/1/jobs", json={"kind": "diagnostics"})
        assert job.status_code == 202
        jobs = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()
        assert len(jobs) == 1 and jobs[0]["kind"] == "diagnostics"
        assert client.post(f"/api/agent/jobs/{jobs[0]['id']}/lease", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        result = client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": jobs[0]["id"], "status": "completed", "output": "ok"})
        assert result.status_code == 200
        duplicate_result = client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": jobs[0]["id"], "status": "completed", "output": "ok"})
        assert duplicate_result.status_code == 200 and duplicate_result.json()["idempotent"] is True

        plugins = client.get("/api/plugins")
        assert plugins.status_code == 200 and [item["id"] for item in plugins.json()] == ["dark-backhaul", "dark-ghostpro", "dark-packetpro", "dark-realm"]
        realm_catalog = next(item for item in plugins.json() if item["id"] == "dark-realm")
        assert realm_catalog["roles"] == {"iran": "edge", "kharej": "gateway"}
        assert realm_catalog["transports"] == ["tcp", "tls", "ws", "wss"]
        packet_catalog = next(item for item in plugins.json() if item["id"] == "dark-packetpro")
        assert packet_catalog["roles"] == {"iran": "client", "kharej": "server"} and packet_catalog["transports"] == ["kcp"]
        exit_node = client.post("/api/nodes", json={
            "name": "DE-TEST-01", "region": "Global Exit", "role": "exit",
            "host": "198.51.100.20", "ssh_port": 22, "ssh_user": "root", "ssh_password": "test-root-password"
        })
        assert exit_node.status_code == 202
        exit_token = enrollment_tokens[exit_node.json()["id"]]
        exit_report = client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {exit_token}"}, json={
            "agent_version": "test", "metrics": {"cpu": 1, "ram": 2, "disk": 3}, "tunnels": []
        })
        assert exit_report.status_code == 200
        wrong_endpoint = client.post("/api/plugins/dark-backhaul/deploy", json={
            "name": "wrong-link", "iran_node_id": 1, "kharej_node_id": 2,
            "iran_endpoint": "203.0.113.10", "tunnel_port": 3080,
            "user_ports": [443], "transport": "tcpmux", "profile": "balanced", "restart_every": "off"
        })
        assert wrong_endpoint.status_code == 422
        deployment = client.post("/api/plugins/dark-backhaul/deploy", json={
            "name": "test-link", "iran_node_id": 1, "kharej_node_id": 2,
            "iran_endpoint": "127.0.0.1", "tunnel_port": 3080,
            "user_ports": [443, 8443], "transport": "tcpmux", "profile": "balanced",
            "restart_every": "off"
        })
        assert deployment.status_code == 202 and set(deployment.json()["jobs"]) == {"iran", "kharej"}
        deployments = client.get("/api/plugin-deployments").json()
        assert len(deployments) == 1 and deployments[0]["status"] == "queued"
        iran_plugin_jobs = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()
        kharej_plugin_jobs = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {exit_token}"}).json()
        assert iran_plugin_jobs[0]["kind"] == "plugin_deploy"
        assert kharej_plugin_jobs[0]["kind"] == "plugin_deploy"
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": iran_plugin_jobs[0]["id"], "status": "failed", "output": "network error"}).status_code == 200
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {exit_token}"}, json={"job_id": kharej_plugin_jobs[0]["id"], "status": "failed", "output": "network error"}).status_code == 200
        assert client.get("/api/plugin-deployments").json()[0]["status"] == "failed"
        retry = client.post("/api/plugin-deployments/1/retry")
        assert retry.status_code == 202 and set(retry.json()["jobs"]) == {"iran", "kharej"}
        retry_iran = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        retry_kharej = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {exit_token}"}).json()[0]
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": retry_iran["id"], "status": "completed", "output": "ok"}).status_code == 200
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {exit_token}"}, json={"job_id": retry_kharej["id"], "status": "completed", "output": "ok"}).status_code == 200
        assert client.get("/api/plugin-deployments").json()[0]["status"] == "completed"
        removal = client.post("/api/plugin-deployments/1/remove")
        assert removal.status_code == 202
        remove_iran = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        remove_kharej = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {exit_token}"}).json()[0]
        assert remove_iran["kind"] == "plugin_remove" and remove_kharej["kind"] == "plugin_remove"
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": remove_iran["id"], "status": "completed", "output": "removed"}).status_code == 200
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {exit_token}"}, json={"job_id": remove_kharej["id"], "status": "completed", "output": "removed"}).status_code == 200
        assert client.get("/api/plugin-deployments").json()[0]["status"] == "removed"

        pair = client.post("/api/plugins/dark-backhaul/pair-code", json={
            "name": "hybrid-link", "iran_node_id": 1, "iran_endpoint": "127.0.0.1",
            "remote_label": "Armenia", "tunnel_port": 3090, "user_ports": [443, 8443],
            "transport": "tcpmux", "profile": "balanced", "restart_every": "6h"
        })
        assert pair.status_code == 202 and pair.json()["pair_code"].startswith("DBH-")
        decoded = base64.b64decode(pair.json()["pair_code"].removeprefix("DBH-")).decode().split("|")
        assert decoded[0:3] == ["B2", "127.0.0.1", "3090"]
        assert decoded[4:] == ["2", "balanced", "6h", "443,8443"]
        pair_rows = client.get("/api/plugin-deployments").json()
        assert pair_rows[0]["mode"] == "pair_code" and pair_rows[0]["kharej_node"] == "Armenia"
        assert "pair_token_enc" not in pair_rows[0]
        revealed = client.post(f"/api/hybrid-deployments/{pair.json()['deployment_id']}/pair-code")
        assert revealed.status_code == 200 and revealed.json()["pair_code"] == pair.json()["pair_code"]
        pair_job = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        assert pair_job["kind"] == "plugin_deploy" and json.loads(pair_job["payload"])["role"] == "server"
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": pair_job["id"], "status": "completed", "output": "ready"}).status_code == 200
        assert client.get("/api/plugin-deployments").json()[0]["status"] == "awaiting_pair"

        tls_without_certificate = client.post("/api/plugins/dark-ghostpro/pair-code", json={
            "name": "tls-missing", "iran_node_id": 1, "iran_endpoint": "127.0.0.1",
            "remote_label": "Ghost Exit", "tunnel_port": 9444, "user_ports": [444],
            "transport": "relay+tls", "profile": "stable", "restart_every": "off"
        })
        assert tls_without_certificate.status_code == 422 and "certificate" in tls_without_certificate.json()["detail"].lower()

        ghost = client.post("/api/plugins/dark-ghostpro/pair-code", json={
            "name": "ghost-link", "iran_node_id": 1, "iran_endpoint": "127.0.0.1",
            "remote_label": "Ghost Exit", "tunnel_port": 9443, "user_ports": [443, 8443],
            "transport": "relay", "profile": "stable", "restart_every": "12h"
        })
        assert ghost.status_code == 202 and ghost.json()["pair_code"].startswith("DGP-")
        ghost_decoded = base64.b64decode(ghost.json()["pair_code"].removeprefix("DGP-")).decode().split("|")
        assert ghost_decoded[0:3] == ["GPC1", "127.0.0.1", "9443"]
        assert ghost_decoded[4:] == ["relay", "stable", "12h", "443,8443"]
        ghost_job = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        assert json.loads(ghost_job["payload"])["plugin_id"] == "dark-ghostpro"
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": ghost_job["id"], "status": "completed", "output": "ready"}).status_code == 200

        packet_pair = client.post("/api/plugins/dark-packetpro/pair-code", json={
            "name": "packet-link", "iran_node_id": 1, "iran_endpoint": "127.0.0.1",
            "kharej_endpoint": "198.51.100.20", "remote_label": "Packet Exit", "tunnel_port": 9898,
            "user_ports": [443, 8443], "transport": "kcp", "profile": "balanced", "restart_every": "6h"
        })
        assert packet_pair.status_code == 202 and packet_pair.json()["pair_code"].startswith("DPP-N1-")
        encoded = packet_pair.json()["pair_code"].removeprefix("DPP-N1-")
        packet_decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode().split("|")
        assert packet_decoded[0:4] == ["N1", "packet-link", "198.51.100.20", "9898"]
        assert packet_decoded[5:10] == ["1", "1", "4", "1150", "6h"] and packet_decoded[-1] == "t443,t8443"
        packet_job = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        packet_payload = json.loads(packet_job["payload"])
        assert packet_payload["plugin_id"] == "dark-packetpro" and packet_payload["role"] == "client"
        revealed_packet = client.post(f"/api/hybrid-deployments/{packet_pair.json()['deployment_id']}/pair-code")
        assert revealed_packet.status_code == 200 and revealed_packet.json()["pair_code"] == packet_pair.json()["pair_code"]

        control = client.post("/api/tunnels/1/action", json={"action": "restart"})
        assert control.status_code == 202
        control_job = client.get("/api/agent/jobs", headers={"Authorization": f"Bearer {token}"}).json()[0]
        assert control_job["kind"] == "tunnel_control" and json.loads(control_job["payload"])["name"] == "test-link"
        assert client.post("/api/agent/jobs/result", headers={"Authorization": f"Bearer {token}"}, json={"job_id": control_job["id"], "status": "completed", "output": "restarted"}).status_code == 200

        failing_telemetry = {
            "agent_version": "test", "metrics": {"cpu": 2, "ram": 3, "disk": 4},
            "tunnels": [{"name": "test-link", "method": "DARK Backhaul", "target": "127.0.0.1:443", "service": "backhaul@test-link.service", "status": "down", "packet_loss": 100, "checks": {"process": False, "path": False}}]
        }
        assert client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {token}"}, json=failing_telemetry).status_code == 200
        assert not [item for item in client.get("/api/incidents").json() if item["status"] == "open"]
        assert client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {token}"}, json=failing_telemetry).status_code == 200
        active_incidents = [item for item in client.get("/api/incidents").json() if item["status"] == "open"]
        assert len(active_incidents) == 1
        assert client.post(f"/api/incidents/{active_incidents[0]['id']}/action", json={"action": "acknowledge"}).status_code == 200
        assert any(item["status"] == "acknowledged" for item in client.get("/api/incidents").json())

        # A heartbeat is authoritative: definitions no longer reported by an Agent are removed.
        empty_report = client.post("/api/agent/heartbeat", headers={"Authorization": f"Bearer {token}"}, json={
            "agent_version": "test", "metrics": {"cpu": 2, "ram": 3, "disk": 4}, "tunnels": []
        })
        assert empty_report.status_code == 200
        assert client.get("/api/tunnels").json() == []

        for _ in range(5):
            assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 429

print("DARK NOC smoke test passed")
