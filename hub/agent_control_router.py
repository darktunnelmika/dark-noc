import asyncio
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket


def _usable_peer_ips(values) -> list[str]:
    """Keep only routable/non-local peer identities reported by tunnel telemetry."""
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.casefold() == "localhost":
            continue
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            normalized = text
        else:
            if address.is_loopback or address.is_unspecified:
                continue
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            normalized = str(address)
        if normalized not in result:
            result.append(normalized)
        if len(result) >= 32:
            break
    return result


def register_agent_control_router(app, **deps):
    router = APIRouter()
    AgentPulse = deps["AgentPulse"]
    AgentReport = deps["AgentReport"]
    JobResult = deps["JobResult"]
    agent_node = deps["agent_node"]
    db = deps["db"]
    utc_ts = deps["utc_ts"]
    token_hash = deps["token_hash"]
    get_agent_inventory_flags = deps["get_agent_inventory_flags"]
    get_managed_tunnel_report = deps["get_managed_tunnel_report"]
    get_create_incident = deps["get_create_incident"]
    get_resolve_incident = deps["get_resolve_incident"]
    get_finalize_fleet_operation = deps["get_finalize_fleet_operation"]
    get_telegram_notify = deps["get_telegram_notify"]
    get_live_clients = deps["get_live_clients"]

    @router.post("/api/agent/local-enroll")
    def local_agent_enroll(
        request: Request,
        x_dark_noc_bootstrap: str | None = Header(default=None),
        x_dark_noc_existing_agent: str | None = Header(default=None),
    ):
        expected = os.getenv("DARK_NOC_LOCAL_ENROLL_SECRET", "")
        if not expected or not x_dark_noc_bootstrap or not secrets.compare_digest(expected, x_dark_noc_bootstrap):
            raise HTTPException(403, "Local enrollment denied")
        now = utc_ts()
        node_name = f"{socket.gethostname()} (Hub)"[:128]
        node_host = os.getenv("DARK_NOC_PUBLIC_HOST", "").strip() or "127.0.0.1"
        supplied_token = str(x_dark_noc_existing_agent or "").strip()
        with db() as conn:
            row = conn.execute("SELECT id,agent_token_hash FROM nodes WHERE role='hub' ORDER BY id LIMIT 1").fetchone()
            if row and conn.execute("SELECT 1 FROM nodes WHERE name=? AND id<>?", (node_name, row["id"])).fetchone():
                node_name = f"{socket.gethostname()} Hub-{row['id']}"[:128]
            reused = bool(
                row
                and supplied_token
                and row["agent_token_hash"]
                and hmac.compare_digest(str(row["agent_token_hash"]), token_hash(supplied_token))
            )
            if row:
                node_id = row["id"]
                if reused:
                    enrollment = supplied_token
                    conn.execute(
                        "UPDATE nodes SET name=?,region='NOC Hub',role='hub',host=?,updated_at=? WHERE id=?",
                        (node_name, node_host, now, node_id),
                    )
                else:
                    enrollment = secrets.token_urlsafe(36)
                    conn.execute(
                        """UPDATE nodes SET name=?,region='NOC Hub',role='hub',host=?,agent_token_hash=?,
                                  status='pending',agent_version=NULL,observed_ip=NULL,plugin_inventory='{}',
                                  last_seen=NULL,updated_at=? WHERE id=?""",
                        (node_name, node_host, token_hash(enrollment), now, node_id),
                    )
            else:
                enrollment = secrets.token_urlsafe(36)
                if conn.execute("SELECT 1 FROM nodes WHERE name=?", (node_name,)).fetchone():
                    node_name = f"{socket.gethostname()} Hub-{secrets.token_hex(2)}"[:128]
                cursor = conn.execute(
                    """INSERT INTO nodes(name,region,role,host,ssh_port,ssh_user,agent_token_hash,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (node_name, "NOC Hub", "hub", node_host, 22, "root", token_hash(enrollment), "pending", now, now),
                )
                node_id = cursor.lastrowid
                reused = False
        return {"id": node_id, "name": node_name, "agent_token": enrollment, "reused": reused}

    @router.post("/api/agent/pulse")
    async def agent_pulse(report: AgentPulse, request: Request, node: sqlite3.Row = Depends(agent_node)):
        # Refresh liveness without parsing or mutating heavy inventory. A malformed,
        # oversized or temporarily stale report can never make a live Node offline.
        now = utc_ts()
        observed_ip = request.client.host if request.client else None
        if observed_ip in {"127.0.0.1", "::1"} and node["role"] == "hub":
            observed_ip = node["host"]
        with db() as conn:
            conn.execute(
                "UPDATE nodes SET status='online',agent_version=?,observed_ip=?,last_seen=?,updated_at=? WHERE id=?",
                (report.agent_version, observed_ip, now, now, node["id"]),
            )
            recovered = conn.execute(
                "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')",
                (node["id"],),
            ).fetchall()
            for incident in recovered:
                get_resolve_incident()(conn, incident["id"], "Agent liveness pulse recovered")
        stale_clients: list[WebSocket] = []
        for live_socket in list(get_live_clients()):
            try:
                await live_socket.send_json(
                    {"type": "telemetry", "node_id": node["id"], "server_time": now, "pulse": True}
                )
            except Exception:
                stale_clients.append(live_socket)
        for live_socket in stale_clients:
            get_live_clients().discard(live_socket)
        return {"ok": True, "server_time": now}

    @router.post("/api/agent/heartbeat")
    async def agent_heartbeat(report: AgentReport, request: Request, node: sqlite3.Row = Depends(agent_node)):
        now = utc_ts()
        m = report.metrics
        inventory_complete, inventory_fresh = get_agent_inventory_flags()(m)
        notifications: list[str] = []
        with db() as conn:
            autoheal = report.autoheal or {}
            try:
                autoheal_cooldown = min(max(int(autoheal.get("cooldown_seconds", 300)), 60), 86400)
                autoheal_max = min(max(int(autoheal.get("max_restarts_per_hour", 3)), 1), 12)
            except (TypeError, ValueError):
                autoheal_cooldown, autoheal_max = 300, 3
            observed_ip = request.client.host if request.client else None
            if observed_ip in {"127.0.0.1", "::1"} and node["role"] == "hub":
                observed_ip = node["host"]
            conn.execute(
                """UPDATE nodes SET status='online',agent_version=?,observed_ip=?,
                          plugin_inventory=CASE WHEN ? THEN ? ELSE plugin_inventory END,
                          autoheal_enabled=?,autoheal_cooldown=?,autoheal_max_restarts=?,
                          last_seen=?,updated_at=? WHERE id=?""",
                (
                    report.agent_version, observed_ip, 1 if inventory_complete else 0,
                    json.dumps(report.plugins), 1 if autoheal.get("enabled") else 0,
                    autoheal_cooldown, autoheal_max, now, now, node["id"],
                ),
            )
            conn.execute("INSERT INTO metrics(node_id,ts,cpu,ram,swap,disk,load1,rx_bps,tx_bps,uptime,connections,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (node["id"], now, m.get("cpu"), m.get("ram"), m.get("swap"), m.get("disk"), m.get("load1"), m.get("rx_bps"), m.get("tx_bps"), m.get("uptime"), m.get("connections"), json.dumps(m)))
            recovered_node_incidents = conn.execute(
                "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')",
                (node["id"],),
            ).fetchall()
            for incident in recovered_node_incidents:
                get_resolve_incident()(conn, incident["id"], "Agent heartbeat recovered")
            inventory = m.get("inventory") if isinstance(m.get("inventory"), dict) else {}
            docker = inventory.get("docker") if isinstance(inventory.get("docker"), dict) else {}
            resource_limits = {
                "CPU": (m.get("cpu"), 90.0), "RAM": (m.get("ram"), 90.0),
                "DISK": (m.get("disk"), 90.0), "INODE": (m.get("inode_percent"), 90.0),
                "LOAD": (m.get("load1"), 8.0), "TEMPERATURE": (m.get("temperature_c"), 85.0),
                "NETWORK ERRORS": (m.get("network_errors_delta"), 10.0),
                "NETWORK DROPS": (m.get("network_drops_delta"), 100.0),
                "DOCKER UNHEALTHY": (docker.get("unhealthy"), 1.0),
            }
            for resource_name, (raw_value, limit) in resource_limits.items():
                if raw_value is None:
                    continue
                value = float(raw_value)
                title = f"Node {node['name']} {resource_name} pressure"
                active_resource = conn.execute("SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged') LIMIT 1", (node["id"], title)).fetchone()
                if value >= limit and not active_resource:
                    get_create_incident()(
                        conn, node_id=node["id"], tunnel_id=None, severity="warning", title=title,
                        detail={"resource": resource_name.lower(), "value": value, "threshold": limit}, opened_at=now,
                    )
                    notifications.append(f"🟠 DARK NOC RESOURCE ALERT\nNode: {node['name']}\n{resource_name}: {value:.1f}\nThreshold: {limit:.1f}")
                elif value < limit * 0.9 and active_resource:
                    get_resolve_incident()(conn, active_resource["id"], f"{resource_name} returned below the recovery threshold")
            active_services = []
            for service in report.services:
                service_name = str(service.get("name", ""))[:128]
                if not service_name:
                    continue
                active_services.append(service_name)
                service_status = str(service.get("status", "unknown"))[:32]
                conn.execute("INSERT INTO node_services(node_id,name,status,last_check) VALUES(?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET status=excluded.status,last_check=excluded.last_check", (node["id"], service_name, service_status, now))
            if inventory_complete:
                if active_services:
                    conn.execute(f"DELETE FROM node_services WHERE node_id=? AND name NOT IN ({','.join('?' for _ in active_services)})", (node["id"], *active_services))
                else:
                    conn.execute("DELETE FROM node_services WHERE node_id=?", (node["id"],))
            for tunnel in report.tunnels:
                method, service = str(tunnel.get("method", "")), str(tunnel.get("service", ""))
                if not get_managed_tunnel_report()(method, service):
                    continue
                name = str(tunnel.get("name", "unnamed"))[:128]
                old = conn.execute("SELECT * FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
                stored_tunnel = dict(tunnel)
                live_peer_ips = _usable_peer_ips(stored_tunnel.get("peer_ips"))
                previous_details = {}
                if old:
                    try:
                        previous_details = json.loads(old["details"] or "{}")
                    except (TypeError, ValueError):
                        previous_details = {}
                remembered_peer_ips = (
                    _usable_peer_ips(previous_details.get("last_known_peer_ips"))
                    or _usable_peer_ips(previous_details.get("peer_ips"))
                )
                stored_tunnel["peer_ips"] = live_peer_ips
                stored_tunnel["last_known_peer_ips"] = live_peer_ips or remembered_peer_ips
                conn.execute("INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,last_check,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET method=excluded.method,target=excluded.target,service=excluded.service,listen_port=excluded.listen_port,status=excluded.status,latency_ms=excluded.latency_ms,packet_loss=excluded.packet_loss,sessions=excluded.sessions,rx_bps=excluded.rx_bps,tx_bps=excluded.tx_bps,last_check=excluded.last_check,details=excluded.details", (node["id"], name, tunnel.get("method"), tunnel.get("target"), tunnel.get("service"), tunnel.get("listen_port"), tunnel.get("status"), tunnel.get("latency_ms"), tunnel.get("packet_loss"), tunnel.get("sessions"), tunnel.get("rx_bps"), tunnel.get("tx_bps"), now, json.dumps(stored_tunnel)))
                current = str(tunnel.get("status", "unknown"))
                previous = old["status"] if old else None
                tunnel_row = conn.execute("SELECT id FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
                checks = tunnel.get("checks") if isinstance(tunnel.get("checks"), dict) else {}
                conn.execute(
                    """INSERT INTO tunnel_samples(
                           tunnel_id,ts,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,
                           service_uptime,process_ok,path_ok
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tunnel_row["id"], now, current, tunnel.get("latency_ms"),
                        tunnel.get("packet_loss"), tunnel.get("sessions"), tunnel.get("rx_bps"),
                        tunnel.get("tx_bps"), tunnel.get("service_uptime"),
                        1 if checks.get("process") else 0, 1 if checks.get("path") else 0,
                    ),
                )
                failure_streak = (int(old["failure_streak"] or 0) + 1) if old and current in {"down", "degraded"} else (1 if current in {"down", "degraded"} else 0)
                conn.execute("UPDATE tunnels SET failure_streak=? WHERE id=?", (failure_streak, tunnel_row["id"]))
                open_incident = conn.execute("SELECT id FROM incidents WHERE tunnel_id=? AND status IN ('open','acknowledged') LIMIT 1", (tunnel_row["id"],)).fetchone()
                threshold = 2 if current == "down" else 3
                if current in {"down", "degraded"} and failure_streak >= threshold and not open_incident:
                    severity = "critical" if current == "down" else "warning"
                    get_create_incident()(
                        conn, node_id=node["id"], tunnel_id=tunnel_row["id"], severity=severity,
                        title=f"Tunnel {name} is {current}", detail=tunnel, opened_at=now,
                    )
                    notifications.append(f"🔴 DARK NOC INCIDENT\nNode: {node['name']}\nTunnel: {name}\nStatus: {current.upper()}\nLatency: {tunnel.get('latency_ms', '—')} ms")
                elif current == "healthy" and open_incident:
                    get_resolve_incident()(conn, open_incident["id"], "Tunnel telemetry returned to healthy")
                    notifications.append(f"🟢 DARK NOC RECOVERED\nNode: {node['name']}\nTunnel: {name}\nStatus: HEALTHY")
            if inventory_fresh:
                active_names = [
                    str(tunnel.get("name", "unnamed"))[:128]
                    for tunnel in report.tunnels
                    if get_managed_tunnel_report()(str(tunnel.get("method", "")), str(tunnel.get("service", "")))
                ]
                stale_rows = conn.execute(
                    "SELECT id FROM tunnels WHERE node_id=?"
                    + (f" AND name NOT IN ({','.join('?' for _ in active_names)})" if active_names else ""),
                    (node["id"], *active_names),
                ).fetchall()
                if stale_rows:
                    stale_ids = [row["id"] for row in stale_rows]
                    stale_incidents = conn.execute(
                        f"SELECT id FROM incidents WHERE tunnel_id IN ({','.join('?' for _ in stale_ids)}) AND status IN ('open','acknowledged')",
                        stale_ids,
                    ).fetchall()
                    for incident in stale_incidents:
                        get_resolve_incident()(conn, incident["id"], "Tunnel was removed from the active Agent inventory")
                    conn.executemany("DELETE FROM tunnels WHERE id=?", [(tunnel_id,) for tunnel_id in stale_ids])
        for message in notifications:
            await asyncio.to_thread(get_telegram_notify(), message)
        stale_clients: list[WebSocket] = []
        for live_socket in list(get_live_clients()):
            try:
                await live_socket.send_json({"type": "telemetry", "node_id": node["id"], "server_time": now})
            except Exception:
                stale_clients.append(live_socket)
        for live_socket in stale_clients:
            get_live_clients().discard(live_socket)
        return {"ok": True, "server_time": now}

    @router.get("/api/agent/jobs")
    def agent_jobs(node: sqlite3.Row = Depends(agent_node)):
        with db() as conn:
            row = conn.execute("""UPDATE jobs SET status='running',started_at=?
              WHERE id=(SELECT id FROM jobs WHERE node_id=? AND status='queued' ORDER BY id LIMIT 1)
                AND status='queued' RETURNING id,kind,payload,created_at""", (utc_ts(), node["id"])).fetchone()
        return [dict(row)] if row else []

    @router.post("/api/agent/jobs/result")
    def agent_job_result(result: JobResult, node: sqlite3.Row = Depends(agent_node)):
        notification: str | None = None
        with db() as conn:
            row = conn.execute("SELECT id,status,kind,payload FROM jobs WHERE id=? AND node_id=?", (result.job_id, node["id"])).fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            if row["status"] in {"completed", "failed"}:
                existing = conn.execute("SELECT status,output FROM jobs WHERE id=?", (result.job_id,)).fetchone()
                if existing["status"] == result.status and existing["output"] == result.output:
                    return {"ok": True, "idempotent": True}
                raise HTTPException(409, "Job already has a different terminal result")
            if row["status"] != "running":
                raise HTTPException(409, "Only a running job can accept a result")
            conn.execute("UPDATE jobs SET status=?,output=?,finished_at=? WHERE id=?", (result.status, result.output, utc_ts(), result.job_id))
            if row["kind"] == "certificate_issue":
                cert_id = int(json.loads(row["payload"]).get("certificate_id", 0))
                if result.status == "completed":
                    try:
                        cert = json.loads(result.output)
                        conn.execute("UPDATE certificates SET status='valid',issuer=?,cert_path=?,key_path=?,expires_at=?,last_error=NULL,updated_at=? WHERE id=? AND node_id=?", (cert.get("issuer", "letsencrypt"), cert["cert_path"], cert["key_path"], int(cert["expires_at"]), utc_ts(), cert_id, node["id"]))
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                        conn.execute("UPDATE certificates SET status='failed',last_error=?,updated_at=? WHERE id=?", ("Agent returned invalid certificate metadata", utc_ts(), cert_id))
                else:
                    conn.execute("UPDATE certificates SET status='failed',last_error=?,updated_at=? WHERE id=?", (result.output[-2000:], utc_ts(), cert_id))
            if row["kind"] == "monitor_run":
                request_payload = json.loads(row["payload"] or "{}")
                monitor_id = int(request_payload.get("monitor_id", 0) or 0)
                monitor = conn.execute("SELECT * FROM monitors WHERE id=? AND node_id=?", (monitor_id, node["id"])).fetchone()
                if monitor:
                    checked_at = utc_ts()
                    try:
                        report = json.loads(result.output)
                        monitor_status = "up" if result.status == "completed" and report.get("status") == "up" else "down"
                        latency = float(report["latency_ms"]) if report.get("latency_ms") is not None else None
                        detail_value = report.get("detail") if isinstance(report.get("detail"), dict) else {"message": str(report.get("detail") or "")}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        monitor_status, latency = "down", None
                        detail_value = {"error": result.output[-2000:] or "Agent monitor execution failed"}
                    failure_streak = 0 if monitor_status == "up" else int(monitor["failure_streak"] or 0) + 1
                    detail_json = json.dumps(detail_value)[:8000]
                    conn.execute(
                        """UPDATE monitors SET status=?,latency_ms=?,detail=?,failure_streak=?,last_run_at=?,updated_at=?
                           WHERE id=?""",
                        (monitor_status, latency, detail_json, failure_streak, checked_at, utc_ts(), monitor_id),
                    )
                    conn.execute(
                        "INSERT INTO monitor_results(monitor_id,ts,status,latency_ms,detail) VALUES(?,?,?,?,?)",
                        (monitor_id, checked_at, monitor_status, latency, detail_json),
                    )
                    title = f"Monitor {monitor['name']} is down"
                    open_monitor_incident = conn.execute(
                        "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged') LIMIT 1",
                        (node["id"], title),
                    ).fetchone()
                    if monitor_status == "down" and failure_streak >= 2 and not open_monitor_incident:
                        get_create_incident()(
                            conn, node_id=node["id"], tunnel_id=None, severity="critical", title=title,
                            detail={"monitor_id": monitor_id, "kind": monitor["kind"], "target": monitor["target"], **detail_value},
                            opened_at=checked_at,
                        )
                        notification = f"🔴 DARK NOC MONITOR\nNode: {node['name']}\nMonitor: {monitor['name']}\nTarget: {monitor['target']}\nStatus: DOWN"
                    elif monitor_status == "up" and open_monitor_incident:
                        get_resolve_incident()(conn, open_monitor_incident["id"], "Synthetic monitor recovered")
                        notification = f"🟢 DARK NOC MONITOR RECOVERED\nNode: {node['name']}\nMonitor: {monitor['name']}\nStatus: UP"
            fleet_item = conn.execute(
                "SELECT * FROM fleet_operation_items WHERE job_id=?",
                (result.job_id,),
            ).fetchone()
            if fleet_item:
                conn.execute("UPDATE fleet_operation_items SET status=? WHERE id=?", (result.status, fleet_item["id"]))
                get_finalize_fleet_operation()(conn, fleet_item["operation_id"])
            deployment = conn.execute("SELECT * FROM plugin_deployments WHERE iran_job_id=? OR kharej_job_id=?", (result.job_id, result.job_id)).fetchone()
            if deployment:
                iran_status = conn.execute("SELECT status FROM jobs WHERE id=?", (deployment["iran_job_id"],)).fetchone()["status"]
                kharej_status = conn.execute("SELECT status FROM jobs WHERE id=?", (deployment["kharej_job_id"],)).fetchone()["status"]
                statuses = {iran_status, kharej_status}
                if deployment["lifecycle"] == "active" and statuses == {"completed", "failed"}:
                    side = "iran" if iran_status == "completed" else "kharej"
                    node_id = deployment[f"{side}_node_id"]
                    rollback_job = conn.execute(
                        "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                        (node_id, "plugin_remove", json.dumps({"plugin_id": deployment["plugin_id"], "name": deployment["name"]}), deployment["created_by"], utc_ts()),
                    ).lastrowid
                    conn.execute(f"UPDATE plugin_deployments SET {side}_job_id=?,lifecycle='rolling_back' WHERE id=?", (rollback_job, deployment["id"]))
                elif deployment["lifecycle"] == "rolling_back" and result.status == "completed":
                    conn.execute("UPDATE plugin_deployments SET lifecycle='rolled_back' WHERE id=?", (deployment["id"],))
                elif deployment["lifecycle"] == "rolling_back" and result.status == "failed":
                    conn.execute("UPDATE plugin_deployments SET lifecycle='rollback_failed' WHERE id=?", (deployment["id"],))
        if notification:
            get_telegram_notify()(notification)
        return {"ok": True}

    @router.post("/api/agent/jobs/{job_id}/lease")
    def renew_agent_job_lease(job_id: int, node: sqlite3.Row = Depends(agent_node)):
        with db() as conn:
            changed = conn.execute("UPDATE jobs SET started_at=? WHERE id=? AND node_id=? AND status='running'", (utc_ts(), job_id, node["id"])).rowcount
        if changed != 1:
            raise HTTPException(409, "Job is no longer running on this node")
        return {"ok": True}

    handlers = {
        "local_agent_enroll": local_agent_enroll,
        "agent_pulse": agent_pulse,
        "agent_heartbeat": agent_heartbeat,
        "agent_jobs": agent_jobs,
        "agent_job_result": agent_job_result,
        "renew_agent_job_lease": renew_agent_job_lease
    }
    app.include_router(router)
    return router, handlers
