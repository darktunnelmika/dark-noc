from pathlib import Path

app_path = Path("hub/app.py")
text = app_path.read_text()

service = r'''from __future__ import annotations

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
    if body.transport not in catalog["transports"]:
        raise PluginDeploymentServiceError(422, f"Unsupported {catalog['name']} transport")
    ports = sorted(set(body.user_ports))
    if any(port < 1 or port > 65535 or (plugin_id != "dark-realm" and port == body.tunnel_port) for port in ports):
        raise PluginDeploymentServiceError(422, "User ports must be unique valid ports and cannot collide with the tunnel port")
    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    if plugin_id in {"dark-packetpro", "dark-realm"} and not body.kharej_endpoint:
        raise PluginDeploymentServiceError(422, f"{catalog['name']} Pair Code requires the KHAREJ endpoint")
    if plugin_id == "dark-packetpro":
        try:
            if ipaddress.ip_address(body.kharej_endpoint).version != 4:
                raise ValueError
        except ValueError as exc:
            raise PluginDeploymentServiceError(422, "DARK Packet Pro KHAREJ endpoint must be an IPv4 address") from exc
    endpoint = body.kharej_endpoint if plugin_id in {"dark-packetpro", "dark-realm"} else body.iran_endpoint
    settings: dict[str, Any] = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every,
    }
    if plugin_id == "dark-realm":
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
            gateway_host=str(body.kharej_endpoint), pair_mode=True,
        )
    elif plugin_id == "dark-packetpro":
        settings.update({"pair_id": secrets.token_hex(8), "pair_created": utc_ts(), "core_tag": paqet_core_tag})
    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,role,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        if not iran:
            raise PluginDeploymentServiceError(404, "Iran node was not found")
        if iran["role"] not in {"edge", "hub"}:
            raise PluginDeploymentServiceError(422, "Select an Iran Edge or Hub node for the IRAN side")
        if plugin_id != "dark-realm":
            cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
            if cert:
                settings.update(cert)
                if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                    raise PluginDeploymentServiceError(422, "TLS endpoint must match the selected certificate domain")
            elif plugin_id != "dark-packetpro" and body.iran_endpoint.casefold() != iran["host"].casefold():
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
    if body.transport not in catalog["transports"]:
        raise PluginDeploymentServiceError(422, f"Unsupported {catalog['name']} transport")
    if body.iran_node_id == body.kharej_node_id:
        raise PluginDeploymentServiceError(422, "IRAN and KHAREJ must be different nodes")
    ports = sorted(set(body.user_ports))
    if any(port < 1 or port > 65535 or (plugin_id != "dark-realm" and port == body.tunnel_port) for port in ports):
        raise PluginDeploymentServiceError(422, "User ports must be unique valid ports and cannot collide with the tunnel port")
    token = secrets.token_urlsafe(24).replace("-", "A").replace("_", "B")
    common: dict[str, Any] = {
        "plugin_id": plugin_id, "name": body.name, "endpoint": body.iran_endpoint,
        "tunnel_port": body.tunnel_port, "user_ports": ports, "transport": body.transport,
        "profile": body.profile, "restart_every": body.restart_every, "token": token,
    }
    now = utc_ts()
    with db() as conn:
        iran = conn.execute("SELECT id,name,host,observed_ip,role,status,last_seen FROM nodes WHERE id=?", (body.iran_node_id,)).fetchone()
        kharej = conn.execute("SELECT id,name,host,observed_ip,role,status,last_seen FROM nodes WHERE id=?", (body.kharej_node_id,)).fetchone()
        if not iran or not kharej:
            raise PluginDeploymentServiceError(404, "One or both nodes were not found")
        if iran["role"] != "edge" or kharej["role"] != "exit":
            raise PluginDeploymentServiceError(422, "Select an Iran Edge node for IRAN and a Global Exit node for KHAREJ")
        if plugin_id == "dark-packetpro":
            packet_endpoint = normalize_ip(kharej["observed_ip"] or kharej["host"])
            try:
                if ipaddress.ip_address(packet_endpoint).version != 4:
                    raise ValueError
            except ValueError as exc:
                raise PluginDeploymentServiceError(422, "The KHAREJ node needs a detected public IPv4 for Packet Pro") from exc
            common["endpoint"] = packet_endpoint
        if plugin_id == "dark-realm":
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
        else:
            cert = certificate_for_deployment(conn, body.certificate_id, iran["id"], body.transport)
            if cert:
                common.update(cert)
                if body.iran_endpoint.casefold() != cert["certificate_domain"].casefold():
                    raise PluginDeploymentServiceError(422, "TLS endpoint must match the selected certificate domain")
            elif plugin_id != "dark-packetpro" and body.iran_endpoint.casefold() != iran["host"].casefold():
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
'''
Path("hub/plugin_deployment_service.py").write_text(service)


