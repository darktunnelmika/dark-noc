import sqlite3
from fastapi import APIRouter, Depends


def register_dashboard_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    utc_ts = deps["utc_ts"]
    NODE_STALE_AFTER = deps["node_stale_after"]
    VERSION = deps["version"]
    SSH_UPLOAD_LIMIT = deps["ssh_upload_limit"]
    SSH_RELAY_LIMIT = deps["ssh_relay_limit"]
    SSH_UPLOAD_QUEUE_TIMEOUT = deps["ssh_upload_queue_timeout"]

    @router.get("/api/dashboard")
    def dashboard(_: sqlite3.Row = Depends(current_user)):
        cutoff = utc_ts() - NODE_STALE_AFTER
        with db() as conn:
            nodes = conn.execute("SELECT CASE WHEN last_seen>=? THEN 'online' WHEN last_seen IS NULL THEN 'pending' ELSE 'offline' END status,COUNT(*) count FROM nodes GROUP BY 1", (cutoff,)).fetchall()
            tunnel_rows = conn.execute("SELECT CASE WHEN nodes.last_seen>=? THEN tunnels.status ELSE 'stale' END status,COUNT(*) count FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id GROUP BY 1", (cutoff,)).fetchall()
            totals = conn.execute("SELECT COALESCE(SUM(rx_bps),0) rx,COALESCE(SUM(tx_bps),0) tx,COALESCE(SUM(connections),0) connections FROM (SELECT m.* FROM metrics m JOIN (SELECT node_id,MAX(ts) ts FROM metrics WHERE ts>=? GROUP BY node_id) x ON x.node_id=m.node_id AND x.ts=m.ts)", (cutoff,)).fetchone()
            incidents = conn.execute("SELECT COUNT(*) count FROM incidents WHERE status IN ('open','acknowledged')").fetchone()["count"]
        node_counts = {r["status"]: r["count"] for r in nodes}
        tunnel_counts = {r["status"]: r["count"] for r in tunnel_rows}
        component_total = sum(node_counts.values()) + sum(tunnel_counts.values())
        health_percent = round(100 * (node_counts.get("online", 0) + tunnel_counts.get("healthy", 0)) / component_total, 1) if component_total else 0.0
        return {"version": VERSION, "nodes": node_counts, "tunnels": tunnel_counts, "health_percent": health_percent, "rx_bps": totals["rx"], "tx_bps": totals["tx"], "throughput_bps": totals["rx"] + totals["tx"], "connections": totals["connections"], "open_incidents": incidents, "server_time": utc_ts(), "limits": {"ssh_upload_bytes": SSH_UPLOAD_LIMIT, "ssh_relay_bytes": SSH_RELAY_LIMIT, "ssh_upload_queue_seconds": SSH_UPLOAD_QUEUE_TIMEOUT}}

    @router.get("/api/dashboard/traffic")
    def dashboard_traffic(minutes: int = 60, _: sqlite3.Row = Depends(current_user)):
        minutes = min(max(minutes, 10), 1440)
        bucket = 60 if minutes <= 180 else 300
        with db() as conn:
            rows = conn.execute(
                """WITH ranked AS (
                     SELECT (ts / ?) * ? bucket,node_id,rx_bps,tx_bps,
                            ROW_NUMBER() OVER (PARTITION BY node_id,(ts / ?) ORDER BY ts DESC) rn
                     FROM metrics WHERE ts>=?
                   )
                   SELECT bucket ts,COALESCE(SUM(rx_bps),0) rx_bps,COALESCE(SUM(tx_bps),0) tx_bps
                   FROM ranked WHERE rn=1 GROUP BY bucket ORDER BY bucket""",
                (bucket, bucket, bucket, utc_ts() - minutes * 60),
            ).fetchall()
        return [dict(row) for row in rows]

    app.include_router(router)
    return router
