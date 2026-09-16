#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_VERSION = "2.9.44"
NEW_VERSION = "2.9.45"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Hub: replace the in-app plugin catalog with validated manifests.
# ---------------------------------------------------------------------------
app_path = ROOT / "hub/app.py"
app = app_path.read_text(encoding="utf-8")
start = app.index("PLUGIN_CATALOG = [{")
end = app.index("\nclass NodeUpdateBody", start)
registry_loader = '''_PLUGIN_REGISTRY_PATH = Path(__file__).resolve().with_name("plugin_registry.py")
_plugin_registry_spec = _realm_support_importlib_util.spec_from_file_location("dark_noc_plugin_registry", _PLUGIN_REGISTRY_PATH)
if _plugin_registry_spec is None or _plugin_registry_spec.loader is None:
    raise ImportError(f"Could not load Plugin Registry: {_PLUGIN_REGISTRY_PATH}")
_plugin_registry_module = _realm_support_importlib_util.module_from_spec(_plugin_registry_spec)
_plugin_registry_spec.loader.exec_module(_plugin_registry_module)
load_plugin_catalog = _plugin_registry_module.load_plugin_catalog
PLUGIN_CATALOG = load_plugin_catalog(Path(__file__).resolve().with_name("plugins"))
'''
app = app[:start] + registry_loader + app[end:]

# Make Pair Code routing manifest-driven while preserving legacy stored rows.
pair_start = app.index("def plugin_pair_code(")
pair_end = app.index("\ndef plugin_job_payload(", pair_start)
pair_runtime = '''def generic_pair_code(settings: dict[str, Any], token: str) -> str:
    payload = {
        "schema": 1,
        "plugin_id": settings.get("plugin_id"),
        "name": settings.get("name"),
        "endpoint": settings.get("endpoint"),
        "tunnel_port": settings.get("tunnel_port"),
        "user_ports": settings.get("user_ports", []),
        "transport": settings.get("transport"),
        "profile": settings.get("profile"),
        "restart_every": settings.get("restart_every"),
        "token": token,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "DNP1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def plugin_pair_code(settings: dict[str, Any], token: str) -> str:
    plugin_id = str(settings.get("plugin_id") or "")
    legacy_codecs = {
        "dark-realm": "realm",
        "dark-backhaul": "backhaul",
        "dark-ghostpro": "ghostpro",
        "dark-packetpro": "packetpro",
    }
    codec = str(settings.get("pair_codec") or legacy_codecs.get(plugin_id, ""))
    if codec == "realm":
        return realm_pair_code(settings)
    if codec == "backhaul":
        return dark_backhaul_pair_code(settings, token)
    if codec == "ghostpro":
        ports = ",".join(str(port) for port in settings["user_ports"])
        raw = "|".join(("GPC1", settings["endpoint"], str(settings["tunnel_port"]), token, settings["transport"], settings["profile"], settings["restart_every"], ports))
        return "DGP-" + base64.b64encode(raw.encode()).decode()
    if codec == "packetpro":
        mode, conn, mtu = PACKET_PROFILES[settings["profile"]]
        mode_index = {"normal": 0, "fast": 1, "fast2": 2, "fast3": 3}[mode]
        ports = ",".join(f"t{port}" for port in settings["user_ports"])
        raw = "|".join((
            "N1", settings["name"], settings["endpoint"], str(settings["tunnel_port"]), token,
            "1", str(mode_index), str(conn), str(mtu), settings["restart_every"], PAQET_CORE_TAG,
            settings["pair_id"], str(settings["pair_created"]), ports,
        ))
        return "DPP-N1-" + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    if codec == "generic-v1":
        return generic_pair_code(settings, token)
    raise HTTPException(409, "Plugin Pair Code codec is not supported by this Hub build")

'''
app = app[:pair_start] + pair_runtime + app[pair_end + 1:]
old_job_payload = '''def plugin_job_payload(settings: dict[str, Any], token: str, role: str) -> dict[str, Any]:
    payload = {**settings, "token": token, "role": role}
    if settings.get("plugin_id") == "dark-realm":
        if role != "gateway":
            payload.pop("certificate_path", None)
            payload.pop("certificate_key_path", None)
        return payload
    if role == "client":
        payload.pop("certificate_path", None)
        payload.pop("certificate_key_path", None)
    return payload
'''
new_job_payload = '''def plugin_job_payload(settings: dict[str, Any], token: str, role: str) -> dict[str, Any]:
    payload = {**settings, "token": token, "role": role}
    certificate_role = str(settings.get("certificate_role") or "")
    if not certificate_role:
        # Rows created before Plugin Contract v1 did not persist this metadata.
        certificate_role = "gateway" if settings.get("plugin_id") == "dark-realm" else "server"
    if role != certificate_role:
        payload.pop("certificate_path", None)
        payload.pop("certificate_key_path", None)
    return payload
'''
if app.count(old_job_payload) != 1:
    raise RuntimeError("hub/app.py: plugin_job_payload boundary changed")
