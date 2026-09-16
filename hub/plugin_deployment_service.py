from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


class PluginDeploymentServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def _catalog_item(plugin_catalog: list[dict[str, Any]], plugin_id: str) -> dict[str, Any]:
    plugin = next((item for item in plugin_catalog if item["id"] == plugin_id), None)
    if not plugin:
        raise PluginDeploymentServiceError(404, "Plugin not found")
    return plugin


def _runtime_settings(catalog: dict[str, Any]) -> dict[str, Any]:
    runtime = catalog.get("runtime")
    if not isinstance(runtime, dict):
        raise PluginDeploymentServiceError(500, "Plugin runtime metadata is invalid")
    return runtime


def _validate_catalog_choices(catalog: dict[str, Any], body: Any) -> None:
    if body.transport not in catalog["transports"]:
        raise PluginDeploymentServiceError(422, f"Unsupported {catalog['name']} transport")
    if body.profile not in catalog["profiles"]:
        raise PluginDeploymentServiceError(422, f"Unsupported {catalog['name']} performance profile")


def deploy_pair_code_mutation(
    db: ConnectionFactory,
    plugin_id: str,
    body: Any,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    node_stale_after: int,
    paqet_core_tag: str,
    utc_ts: Callable[[], int],
    prepare_realm_settings: Callable[..., dict[str, Any]],
    certificate_for_deployment: Callable[..., dict[str, Any] | None],
    plugin_pair_code: Callable[[dict[str, Any], str], str],
    plugin_job_payload: Callable[[dict[str, Any], str, str], dict[str, Any]],
    encrypt: Callable[[str | None], str | None],
    token_hash: Callable[[str], str],
) -> dict[str, Any]:
    catalog = _catalog_item(plugin_catalog, plugin_id)
    runtime = _runtime_settings(catalog)
    _validate_catalog_choices(catalog, body)
    if not runtime.get("pair_code", False):
        raise PluginDeploymentServiceError(422, f"{catalog['name']} does not support Pair Code mode")
    settings_profile = str(runtime.get("settings_profile") or "standard")
    ports = sorted(set(body.user_ports))
    allow_overlap = bool(runtime.get("allow_tunnel_port_overlap"))
    if any(port < 1 or port > 65535 or (not allow_overlap and port == body.tunnel_port) for port in ports):
        raise PluginDeploymentServiceError(422, "User ports must be unique valid ports and cannot collide with the tunnel port")

    pair_endpoint_side = str(runtime.get("pair_endpoint") or "iran")
    if pair_endpoint_side == "kharej":
        if not body.kharej_endpoint:
            raise PluginDeploymentServiceError(422, f"{catalog['name']} Pair Code requires the KHAREJ endpoint")
        endpoint = str(body.kharej_endpoint)
        if runtime.get("pair_endpoint_ipv4"):
            try:
                if ipaddress.ip_address(endpoint).version != 4:
                    raise ValueError
            except ValueError as exc:
                raise PluginDeploymentServiceError(422, f"{catalog['name']} KHAREJ endpoint must be an IPv4 address") from exc
    else:
        endpoint = str(body.iran_endpoint)

    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    settings: dict[str, Any] = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every,
        "pair_codec": str(runtime.get("pair_codec") or "generic-v1"),
        "certificate_role": str(runtime.get("certificate_role") or ""),
    }
    if settings_profile == "realm":
        settings = prepare_realm_settings(
            {
                **settings,
                "target_host": body.target_host,
                "port_mappings": body.port_mappings,
                "tls_domain": body.tls_domain,
                "tls_insecure": body.tls_insecure,
                "sni": body.sni,
                "alpn": body.alpn,
                "ws_host": body.ws_host,
                "ws_path": body.ws_path,
                "ws_mask": body.ws_mask,
            },
            gateway_host=endpoint, pair_mode=True,
        )
        settings["pair_codec"] = str(runtime.get("pair_codec") or "realm")
        settings["certificate_role"] = str(runtime.get("certificate_role") or "gateway")
    elif settings_profile == "packet":
        settings.update({"pair_id": secrets.token_hex(8), "pair_created": utc_ts(), "core_tag": paqet_core_tag})

    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,role,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        if not iran:
            raise PluginDeploymentServiceError(404, "Iran node was not found")
        if iran["role"] not in {"edge", "hub"}:
            raise PluginDeploymentServiceError(422, "Select an Iran Edge or Hub node for the IRAN side")

        if settings_profile != "realm":
            certificate_side = str(runtime.get("certificate_side") or "none")
            cert = None
            if certificate_side == "iran":
                cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
                if cert:
                    settings.update(cert)
                    if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                        raise PluginDeploymentServiceError(422, "TLS endpoint must match the selected certificate domain")
            elif certificate_side not in {"none", "kharej"}:
                raise PluginDeploymentServiceError(500, "Plugin certificate-side metadata is invalid")
            if not cert and runtime.get("iran_endpoint_must_match_node") and body.iran_endpoint.casefold() != iran["host"].casefold():
                raise PluginDeploymentServiceError(422, "Iran endpoint must match the selected Iran node host/IP")

        if not iran["last_seen"] or iran["last_seen"] < now - node_stale_after:
            raise PluginDeploymentServiceError(409, "The Iran Agent must be online before deployment")
        managed_active = conn.execute(
            "SELECT 1 FROM plugin_deployments WHERE name=? AND (iran_node_id=? OR kharej_node_id=?) AND lifecycle IN ('active','removing','rolling_back','rollback_failed') LIMIT 1",
            (body.name, iran["id"], iran["id"]),
        ).fetchone()
        hybrid_active = conn.execute(
            "SELECT 1 FROM hybrid_deployments WHERE name=? AND iran_node_id=? AND lifecycle IN ('active','removing') LIMIT 1",
            (body.name, iran["id"]),
        ).fetchone()
        if managed_active or hybrid_active:
            raise PluginDeploymentServiceError(409, "A deployment with this name is already active on the selected Iran node")
        pair_code = plugin_pair_code(settings, token)
        iran_job = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (iran["id"], "plugin_deploy", json.dumps(plugin_job_payload(settings, token, catalog["roles"]["iran"])), user_id, now),
        ).lastrowid)
        deployment_id = int(conn.execute(
            "INSERT INTO hybrid_deployments(plugin_id,name,iran_node_id,iran_job_id,iran_endpoint,remote_label,settings,pair_token_enc,pair_code_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (plugin_id, body.name, iran["id"], iran_job, endpoint, body.remote_label, json.dumps(settings), encrypt(token), token_hash(pair_code), user_id, now, now),
        ).lastrowid)
    return {
        "deployment_id": deployment_id,
        "iran_job_id": iran_job,
        "pair_code": pair_code,
        "iran_name": iran["name"],
        "catalog_name": catalog["name"],
    }


