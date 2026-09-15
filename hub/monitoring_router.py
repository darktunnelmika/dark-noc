import json
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request


def register_monitoring_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    MonitorBody = deps["MonitorBody"]
    fetch_monitor_inventory = deps["fetch_monitor_inventory"]
    public_monitor = deps["public_monitor"]
    create_monitor_mutation = deps["create_monitor_mutation"]
    update_monitor_mutation = deps["update_monitor_mutation"]
    delete_monitor_mutation = deps["delete_monitor_mutation"]
    queue_monitor_run_mutation = deps["queue_monitor_run_mutation"]
    MonitorIncidentServiceError = deps["MonitorIncidentServiceError"]
    monitor_values = deps["monitor_values"]
    resolve_incident = deps["resolve_incident"]
    utc_ts = deps["utc_ts"]
    decrypt = deps["decrypt"]
    audit = deps["audit"]
    fetch_monitor_result_rows = deps["fetch_monitor_result_rows"]
    NODE_STALE_AFTER = deps["node_stale_after"]

    @router.get("/api/monitors")
    def list_monitors(_: sqlite3.Row = Depends(current_user)):
        rows = fetch_monitor_inventory(db)
        return [public_monitor(row) for row in rows]


    @router.post("/api/monitors", status_code=201)
    def create_monitor(body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        now = utc_ts()
        try:
            monitor_id, target = create_monitor_mutation(
                db, body, user["id"], now, monitor_values=monitor_values,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "monitor_create", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
        return {"id": monitor_id, "status": "pending"}


    @router.put("/api/monitors/{monitor_id}")
    def update_monitor(monitor_id: int, body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            target = update_monitor_mutation(
                db, monitor_id, body, user["id"], utc_ts(),
                monitor_values=monitor_values, resolve_incident=resolve_incident,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "monitor_update", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
        return {"ok": True}


    @router.delete("/api/monitors/{monitor_id}")
    def delete_monitor(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row = delete_monitor_mutation(
                db, monitor_id, user["id"], resolve_incident=resolve_incident,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "monitor_delete", row["name"], str(monitor_id), request.client.host if request.client else None)
        return {"ok": True}


    @router.post("/api/monitors/{monitor_id}/run", status_code=202)
    def run_monitor_now(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            monitor, job_id = queue_monitor_run_mutation(
                db, monitor_id, user["id"], utc_ts(),
                node_stale_after=NODE_STALE_AFTER, decrypt=decrypt,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "monitor_run", monitor["name"], str(job_id), request.client.host if request.client else None)
        return {"job_id": job_id, "status": "queued"}


    @router.get("/api/monitors/{monitor_id}/results")
    def monitor_results(monitor_id: int, limit: int = 200, _: sqlite3.Row = Depends(current_user)):
        limit = min(max(limit, 1), 2000)
        exists, rows = fetch_monitor_result_rows(db, monitor_id, limit)
        if not exists:
            raise HTTPException(404, "Monitor not found")
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except (TypeError, ValueError):
                item["detail"] = {"message": str(item.get("detail") or "")[:1000]}
            result.append(item)
        return result

    app.include_router(router)
    return router
