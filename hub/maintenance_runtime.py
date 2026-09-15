from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Callable

ConnectionFactory = Callable[[], sqlite3.Connection]


def acquire_hub_lease(
    *,
    db: ConnectionFactory,
    utc_ts: Callable[[], int],
    hub_lease_seconds: int,
    hub_instance_id: str,
    name: str,
    now: int | None = None,
) -> bool:
    current = now or utc_ts()
    expiry = current + hub_lease_seconds
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO hub_leases(name,holder,expires_at,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at,updated_at=excluded.updated_at
               WHERE hub_leases.holder=excluded.holder OR hub_leases.expires_at<?""",
            (name, hub_instance_id, expiry, current, current),
        )
        row = conn.execute("SELECT holder,expires_at FROM hub_leases WHERE name=?", (name,)).fetchone()
    return bool(row and row["holder"] == hub_instance_id and int(row["expires_at"]) >= expiry)


def queue_due_monitors(
    conn: sqlite3.Connection,
    now: int,
    online_cutoff: int,
    *,
    decrypt: Callable[[str | None], str | None],
    logger: Any,
    create_incident: Callable[..., int],
) -> None:
    due = conn.execute(
        """SELECT monitors.* FROM monitors JOIN nodes ON nodes.id=monitors.node_id
           WHERE monitors.enabled=1 AND monitors.next_run_at<=? AND nodes.last_seen>=?
           ORDER BY monitors.next_run_at,monitors.id LIMIT 100""",
        (now, online_cutoff),
    ).fetchall()
    for monitor in due:
        active = conn.execute(
            "SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')",
            (monitor["job_id"],),
        ).fetchone() if monitor["job_id"] else None
        if active:
            continue
        payload = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        try:
            if monitor["kind"] == "snmp":
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
        except Exception:
            logger.exception("Could not decrypt synthetic monitor secret for monitor %s", monitor["id"])
            failure_streak = int(monitor["failure_streak"] or 0) + 1
            detail = json.dumps({"error": "Encrypted monitor secret cannot be read; edit and save the monitor again"})
            conn.execute(
                """UPDATE monitors SET status='down',detail=?,failure_streak=?,last_run_at=?,next_run_at=?,updated_at=?
                   WHERE id=?""",
                (detail, failure_streak, now, now + int(monitor["interval_seconds"]), now, monitor["id"]),
            )
            conn.execute(
                "INSERT INTO monitor_results(monitor_id,ts,status,detail) VALUES(?,?,'down',?)",
                (monitor["id"], now, detail),
            )
            title = f"Monitor {monitor['name']} is down"
            active_incident = conn.execute(
                "SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (monitor["node_id"], title),
            ).fetchone()
            if failure_streak >= 2 and not active_incident:
                create_incident(
                    conn, node_id=monitor["node_id"], tunnel_id=None, severity="critical", title=title,
                    detail={"monitor_id": monitor["id"], "reason": "encrypted_secret_unreadable"}, opened_at=now,
                )
            continue
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), now),
        ).lastrowid
        conn.execute(
            "UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?",
            (job_id, now + int(monitor["interval_seconds"]), now, monitor["id"]),
        )


def queue_due_fleet_operations(conn: sqlite3.Connection, now: int) -> None:
    operations = conn.execute(
        "SELECT * FROM fleet_operations WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at,id LIMIT 20",
        (now,),
    ).fetchall()
    for operation in operations:
        payload = json.loads(operation["payload"] or "{}")
        items = conn.execute(
            "SELECT * FROM fleet_operation_items WHERE operation_id=? ORDER BY id",
            (operation["id"],),
        ).fetchall()
        for item in items:
            if item["job_id"]:
                continue
            job_id = conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (item["node_id"], operation["kind"], json.dumps(payload), operation["created_by"], now),
            ).lastrowid
            conn.execute(
                "UPDATE fleet_operation_items SET job_id=?,status='queued' WHERE id=?",
                (job_id, item["id"]),
            )
        conn.execute("UPDATE fleet_operations SET status='running',started_at=? WHERE id=?", (now, operation["id"]))


def rollup_previous_hour(conn: sqlite3.Connection, now: int) -> None:
    bucket = ((now // 3600) - 1) * 3600
    if bucket < 0:
        return
    conn.execute(
        """INSERT INTO metric_rollups(node_id,bucket,samples,cpu_avg,ram_avg,disk_max,load_avg,rx_avg,tx_avg,connections_max)
           SELECT node_id,?,COUNT(*),AVG(cpu),AVG(ram),MAX(disk),AVG(load1),AVG(rx_bps),AVG(tx_bps),MAX(connections)
           FROM metrics WHERE ts>=? AND ts<? GROUP BY node_id
           ON CONFLICT(node_id,bucket) DO UPDATE SET
             samples=excluded.samples,cpu_avg=excluded.cpu_avg,ram_avg=excluded.ram_avg,
             disk_max=excluded.disk_max,load_avg=excluded.load_avg,rx_avg=excluded.rx_avg,
             tx_avg=excluded.tx_avg,connections_max=excluded.connections_max""",
        (bucket, bucket, bucket + 3600),
    )


def run_maintenance_cycle(
    *,
    db: ConnectionFactory,
    utc_ts: Callable[[], int],
    node_stale_after: int,
    metric_raw_retention_days: int,
    metric_rollup_retention_days: int,
    tunnel_sample_retention_days: int,
    monitor_result_retention_days: int,
    create_incident: Callable[..., int],
    resolve_incident: Callable[..., bool],
    rollup_previous_hour: Callable[[sqlite3.Connection, int], None],
    queue_due_monitors: Callable[[sqlite3.Connection, int, int], None],
    queue_due_fleet_operations: Callable[[sqlite3.Connection, int], None],
) -> None:
    now = utc_ts()
    cutoff = now - node_stale_after
    with db() as conn:
        stale_nodes = conn.execute("SELECT id,name FROM nodes WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,)).fetchall()
        for stale in stale_nodes:
            title = f"Node {stale['name']} is offline"
            if not conn.execute("SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')", (stale["id"], title)).fetchone():
                create_incident(conn, node_id=stale["id"], tunnel_id=None, severity="critical", title=title, detail={"reason": "heartbeat_timeout"})
        online_nodes = conn.execute("SELECT id FROM nodes WHERE last_seen>=?", (cutoff,)).fetchall()
        for online in online_nodes:
            active = conn.execute("SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')", (online["id"],)).fetchall()
            for incident in active:
                resolve_incident(conn, incident["id"], "Agent heartbeat recovered")
        conn.execute("UPDATE nodes SET status='offline' WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        rollup_previous_hour(conn, now)
        conn.execute("DELETE FROM metrics WHERE ts < ?", (now - metric_raw_retention_days * 86400,))
        conn.execute("DELETE FROM metric_rollups WHERE bucket < ?", (now - metric_rollup_retention_days * 86400,))
        conn.execute("DELETE FROM tunnel_samples WHERE ts < ?", (now - tunnel_sample_retention_days * 86400,))
        conn.execute("DELETE FROM monitor_results WHERE ts < ?", (now - monitor_result_retention_days * 86400,))
        conn.execute("UPDATE jobs SET status='queued',started_at=NULL WHERE status='running' AND started_at<?", (now - 5 * 60,))
        conn.execute("DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at<?", (now - 90 * 86400,))
        conn.execute("DELETE FROM audit_logs WHERE created_at<?", (now - 180 * 86400,))
        conn.execute("UPDATE certificates SET status='expired',updated_at=? WHERE expires_at IS NOT NULL AND expires_at<=?", (now, now))
        conn.execute("UPDATE certificates SET status='expiring',updated_at=? WHERE status='valid' AND expires_at BETWEEN ? AND ?", (now, now, now + 14 * 86400))
        renewable = conn.execute("SELECT c.* FROM certificates c JOIN nodes n ON n.id=c.node_id WHERE c.status IN ('valid','expiring') AND c.expires_at BETWEEN ? AND ? AND n.last_seen>=?", (now, now + 30 * 86400, cutoff)).fetchall()
        for cert in renewable:
            pending = conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (cert["job_id"],)).fetchone() if cert["job_id"] else None
            if pending:
                continue
            job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)", (cert["node_id"], "certificate_issue", json.dumps({"certificate_id": cert["id"], "domain": cert["domain"], "renew": True}), now)).lastrowid
            conn.execute("UPDATE certificates SET status='renewing',job_id=?,updated_at=? WHERE id=?", (job_id, now, cert["id"]))
        queue_due_monitors(conn, now, cutoff)
        queue_due_fleet_operations(conn, now)


async def maintenance_loop(
    *,
    acquire_hub_lease: Callable[[str], bool],
    run_cycle: Callable[[], None],
    logger: Any,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> None:
    while True:
        try:
            if not acquire_hub_lease("maintenance"):
                await sleep(15)
                continue
            run_cycle()
        except Exception:
            logger.exception("maintenance loop failed")
        await sleep(30)


def build_lifespan(
    *,
    bootstrap: Callable[[], None],
    fernet_cls: Any,
    key_path: Any,
    maintenance_loop: Callable[[], Any],
    db: ConnectionFactory,
    hub_instance_id: str,
    logger: Any,
):
    @asynccontextmanager
    async def lifespan(application):
        bootstrap()
        application.state.fernet = fernet_cls(key_path.read_bytes())
        task = asyncio.create_task(maintenance_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                with db() as conn:
                    conn.execute("DELETE FROM hub_leases WHERE holder=?", (hub_instance_id,))
            except Exception:
                logger.exception("Could not release Hub controller leases")

    return lifespan