def deploy_managed_mutation(
    db: ConnectionFactory,
    plugin_id: str,
    body: Any,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    node_stale_after: int,
    utc_ts: Callable[[], int],
    normalize_ip: Callable[[Any], str],
    prepare_realm_settings: Callable[..., dict[str, Any]],
    certificate_for_deployment: Callable[..., dict[str, Any] | None],
    plugin_job_payload: Callable[[dict[str, Any], str, str], dict[str, Any]],
    encrypt: Callable[[str | None], str | None],
) -> dict[str, Any]:
    catalog = _catalog_item(plugin_catalog, plugin_id)
    runtime = _runtime_settings(catalog)
    _validate_catalog_choices(catalog, body)
    settings_profile = str(runtime.get("settings_profile") or "standard")
    if body.iran_node_id == body.kharej_node_id:
        raise PluginDeploymentServiceError(422, "IRAN and KHAREJ must be different nodes")
    ports = sorted(set(body.user_ports))
    allow_overlap = bool(runtime.get("allow_tunnel_port_overlap"))
    if any(port < 1 or port > 65535 or (not allow_overlap and port == body.tunnel_port) for port in ports):
        raise PluginDeploymentServiceError(422, "User ports must be unique valid ports and cannot collide with the tunnel port")
    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    common: dict[str, Any] = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": body.iran_endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every, "token": token,
        "pair_codec": str(runtime.get("pair_codec") or "generic-v1"),
        "certificate_role": str(runtime.get("certificate_role") or ""),
    }
    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,observed_ip,role,status,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        kharej = conn.execute("SELECT id,name,host,observed_ip,role,status,last_seen FROM nodes WHERE id=?", (body.kharej_node_id,)).fetchone()
        if not iran or not kharej:
            raise PluginDeploymentServiceError(404, "One or both nodes were not found")
        if iran["role"] != "edge" or kharej["role"] != "exit":
            raise PluginDeploymentServiceError(422, "Select an Iran Edge node for IRAN and a Global Exit node for KHAREJ")

        managed_endpoint = str(runtime.get("managed_endpoint") or "iran")
        if managed_endpoint == "kharej_public_ipv4":
            packet_endpoint = normalize_ip(kharej["observed_ip"] or kharej["host"])
            try:
                if ipaddress.ip_address(packet_endpoint).version != 4:
                    raise ValueError
            except ValueError as exc:
                raise PluginDeploymentServiceError(422, f"The KHAREJ node needs a detected public IPv4 for {catalog['name']}") from exc
            common["endpoint"] = packet_endpoint
        elif managed_endpoint == "kharej":
            common["endpoint"] = str(kharej["observed_ip"] or kharej["host"])
        elif managed_endpoint != "iran":
            raise PluginDeploymentServiceError(500, "Plugin managed-endpoint metadata is invalid")

        if settings_profile == "realm":
            cert = certificate_for_deployment(conn, body.certificate_id, kharej["id"], body.transport)
            gateway_host = str(kharej["observed_ip"] or kharej["host"])
            if cert:
                gateway_host = cert["certificate_domain"]
            common = prepare_realm_settings(
                {
                    **common,
                    "target_host": body.target_host,
                    "port_mappings": body.port_mappings,
                    "tls_domain": body.tls_domain,
                    "tls_insecure": body.tls_insecure,
                    "sni": body.sni,
                    "alpn": body.alpn,
                    "ws_host": body.ws_host,
                    "ws_path": body.ws_path,
                    "ws_mask": body.ws_mask,
                },
                gateway_host=gateway_host, certificate=cert, pair_mode=False,
            )
            common["token"] = token
            common["pair_codec"] = str(runtime.get("pair_codec") or "realm")
            common["certificate_role"] = str(runtime.get("certificate_role") or "gateway")
        else:
            certificate_side = str(runtime.get("certificate_side") or "none")
            cert = None
            if certificate_side == "iran":
                cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
                if cert:
                    common.update(cert)
                    if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                        raise PluginDeploymentServiceError(422, "TLS endpoint must match the selected certificate domain")
            elif certificate_side == "kharej":
                cert = certificate_for_deployment(conn, body.certificate_id, kharej["id"], body.transport)
                if cert:
                    common.update(cert)
            elif certificate_side != "none":
                raise PluginDeploymentServiceError(500, "Plugin certificate-side metadata is invalid")
            if not cert and runtime.get("iran_endpoint_must_match_node") and body.iran_endpoint.casefold() != iran["host"].casefold():
                raise PluginDeploymentServiceError(422, "Iran endpoint must match the selected Iran node host/IP")

        if (
            not iran["last_seen"] or iran["last_seen"] < now - node_stale_after
            or not kharej["last_seen"] or kharej["last_seen"] < now - node_stale_after
        ):
            raise PluginDeploymentServiceError(409, "Both nodes must be online before deployment")
        active = conn.execute(
            """SELECT 1 FROM plugin_deployments d
              WHERE d.name=? AND (d.iran_node_id IN (?,?) OR d.kharej_node_id IN (?,?))
                AND d.lifecycle IN ('active','removing','rolling_back','rollback_failed') LIMIT 1""",
            (body.name, iran["id"], kharej["id"], iran["id"], kharej["id"]),
        ).fetchone()
        if active:
            raise PluginDeploymentServiceError(409, "A deployment with this name is already running on one of the selected nodes")
        iran_job = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (iran["id"], "plugin_deploy", json.dumps(plugin_job_payload(common, token, catalog["roles"]["iran"])), user_id, now),
        ).lastrowid)
        kharej_job = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (kharej["id"], "plugin_deploy", json.dumps(plugin_job_payload(common, token, catalog["roles"]["kharej"])), user_id, now),
        ).lastrowid)
        deployment_id = int(conn.execute(
            "INSERT INTO plugin_deployments(plugin_id,name,iran_node_id,kharej_node_id,iran_job_id,kharej_job_id,settings,pair_token_enc,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (plugin_id, body.name, iran["id"], kharej["id"], iran_job, kharej_job, json.dumps({k: v for k, v in common.items() if k != "token"}), encrypt(token), user_id, now),
        ).lastrowid)
    return {
        "deployment_id": deployment_id,
        "jobs": {"iran": iran_job, "kharej": kharej_job},
        "iran_name": iran["name"],
        "kharej_name": kharej["name"],
    }

