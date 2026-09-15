import json
import secrets
import sqlite3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request


def register_nodes_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    NodeBody = deps["NodeBody"]
    NodeUpdateBody = deps["NodeUpdateBody"]
    JobBody = deps["JobBody"]
    fetch_node_inventory = deps["fetch_node_inventory"]
    public_node = deps["public_node"]
    utc_ts = deps["utc_ts"]
    create_node_mutation = deps["create_node_mutation"]
    encrypt = deps["encrypt"]
    token_hash = deps["token_hash"]
    node_endpoint_conflict = deps["node_endpoint_conflict"]
    NodeTunnelServiceError = deps["NodeTunnelServiceError"]
    get_provision_node = deps["get_provision_node"]
    audit = deps["audit"]
    LOGGER = deps["LOGGER"]
    prepare_node_provision_mutation = deps["prepare_node_provision_mutation"]
    delete_node_mutation = deps["delete_node_mutation"]
    update_node_mutation = deps["update_node_mutation"]
    reset_node_fingerprint_mutation = deps["reset_node_fingerprint_mutation"]
    queue_plugin_install_mutation = deps["queue_plugin_install_mutation"]
    PLUGIN_CATALOG = deps["PLUGIN_CATALOG"]
    NODE_STALE_AFTER = deps["node_stale_after"]
    METRIC_ROLLUP_RETENTION_DAYS = deps["metric_rollup_retention_days"]

    @router.get("/api/nodes")
    def list_nodes(_: sqlite3.Row = Depends(current_user)):
        records = fetch_node_inventory(db, utc_ts() - NODE_STALE_AFTER)
        result = []
        for row, metric, services in records:
            item = public_node(row, metric)
            try:
                item["plugins"] = json.loads(row["plugin_inventory"] or "{}")
            except (TypeError, ValueError):
                item["plugins"] = {}
            item["services"] = [dict(service) for service in services]
            if not row["last_seen"] or row["last_seen"] < utc_ts() - NODE_STALE_AFTER:
                item["status"] = "pending" if not row["last_seen"] else "offline"
            result.append(item)
        return result

    @router.post("/api/nodes", status_code=202)
    def create_node(body: NodeBody, background: BackgroundTasks, request: Request, user: sqlite3.Row = Depends(current_user)):
        enrollment = secrets.token_urlsafe(36)
        try:
            node_id = create_node_mutation(
                db, body, enrollment, utc_ts(), encrypt=encrypt, token_hash=token_hash,
                endpoint_conflict=node_endpoint_conflict,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        background.add_task(get_provision_node(), node_id, enrollment)
        try:
            audit(user["id"], "node_create", body.name, f"Node {body.host} registered", request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record Node creation audit event")
        return {"id": node_id, "name": body.name, "provisioning": True, "message": "Secure SSH installation started"}

    @router.post("/api/nodes/{node_id}/provision", status_code=202)
    def retry_node_provision(node_id: int, background: BackgroundTasks, request: Request, user: sqlite3.Row = Depends(current_user)):
        enrollment = secrets.token_urlsafe(36)
        try:
            node = prepare_node_provision_mutation(
                db, node_id, utc_ts(), endpoint_conflict=node_endpoint_conflict,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        background.add_task(get_provision_node(), node_id, enrollment)
        try:
            audit(user["id"], "node_provision_retry", node["name"], "Automatic Agent installation retried", request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record Agent synchronization audit event")
        return {"ok": True, "provisioning": True}

    @router.delete("/api/nodes/{node_id}")
    def delete_node(node_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row = delete_node_mutation(db, node_id)
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "node_delete", row["name"], "Node and telemetry removed", request.client.host if request.client else None)
        return {"ok": True}

    @router.put("/api/nodes/{node_id}")
    def update_node(node_id: int, body: NodeUpdateBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            reset_pin = update_node_mutation(
                db, node_id, body, utc_ts(), encrypt=encrypt, endpoint_conflict=node_endpoint_conflict,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "node_update", body.name, "Node connection settings updated", request.client.host if request.client else None)
        return {"ok": True, "ssh_pin_reset": reset_pin}

    @router.delete("/api/nodes/{node_id}/ssh-fingerprint")
    def reset_ssh_fingerprint(node_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row = reset_node_fingerprint_mutation(db, node_id, utc_ts())
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "ssh_pin_reset", row["name"], "SSH host fingerprint reset", request.client.host if request.client else None)
        return {"ok": True}

    @router.get("/api/nodes/{node_id}/metrics")
    def metrics(node_id: int, hours: int = 24, resolution: str = "auto", _: sqlite3.Row = Depends(current_user)):
        hours = min(max(hours, 1), METRIC_ROLLUP_RETENTION_DAYS * 24)
        if resolution not in {"auto", "raw", "hour"}:
            raise HTTPException(422, "resolution must be auto, raw or hour")
        use_hourly = resolution == "hour" or (resolution == "auto" and hours > 48)
        cutoff = utc_ts() - hours * 3600
        with db() as conn:
            if not conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
                raise HTTPException(404, "Node not found")
            if not use_hourly:
                rows = conn.execute(
                    "SELECT ts,cpu,ram,swap,disk,load1,rx_bps,tx_bps,uptime,connections FROM metrics WHERE node_id=? AND ts>? ORDER BY ts LIMIT 20000",
                    (node_id, cutoff),
                ).fetchall()
                return [{**dict(row), "resolution": "raw"} for row in rows]
            current_bucket = (utc_ts() // 3600) * 3600
            rolled = conn.execute(
                """SELECT bucket ts,cpu_avg cpu,ram_avg ram,NULL swap,disk_max disk,load_avg load1,
                          rx_avg rx_bps,tx_avg tx_bps,NULL uptime,connections_max connections,samples
                   FROM metric_rollups WHERE node_id=? AND bucket>=? AND bucket<? ORDER BY bucket""",
                (node_id, (cutoff // 3600) * 3600, current_bucket),
            ).fetchall()
            raw_hourly = conn.execute(
                """SELECT (ts/3600)*3600 ts,AVG(cpu) cpu,AVG(ram) ram,AVG(swap) swap,MAX(disk) disk,AVG(load1) load1,
                          AVG(rx_bps) rx_bps,AVG(tx_bps) tx_bps,MAX(uptime) uptime,MAX(connections) connections,
                          COUNT(*) samples
                   FROM metrics WHERE node_id=? AND ts>=? GROUP BY (ts/3600)*3600 ORDER BY ts""",
                (node_id, cutoff),
            ).fetchall()
        rollup_buckets = {int(row["ts"]) for row in rolled}
        combined = [*rolled, *(row for row in raw_hourly if int(row["ts"]) not in rollup_buckets)]
        combined.sort(key=lambda row: int(row["ts"]))
        return [{**dict(row), "resolution": "hour"} for row in combined]

    @router.post("/api/nodes/{node_id}/plugins/{plugin_id}/install", status_code=202)
    def install_plugin(node_id: int, plugin_id: str, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            node, job_id = queue_plugin_install_mutation(
                db, node_id, plugin_id, user["id"], plugin_catalog=PLUGIN_CATALOG,
                utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_install", node["name"], plugin_id, request.client.host if request.client else None)
        return {"job_id": job_id, "status": "queued"}

    @router.post("/api/nodes/{node_id}/jobs", status_code=202)
    def create_job(node_id: int, body: JobBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        payload = dict(body.payload)
        if body.service:
            payload["service"] = body.service
        with db() as conn:
            node = conn.execute("SELECT name FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not node:
                raise HTTPException(404, "Node not found")
            cursor = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)", (node_id, body.kind, json.dumps(payload), user["id"], utc_ts()))
        audit(user["id"], "job_create", node["name"], body.kind, request.client.host if request.client else None)
        return {"job_id": cursor.lastrowid, "status": "queued"}

    app.include_router(router)
    return router
