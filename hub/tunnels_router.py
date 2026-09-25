import json
import sqlite3
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request


def register_tunnels_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    fetch_tunnel_inventory = deps["fetch_tunnel_inventory"]
    utc_ts = deps["utc_ts"]
    normalize_ip = deps["normalize_ip"]
    tunnel_topology_side = deps["tunnel_topology_side"]
    tunnel_health_score = deps["tunnel_health_score"]
    fetch_tunnel_operation_rows = deps["fetch_tunnel_operation_rows"]
    public_job = deps["public_job"]
    TunnelActionBody = deps["TunnelActionBody"]
    queue_tunnel_action_mutation = deps["queue_tunnel_action_mutation"]
    NodeTunnelServiceError = deps["NodeTunnelServiceError"]
    audit = deps["audit"]
    TunnelReconfigureBody = deps["TunnelReconfigureBody"]
    reconfigure_tunnel_mutation = deps["reconfigure_tunnel_mutation"]
    PLUGIN_CATALOG = deps["PLUGIN_CATALOG"]
    certificate_for_deployment = deps["certificate_for_deployment"]
    decrypt = deps["decrypt"]
    plugin_job_payload = deps["plugin_job_payload"]
    plugin_pair_code = deps["plugin_pair_code"]
    token_hash = deps["token_hash"]
    remove_tunnel_mutation = deps["remove_tunnel_mutation"]
    NODE_STALE_AFTER = deps["node_stale_after"]
    TUNNEL_SAMPLE_RETENTION_DAYS = deps["tunnel_sample_retention_days"]

    @router.get("/api/tunnels")
    def list_tunnels(_: sqlite3.Row = Depends(current_user)):
        rows, nodes, deployments = fetch_tunnel_inventory(db)
        now = utc_ts()
        node_index = {row["id"]: dict(row) for row in nodes}
        deployment_index: dict[tuple[str, int], dict[str, Any]] = {}
        for row in deployments:
            deployment = dict(row)
            deployment_index.setdefault((row["name"], row["iran_node_id"]), deployment)
            deployment_index.setdefault((row["name"], row["kharej_node_id"]), deployment)
        local_endpoint_values = {"", "127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}

        def remote_values(values: Any) -> list[str]:
            if not isinstance(values, list):
                return []
            result_values: list[str] = []
            for value in values:
                normalized = normalize_ip(value)
                if not normalized or normalized.casefold() in local_endpoint_values:
                    continue
                if normalized not in result_values:
                    result_values.append(normalized)
                if len(result_values) >= 32:
                    break
            return result_values

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                details = json.loads(item.get("details") or "{}")
            except (TypeError, ValueError):
                details = {}
            item["tunnel_role"] = str(details.get("role") or "")
            item["topology_side"] = tunnel_topology_side(item["node_role"], str(item.get("method") or ""), item["tunnel_role"])
            item["target_host"] = normalize_ip(details.get("target_host"))
            item["target_port"] = details.get("target_port")
            item["user_ports"] = details.get("user_ports") if isinstance(details.get("user_ports"), list) else []
            item["transport"] = str(details.get("transport") or "unknown")
            item["profile"] = str(details.get("profile") or "unknown")
            item["restart_every"] = str(details.get("restart_every") or "off")
            item["checks"] = details.get("checks") if isinstance(details.get("checks"), dict) else {}
            try:
                item["service_uptime"] = max(0, int(details.get("service_uptime") or 0))
            except (TypeError, ValueError):
                item["service_uptime"] = 0
            item["peer_ips"] = remote_values(details.get("peer_ips"))
            item["last_known_peer_ips"] = remote_values(details.get("last_known_peer_ips"))
            item["node_agent_online"] = bool(item["node_last_seen"] and item["node_last_seen"] >= now - NODE_STALE_AFTER)
            if not item["node_agent_online"]:
                item["status"] = "stale"
            item["traffic_bps"] = float(item.get("rx_bps") or 0) + float(item.get("tx_bps") or 0)
            item["health_score"] = tunnel_health_score(item)
            item.pop("details", None)
            result.append(item)

        for item in result:
            peer_id: int | None = None
            deployment = deployment_index.get((item["name"], item["node_id"]))
            if deployment:
                peer_id = (
                    deployment["kharej_node_id"]
                    if item["node_id"] == deployment["iran_node_id"]
                    else deployment["iran_node_id"]
                )

            remote_addresses = set(item["peer_ips"]) | set(item["last_known_peer_ips"])
            if item["target_host"] and item["target_host"].casefold() not in local_endpoint_values:
                remote_addresses.add(item["target_host"])
            if not peer_id and remote_addresses:
                matching_nodes = [
                    candidate for candidate in node_index.values()
                    if candidate["id"] != item["node_id"]
                    and remote_addresses.intersection({
                        normalize_ip(candidate["host"]),
                        normalize_ip(candidate.get("observed_ip")),
                    })
                ]
                if len(matching_nodes) == 1:
                    peer_id = matching_nodes[0]["id"]

            if not peer_id:
                opposite_candidates = [
                    candidate for candidate in result
                    if candidate["node_id"] != item["node_id"]
                    and candidate["name"] == item["name"]
                    and candidate.get("method") == item.get("method")
                    and (
                        item["topology_side"] == "unknown"
                        or candidate["topology_side"] == "unknown"
                        or candidate["topology_side"] != item["topology_side"]
                    )
                ]
                if len(opposite_candidates) == 1:
                    peer_id = opposite_candidates[0]["node_id"]

            peer = node_index.get(peer_id) if peer_id else None
            item["peer_node_id"] = peer_id
            item["peer_name"] = peer["name"] if peer else None
            if peer:
                item["peer_host"] = peer.get("observed_ip") or peer["host"]
                item["peer_host_source"] = "node"
            elif item["peer_ips"]:
                item["peer_host"] = item["peer_ips"][0]
                item["peer_host_source"] = "live"
            elif item["last_known_peer_ips"]:
                item["peer_host"] = item["last_known_peer_ips"][0]
                item["peer_host_source"] = "last_known"
            elif item["target_host"] and item["target_host"].casefold() not in local_endpoint_values:
                item["peer_host"] = item["target_host"]
                item["peer_host_source"] = "target"
            else:
                item["peer_host"] = None
                item["peer_host_source"] = None
            item["peer_host_last_known"] = item["peer_host_source"] == "last_known"
            item["peer_role"] = peer["role"] if peer else None
            item["peer_agent_online"] = bool(peer and peer.get("last_seen") and peer["last_seen"] >= now - NODE_STALE_AFTER)
            item["peer_ssh_configured"] = bool(peer and peer.get("ssh_configured"))
            if peer_id:
                first, second = sorted((int(item["node_id"]), int(peer_id)))
                item["topology_key"] = f"{item.get('method') or 'DARK'}|{item['name']}|{first}|{second}"
            else:
                item["topology_key"] = f"{item.get('method') or 'DARK'}|{item['name']}|{item['node_id']}|{item.get('service') or item['id']}"
            item["peer_resolved"] = bool(peer_id)
        return result

    @router.get("/api/tunnels/{tunnel_id}/operations")
    def tunnel_operations(
        tunnel_id: int,
        hours: int = 24,
        user: sqlite3.Row = Depends(current_user),
    ):
        hours = min(max(hours, 1), TUNNEL_SAMPLE_RETENTION_DAYS * 24)
        inventory = list_tunnels(user)
        tunnel = next((item for item in inventory if int(item["id"]) == tunnel_id), None)
        if not tunnel:
            raise HTTPException(404, "Tunnel not found")
        peer = next(
            (
                item for item in inventory
                if item["name"] == tunnel["name"]
                and int(item["node_id"]) == int(tunnel.get("peer_node_id") or 0)
            ),
            None,
        )
        cutoff = utc_ts() - hours * 3600
        node_ids = [int(tunnel["node_id"])]
        if tunnel.get("peer_node_id"):
            node_ids.append(int(tunnel["peer_node_id"]))
        samples, job_rows, managed, hybrid = fetch_tunnel_operation_rows(
            db,
            tunnel_id=tunnel_id,
            cutoff=cutoff,
            node_ids=node_ids,
            tunnel_name=tunnel["name"],
        )
        recent_jobs: list[dict[str, Any]] = []
        for row in job_rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if (
                payload.get("name") == tunnel["name"]
                or payload.get("service") == tunnel.get("service")
                or row["kind"] == "tunnel_test"
            ):
                recent_jobs.append(public_job(row))
            if len(recent_jobs) >= 20:
                break
        deployment = managed or hybrid
        deployment_info = None
        if deployment:
            try:
                settings = json.loads(deployment["settings"] or "{}")
            except (TypeError, ValueError):
                settings = {}
            deployment_info = {
                "id": deployment["id"],
                "plugin_id": deployment["plugin_id"],
                "mode": "managed" if managed else "pair_code",
                "lifecycle": deployment["lifecycle"],
                "created_at": deployment["created_at"],
                "endpoint": settings.get("endpoint") or settings.get("iran_endpoint"),
                "tunnel_port": settings.get("tunnel_port"),
                "certificate_domain": settings.get("certificate_domain"),
                "remote_label": hybrid["remote_label"] if hybrid else None,
            }
        sample_list = [dict(row) for row in samples]
        return {
            "tunnel": tunnel,
            "peer": peer,
            "deployment": deployment_info,
            "samples": sample_list,
            "recent_jobs": recent_jobs,
            "window_hours": hours,
            "sample_count": len(sample_list),
        }

    @router.post("/api/tunnels/{tunnel_id}/action", status_code=202)
    def tunnel_action(tunnel_id: int, body: TunnelActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            tunnel, job_id = queue_tunnel_action_mutation(
                db, tunnel_id, body.action, user["id"], utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], f"tunnel_{body.action}", tunnel["name"], str(tunnel_id), request.client.host if request.client else None)
        return {"job_id": job_id, "status": "queued"}

    @router.put("/api/tunnels/{tunnel_id}", status_code=202)
    def reconfigure_tunnel(tunnel_id: int, body: TunnelReconfigureBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            mutation = reconfigure_tunnel_mutation(
                db, tunnel_id, body, user["id"], plugin_catalog=PLUGIN_CATALOG,
                certificate_for_deployment=certificate_for_deployment, decrypt=decrypt,
                plugin_job_payload=plugin_job_payload, plugin_pair_code=plugin_pair_code,
                token_hash=token_hash, utc_ts=utc_ts,
            )
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(
            user["id"], "tunnel_reconfigure", mutation["tunnel_name"],
            json.dumps({"ports": mutation["ports"], "transport": body.transport, "profile": body.profile}),
            request.client.host if request.client else None,
        )
        result: dict[str, Any] = {
            "status": "queued", "jobs": mutation["jobs"],
            "mode": "managed" if mutation["managed"] else "pair_code",
        }
        if mutation["hybrid"]:
            result["pair_code"] = mutation["pair_code"]
            result["foreign_action_required"] = True
        return result

    @router.delete("/api/tunnels/{tunnel_id}", status_code=202)
    def remove_tunnel_from_manager(tunnel_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            mutation = remove_tunnel_mutation(db, tunnel_id, user["id"], utc_ts())
        except NodeTunnelServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(
            user["id"], "tunnel_remove", mutation["tunnel_name"],
            "managed pair" if mutation["managed"] else "selected node",
            request.client.host if request.client else None,
        )
        return {"status": "queued", "jobs": mutation["jobs"], "foreign_action_required": mutation["hybrid"]}

    app.include_router(router)
    return router
