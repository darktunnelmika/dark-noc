from __future__ import annotations

import json
import secrets
import socket
import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


class CertificateFleetServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def issue_certificate_mutation(
    db: ConnectionFactory,
    body: Any,
    user_id: int,
    *,
    utc_ts: Callable[[], int],
    node_stale_after: int,
    normalize_ip: Callable[[Any], str],
) -> dict[str, Any]:
    domain = body.domain.casefold().rstrip(".")
    with db() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (body.node_id,)).fetchone()
        if not node:
            raise CertificateFleetServiceError(404, "Node not found")
        if not node["last_seen"] or node["last_seen"] < utc_ts() - node_stale_after:
            raise CertificateFleetServiceError(409, "The selected Agent must be online")
        try:
            resolved = {normalize_ip(item[4][0]) for item in socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise CertificateFleetServiceError(422, "Domain DNS does not resolve yet") from exc
        expected = {normalize_ip(node["host"]), normalize_ip(node["observed_ip"])} - {""}
        if not resolved.intersection(expected):
            raise CertificateFleetServiceError(
                422,
                f"DNS mismatch: domain resolves to {', '.join(sorted(resolved))}, not the selected node",
            )
        now = utc_ts()
        existing = conn.execute(
            "SELECT id FROM certificates WHERE node_id=? AND domain=?",
            (body.node_id, domain),
        ).fetchone()
        if existing:
            cert_id = int(existing["id"])
            conn.execute(
                "UPDATE certificates SET status='pending',last_error=NULL,updated_at=? WHERE id=?",
                (now, cert_id),
            )
        else:
            cert_id = int(
                conn.execute(
                    "INSERT INTO certificates(node_id,domain,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (body.node_id, domain, "pending", user_id, now, now),
                ).lastrowid
            )
        job_id = int(
            conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (
                    body.node_id,
                    "certificate_issue",
                    json.dumps({"certificate_id": cert_id, "domain": domain}),
                    user_id,
                    now,
                ),
            ).lastrowid
        )
        conn.execute("UPDATE certificates SET job_id=? WHERE id=?", (job_id, cert_id))
    return {
        "certificate_id": cert_id,
        "job_id": job_id,
        "domain": domain,
        "node_name": node["name"],
    }


def renew_certificate_mutation(
    db: ConnectionFactory,
    certificate_id: int,
    user_id: int,
    *,
    utc_ts: Callable[[], int],
    node_stale_after: int,
) -> tuple[sqlite3.Row, int]:
    with db() as conn:
        cert = conn.execute(
            "SELECT c.*,n.last_seen FROM certificates c JOIN nodes n ON n.id=c.node_id WHERE c.id=?",
            (certificate_id,),
        ).fetchone()
        if not cert:
            raise CertificateFleetServiceError(404, "Certificate not found")
        if not cert["last_seen"] or cert["last_seen"] < utc_ts() - node_stale_after:
            raise CertificateFleetServiceError(409, "Agent is offline")
        job_id = int(
            conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (
                    cert["node_id"],
                    "certificate_issue",
                    json.dumps({"certificate_id": cert["id"], "domain": cert["domain"], "renew": True}),
                    user_id,
                    utc_ts(),
                ),
            ).lastrowid
        )
        conn.execute(
            "UPDATE certificates SET status='renewing',job_id=?,last_error=NULL,updated_at=? WHERE id=?",
            (job_id, utc_ts(), certificate_id),
        )
    return cert, job_id