def recover_pair_code_mutation(
    db: ConnectionFactory,
    deployment_id: int,
    *,
    decrypt: Callable[[str | None], str | None],
    plugin_pair_code: Callable[[dict[str, Any], str], str],
    token_hash: Callable[[str], str],
) -> tuple[sqlite3.Row, str]:
    with db() as conn:
        row = conn.execute("SELECT * FROM hybrid_deployments WHERE id=?", (deployment_id,)).fetchone()
    if not row:
        raise PluginDeploymentServiceError(404, "Pair Code deployment not found")
    if row["lifecycle"] != "active":
        raise PluginDeploymentServiceError(409, "This Pair Code deployment is no longer active")
    token = decrypt(row["pair_token_enc"])
    pair_code = plugin_pair_code(json.loads(row["settings"]), token)
    if not hmac.compare_digest(token_hash(pair_code), row["pair_code_hash"]):
        raise PluginDeploymentServiceError(500, "Pair Code integrity check failed")
    return row, pair_code


def retry_hybrid_mutation(
    db: ConnectionFactory,
    deployment_id: int,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    node_stale_after: int,
    utc_ts: Callable[[], int],
    decrypt: Callable[[str | None], str | None],
) -> tuple[sqlite3.Row, int]:
    now = utc_ts()
    with db() as conn:
        row = conn.execute(
            "SELECT d.*,j.status iran_status FROM hybrid_deployments d LEFT JOIN jobs j ON j.id=d.iran_job_id WHERE d.id=?",
            (deployment_id,),
        ).fetchone()
        if not row:
            raise PluginDeploymentServiceError(404, "Pair Code deployment not found")
        if row["lifecycle"] != "active":
            raise PluginDeploymentServiceError(409, "This Pair Code deployment is no longer active")
        if row["iran_status"] in {"queued", "running"}:
            raise PluginDeploymentServiceError(409, "The Iran deployment is still running")
        online = conn.execute("SELECT 1 FROM nodes WHERE id=? AND last_seen>=?", (row["iran_node_id"], now - node_stale_after)).fetchone()
        if not online:
            raise PluginDeploymentServiceError(409, "The Iran Agent must be online before retry")
        settings = json.loads(row["settings"])
        plugin = next((item for item in plugin_catalog if item["id"] == row["plugin_id"]), None)
        if not plugin:
            raise PluginDeploymentServiceError(409, "Plugin is no longer available")
        payload = {**settings, "token": decrypt(row["pair_token_enc"]), "role": plugin["roles"]["iran"]}
        job_id = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (row["iran_node_id"], "plugin_deploy", json.dumps(payload), user_id, now),
        ).lastrowid)
        conn.execute("UPDATE hybrid_deployments SET iran_job_id=?,updated_at=? WHERE id=?", (job_id, now, deployment_id))
    return row, job_id