app = app.replace(old_job_payload, new_job_payload, 1)
app = app.replace(f'VERSION = "{OLD_VERSION}"', f'VERSION = "{NEW_VERSION}"', 1)
app_path.write_text(app, encoding="utf-8")

# ---------------------------------------------------------------------------
# Hub service: convert plugin-ID branching into manifest runtime profiles.
# ---------------------------------------------------------------------------
service_path = ROOT / "hub/plugin_deployment_service.py"
service = service_path.read_text(encoding="utf-8")
svc_start = service.index("def deploy_pair_code_mutation(")
svc_end = service.index("\ndef recover_pair_code_mutation(", svc_start)
new_deploy_functions = r'''def _runtime_settings(catalog: dict[str, Any]) -> dict[str, Any]:
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

'''
service = service[:svc_start] + new_deploy_functions + service[svc_end + 1:]
service_path.write_text(service, encoding="utf-8")

# Pair-Code reveal returns catalog identity so the UI never guesses from prefixes.
replace_once(
    "hub/plugin_deployments_router.py",
    '        return {"deployment_id": deployment_id, "pair_code": pair_code}\n',
    '        plugin = next((item for item in PLUGIN_CATALOG if item["id"] == row["plugin_id"]), None)\n'
    '        return {"deployment_id": deployment_id, "pair_code": pair_code, "plugin_id": row["plugin_id"], "plugin_name": plugin["name"] if plugin else row["plugin_id"]}\n',
)

# ---------------------------------------------------------------------------
# Frontend: all plugin identity/form behavior comes from catalog metadata.
# ---------------------------------------------------------------------------
frontend_path = ROOT / "hub/static/app.js"
frontend = frontend_path.read_text(encoding="utf-8")
front_start = frontend.index("function pluginInventory(")
front_end = frontend.index("function renderPlugins()", front_start)
helpers = '''function pluginSpec(pluginId=state.selectedPlugin){return state.plugins.find(item=>item.id===pluginId)||null;}
function pluginInventory(node, pluginId){const plugin=pluginSpec(pluginId),key=plugin?.ui?.inventory_key||String(pluginId||'').replaceAll('-','_');return node.plugins?.[key];}
function pluginMethod(pluginId){const plugin=pluginSpec(pluginId);return plugin?.ui?.method||plugin?.name||pluginId||'DARK Tunnel';}
function pluginIcon(pluginId){const plugin=pluginSpec(pluginId);return plugin?.ui?.icon||String(plugin?.name||pluginId||'DP').split(/\\s+/).map(part=>part[0]||'').join('').slice(0,2).toUpperCase();}
function pluginFormProfile(pluginId=state.selectedPlugin){return pluginSpec(pluginId)?.ui?.form_profile||'standard';}
function pluginRuntime(pluginId=state.selectedPlugin){return pluginSpec(pluginId)?.runtime||{};}
function tunnelPluginId(tunnel){return state.plugins.find(item=>pluginMethod(item.id)===tunnel.method)?.id||state.plugins[0]?.id||'';}
'''
frontend = frontend[:front_start] + helpers + frontend[front_end:]
frontend = frontend.replace("state.selectedPlugin==='dark-realm'", "pluginFormProfile()==='realm'")
frontend = frontend.replace("state.selectedPlugin==='dark-packetpro'", "pluginFormProfile()==='packet'")
old_cert_node = '''function realmCertificateNodeId(){
  const form=$('#plugin-form');
  return pluginFormProfile()==='realm'?Number(form.elements.kharej_node_id.value):Number(form.elements.iran_node_id.value);
}
'''
new_cert_node = '''function realmCertificateNodeId(){
  const form=$('#plugin-form'),side=pluginRuntime().certificate_side;
  return side==='kharej'?Number(form.elements.kharej_node_id.value):Number(form.elements.iran_node_id.value);
}
'''
if frontend.count(old_cert_node) != 1:
    raise RuntimeError("app.js: certificate node helper changed")
