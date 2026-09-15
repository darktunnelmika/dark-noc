import json
import secrets
import sqlite3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request


def register_fleet_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    FleetOperationBody = deps["FleetOperationBody"]
    create_fleet_operation_mutation = deps["create_fleet_operation_mutation"]
    cancel_fleet_operation_mutation = deps["cancel_fleet_operation_mutation"]
    CertificateFleetServiceError = deps["CertificateFleetServiceError"]
    utc_ts = deps["utc_ts"]
    audit = deps["audit"]
    get_queue_due_fleet_operations = deps["get_queue_due_fleet_operations"]
    get_provision_node_for_fleet = deps["get_provision_node_for_fleet"]
    get_orchestrate_agent_upgrade = deps["get_orchestrate_agent_upgrade"]
    VERSION = deps["version"]

    @router.get("/api/fleet/operations")
    def list_fleet_operations(limit: int = 100, _: sqlite3.Row = Depends(current_user)):
        limit = min(max(limit, 1), 500)
        with db() as conn:
            operations = conn.execute(
                "SELECT * FROM fleet_operations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for operation in operations:
                item = dict(operation)
                try:
                    payload = json.loads(item.get("payload") or "{}")
                    for key in list(payload):
                        if any(word in key.casefold() for word in ("token", "password", "secret", "community", "private_key")):
                            payload[key] = "[REDACTED]"
                    item["payload"] = payload
                except (TypeError, ValueError):
                    item["payload"] = {}
                rows = conn.execute(
                    """SELECT fleet_operation_items.id,fleet_operation_items.node_id,fleet_operation_items.job_id,
                              fleet_operation_items.status,nodes.name node_name,jobs.output
                       FROM fleet_operation_items JOIN nodes ON nodes.id=fleet_operation_items.node_id
                       LEFT JOIN jobs ON jobs.id=fleet_operation_items.job_id
                       WHERE operation_id=? ORDER BY nodes.name COLLATE NOCASE""",
                    (operation["id"],),
                ).fetchall()
                item["items"] = [dict(row) for row in rows]
                result.append(item)
        return result

    @router.post("/api/fleet/operations", status_code=202)
    def create_fleet_operation(
        body: FleetOperationBody,
        background: BackgroundTasks,
        request: Request,
        user: sqlite3.Row = Depends(current_user),
    ):
        try:
            mutation = create_fleet_operation_mutation(
                db, body, user["id"], version=VERSION, utc_ts=utc_ts,
                queue_due_fleet_operations=get_queue_due_fleet_operations(),
            )
        except CertificateFleetServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

        if mutation["kind"] == "sync_agent":
            for item in mutation["created_items"]:
                background.add_task(
                    get_provision_node_for_fleet(),
                    item["item_id"], item["node_id"], item["enrollment"] or secrets.token_urlsafe(36),
                )
        elif mutation["kind"] == "upgrade_agents":
            background.add_task(
                get_orchestrate_agent_upgrade(),
                mutation["operation_id"],
                mutation["rollout"],
                mutation["batch_size"],
                mutation["pause_seconds"],
                mutation["stop_on_failure"],
            )
        audit(
            user["id"], "fleet_operation_create", body.name,
            f"{body.kind}:{mutation['node_count']} nodes",
            request.client.host if request.client else None,
        )
        return {
            "id": mutation["operation_id"],
            "status": mutation["status"],
            "nodes": mutation["node_count"],
        }

    @router.delete("/api/fleet/operations/{operation_id}")
    def cancel_fleet_operation(operation_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            operation = cancel_fleet_operation_mutation(db, operation_id, utc_ts=utc_ts)
        except CertificateFleetServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(
            user["id"], "fleet_operation_cancel", operation["name"], str(operation_id),
            request.client.host if request.client else None,
        )
        return {"ok": True}

    app.include_router(router)
    return router
