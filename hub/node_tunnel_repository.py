from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


def find_node_endpoint_conflict(
    conn: sqlite3.Connection,
    host: str,
    ssh_port: int,
    canonicalize_host: Callable[[Any], str],
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    canonical_host = canonicalize_host(host)
    rows = conn.execute("SELECT id,name,host FROM nodes WHERE ssh_port=?", (ssh_port,)).fetchall()
    return next(
        (
            row for row in rows
            if row["id"] != exclude_id and canonicalize_host(row["host"]) == canonical_host
        ),
        None,
    )


def fetch_node_inventory(
    db: ConnectionFactory,
    cutoff: int,
) -> list[tuple[sqlite3.Row, sqlite3.Row | None, list[sqlite3.Row]]]:
    records: list[tuple[sqlite3.Row, sqlite3.Row | None, list[sqlite3.Row]]] = []
    with db() as conn:
        rows = conn.execute("""SELECT * FROM nodes
            ORDER BY CASE role WHEN 'hub' THEN 0 WHEN 'edge' THEN 1 WHEN 'exit' THEN 2 ELSE 3 END,
                     CASE WHEN last_seen>=? THEN 0 WHEN last_seen IS NULL THEN 2 ELSE 1 END,
                     name COLLATE NOCASE""", (cutoff,)).fetchall()
        for row in rows:
            metric = conn.execute(
                "SELECT * FROM metrics WHERE node_id=? ORDER BY ts DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            services = conn.execute(
                "SELECT name,status,last_check FROM node_services WHERE node_id=? ORDER BY name",
                (row["id"],),
            ).fetchall()
            records.append((row, metric, services))
    return records


def fetch_tunnel_inventory(
    db: ConnectionFactory,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
    with db() as conn:
        rows = conn.execute("""SELECT tunnels.*,nodes.name node_name,nodes.host node_host,
            nodes.observed_ip node_observed_ip,nodes.region,nodes.role node_role,
            nodes.last_seen node_last_seen,nodes.status node_agent_status,
            CASE WHEN nodes.ssh_password_enc IS NOT NULL OR nodes.ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END node_ssh_configured
            FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id ORDER BY tunnels.status DESC,tunnels.name""").fetchall()
        nodes = conn.execute("""SELECT id,name,host,observed_ip,region,role,status,last_seen,
            CASE WHEN ssh_password_enc IS NOT NULL OR ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END ssh_configured FROM nodes""").fetchall()
        deployments = conn.execute(
            """SELECT id,name,iran_node_id,kharej_node_id FROM plugin_deployments
               WHERE lifecycle NOT IN ('removed','rolled_back') ORDER BY id DESC"""
        ).fetchall()
    return rows, nodes, deployments


def fetch_tunnel_operation_rows(
    db: ConnectionFactory,
    *,
    tunnel_id: int,
    cutoff: int,
    node_ids: list[int],
    tunnel_name: str,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], sqlite3.Row | None, sqlite3.Row | None]:
    with db() as conn:
        samples = conn.execute(
            """SELECT ts,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,
                      service_uptime,process_ok,path_ok
               FROM tunnel_samples WHERE tunnel_id=? AND ts>=? ORDER BY ts LIMIT 10000""",
            (tunnel_id, cutoff),
        ).fetchall()
        placeholders = ",".join("?" for _ in node_ids)
        job_rows = conn.execute(
            f"SELECT jobs.*,nodes.name node_name FROM jobs JOIN nodes ON nodes.id=jobs.node_id "
            f"WHERE jobs.node_id IN ({placeholders}) ORDER BY jobs.id DESC LIMIT 100",
            node_ids,
        ).fetchall()
        managed = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings FROM plugin_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel_name,),
        ).fetchone()
        hybrid = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings,remote_label FROM hybrid_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel_name,),
        ).fetchone()
    return samples, job_rows, managed, hybrid