frontend = frontend.replace(old_cert_node, new_cert_node, 1)
old_tls_head = "  const form=$('#plugin-form'),transport=form.elements.transport.value,realm=pluginFormProfile()==='realm',pairMode=form.elements.deployment_mode.value==='pair_code',required=['tls','wss','wssmux','h2','grpc','relay+tls','relay+wss','relay+h2','relay+grpc'].includes(transport),nodeId=realmCertificateNodeId(),box=$('#plugin-tls'),select=$('#plugin-certificate');\n"
new_tls_head = "  const form=$('#plugin-form'),plugin=pluginSpec(),transport=form.elements.transport.value,realm=pluginFormProfile()==='realm',pairMode=form.elements.deployment_mode.value==='pair_code',required=(plugin?.ui?.tls_transports||[]).includes(transport),hideInPair=Boolean(pluginRuntime().pair_hides_certificate),nodeId=realmCertificateNodeId(),box=$('#plugin-tls'),select=$('#plugin-certificate');\n"
if frontend.count(old_tls_head) != 1:
    raise RuntimeError("app.js: TLS selector header changed")
frontend = frontend.replace(old_tls_head, new_tls_head, 1)
frontend = frontend.replace("  box.hidden=!required||realm&&pairMode;select.required=required&&!(realm&&pairMode);\n  if(!required||realm&&pairMode){select.value='';return;}\n", "  box.hidden=!required||hideInPair&&pairMode;select.required=required&&!(hideInPair&&pairMode);\n  if(!required||hideInPair&&pairMode){select.value='';return;}\n", 1)
old_mode_head = "  const form=$('#plugin-form'),managed=$('#plugin-mode').value==='managed',packet=pluginFormProfile()==='packet',realm=pluginFormProfile()==='realm';\n"
new_mode_head = "  const form=$('#plugin-form'),plugin=pluginSpec(),managed=$('#plugin-mode').value==='managed',packet=pluginFormProfile()==='packet',realm=pluginFormProfile()==='realm',pairEndpoint=pluginRuntime().pair_endpoint||'iran';\n"
if frontend.count(old_mode_head) != 1:
    raise RuntimeError("app.js: plugin mode header changed")
frontend = frontend.replace(old_mode_head, new_mode_head, 1)
frontend = frontend.replace("  $('#plugin-kharej-endpoint').hidden=managed||!(packet||realm); $('[name=\"kharej_endpoint\"]',form).required=!managed&&(packet||realm);\n", "  $('#plugin-kharej-endpoint').hidden=managed||pairEndpoint!=='kharej'; $('[name=\"kharej_endpoint\"]',form).required=!managed&&pairEndpoint==='kharej';\n", 1)
frontend = frontend.replace("  $('#plugin-iran-role').textContent=realm?'Edge · exposes public user ports':packet?'Client · exposes local user ports':'Server · accepts tunnel';\n", "  $('#plugin-iran-role').textContent=plugin?.ui?.iran_role_label||'IRAN side';\n", 1)
old_submit_head = "  const pairMode=values.deployment_mode==='pair_code',realm=pluginFormProfile()==='realm';delete values.deployment_mode;\n"
new_submit_head = "  const plugin=pluginSpec(),pairMode=values.deployment_mode==='pair_code',realm=pluginFormProfile()==='realm',pairEndpoint=pluginRuntime().pair_endpoint||'iran';delete values.deployment_mode;\n"
if frontend.count(old_submit_head) != 1:
    raise RuntimeError("app.js: plugin submit header changed")
