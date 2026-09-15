from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="dark-noc-topology-") as data_dir:
    os.environ["DARK_NOC_DATA"] = data_dir
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "TestPassword-1234"
    os.environ["DARK_NOC_PUBLIC_HOST"] = "noc.example.test"
    os.environ["DARK_NOC_PUBLIC_PORT"] = "9090"
    os.environ["DARK_NOC_LOCAL_ENROLL_SECRET"] = "TopologyBootstrapSecret"
    sys.path.insert(0, str(ROOT / "hub"))
    import app

    with TestClient(app.app, base_url="https://testserver") as client:
        enrolled = client.post(
            "/api/agent/local-enroll",
            headers={"X-Dark-Noc-Bootstrap": "TopologyBootstrapSecret"},
        )
        assert enrolled.status_code == 200
        hub_id = enrolled.json()["id"]
        token = enrolled.json()["agent_token"]
        auth = {"Authorization": f"Bearer {token}"}

        pulse = client.post(
            "/api/agent/pulse",
            headers=auth,
            json={
                "agent_version": "2.9.32",
                "agent_loop_ts": app.utc_ts(),
                "telemetry_status": "collecting",
                "telemetry_age_seconds": 0,
            },
        )
        assert pulse.status_code == 200
        with app.db() as conn:
            pulsed = conn.execute("SELECT status,last_seen FROM nodes WHERE id=?", (hub_id,)).fetchone()
            assert pulsed["status"] == "online" and int(pulsed["last_seen"] or 0) > 0

        full_report = {
            "agent_version": "2.9.32",
            "metrics": {
                "cpu": 1, "ram": 2, "swap": 0, "disk": 3, "load1": 0.1,
                "rx_bps": 10, "tx_bps": 20, "uptime": 30, "connections": 4,
                "telemetry_status": "fresh", "inventory_complete": True,
                "inventory_snapshot_at": app.utc_ts(),
            },
            "services": [{"name": "backhaul@hub-local.service", "status": "active"}],
            "plugins": {"dark_backhaul": {"installed": True, "instances": 1}},
            "tunnels": [{
                "name": "hub-local", "method": "DARK Backhaul", "role": "server",
                "target": "local", "service": "backhaul@hub-local.service",
                "listen_port": 3080, "status": "healthy", "latency_ms": None,
                "packet_loss": 0, "sessions": 2, "peer_ips": ["198.51.100.20"],
                "target_host": "127.0.0.1", "target_port": 3080,
                "user_ports": [443], "transport": "tcpmux", "profile": "balanced",
                "restart_every": "off", "checks": {"process": True, "path": True},
            }],
        }
        assert client.post("/api/agent/heartbeat", headers=auth, json=full_report).status_code == 200
        with app.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tunnels WHERE node_id=?", (hub_id,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM node_services WHERE node_id=?", (hub_id,)).fetchone()[0] == 1

        # A liveness-only heartbeat must preserve the last authoritative inventory.
        incomplete = {
            "agent_version": "2.9.32",
            "metrics": {
                "cpu": 5, "ram": 6, "disk": 7, "telemetry_status": "collecting",
                "inventory_complete": False, "inventory_snapshot_at": 0,
            },
            "services": [], "plugins": {}, "tunnels": [],
        }
        assert client.post("/api/agent/heartbeat", headers=auth, json=incomplete).status_code == 200
        with app.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tunnels WHERE node_id=?", (hub_id,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM node_services WHERE node_id=?", (hub_id,)).fetchone()[0] == 1
            plugin_inventory = json.loads(conn.execute("SELECT plugin_inventory FROM nodes WHERE id=?", (hub_id,)).fetchone()[0])
            assert plugin_inventory["dark_backhaul"]["instances"] == 1

        # A complete but stale cached snapshot must not authoritatively delete paths.
        stale = dict(incomplete)
        stale["metrics"] = {**incomplete["metrics"], "telemetry_status": "stale", "inventory_complete": True}
        assert client.post("/api/agent/heartbeat", headers=auth, json=stale).status_code == 200
        with app.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tunnels WHERE node_id=?", (hub_id,)).fetchone()[0] == 1

        login = client.post("/api/auth/login", json={"username": "admin", "password": "TestPassword-1234"})
        assert login.status_code == 200
        visible = client.get("/api/tunnels").json()
        assert len(visible) == 1
        assert visible[0]["node_role"] == "hub"
        assert visible[0]["topology_side"] == "iran"
        assert visible[0]["peer_host"] == "198.51.100.20"

        # A fresh authoritative empty snapshot is the only report allowed to prune.
        fresh_empty = dict(incomplete)
        fresh_empty["metrics"] = {
            **incomplete["metrics"], "telemetry_status": "fresh", "inventory_complete": True,
            "inventory_snapshot_at": app.utc_ts(),
        }
        assert client.post("/api/agent/heartbeat", headers=auth, json=fresh_empty).status_code == 200
        with app.db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM tunnels WHERE node_id=?", (hub_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM node_services WHERE node_id=?", (hub_id,)).fetchone()[0] == 0

        # Packet Pro reverses client/server semantics; node roles must keep the
        # Hub on the IRAN side and the exit node on the KHAREJ side.
        now = app.utc_ts()
        with app.db() as conn:
            exit_id = conn.execute(
                """INSERT INTO nodes(name,region,role,host,ssh_port,ssh_user,status,last_seen,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("TR-EXIT", "Turkey", "exit", "198.51.100.20", 22, "root", "online", now, now, now),
            ).lastrowid
            hub_details = json.dumps({
                "role": "client", "target_host": "198.51.100.20", "target_port": 9797,
                "user_ports": [443], "transport": "kcp", "profile": "balanced",
            })
            exit_details = json.dumps({
                "role": "server", "target_host": "127.0.0.1", "target_port": 9797,
                "user_ports": [], "transport": "kcp", "profile": "balanced",
                "peer_ips": [app.normalize_ip("127.0.0.1")],
            })
            conn.execute(
                """INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,last_check,details)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (hub_id, "packet-route", "DARK Packet Pro", "198.51.100.20:9797", "paqetpro@packet-route.service", 443, "healthy", now, hub_details),
            )
            conn.execute(
                """INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,last_check,details)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (exit_id, "packet-route", "DARK Packet Pro", "local", "paqetpro@packet-route.service", 9797, "healthy", now, exit_details),
            )
        packet_rows = [row for row in client.get("/api/tunnels").json() if row["name"] == "packet-route"]
        assert len(packet_rows) == 2
        by_node = {row["node_id"]: row for row in packet_rows}
        assert by_node[hub_id]["topology_side"] == "iran"
        assert by_node[exit_id]["topology_side"] == "kharej"
        assert by_node[hub_id]["peer_node_id"] == exit_id
        assert by_node[exit_id]["peer_node_id"] == hub_id
        assert by_node[hub_id]["topology_key"] == by_node[exit_id]["topology_key"]

app_js = (ROOT / "hub" / "static" / "app.js").read_text(encoding="utf-8")
styles = (ROOT / "hub" / "static" / "styles.css").read_text(encoding="utf-8")
topology_js = (ROOT / "hub" / "static" / "topology.js").read_text()
assert "topology_side" in topology_js and "pairTotals" in app_js
assert "--topology-canvas-height" in app_js and "--topology-canvas-height" in styles
assert "cyber-route-backbone" in app_js and "cyber-route-flow" in app_js
assert ".cyber-route-backbone" in styles and "@keyframes route-energy-pulse" in styles
assert "route-packet-core" in app_js and "route-packet-halo" in app_js
assert "No matching tunnel path" in app_js
flow_css = styles.split(".cyber-route-flow{", 1)[1].split("}", 1)[0]
backbone_css = styles.split(".cyber-route-backbone{", 1)[1].split("}", 1)[0]
assert "stroke-dasharray" not in flow_css
assert "stroke-dasharray" not in backbone_css
agent_source = (ROOT / "agent" / "agent.py").read_text(encoding="utf-8")
assert "list-unit-files" in agent_source
assert "config.ini" in agent_source and "config.yml" in agent_source

print("Topology and authoritative inventory regression tests passed")
