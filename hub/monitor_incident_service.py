from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


class MonitorIncidentServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def create_monitor_mutation(
    db: ConnectionFactory,
    body: Any,
    user_id: int,
    now: int,
    *,
    monitor_values: Callable[..., tuple[str, int | None, str | None, str | None]],
) -> tuple[int, str]:
    target, port, secret, oid = monitor_values(body)
    try:
        with db() as conn:
            node = conn.execute("SELECT name FROM nodes WHERE id=?", (body.node_id,)).fetchone()
            if not node:
                raise MonitorIncidentServiceError(404, "Node not found")
            monitor_id = int(conn.execute(
                """INSERT INTO monitors(node_id,name,kind,target,port,secret_enc,snmp_oid,interval_seconds,
                       timeout_seconds,expected_status,enabled,status,next_run_at,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, "pending", now,
                 user_id, now, now),
            ).lastrowid)
    except sqlite3.IntegrityError as exc:
        raise MonitorIncidentServiceError(409, "A monitor with this name already exists on the selected Node") from exc
    return monitor_id, target


def update_monitor_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    monitor_values: Callable[..., tuple[str, int | None, str | None, str | None]],
    resolve_incident: Callable[..., None],
) -> str:
    try:
        with db() as conn:
            current = conn.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
            if not current:
                raise MonitorIncidentServiceError(404, "Monitor not found")
            if not conn.execute("SELECT 1 FROM nodes WHERE id=?", (body.node_id,)).fetchone():
                raise MonitorIncidentServiceError(404, "Node not found")
            target, port, secret, oid = monitor_values(body, current["secret_enc"])
            old_title = f"Monitor {current['name']} is down"
            old_incidents = conn.execute(
                "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (current["node_id"], old_title),
            ).fetchall()
            for incident in old_incidents:
                resolve_incident(conn, incident["id"], "Monitor configuration changed", actor_id=user_id)
            conn.execute(
                """UPDATE monitors SET node_id=?,name=?,kind=?,target=?,port=?,secret_enc=?,snmp_oid=?,
                       interval_seconds=?,timeout_seconds=?,expected_status=?,enabled=?,status='pending',
                       failure_streak=0,next_run_at=?,updated_at=? WHERE id=?""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, now, now, monitor_id),
            )
    except sqlite3.IntegrityError as exc:
        raise MonitorIncidentServiceError(409, "A monitor with this name already exists on the selected Node") from exc
    return target


def delete_monitor_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    user_id: int,
    *,
    resolve_incident: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT node_id,name FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Monitor not found")
        title = f"Monitor {row['name']} is down"
        active = conn.execute(
            "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
            (row["node_id"], title),
        ).fetchall()
        for incident in active:
            resolve_incident(conn, incident["id"], "Monitor was removed by the operator", actor_id=user_id)
        conn.execute("DELETE FROM monitors WHERE id=?", (monitor_id,))
    return row


def queue_monitor_run_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    user_id: int,
    now: int,
    *,
    node_stale_after: int,
    decrypt: Callable[[str | None], str | None],
) -> tuple[sqlite3.Row, int]:
    with db() as conn:
        monitor = conn.execute(
            "SELECT monitors.*,nodes.last_seen FROM monitors JOIN nodes ON nodes.id=monitors.node_id WHERE monitors.id=?",
            (monitor_id,),
        ).fetchone()
        if not monitor:
            raise MonitorIncidentServiceError(404, "Monitor not found")
        if not monitor["last_seen"] or monitor["last_seen"] < now - node_stale_after:
            raise MonitorIncidentServiceError(409, "Agent is offline")
        active = (
            conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (monitor["job_id"],)).fetchone()
            if monitor["job_id"] else None
        )
        if active:
            raise MonitorIncidentServiceError(409, "Monitor check is already running")
        payload: dict[str, Any] = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        if monitor["kind"] == "snmp":
            try:
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
            except Exception as exc:
                raise MonitorIncidentServiceError(409, "Encrypted SNMP community cannot be read; edit and save the monitor again") from exc
        job_id = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), user_id, now),
        ).lastrowid)
        conn.execute(
            "UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?",
            (job_id, now + monitor["interval_seconds"], now, monitor_id),
        )
    return monitor, job_id


def add_incident_note_mutation(
    db: ConnectionFactory,
    incident_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    append_incident_event: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT title FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Incident not found")
        append_incident_event(conn, incident_id, body.event_type, body.message, actor_id=user_id)
        conn.execute("UPDATE incidents SET last_changed_at=? WHERE id=?", (now, incident_id))
    return row


def incident_action_mutation(
    db: ConnectionFactory,
    incident_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    append_incident_event: Callable[..., None],
    resolve_incident: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Incident not found")
        if body.action == "acknowledge":
            if row["status"] != "open":
                raise MonitorIncidentServiceError(409, "Only an open incident can be acknowledged")
            conn.execute(
                "UPDATE incidents SET status='acknowledged',acknowledged_at=?,last_changed_at=?,root_cause=COALESCE(?,root_cause) WHERE id=?",
                (now, now, body.root_cause, incident_id),
            )
            append_incident_event(
                conn, incident_id, "acknowledged", body.note or "Operator acknowledged the incident",
                actor_id=user_id, created_at=now,
            )
        elif body.action == "resolve":
            if row["status"] == "resolved":
                raise MonitorIncidentServiceError(409, "Incident is already resolved")
            conn.execute(
                "UPDATE incidents SET root_cause=COALESCE(?,root_cause) WHERE id=?",
                (body.root_cause, incident_id),
            )
            resolve_incident(
                conn, incident_id, body.note or "Operator resolved the incident",
                actor_id=user_id, resolution=body.resolution,
            )
        else:
            if row["status"] != "resolved":
                raise MonitorIncidentServiceError(409, "Only a resolved incident can be reopened")
            conn.execute(
                "UPDATE incidents SET status='open',resolved_at=NULL,last_changed_at=?,resolution=NULL WHERE id=?",
                (now, incident_id),
            )
            append_incident_event(
                conn, incident_id, "reopened", body.note or "Operator reopened the incident",
                actor_id=user_id, created_at=now,
            )
    return row