frontend = frontend.replace(old_submit_head, new_submit_head, 1)
frontend = frontend.replace("  if(pairMode){delete values.kharej_node_id;if(realm&&!values.kharej_endpoint)return showToast('GATEWAY REQUIRED','Enter the Kharej Realm Gateway IP or domain.',true);}", "  if(pairMode){delete values.kharej_node_id;if(pairEndpoint==='kharej'&&!values.kharej_endpoint)return showToast('KHAREJ ENDPOINT REQUIRED',`Enter the KHAREJ endpoint required by ${plugin?.name||'this plugin'}.`,true);}", 1)
frontend_path.write_text(frontend, encoding="utf-8")

# Plugin action routing also uses manifest metadata, including Pair-Code reveal names.
actions_path = ROOT / "hub/static/plugin-actions.js"
actions = actions_path.read_text(encoding="utf-8")
actions = actions.replace("$('#plugin-endpoint').value=state.selectedPlugin==='dark-realm'?(kharej?.host||''):iran.host;", "$('#plugin-endpoint').value=plugin?.ui?.endpoint_default_side==='kharej'?(kharej?.host||''):iran.host;", 1)
old_reveal = "if(pairReveal){api(`/api/hybrid-deployments/${Number(pairReveal.dataset.deploymentId)}/pair-code`,{method:'POST'}).then(result=>{const name=result.pair_code.startsWith('DR1.')?'DARK Realm Pro':result.pair_code.startsWith('DGP-')?'DARK Ghost Pro':result.pair_code.startsWith('DPP-N1-')?'DARK Packet Pro':'DARK Backhaul';$('#output-title').textContent='DARK NOC PAIR CODE';$('#job-output').textContent=`KEEP THIS CODE SECRET\\n\\n${result.pair_code}\\n\\nOn the KHAREJ server run ${name}, select KHAREJ, choose Connect with DARK NOC Pair Code, and paste this code.`;openModal('#output-modal');}).catch(error=>showToast('PAIR CODE FAILED',error.message,true));return true;}"
new_reveal = "if(pairReveal){api(`/api/hybrid-deployments/${Number(pairReveal.dataset.deploymentId)}/pair-code`,{method:'POST'}).then(result=>{const name=result.plugin_name||'matching DARK tunnel plugin';$('#output-title').textContent='DARK NOC PAIR CODE';$('#job-output').textContent=`KEEP THIS CODE SECRET\\n\\n${result.pair_code}\\n\\nOn the KHAREJ server run ${name}, select KHAREJ, choose Connect with DARK NOC Pair Code, and paste this code.`;openModal('#output-modal');}).catch(error=>showToast('PAIR CODE FAILED',error.message,true));return true;}"
if actions.count(old_reveal) != 1:
    raise RuntimeError("plugin-actions.js: Pair Code reveal block changed")
actions = actions.replace(old_reveal, new_reveal, 1)
actions_path.write_text(actions, encoding="utf-8")

# ---------------------------------------------------------------------------
# Agent: centralize privileged plugin dispatch and allow generic managed tunnels.
# ---------------------------------------------------------------------------
agent_path = ROOT / "agent/agent.py"
agent = agent_path.read_text(encoding="utf-8")
agent = agent.replace(f'VERSION = "{OLD_VERSION}"', f'VERSION = "{NEW_VERSION}"', 1)
old_monitored = '''def monitored_tunnels(config: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [
        item for item in config.get("tunnels", [])
        if str(item.get("method", "")) in {"DARK Backhaul", "DARK Ghost Pro", "DARK Packet Pro", "DARK Realm Pro"}
        and (DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or GHOSTPRO_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or PACKETPRO_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))) or REALM_SERVICE_PATTERN.fullmatch(str(item.get("service", ""))))
    ]
'''
new_monitored = '''def monitored_tunnels(config: dict[str, Any]) -> list[dict[str, Any]]:
    managed_services = {str(item) for item in config.get("managed_services", []) if str(item)}
    explicit = [
        item for item in config.get("tunnels", [])
        if str(item.get("service", "")) in managed_services
    ]
'''
if agent.count(old_monitored) != 1:
    raise RuntimeError("agent.py: monitored_tunnels boundary changed")
