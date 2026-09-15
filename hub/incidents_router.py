import json
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request


def register_incidents_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    IncidentNoteBody = deps["IncidentNoteBody"]
    IncidentActionBody = deps["IncidentActionBody"]
    fetch_incident_inventory = deps["fetch_incident_inventory"]
    fetch_incident_detail_rows = deps["fetch_incident_detail_rows"]
    add_incident_note_mutation = deps["add_incident_note_mutation"]
    incident_action_mutation = deps["incident_action_mutation"]
    MonitorIncidentServiceError = deps["MonitorIncidentServiceError"]
    append_incident_event = deps["append_incident_event"]
    resolve_incident = deps["resolve_incident"]
    utc_ts = deps["utc_ts"]
    audit = deps["audit"]

    @router.get("/api/incidents")
    def list_incidents(_: sqlite3.Row = Depends(current_user)):
        rows = fetch_incident_inventory(db)
        now = utc_ts()
        result = []
        for row in rows:
            item = dict(row)
            item["downtime_seconds"] = max(0, int(item.get("resolved_at") or now) - int(item["opened_at"]))
            result.append(item)
        return result


    @router.get("/api/incidents/{incident_id}")
    def incident_detail(incident_id: int, _: sqlite3.Row = Depends(current_user)):
        row, events = fetch_incident_detail_rows(db, incident_id)
        if not row:
            raise HTTPException(404, "Incident not found")
        item = dict(row)
        item["downtime_seconds"] = max(0, int(item.get("resolved_at") or utc_ts()) - int(item["opened_at"]))
        item["events"] = [dict(event) for event in events]
        return item


    @router.post("/api/incidents/{incident_id}/notes", status_code=201)
    def add_incident_note(incident_id: int, body: IncidentNoteBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            add_incident_note_mutation(
                db, incident_id, body, user["id"], utc_ts(), append_incident_event=append_incident_event,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "incident_note", str(incident_id), body.message, request.client.host if request.client else None)
        return {"ok": True}


    @router.post("/api/incidents/{incident_id}/action")
    def incident_action(incident_id: int, body: IncidentActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row = incident_action_mutation(
                db, incident_id, body, user["id"], utc_ts(),
                append_incident_event=append_incident_event, resolve_incident=resolve_incident,
            )
        except MonitorIncidentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], f"incident_{body.action}", str(incident_id), row["title"], request.client.host if request.client else None)
        return {"ok": True, "action": body.action}

    app.include_router(router)
    return router