def create_fleet_operation_mutation(
    db: ConnectionFactory,
    body: Any,
    user_id: int,
    *,
    version: str,
    utc_ts: Callable[[], int],
    queue_due_fleet_operations: Callable[[sqlite3.Connection, int], None],
) -> dict[str, Any]:
    node_ids = list(dict.fromkeys(body.node_ids))
    now = utc_ts()
    scheduled_at = int(body.scheduled_at or now)
    if scheduled_at > now + 366 * 86400:
        raise CertificateFleetServiceError(422, "Fleet operations can be scheduled at most one year ahead")
    if body.kind in {"restart_service", "service_status"} and not body.service:
        raise CertificateFleetServiceError(422, "This fleet operation requires a managed service name")
    remote_rollout = body.kind in {"sync_agent", "upgrade_agents"}
    if remote_rollout and scheduled_at > now + 5:
        raise CertificateFleetServiceError(422, "Agent synchronization and upgrades must be started immediately")

    payload = dict(body.payload)
    batch_size = 1
    pause_seconds = 5
    stop_on_failure = True
    canary_node_id: int | None = None
    if body.kind == "upgrade_agents":
        try:
            batch_size = min(max(int(payload.get("batch_size", 1)), 1), 10)
            pause_seconds = min(max(int(payload.get("pause_seconds", 5)), 0), 300)
            raw_stop = payload.get("stop_on_failure", True)
            if isinstance(raw_stop, bool):
                stop_on_failure = raw_stop
            elif str(raw_stop).strip().casefold() in {"true", "1", "yes", "on"}:
                stop_on_failure = True
            elif str(raw_stop).strip().casefold() in {"false", "0", "no", "off"}:
                stop_on_failure = False
            else:
                raise ValueError("invalid stop_on_failure")
            raw_canary = payload.get("canary_node_id")
            canary_node_id = int(raw_canary) if raw_canary not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise CertificateFleetServiceError(422, "Invalid Agent upgrade rollout settings") from exc
        payload = {
            "target_version": version,
            "batch_size": batch_size,
            "pause_seconds": pause_seconds,
            "stop_on_failure": stop_on_failure,
            "canary_node_id": canary_node_id,
        }
    if body.service:
        payload["service"] = body.service

    with db() as conn:
        placeholders = ",".join("?" for _ in node_ids)
        nodes = conn.execute(
            f"SELECT * FROM nodes WHERE id IN ({placeholders}) ORDER BY id",
            node_ids,
        ).fetchall()
        if len(nodes) != len(node_ids):
            raise CertificateFleetServiceError(404, "One or more selected Nodes do not exist")
        if remote_rollout:
            for node in nodes:
                if node["role"] == "hub" or (not node["ssh_password_enc"] and not node["ssh_key_enc"]):
                    raise CertificateFleetServiceError(409, f"{node['name']} cannot be managed through remote SSH")
                if node["provision_status"] == "provisioning":
                    raise CertificateFleetServiceError(409, f"{node['name']} is already being provisioned")
        if body.kind == "upgrade_agents" and canary_node_id is None:
            canary_node_id = node_ids[0]
            payload["canary_node_id"] = canary_node_id
        if canary_node_id is not None and canary_node_id not in node_ids:
            raise CertificateFleetServiceError(422, "The canary Node must be included in the selected rollout Nodes")

        operation_id = int(
            conn.execute(
                """INSERT INTO fleet_operations(name,kind,payload,status,scheduled_at,created_by,created_at,started_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    body.name,
                    body.kind,
                    json.dumps(payload),
                    "running" if remote_rollout else "scheduled",
                    scheduled_at,
                    user_id,
                    now,
                    now if remote_rollout else None,
                ),
            ).lastrowid
        )
        created_items: list[dict[str, Any]] = []
        for node in nodes:
            enrollment = secrets.token_urlsafe(36) if remote_rollout else None
            if body.kind == "sync_agent":
                conn.execute(
                    "UPDATE nodes SET provision_status='provisioning',provision_output='',updated_at=? WHERE id=?",
                    (now, node["id"]),
                )
            item_id = int(
                conn.execute(
                    "INSERT INTO fleet_operation_items(operation_id,node_id,status) VALUES(?,?,?)",
                    (operation_id, node["id"], "running" if body.kind == "sync_agent" else "scheduled"),
                ).lastrowid
            )
            created_items.append(
                {
                    "item_id": item_id,
                    "node_id": int(node["id"]),
                    "enrollment": enrollment,
                    "canary": bool(canary_node_id is not None and int(node["id"]) == int(canary_node_id)),
                }
            )
        if not remote_rollout and scheduled_at <= now:
            queue_due_fleet_operations(conn, now)

    rollout = sorted(created_items, key=lambda item: 0 if item["canary"] else 1)
    return {
        "operation_id": operation_id,
        "status": "running" if remote_rollout or scheduled_at <= now else "scheduled",
        "node_count": len(node_ids),
        "kind": body.kind,
        "created_items": created_items,
        "rollout": rollout,
        "batch_size": batch_size,
        "pause_seconds": pause_seconds,
        "stop_on_failure": stop_on_failure,
    }


def cancel_fleet_operation_mutation(
    db: ConnectionFactory,
    operation_id: int,
    *,
    utc_ts: Callable[[], int],
) -> sqlite3.Row:
    with db() as conn:
        operation = conn.execute("SELECT * FROM fleet_operations WHERE id=?", (operation_id,)).fetchone()
        if not operation:
            raise CertificateFleetServiceError(404, "Fleet operation not found")
        cancellable_rollout = operation["kind"] == "upgrade_agents" and operation["status"] == "running"
        if operation["status"] != "scheduled" and not cancellable_rollout:
            raise CertificateFleetServiceError(409, "Only a scheduled operation or active Agent rollout can be cancelled")
        conn.execute(
            "UPDATE fleet_operations SET status='cancelled',finished_at=? WHERE id=?",
            (utc_ts(), operation_id),
        )
        conn.execute(
            "UPDATE fleet_operation_items SET status='cancelled' WHERE operation_id=? AND status='scheduled'",
            (operation_id,),
        )
    return operation