agent = agent.replace(old_monitored, new_monitored, 1)
execute_marker = "def execute_job(job: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:\n"
execute_index = agent.index(execute_marker)
adapter_runtime = '''def plugin_adapter(plugin_id: str) -> dict[str, Any]:
    """Return the single privileged adapter entry for a native DARK NOC plugin.

    Adding a new native plugin should add one adapter entry here instead of
    branching separately in plugin_deploy/plugin_install/plugin_remove/control.
    """
    adapters: dict[str, dict[str, Any]] = {
        "dark-backhaul": {
            "name": "DARK Backhaul", "service_prefix": "backhaul@",
            "install": _install_darkbh_core,
            "deploy": lambda payload, config: _deploy_dark_backhaul(payload, config),
            "remove": lambda payload, config: _remove_dark_backhaul(payload, config),
        },
        "dark-ghostpro": {
            "name": "DARK Ghost Pro", "service_prefix": "ghostpro@",
            "install": _install_gost_core,
            "deploy": lambda payload, config: _deploy_dark_ghost(payload, config),
            "remove": lambda payload, config: _remove_dark_ghost(payload, config),
        },
        "dark-packetpro": {
            "name": "DARK Packet Pro", "service_prefix": "paqetpro@",
            "install": _install_paqet_core,
            "deploy": lambda payload, config: _deploy_dark_packet(payload, config),
            "remove": lambda payload, config: _remove_dark_packet(payload, config),
        },
        "dark-realm": {
            "name": "DARK Realm Pro", "service_prefix": "dark-realm@",
            "install": install_dark_realm,
            "deploy": lambda payload, config: deploy_dark_realm(payload, config, CONFIG_PATH),
            "remove": lambda payload, config: remove_dark_realm(payload, config, CONFIG_PATH),
        },
    }
    adapter = adapters.get(plugin_id)
    if not adapter:
        raise ValueError("Plugin is not supported by this Agent build")
    return adapter


'''
agent = agent[:execute_index] + adapter_runtime + agent[execute_index:]
block_start = agent.index('    if kind == "plugin_deploy":', agent.index(execute_marker))
block_end = agent.index('    if kind == "speed_test":', block_start)
generic_dispatch = '''    if kind in {"plugin_deploy", "plugin_install", "plugin_remove"}:
        try:
            adapter = plugin_adapter(str(payload.get("plugin_id") or ""))
            if kind == "plugin_deploy":
                return "completed", str(adapter["deploy"](payload, config))
            if kind == "plugin_install":
                return "completed", f"{adapter['name']} core ready: {adapter['install']()}"
            return "completed", str(adapter["remove"](payload, config))
        except Exception as exc:
            action = "deployment" if kind == "plugin_deploy" else "installation" if kind == "plugin_install" else "removal"
            return "failed", f"Plugin {action} failed: {exc}"
    if kind == "tunnel_control":
        name, action = str(payload.get("name", "")), str(payload.get("action", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", name) or action not in {"start", "stop", "restart"}:
            return "failed", "Invalid tunnel control request"
        try:
            adapter = plugin_adapter(str(payload.get("plugin_id") or "dark-backhaul"))
        except Exception as exc:
            return "failed", str(exc)
        service = f"{adapter['service_prefix']}{name}.service"
        if not restart_allowed(service, config):
            return "failed", "Tunnel is not managed by DARK NOC"
        code, output = run(["systemctl", action, service], timeout=30)
        return ("completed" if code == 0 else "failed"), output or f"{service} {action} completed"
'''
agent = agent[:block_start] + generic_dispatch + agent[block_end:]
agent_path.write_text(agent, encoding="utf-8")

# ---------------------------------------------------------------------------
# Permanent test/release wiring and version metadata.
# ---------------------------------------------------------------------------
replace_once(
    ".github/workflows/ci.yml",
    "          python tests/backend_plugin_deployment_service_contract.py\n",
    "          python tests/backend_plugin_deployment_service_contract.py\n          python tests/plugin_registry_contract.py\n",
)
replace_once(
    ".github/workflows/release.yml",
    "          python tests/installer_contracts.py\n          python tests/dependency_lock_contract.py\n",
    "          python tests/installer_contracts.py\n          python tests/plugin_registry_contract.py\n          python tests/dependency_lock_contract.py\n",
)