def remove_hybrid_mutation(
    db: ConnectionFactory,
    deployment_id: int,
    user_id: int,
    *,
    utc_ts: Callable[[], int],
) -> tuple[sqlite3.Row, int]:
    now = utc_ts()
    with db() as conn:
        row = conn.execute(
            "SELECT d.*,j.status iran_status FROM hybrid_deployments d LEFT JOIN jobs j ON j.id=d.iran_job_id WHERE d.id=?",
            (deployment_id,),
        ).fetchone()
        if not row:
            raise PluginDeploymentServiceError(404, "Pair Code deployment not found")
        if row["lifecycle"] == "removing":
            raise PluginDeploymentServiceError(409, "The Iran side is already being removed")
        if row["iran_status"] in {"queued", "running"}:
            raise PluginDeploymentServiceError(409, "Wait for the current Iran job to finish")
        job_id = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (row["iran_node_id"], "plugin_remove", json.dumps({"plugin_id": row["plugin_id"], "name": row["name"]}), user_id, now),
        ).lastrowid)
        conn.execute("UPDATE hybrid_deployments SET iran_job_id=?,lifecycle='removing',updated_at=? WHERE id=?", (job_id, now, deployment_id))
    return row, job_id


def retry_managed_mutation(
    db: ConnectionFactory,
    deployment_id: int,
    user_id: int,
    *,
    plugin_catalog: list[dict[str, Any]],
    node_stale_after: int,
    utc_ts: Callable[[], int],
    decrypt: Callable[[str | None], str | None],
    plugin_job_payload: Callable[[dict[str, Any], str, str], dict[str, Any]],
) -> tuple[sqlite3.Row, dict[str, int]]:
    now = utc_ts()
    with db() as conn:
        row = conn.execute(
            """SELECT d.*,ji.status iran_status,jk.status kharej_status
              FROM plugin_deployments d LEFT JOIN jobs ji ON ji.id=d.iran_job_id LEFT JOIN jobs jk ON jk.id=d.kharej_job_id
              WHERE d.id=?""",
            (deployment_id,),
        ).fetchone()
        if not row:
            raise PluginDeploymentServiceError(404, "Deployment not found")
        if row["lifecycle"] == "removing" and row["iran_status"] == "completed" and row["kharej_status"] == "completed":
            raise PluginDeploymentServiceError(409, "This deployment was removed; create a new deployment instead")
        if row["lifecycle"] == "rolling_back":
            raise PluginDeploymentServiceError(409, "Automatic rollback is still running")
        if row["iran_status"] in {"queued", "running"} or row["kharej_status"] in {"queued", "running"}:
            raise PluginDeploymentServiceError(409, "Deployment is still running")
        online_nodes = conn.execute(
            "SELECT COUNT(*) count FROM nodes WHERE id IN (?,?) AND last_seen>=?",
            (row["iran_node_id"], row["kharej_node_id"], now - node_stale_after),
        ).fetchone()["count"]
        if online_nodes != 2:
            raise PluginDeploymentServiceError(409, "Both nodes must be online before retry")
        token = decrypt(row["pair_token_enc"])
        if not token:
            raise PluginDeploymentServiceError(409, "This older deployment has no recoverable pair token; create it again")
        settings = json.loads(row["settings"])
        plugin = next((item for item in plugin_catalog if item["id"] == row["plugin_id"]), None)
        if not plugin:
            raise PluginDeploymentServiceError(409, "Plugin is no longer available")
        was_rolled_back = row["lifecycle"] == "rolled_back"
        conn.execute("UPDATE plugin_deployments SET lifecycle='active' WHERE id=?", (deployment_id,))
        new_jobs: dict[str, int] = {}
        for side, node_id, role, old_status in (
            ("iran", row["iran_node_id"], plugin["roles"]["iran"], row["iran_status"]),
            ("kharej", row["kharej_node_id"], plugin["roles"]["kharej"], row["kharej_status"]),
        ):
            if old_status == "completed" and not was_rolled_back:
                continue
            job_id = int(conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (node_id, "plugin_deploy", json.dumps(plugin_job_payload(settings, token, role)), user_id, now),
            ).lastrowid)
            conn.execute(f"UPDATE plugin_deployments SET {side}_job_id=? WHERE id=?", (job_id, deployment_id))
            new_jobs[side] = job_id
    return row, new_jobs