def replace_route(start_marker: str, end_marker: str, replacement: str) -> None:
    global text
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    text = text[:start] + replacement.rstrip() + "\n\n\n" + text[end:]

pair_wrapper = '''@app.post("/api/plugins/{plugin_id}/pair-code", status_code=202)
def deploy_plugin_pair_code(plugin_id: str, body: PairCodeDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        mutation = deploy_pair_code_mutation(
            db, plugin_id, body, user["id"], plugin_catalog=PLUGIN_CATALOG,
            node_stale_after=NODE_STALE_AFTER, paqet_core_tag=PAQET_CORE_TAG, utc_ts=utc_ts,
            prepare_realm_settings=prepare_realm_settings,
            certificate_for_deployment=certificate_for_deployment,
            plugin_pair_code=plugin_pair_code, plugin_job_payload=plugin_job_payload,
            encrypt=encrypt, token_hash=token_hash,
        )
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_pair_create", body.name, f"{mutation['iran_name']} -> pair-code:{body.remote_label}", request.client.host if request.client else None)
    return {
        "deployment_id": mutation["deployment_id"], "mode": "pair_code", "status": "queued",
        "iran_job_id": mutation["iran_job_id"], "pair_code": mutation["pair_code"],
        "instructions": ["Wait until the IRAN side shows READY.", f"Run {mutation['catalog_name']} on the foreign server.", "Select KHAREJ, then choose Connect with DARK NOC Pair Code.", "Paste the Pair Code exactly as shown."],
    }'''
replace_route('@app.post("/api/plugins/{plugin_id}/pair-code", status_code=202)', '@app.post("/api/plugins/{plugin_id}/deploy", status_code=202)', pair_wrapper)

managed_wrapper = '''@app.post("/api/plugins/{plugin_id}/deploy", status_code=202)
def deploy_plugin(plugin_id: str, body: PluginDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        mutation = deploy_managed_mutation(
            db, plugin_id, body, user["id"], plugin_catalog=PLUGIN_CATALOG,
            node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, normalize_ip=normalize_ip,
            prepare_realm_settings=prepare_realm_settings,
            certificate_for_deployment=certificate_for_deployment,
            plugin_job_payload=plugin_job_payload, encrypt=encrypt,
        )
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_deploy", body.name, f"{plugin_id}: {mutation['iran_name']} -> {mutation['kharej_name']}", request.client.host if request.client else None)
    return {"deployment_id": mutation["deployment_id"], "status": "queued", "jobs": mutation["jobs"]}'''
replace_route('@app.post("/api/plugins/{plugin_id}/deploy", status_code=202)', '@app.get("/api/plugin-deployments")', managed_wrapper)

recover_wrapper = '''@app.post("/api/hybrid-deployments/{deployment_id}/pair-code")
def recover_hybrid_pair_code(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row, pair_code = recover_pair_code_mutation(
            db, deployment_id, decrypt=decrypt, plugin_pair_code=plugin_pair_code, token_hash=token_hash,
        )
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_pair_reveal", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "pair_code": pair_code}'''
replace_route('@app.post("/api/hybrid-deployments/{deployment_id}/pair-code")', '@app.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)', recover_wrapper)

retry_hybrid_wrapper = '''@app.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)
def retry_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row, job_id = retry_hybrid_mutation(
            db, deployment_id, user["id"], plugin_catalog=PLUGIN_CATALOG,
            node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, decrypt=decrypt,
        )
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_pair_retry", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id}'''
replace_route('@app.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)', '@app.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)', retry_hybrid_wrapper)

remove_hybrid_wrapper = '''@app.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)
def remove_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row, job_id = remove_hybrid_mutation(db, deployment_id, user["id"], utc_ts=utc_ts)
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_pair_remove", row["name"], "Iran side only; foreign side is script-managed", request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id, "foreign_action_required": True}'''
replace_route('@app.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)', '@app.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)', remove_hybrid_wrapper)

retry_managed_wrapper = '''@app.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)
def retry_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row, new_jobs = retry_managed_mutation(
            db, deployment_id, user["id"], plugin_catalog=PLUGIN_CATALOG,
            node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, decrypt=decrypt,
            plugin_job_payload=plugin_job_payload,
        )
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_retry", str(deployment_id), json.dumps(new_jobs), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "jobs": new_jobs}'''
replace_route('@app.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)', '@app.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)', retry_managed_wrapper)

remove_managed_wrapper = '''@app.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)
def remove_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row, jobs = remove_managed_mutation(db, deployment_id, user["id"], utc_ts=utc_ts)
    except PluginDeploymentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "plugin_remove", row["name"], str(deployment_id), request.client.host if request.client else None)
    return {"deployment_id": deployment_id, "status": "queued", "jobs": jobs}'''
replace_route('@app.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)', '@app.get("/api/monitors")', remove_managed_wrapper)

