import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def load_hub() -> Any:
    data_dir = tempfile.TemporaryDirectory(prefix="dark-noc-features-")
    os.environ["DARK_NOC_DATA"] = data_dir.name
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "Feature-TestPassword-1234"
    module_name = "dark_noc_feature_app"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "hub" / "app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module._feature_test_data_dir = data_dir
    return module


hub = load_hub()
enrollment_tokens: dict[int, str] = {}


async def fake_provision_node(node_id: int, enrollment: str) -> None:
    enrollment_tokens[node_id] = enrollment
    with hub.db() as conn:
        conn.execute(
            "UPDATE nodes SET provision_status='completed',updated_at=? WHERE id=?",
            (hub.utc_ts(), node_id),
        )


hub.provision_node = fake_provision_node


with TestClient(hub.app, base_url="https://testserver") as client:
    assert client.get("/readyz").status_code == 200
    assert client.get("/api/system/status").status_code == 401
    assert client.post("/api/auth/login", json={
        "username": "admin", "password": "Feature-TestPassword-1234"
    }).status_code == 200

    node = client.post("/api/nodes", json={
        "name": "IR-MON-01", "region": "Iran Edge", "role": "edge",
        "host": "127.0.0.1", "ssh_port": 22, "ssh_user": "root",
        "ssh_password": "root-test-password",
    })
    assert node.status_code == 202
    node_id = node.json()["id"]
    token = enrollment_tokens[node_id]
    auth = {"Authorization": f"Bearer {token}"}

    heartbeat = client.post("/api/agent/heartbeat", headers=auth, json={
        "agent_version": "feature-test",
        "metrics": {
            "cpu": 21, "ram": 32, "swap": 0, "disk": 41, "load1": 0.4,
            "rx_bps": 1200, "tx_bps": 800, "uptime": 4000, "connections": 12,
            "inode_percent": 14, "temperature_c": 51,
            "disk_read_bps": 400, "disk_write_bps": 600,
            "network_errors_delta": 0, "network_drops_delta": 0,
            "interfaces": {"eth0": {"rx_bytes": 100, "tx_bytes": 200}},
            "inventory": {
                "os": "Ubuntu Test", "kernel": "6.8-test", "updates": 3,
                "reboot_required": False,
                "docker": {"available": True, "running": 2, "total": 2, "unhealthy": 0},
            },
        },
        "services": [], "tunnels": [],
    })
    assert heartbeat.status_code == 200
    public_node = client.get("/api/nodes").json()[0]
    assert public_node["metric"]["detail"]["inventory"]["os"] == "Ubuntu Test"
    assert public_node["metric"]["detail"]["inode_percent"] == 14

    raw_metrics = client.get(f"/api/nodes/{node_id}/metrics?hours=1&resolution=raw")
    assert raw_metrics.status_code == 200
    assert raw_metrics.json()[0]["resolution"] == "raw"
    hourly_metrics = client.get(f"/api/nodes/{node_id}/metrics?hours=72")
    assert hourly_metrics.status_code == 200
    assert hourly_metrics.json()[0]["resolution"] == "hour"
    assert client.get("/api/nodes/999/metrics").status_code == 404
    assert client.get(f"/api/nodes/{node_id}/metrics?resolution=bad").status_code == 422

    monitor = client.post("/api/monitors", json={
        "name": "TCP edge test", "node_id": node_id, "kind": "tcp",
        "target": "127.0.0.1", "port": 443, "interval_seconds": 60,
        "timeout_seconds": 5, "enabled": True,
    })
    assert monitor.status_code == 201
    monitor_id = monitor.json()["id"]
    assert client.post("/api/monitors", json={
        "name": "Invalid TCP", "node_id": node_id, "kind": "tcp",
        "target": "127.0.0.1", "interval_seconds": 60, "timeout_seconds": 5,
    }).status_code == 422

    for streak in (1, 2):
        queued = client.post(f"/api/monitors/{monitor_id}/run")
        assert queued.status_code == 202
        jobs = client.get("/api/agent/jobs", headers=auth).json()
        assert len(jobs) == 1 and jobs[0]["kind"] == "monitor_run"
        payload = json.loads(jobs[0]["payload"])
        assert payload["monitor_id"] == monitor_id and "snmp_community" not in payload
        result = client.post("/api/agent/jobs/result", headers=auth, json={
            "job_id": jobs[0]["id"], "status": "completed",
            "output": json.dumps({
                "monitor_id": monitor_id, "status": "down", "latency_ms": None,
                "detail": {"error": "connection refused"}, "checked_at": hub.utc_ts() + streak,
            }),
        })
        assert result.status_code == 200

    monitor_row = client.get("/api/monitors").json()[0]
    assert monitor_row["status"] == "down" and monitor_row["failure_streak"] == 2
    assert monitor_row["detail"]["error"] == "connection refused"
    results = client.get(f"/api/monitors/{monitor_id}/results").json()
    assert len(results) == 2 and results[0]["status"] == "down"

    incident = next(item for item in client.get("/api/incidents").json() if item["title"] == "Monitor TCP edge test is down")
    incident_id = incident["id"]
    assert incident["event_count"] == 1
    assert client.post(f"/api/incidents/{incident_id}/notes", json={
        "message": "Confirmed the upstream port is closed", "event_type": "diagnostic"
    }).status_code == 201
    assert client.post(f"/api/incidents/{incident_id}/action", json={
        "action": "acknowledge", "note": "NOC engineer investigating", "root_cause": "Upstream service stopped"
    }).status_code == 200
    assert client.post(f"/api/incidents/{incident_id}/action", json={
        "action": "resolve", "note": "Service restored", "resolution": "Restarted the upstream service"
    }).status_code == 200
    incident_detail = client.get(f"/api/incidents/{incident_id}").json()
    assert incident_detail["status"] == "resolved"
    assert incident_detail["root_cause"] == "Upstream service stopped"
    assert incident_detail["resolution"] == "Restarted the upstream service"
    event_types = [event["event_type"] for event in incident_detail["events"]]
    assert event_types == ["detected", "diagnostic", "acknowledged", "resolved"], event_types
    assert client.post(f"/api/incidents/{incident_id}/action", json={"action": "reopen"}).status_code == 200

    snmp = client.post("/api/monitors", json={
        "name": "SNMP uptime", "node_id": node_id, "kind": "snmp",
        "target": "127.0.0.1", "port": 161, "interval_seconds": 300,
        "timeout_seconds": 5, "snmp_community": "private-community",
        "snmp_oid": ".1.3.6.1.2.1.1.3.0", "enabled": False,
    })
    assert snmp.status_code == 201
    serialized_monitors = json.dumps(client.get("/api/monitors").json())
    assert "private-community" not in serialized_monitors and "secret_enc" not in serialized_monitors
    assert client.put(f"/api/monitors/{snmp.json()['id']}", json={
        "name": "SNMP uptime", "node_id": node_id, "kind": "snmp",
        "target": "127.0.0.1", "port": 161, "interval_seconds": 600,
        "timeout_seconds": 5, "snmp_oid": ".1.3.6.1.2.1.1.3.0", "enabled": False,
    }).status_code == 200
    assert client.delete(f"/api/monitors/{snmp.json()['id']}").status_code == 200

    fleet = client.post("/api/fleet/operations", json={
        "name": "Fleet diagnostic sweep", "node_ids": [node_id],
        "kind": "diagnostics", "payload": {},
    })
    assert fleet.status_code == 202
    fleet_job = client.get("/api/agent/jobs", headers=auth).json()[0]
    assert fleet_job["kind"] == "diagnostics"
    assert client.post("/api/agent/jobs/result", headers=auth, json={
        "job_id": fleet_job["id"], "status": "completed", "output": "fleet ok"
    }).status_code == 200
    fleet_result = client.get("/api/fleet/operations").json()[0]
    assert fleet_result["status"] == "completed"
    assert fleet_result["items"][0]["output"] == "fleet ok"

    future = hub.utc_ts() + 3600
    scheduled = client.post("/api/fleet/operations", json={
        "name": "Scheduled logs", "node_ids": [node_id], "kind": "logs",
        "payload": {}, "scheduled_at": future,
    })
    assert scheduled.status_code == 202 and scheduled.json()["status"] == "scheduled"
    assert client.delete(f"/api/fleet/operations/{scheduled.json()['id']}").status_code == 200

    status = client.get("/api/system/status")
    assert status.status_code == 200
    assert status.json()["counts"]["monitors"] == 1
    assert status.json()["retention"]["rollups_days"] >= 30

    with hub.db() as conn:
        now = hub.utc_ts()
        conn.execute(
            "INSERT INTO hub_leases(name,holder,expires_at,updated_at) VALUES(?,?,?,?)",
            ("foreign-test", "another-hub", now + 120, now),
        )
    assert hub.acquire_hub_lease("foreign-test") is False
    assert hub.acquire_hub_lease("owned-test") is True

    assert client.delete(f"/api/monitors/{monitor_id}").status_code == 200


print("DARK NOC NOC feature integration tests passed")