def remove_managed_mutation(
    db: ConnectionFactory,
    deployment_id: int,
    user_id: int,
    *,
    utc_ts: Callable[[], int],
) -> tuple[sqlite3.Row, dict[str, int]]:
    now = utc_ts()
    with db() as conn:
        row = conn.execute("SELECT * FROM plugin_deployments WHERE id=?", (deployment_id,)).fetchone()
        if not row:
            raise PluginDeploymentServiceError(404, "Deployment not found")
        if row["lifecycle"] == "removing":
            raise PluginDeploymentServiceError(409, "Deployment is already being removed or has been removed")
        active_jobs = conn.execute(
            "SELECT COUNT(*) count FROM jobs WHERE id IN (?,?) AND status IN ('queued','running')",
            (row["iran_job_id"], row["kharej_job_id"]),
        ).fetchone()["count"]
        if active_jobs:
            raise PluginDeploymentServiceError(409, "Wait for the current deployment action to finish")
        jobs: dict[str, int] = {}
        for side, node_id in (("iran", row["iran_node_id"]), ("kharej", row["kharej_node_id"])):
            jobs[side] = int(conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (node_id, "plugin_remove", json.dumps({"plugin_id": row["plugin_id"], "name": row["name"]}), user_id, now),
            ).lastrowid)
        conn.execute(
            "UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing' WHERE id=?",
            (jobs["iran"], jobs["kharej"], deployment_id),
        )
    return row, jobs