anchor = "del _node_tunnel_service_name\n"
assert anchor in text
loader = r'''

_PLUGIN_DEPLOYMENT_SERVICE_PATH = Path(__file__).resolve().with_name('plugin_deployment_service.py')
_plugin_deployment_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_plugin_deployment_service', _PLUGIN_DEPLOYMENT_SERVICE_PATH)
if _plugin_deployment_service_spec is None or _plugin_deployment_service_spec.loader is None:
    raise ImportError(f'Could not load Plugin Deployment service: {_PLUGIN_DEPLOYMENT_SERVICE_PATH}')
_plugin_deployment_service_module = _realm_support_importlib_util.module_from_spec(_plugin_deployment_service_spec)
_plugin_deployment_service_spec.loader.exec_module(_plugin_deployment_service_module)
for _plugin_deployment_service_name in [
    'PluginDeploymentServiceError', 'deploy_pair_code_mutation', 'deploy_managed_mutation',
    'recover_pair_code_mutation', 'retry_hybrid_mutation', 'remove_hybrid_mutation',
    'retry_managed_mutation', 'remove_managed_mutation',
]:
    globals()[_plugin_deployment_service_name] = getattr(_plugin_deployment_service_module, _plugin_deployment_service_name)
del _plugin_deployment_service_name
'''
text = text.replace(anchor, anchor + loader, 1)
app_path.write_text(text)

contract = r'''from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
service = (root / 'hub/plugin_deployment_service.py').read_text()
assert "with_name('plugin_deployment_service.py')" in app
assert '_plugin_deployment_service_spec.loader.exec_module' in app
for marker in [
    'def deploy_pair_code_mutation(', 'def deploy_managed_mutation(',
    'def recover_pair_code_mutation(', 'def retry_hybrid_mutation(',
    'def remove_hybrid_mutation(', 'def retry_managed_mutation(',
    'def remove_managed_mutation(',
]:
    assert marker in service
for route in [
    '@app.post("/api/plugins/{plugin_id}/pair-code", status_code=202)',
    '@app.post("/api/plugins/{plugin_id}/deploy", status_code=202)',
    '@app.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)',
    '@app.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)',
    '@app.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)',
    '@app.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)',
]:
    assert route in app
for call in [
    'deploy_pair_code_mutation(', 'deploy_managed_mutation(', 'recover_pair_code_mutation(',
    'retry_hybrid_mutation(', 'remove_hybrid_mutation(', 'retry_managed_mutation(', 'remove_managed_mutation(',
]:
    assert call in app
for sql in [
    'INSERT INTO hybrid_deployments(',
    'INSERT INTO plugin_deployments(',
    "UPDATE hybrid_deployments SET iran_job_id=?,updated_at=?",
    "UPDATE plugin_deployments SET lifecycle='active'",
    "UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing'",
]:
    assert sql in service
    assert sql not in app
assert '@app.get("/api/plugin-deployments")' in app
assert 'VERSION = "2.9.26"' in app
print('Plugin Deployment service boundary passed')
'''
Path("tests/backend_plugin_deployment_service_contract.py").write_text(contract)

identity_paths = [
    Path("hub/app.py"), Path("agent/agent.py"), Path("darknoc"),
    Path("install-hub.sh"), Path("install-node.sh"), Path("upgrade.sh"),
    Path("hub/static/index.html"),
]
identity_paths.extend(Path("tests").glob("*.py"))
for path in identity_paths:
    value = path.read_text()
    if "2.9.25" in value:
        path.write_text(value.replace("2.9.25", "2.9.26"))

notes = '''# DARK NOC v2.9.26 — Plugin Deployment Service Layer

- Continue the backend write/service layer by extracting Managed and Pair-Code plugin deployment creation plus deployment recovery/retry/remove transactions from `hub/app.py` into `hub/plugin_deployment_service.py`.
- Keep FastAPI routes, deployment list rendering, audit logging and response shaping in `hub/app.py`.
- Preserve transaction boundaries, Pair Code compatibility, Realm/Packet/Backhaul/Ghost behavior, Agent behavior and frontend visuals.
- Add permanent Plugin Deployment service-boundary regression coverage.

## فارسی

عملیات ساخت، Retry و Remove دیپلوی‌های Managed و Pair-Code از `hub/app.py` به `hub/plugin_deployment_service.py` منتقل شدند؛ Routeها، Audit و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

'''
p = Path("RELEASE_NOTES.md")
p.write_text(notes + p.read_text())

p = Path("CHANGELOG.md")
old = p.read_text()
first, sep, rest = old.partition("\n")
p.write_text(
    first + sep + "\n## 2.9.26 — Plugin Deployment service layer\n\n"
    "- Extract Managed/Pair-Code deployment create, recover, retry and remove transactions into hub/plugin_deployment_service.py.\n"
    "- Preserve FastAPI route orchestration, audit behavior and frontend/API contracts.\n"
    "- Add service-boundary regression coverage.\n\n" + rest
)