# Tests which assert the active release version move with the release.
for test in (ROOT / "tests").glob("*.py"):
    value = test.read_text(encoding="utf-8")
    if OLD_VERSION in value:
        test.write_text(value.replace(OLD_VERSION, NEW_VERSION), encoding="utf-8")

# User-facing version strings.
for path, old, new in (
    ("darknoc", f'VERSION="{OLD_VERSION}"', f'VERSION="{NEW_VERSION}"'),
    ("install-hub.sh", f"Nightfall Command v{OLD_VERSION}", f"Nightfall Command v{NEW_VERSION}"),
):
    replace_once(path, old, new)

changelog = read("CHANGELOG.md")
changelog_entry = f'''## v{NEW_VERSION}\n- Add Plugin Contract v1 with validated per-plugin manifests and a fail-closed Hub registry.\n- Move plugin card identity, form/TLS behavior and deployment semantics to catalog metadata instead of plugin-ID branches.\n- Add a generic `DNP1.` Pair-Code codec for future standard plugins while preserving existing DBH/DGP/DPP/DR1 formats.\n- Centralize Agent install/deploy/remove/control dispatch behind one adapter registry and allow any explicitly managed service into generic tunnel monitoring.\n- Add permanent Plugin Registry contract coverage and a plugin-authoring guide.\n\n'''
if changelog.startswith(f"## v{NEW_VERSION}"):
    raise RuntimeError("CHANGELOG already contains target version")
write("CHANGELOG.md", changelog_entry + changelog)

release_notes = read("RELEASE_NOTES.md")
release_entry = f'''# DARK NOC v{NEW_VERSION} — Plugin Contract v1\n\n- Replace the hardcoded Hub plugin catalog with validated JSON manifests under `hub/plugins/`.\n- Make plugin icon, method label, inventory key, form profile, TLS transports, endpoint behavior, Pair-Code behavior and certificate ownership declarative.\n- Refactor plugin deployment transactions to consume manifest runtime profiles instead of branching on concrete plugin IDs.\n- Add `DNP1.` generic Pair Code support for future standard plugins while retaining every existing Pair Code format for compatibility.\n- Return plugin identity with Pair-Code recovery so the UI no longer guesses plugin type from secret prefixes.\n- Centralize privileged Agent plugin job dispatch in `plugin_adapter()` and remove the old unknown-plugin fallback to Backhaul.\n- Let new adapters participate in generic monitoring automatically when their service is explicitly recorded in `managed_services`.\n- Add fail-closed manifest validation, duplicate protection, CI regression coverage and `docs/plugin-authoring.md`.\n\n## فارسی\n\nبخش پلاگین به Plugin Contract v1 منتقل شد. از این نسخه مشخصات هر پلاگین داخل Manifest مستقل قرار می‌گیرد و Hub/UI برای نام، آیکون، ترنسپورت، پروفایل، TLS، Pair Code و سمت Endpoint به شرط‌های پراکنده وابسته نیستند. برای پلاگین استاندارد جدید، بخش Hub با اضافه‌کردن Manifest قابل توسعه است و در Agent فقط Adapter همان پلاگین در Registry مرکزی اضافه می‌شود؛ دیگر نیازی به دستکاری Routeها و فرم‌های اصلی پنل نیست.\n\n---\n\n'''
if release_notes.startswith(f"# DARK NOC v{NEW_VERSION}"):
    raise RuntimeError("RELEASE_NOTES already contains target version")
write("RELEASE_NOTES.md", release_entry + release_notes)

# Contract assertions before the workflow runs the real test suite.
assert 'PLUGIN_CATALOG = [{' not in read("hub/app.py")
assert "load_plugin_catalog" in read("hub/app.py")
assert "state.selectedPlugin==='dark-realm'" not in read("hub/static/app.js")
assert "state.selectedPlugin==='dark-packetpro'" not in read("hub/static/app.js")
assert "state.selectedPlugin==='dark-realm'" not in read("hub/static/plugin-actions.js")
assert 'if payload.get("plugin_id") == "dark-realm"' not in read("agent/agent.py")[read("agent/agent.py").index(execute_marker):]

print(f"Prepared DARK NOC v{NEW_VERSION} Plugin Contract v1")
