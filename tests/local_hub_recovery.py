from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory(prefix="dark-noc-local-hub-") as data_dir:
    os.environ["DARK_NOC_DATA"] = data_dir
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "TestPassword-1234"
    os.environ["DARK_NOC_PUBLIC_HOST"] = "noc.example.test"
    os.environ["DARK_NOC_PUBLIC_PORT"] = "9090"
    os.environ["DARK_NOC_LOCAL_ENROLL_SECRET"] = "local-bootstrap-secret"
    hub_dir = Path(__file__).resolve().parents[1] / "hub"
    sys.path.insert(0, str(hub_dir))
    import app

    headers = {"X-Dark-Noc-Bootstrap": "local-bootstrap-secret"}
    with TestClient(app.app, base_url="https://testserver") as client:
        first = client.post("/api/agent/local-enroll", headers=headers)
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["reused"] is False
        node_id = first_payload["id"]
        token = first_payload["agent_token"]
        with app.db() as conn:
            row = conn.execute("SELECT status,last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
        assert row["status"] == "pending" and row["last_seen"] is None

        heartbeat = client.post(
            "/api/agent/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "agent_version": "2.9.26",
                "metrics": {"cpu": 1, "ram": 2, "swap": 0, "disk": 3, "load1": 0.1},
                "services": [], "tunnels": [], "plugins": {}, "autoheal": {},
            },
        )
        assert heartbeat.status_code == 200
        with app.db() as conn:
            before = conn.execute(
                "SELECT status,last_seen,agent_version,agent_token_hash FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
        assert before["status"] == "online" and before["agent_version"] == "2.9.26" and before["last_seen"]

        reused = client.post(
            "/api/agent/local-enroll",
            headers={**headers, "X-Dark-Noc-Existing-Agent": token},
        )
        assert reused.status_code == 200
        assert reused.json()["id"] == node_id
        assert reused.json()["agent_token"] == token
        assert reused.json()["reused"] is True
        with app.db() as conn:
            after = conn.execute(
                "SELECT status,last_seen,agent_version,agent_token_hash FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
        assert dict(after) == dict(before)

        rotated = client.post(
            "/api/agent/local-enroll",
            headers={**headers, "X-Dark-Noc-Existing-Agent": "wrong-local-token"},
        )
        assert rotated.status_code == 200
        assert rotated.json()["id"] == node_id
        assert rotated.json()["agent_token"] != token
        assert rotated.json()["reused"] is False
        with app.db() as conn:
            pending = conn.execute(
                "SELECT status,last_seen,agent_version,plugin_inventory FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
        assert pending["status"] == "pending"
        assert pending["last_seen"] is None and pending["agent_version"] is None
        assert json.loads(pending["plugin_inventory"]) == {}

print("Local Hub enrollment recovery tests passed")
