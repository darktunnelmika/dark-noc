from __future__ import annotations

import sqlite3
from collections.abc import Callable

ConnectionFactory = Callable[[], sqlite3.Connection]


def fetch_monitor_inventory(db: ConnectionFactory) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """SELECT monitors.*,nodes.name node_name,nodes.role node_role,nodes.host node_host,
                      nodes.last_seen node_last_seen
               FROM monitors JOIN nodes ON nodes.id=monitors.node_id
               ORDER BY CASE monitors.status WHEN 'down' THEN 0 WHEN 'degraded' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                        monitors.name COLLATE NOCASE"""
        ).fetchall()


def fetch_monitor_result_rows(
    db: ConnectionFactory,
    monitor_id: int,
    limit: int,
) -> tuple[bool, list[sqlite3.Row]]:
    with db() as conn:
        exists = bool(conn.execute("SELECT 1 FROM monitors WHERE id=?", (monitor_id,)).fetchone())
        if not exists:
            return False, []
        rows = conn.execute(
            "SELECT ts,status,latency_ms,detail FROM monitor_results WHERE monitor_id=? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    return True, rows


def fetch_incident_inventory(db: ConnectionFactory) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name,
                      (SELECT COUNT(*) FROM incident_events WHERE incident_id=incidents.id) event_count
               FROM incidents
               LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id
               ORDER BY incidents.status='open' DESC,incidents.status='acknowledged' DESC,incidents.opened_at DESC LIMIT 500"""
        ).fetchall()


def fetch_incident_detail_rows(
    db: ConnectionFactory,
    incident_id: int,
) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    with db() as conn:
        row = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name
               FROM incidents LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id WHERE incidents.id=?""",
            (incident_id,),
        ).fetchone()
        if not row:
            return None, []
        events = conn.execute(
            """SELECT incident_events.*,users.username actor
               FROM incident_events LEFT JOIN users ON users.id=incident_events.actor_id
               WHERE incident_id=? ORDER BY created_at,id""",
            (incident_id,),
        ).fetchall()
    return row, events
