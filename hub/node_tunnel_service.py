from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


class NodeTunnelServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def create_node_mutation(
    db: ConnectionFactory,
    body: Any,
    enrollment: str,
    now: int,
    *,
    encrypt: Callable[[str | None], str | None],
    token_hash: Callable[[str], str],
    endpoint_conflict: Callable[..., sqlite3.Row | None],
) -> int:
    if body.role == "hub":
        raise NodeTunnelServiceError(400, "The Hub node is registered automatically; add this server as Iran Edge or Global Exit")
    if not body.ssh_password and not body.ssh_private_key:
        raise NodeTunnelServiceError(400, "SSH password or private key is required for automatic Node installation")
    if body.ssh_user != "root":
        raise NodeTunnelServiceError(400, "Automatic Node installation currently requires SSH user root")
    try:
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conflict = endpoint_conflict(conn, body.host, body.ssh_port)
            if conflict:
                raise NodeTunnelServiceError(409, f"SSH endpoint is already registered as {conflict['name']}")
            cursor = conn.execute(
                "INSERT INTO nodes(name,region,role,host,ssh_port,ssh_user,ssh_password_enc,ssh_key_enc,agent_token_hash,status,provision_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    body.name, body.region, body.role, body.host, body.ssh_port, body.ssh_user,
                    encrypt(body.ssh_password), encrypt(body.ssh_private_key), token_hash(enrollment),
                    "pending", "provisioning", now, now,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise NodeTunnelServiceError(409, "Node name already exists") from exc


def prepare_node_provision_mutation(
    db: ConnectionFactory,
    node_id: int,
    now: int,
    *,
    endpoint_conflict: Callable[..., sqlite3.Row | None],
) -> sqlite3.Row:
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise NodeTunnelServiceError(404, "Node not found")
        if node["role"] == "hub":
            raise NodeTunnelServiceError(400, "The local Hub Agent is managed by the Hub installer")
        if not node["ssh_password_enc"] and not node["ssh_key_enc"]:
            raise NodeTunnelServiceError(400, "Configure SSH credentials before retrying installation")
        conflict = endpoint_conflict(conn, node["host"], node["ssh_port"], node_id)
        if conflict:
            raise NodeTunnelServiceError(409, f"SSH endpoint is already registered as {conflict['name']}")
        changed = conn.execute(
            """UPDATE nodes SET provision_status='provisioning',provision_output='',updated_at=?
               WHERE id=? AND COALESCE(provision_status,'')<>'provisioning'""",
            (now, node_id),
        ).rowcount
        if changed != 1:
            raise NodeTunnelServiceError(409, "Agent installation or synchronization is already running")
        return node


def delete_node_mutation(db: ConnectionFactory, node_id: int) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT name,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise NodeTunnelServiceError(404, "Node not found")
        if row["provision_status"] == "provisioning":
            raise NodeTunnelServiceError(409, "Wait for Agent installation or synchronization to finish before deleting this Node")
        deployment = conn.execute(
            "SELECT id FROM plugin_deployments WHERE lifecycle IN ('active','removing','rolling_back') AND (iran_node_id=? OR kharej_node_id=?) LIMIT 1",
            (node_id, node_id),
        ).fetchone()
        if deployment:
            raise NodeTunnelServiceError(409, "Remove active DARK NOC tunnel deployments before deleting this node")
        conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        return row


def update_node_mutation(
    db: ConnectionFactory,
    node_id: int,
    body: Any,
    now: int,
    *,
    encrypt: Callable[[str | None], str | None],
    endpoint_conflict: Callable[..., sqlite3.Row | None],
) -> bool:
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not current:
            raise NodeTunnelServiceError(404, "Node not found")
        if current["provision_status"] == "provisioning":
            raise NodeTunnelServiceError(409, "Wait for Agent installation or synchronization to finish before editing this Node")
        conflict = endpoint_conflict(conn, body.host, body.ssh_port, node_id)
        if conflict:
            raise NodeTunnelServiceError(409, f"SSH endpoint is already registered as {conflict['name']}")
        password_enc = encrypt(body.ssh_password) if body.ssh_password else current["ssh_password_enc"]
        key_enc = encrypt(body.ssh_private_key) if body.ssh_private_key else current["ssh_key_enc"]
        reset_pin = current["host"] != body.host or current["ssh_port"] != body.ssh_port
        try:
            conn.execute(
                """UPDATE nodes SET name=?,region=?,role=?,host=?,ssh_port=?,ssh_user=?,
                  ssh_password_enc=?,ssh_key_enc=?,ssh_host_fingerprint=?,updated_at=? WHERE id=?""",
                (
                    body.name, body.region, body.role, body.host, body.ssh_port, body.ssh_user,
                    password_enc, key_enc, None if reset_pin else current["ssh_host_fingerprint"], now, node_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise NodeTunnelServiceError(409, "Node name already exists") from exc
        return bool(reset_pin)


def reset_node_fingerprint_mutation(db: ConnectionFactory, node_id: int, now: int) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT name,provision_status FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not row:
            raise NodeTunnelServiceError(404, "Node not found")
        if row["provision_status"] == "provisioning":
            raise NodeTunnelServiceError(409, "Wait for Agent installation or synchronization to finish before resetting its SSH fingerprint")
        conn.execute("UPDATE nodes SET ssh_host_fingerprint=NULL,updated_at=? WHERE id=?", (now, node_id))
        return row


def queue_tunnel_action_mutation(
    db: ConnectionFactory,
    tunnel_id: int,
    action: str,
    user_id: int,
    *,
    utc_ts: Callable[[], int],
    node_stale_after: int,
) -> tuple[sqlite3.Row, int]:
    with db() as conn:
        tunnel = conn.execute(
            "SELECT tunnels.*,nodes.last_seen FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id WHERE tunnels.id=?",
            (tunnel_id,),
        ).fetchone()
        if not tunnel:
            raise NodeTunnelServiceError(404, "Tunnel not found")
        if not tunnel["last_seen"] or tunnel["last_seen"] < utc_ts() - node_stale_after:
            raise NodeTunnelServiceError(409, "The tunnel Agent is offline")
        service = str(tunnel["service"] or "")
        plugin_id = (
            "dark-realm" if service.startswith("dark-realm@") else
            "dark-ghostpro" if service.startswith("ghostpro@") else
            "dark-packetpro" if service.startswith("paqetpro@") else
            "dark-backhaul" if service.startswith("backhaul@") else None
        )
        if not plugin_id:
            raise NodeTunnelServiceError(422, "This tunnel is not managed by a DARK NOC plugin")
        kind, payload = {
            "start": ("tunnel_control", {"name": tunnel["name"], "action": "start", "plugin_id": plugin_id}),
            "stop": ("tunnel_control", {"name": tunnel["name"], "action": "stop", "plugin_id": plugin_id}),
            "restart": ("tunnel_control", {"name": tunnel["name"], "action": "restart", "plugin_id": plugin_id}),
            "logs": ("logs", {"service": service}),
            "status": ("service_status", {"service": service}),
            "test": ("tunnel_test", {}),
            "install": ("plugin_install", {"plugin_id": plugin_id}),
        }[action]
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (tunnel["node_id"], kind, json.dumps(payload), user_id, utc_ts()),
        ).lastrowid
        return tunnel, int(job_id)


def queue_plugin_install_mutation(
    db: ConnectionFactory,
    node_id: int,
    plugin_id: str,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    utc_ts: Callable[[], int],
    node_stale_after: int,
) -> tuple[sqlite3.Row, int]:
    if plugin_id not in {item["id"] for item in plugin_catalog}:
        raise NodeTunnelServiceError(404, "Plugin not found")
    with db() as conn:
        node = conn.execute("SELECT id,name,last_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise NodeTunnelServiceError(404, "Node not found")
        if not node["last_seen"] or node["last_seen"] < utc_ts() - node_stale_after:
            raise NodeTunnelServiceError(409, "Agent must be online")
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (node_id, "plugin_install", json.dumps({"plugin_id": plugin_id}), user_id, utc_ts()),
        ).lastrowid
        return node, int(job_id)


def reconfigure_tunnel_mutation(
    db: ConnectionFactory,
    tunnel_id: int,
    body: Any,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    certificate_for_deployment: Callable[..., dict[str, Any] | None],
    decrypt: Callable[[str | None], str | None],
    plugin_job_payload: Callable[[dict[str, Any], str, str], dict[str, Any]],
    plugin_pair_code: Callable[[dict[str, Any], str], str],
    token_hash: Callable[[str], str],
    utc_ts: Callable[[], int],
) -> dict[str, Any]:
    ports = sorted(set(body.user_ports))
    pair_code: str | None = None
    with db() as conn:
        tunnel = conn.execute("SELECT * FROM tunnels WHERE id=?", (tunnel_id,)).fetchone()
        if not tunnel:
            raise NodeTunnelServiceError(404, "Tunnel not found")
        managed = conn.execute(
            "SELECT * FROM plugin_deployments WHERE name=? AND lifecycle='active' AND (iran_node_id=? OR kharej_node_id=?)",
            (tunnel["name"], tunnel["node_id"], tunnel["node_id"]),
        ).fetchone()
        hybrid = conn.execute(
            "SELECT * FROM hybrid_deployments WHERE name=? AND lifecycle='active' AND iran_node_id=?",
            (tunnel["name"], tunnel["node_id"]),
        ).fetchone()
        deployment = managed or hybrid
        if not deployment:
            raise NodeTunnelServiceError(409, "This discovered tunnel has no recoverable DARK NOC pairing token; recreate it from the panel to edit configuration")
        settings = json.loads(deployment["settings"])
        plugin = next((item for item in plugin_catalog if item["id"] == settings.get("plugin_id")), None)
        if not plugin or body.transport not in plugin["transports"]:
            raise NodeTunnelServiceError(422, "Transport is not supported by this plugin")
        if body.profile not in plugin["profiles"]:
            raise NodeTunnelServiceError(422, "Performance profile is not supported by this plugin")
        if any(
            port < 1 or port > 65535
            or (settings.get("plugin_id") != "dark-realm" and port == int(settings["tunnel_port"]))
            for port in ports
        ):
            raise NodeTunnelServiceError(422, "Invalid user port or collision with the tunnel port")
        if settings.get("plugin_id") == "dark-realm":
            old_maps = [str(item) for item in settings.get("port_mappings") or []]
            rebuilt: list[str] = []
            for index, public_port in enumerate(ports):
                target_port = public_port
                backbone_port = int(settings["tunnel_port"]) + index
                if index < len(old_maps):
                    match = re.fullmatch(r"[0-9]{1,5}>([0-9]{1,5})@([0-9]{1,5})", old_maps[index])
                    if match:
                        target_port, backbone_port = int(match.group(1)), int(match.group(2))
                rebuilt.append(f"{public_port}>{target_port}@{backbone_port}")
            settings.update(
                {
                    "user_ports": ports,
                    "port_mappings": rebuilt,
                    "maps": ",".join(rebuilt),
                    "transport": body.transport,
                    "profile": body.profile,
                    "restart_every": body.restart_every,
                }
            )
            selected_certificate_id = body.certificate_id or settings.get("certificate_id")
            for key in ("certificate_id", "certificate_domain", "certificate_path", "certificate_key_path"):
                settings.pop(key, None)
            if managed:
                cert = certificate_for_deployment(conn, selected_certificate_id, managed["kharej_node_id"], body.transport)
                if cert:
                    settings.update(cert)
                    settings["gateway_host"] = cert["certificate_domain"]
                    settings["endpoint"] = cert["certificate_domain"]
                    settings["tls_domain"] = cert["certificate_domain"]
                    settings["sni"] = cert["certificate_domain"]
                    if body.transport == "wss":
                        settings["ws_host"] = cert["certificate_domain"]
            elif body.transport in {"tls", "wss"} and not settings.get("tls_domain"):
                raise NodeTunnelServiceError(422, "Pair Code Realm TLS/WSS requires a Gateway domain in the original deployment")
        else:
            settings.update(
                {
                    "user_ports": ports,
                    "transport": body.transport,
                    "profile": body.profile,
                    "restart_every": body.restart_every,
                }
            )
            selected_certificate_id = body.certificate_id or settings.get("certificate_id")
            cert = certificate_for_deployment(conn, selected_certificate_id, tunnel["node_id"], body.transport)
            for key in ("certificate_id", "certificate_domain", "certificate_path", "certificate_key_path"):
                settings.pop(key, None)
            if cert:
                settings.update(cert)
                settings["endpoint"] = cert["certificate_domain"]
        token = decrypt(deployment["pair_token_enc"])
        jobs: dict[str, int] = {}
        roles = plugin["roles"]
        if managed:
            for side, node_id, role in (
                ("iran", managed["iran_node_id"], roles["iran"]),
                ("kharej", managed["kharej_node_id"], roles["kharej"]),
            ):
                jobs[side] = int(
                    conn.execute(
                        "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                        (node_id, "plugin_deploy", json.dumps(plugin_job_payload(settings, token, role)), user_id, utc_ts()),
                    ).lastrowid
                )
            conn.execute(
                "UPDATE plugin_deployments SET settings=?,iran_job_id=?,kharej_job_id=? WHERE id=?",
                (json.dumps(settings), jobs["iran"], jobs["kharej"], managed["id"]),
            )
        else:
            jobs["iran"] = int(
                conn.execute(
                    "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                    (hybrid["iran_node_id"], "plugin_deploy", json.dumps(plugin_job_payload(settings, token, roles["iran"])), user_id, utc_ts()),
                ).lastrowid
            )
            pair_code = plugin_pair_code(settings, token)
            conn.execute(
                "UPDATE hybrid_deployments SET settings=?,iran_job_id=?,pair_code_hash=?,updated_at=? WHERE id=?",
                (json.dumps(settings), jobs["iran"], token_hash(pair_code), utc_ts(), hybrid["id"]),
            )
        return {
            "tunnel_name": tunnel["name"],
            "ports": ports,
            "jobs": jobs,
            "managed": bool(managed),
            "hybrid": bool(hybrid),
            "pair_code": pair_code,
        }


def remove_tunnel_mutation(
    db: ConnectionFactory,
    tunnel_id: int,
    user_id: int,
    now: int,
) -> dict[str, Any]:
    with db() as conn:
        tunnel = conn.execute("SELECT * FROM tunnels WHERE id=?", (tunnel_id,)).fetchone()
        if not tunnel:
            raise NodeTunnelServiceError(404, "Tunnel not found")
        managed = conn.execute(
            "SELECT * FROM plugin_deployments WHERE name=? AND lifecycle='active' AND (iran_node_id=? OR kharej_node_id=?)",
            (tunnel["name"], tunnel["node_id"], tunnel["node_id"]),
        ).fetchone()
        hybrid = conn.execute(
            "SELECT * FROM hybrid_deployments WHERE name=? AND lifecycle='active' AND iran_node_id=?",
            (tunnel["name"], tunnel["node_id"]),
        ).fetchone()
        jobs: dict[str, int] = {}
        if managed:
            settings = json.loads(managed["settings"])
            for side, node_id in (("iran", managed["iran_node_id"]), ("kharej", managed["kharej_node_id"])):
                jobs[side] = int(
                    conn.execute(
                        "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                        (
                            node_id,
                            "plugin_remove",
                            json.dumps({"plugin_id": settings.get("plugin_id", "dark-backhaul"), "name": tunnel["name"]}),
                            user_id,
                            now,
                        ),
                    ).lastrowid
                )
            conn.execute(
                "UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing' WHERE id=?",
                (jobs["iran"], jobs["kharej"], managed["id"]),
            )
        else:
            plugin_id = (
                json.loads(hybrid["settings"]).get("plugin_id", "dark-backhaul")
                if hybrid
                else ("dark-ghostpro" if str(tunnel["service"]).startswith("ghostpro@") else "dark-backhaul")
            )
            jobs["iran"] = int(
                conn.execute(
                    "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                    (
                        tunnel["node_id"],
                        "plugin_remove",
                        json.dumps({"plugin_id": plugin_id, "name": tunnel["name"]}),
                        user_id,
                        now,
                    ),
                ).lastrowid
            )
            if hybrid:
                conn.execute(
                    "UPDATE hybrid_deployments SET iran_job_id=?,lifecycle='removing',updated_at=? WHERE id=?",
                    (jobs["iran"], now, hybrid["id"]),
                )
        return {
            "tunnel_name": tunnel["name"],
            "jobs": jobs,
            "managed": bool(managed),
            "hybrid": bool(hybrid),
        }
