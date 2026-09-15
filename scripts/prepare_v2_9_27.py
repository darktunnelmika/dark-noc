from pathlib import Path

app_path = Path("hub/app.py")
text = app_path.read_text()

service = r'''from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


class MonitorIncidentServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def create_monitor_mutation(
    db: ConnectionFactory,
    body: Any,
    user_id: int,
    now: int,
    *,
    monitor_values: Callable[..., tuple[str, int | None, str | None, str | None]],
) -> tuple[int, str]:
    target, port, secret, oid = monitor_values(body)
    try:
        with db() as conn:
            node = conn.execute("SELECT name FROM nodes WHERE id=?", (body.node_id,)).fetchone()
            if not node:
                raise MonitorIncidentServiceError(404, "Node not found")
            monitor_id = int(conn.execute(
                """INSERT INTO monitors(node_id,name,kind,target,port,secret_enc,snmp_oid,interval_seconds,
                       timeout_seconds,expected_status,enabled,status,next_run_at,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, "pending", now,
                 user_id, now, now),
            ).lastrowid)
    except sqlite3.IntegrityError as exc:
        raise MonitorIncidentServiceError(409, "A monitor with this name already exists on the selected Node") from exc
    return monitor_id, target


def update_monitor_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    monitor_values: Callable[..., tuple[str, int | None, str | None, str | None]],
    resolve_incident: Callable[..., None],
) -> str:
    try:
        with db() as conn:
            current = conn.execute("SELECT * FROM monitors WHERE id=?", (monitor_id,)).fetchone()
            if not current:
                raise MonitorIncidentServiceError(404, "Monitor not found")
            if not conn.execute("SELECT 1 FROM nodes WHERE id=?", (body.node_id,)).fetchone():
                raise MonitorIncidentServiceError(404, "Node not found")
            target, port, secret, oid = monitor_values(body, current["secret_enc"])
            old_title = f"Monitor {current['name']} is down"
            old_incidents = conn.execute(
                "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (current["node_id"], old_title),
            ).fetchall()
            for incident in old_incidents:
                resolve_incident(conn, incident["id"], "Monitor configuration changed", actor_id=user_id)
            conn.execute(
                """UPDATE monitors SET node_id=?,name=?,kind=?,target=?,port=?,secret_enc=?,snmp_oid=?,
                       interval_seconds=?,timeout_seconds=?,expected_status=?,enabled=?,status='pending',
                       failure_streak=0,next_run_at=?,updated_at=? WHERE id=?""",
                (body.node_id, body.name, body.kind, target, port, secret, oid, body.interval_seconds,
                 body.timeout_seconds, body.expected_status, 1 if body.enabled else 0, now, now, monitor_id),
            )
    except sqlite3.IntegrityError as exc:
        raise MonitorIncidentServiceError(409, "A monitor with this name already exists on the selected Node") from exc
    return target


def delete_monitor_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    user_id: int,
    *,
    resolve_incident: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT node_id,name FROM monitors WHERE id=?", (monitor_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Monitor not found")
        title = f"Monitor {row['name']} is down"
        active = conn.execute(
            "SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
            (row["node_id"], title),
        ).fetchall()
        for incident in active:
            resolve_incident(conn, incident["id"], "Monitor was removed by the operator", actor_id=user_id)
        conn.execute("DELETE FROM monitors WHERE id=?", (monitor_id,))
    return row


def queue_monitor_run_mutation(
    db: ConnectionFactory,
    monitor_id: int,
    user_id: int,
    now: int,
    *,
    node_stale_after: int,
    decrypt: Callable[[str | None], str | None],
) -> tuple[sqlite3.Row, int]:
    with db() as conn:
        monitor = conn.execute(
            "SELECT monitors.*,nodes.last_seen FROM monitors JOIN nodes ON nodes.id=monitors.node_id WHERE monitors.id=?",
            (monitor_id,),
        ).fetchone()
        if not monitor:
            raise MonitorIncidentServiceError(404, "Monitor not found")
        if not monitor["last_seen"] or monitor["last_seen"] < now - node_stale_after:
            raise MonitorIncidentServiceError(409, "Agent is offline")
        active = (
            conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (monitor["job_id"],)).fetchone()
            if monitor["job_id"] else None
        )
        if active:
            raise MonitorIncidentServiceError(409, "Monitor check is already running")
        payload: dict[str, Any] = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        if monitor["kind"] == "snmp":
            try:
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
            except Exception as exc:
                raise MonitorIncidentServiceError(409, "Encrypted SNMP community cannot be read; edit and save the monitor again") from exc
        job_id = int(conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), user_id, now),
        ).lastrowid)
        conn.execute(
            "UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?",
            (job_id, now + monitor["interval_seconds"], now, monitor_id),
        )
    return monitor, job_id


def add_incident_note_mutation(
    db: ConnectionFactory,
    incident_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    append_incident_event: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT title FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Incident not found")
        append_incident_event(conn, incident_id, body.event_type, body.message, actor_id=user_id)
        conn.execute("UPDATE incidents SET last_changed_at=? WHERE id=?", (now, incident_id))
    return row


def incident_action_mutation(
    db: ConnectionFactory,
    incident_id: int,
    body: Any,
    user_id: int,
    now: int,
    *,
    append_incident_event: Callable[..., None],
    resolve_incident: Callable[..., None],
) -> sqlite3.Row:
    with db() as conn:
        row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not row:
            raise MonitorIncidentServiceError(404, "Incident not found")
        if body.action == "acknowledge":
            if row["status"] != "open":
                raise MonitorIncidentServiceError(409, "Only an open incident can be acknowledged")
            conn.execute(
                "UPDATE incidents SET status='acknowledged',acknowledged_at=?,last_changed_at=?,root_cause=COALESCE(?,root_cause) WHERE id=?",
                (now, now, body.root_cause, incident_id),
            )
            append_incident_event(
                conn, incident_id, "acknowledged", body.note or "Operator acknowledged the incident",
                actor_id=user_id, created_at=now,
            )
        elif body.action == "resolve":
            if row["status"] == "resolved":
                raise MonitorIncidentServiceError(409, "Incident is already resolved")
            conn.execute(
                "UPDATE incidents SET root_cause=COALESCE(?,root_cause) WHERE id=?",
                (body.root_cause, incident_id),
            )
            resolve_incident(
                conn, incident_id, body.note or "Operator resolved the incident",
                actor_id=user_id, resolution=body.resolution,
            )
        else:
            if row["status"] != "resolved":
                raise MonitorIncidentServiceError(409, "Only a resolved incident can be reopened")
            conn.execute(
                "UPDATE incidents SET status='open',resolved_at=NULL,last_changed_at=?,resolution=NULL WHERE id=?",
                (now, incident_id),
            )
            append_incident_event(
                conn, incident_id, "reopened", body.note or "Operator reopened the incident",
                actor_id=user_id, created_at=now,
            )
    return row
'''
Path("hub/monitor_incident_service.py").write_text(service)


def replace_route(source: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[:start] + replacement.rstrip() + "\n\n\n" + source[end:]


text = replace_route(
    text,
    '@app.post("/api/monitors", status_code=201)\ndef create_monitor',
    '@app.put("/api/monitors/{monitor_id}")\ndef update_monitor',
    '''@app.post("/api/monitors", status_code=201)
def create_monitor(body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    now = utc_ts()
    try:
        monitor_id, target = create_monitor_mutation(
            db, body, user["id"], now, monitor_values=monitor_values,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "monitor_create", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
    return {"id": monitor_id, "status": "pending"}''',
)

text = replace_route(
    text,
    '@app.put("/api/monitors/{monitor_id}")\ndef update_monitor',
    '@app.delete("/api/monitors/{monitor_id}")\ndef delete_monitor',
    '''@app.put("/api/monitors/{monitor_id}")
def update_monitor(monitor_id: int, body: MonitorBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        target = update_monitor_mutation(
            db, monitor_id, body, user["id"], utc_ts(),
            monitor_values=monitor_values, resolve_incident=resolve_incident,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "monitor_update", body.name, f"{body.kind}:{target}", request.client.host if request.client else None)
    return {"ok": True}''',
)

text = replace_route(
    text,
    '@app.delete("/api/monitors/{monitor_id}")\ndef delete_monitor',
    '@app.post("/api/monitors/{monitor_id}/run", status_code=202)\ndef run_monitor_now',
    '''@app.delete("/api/monitors/{monitor_id}")
def delete_monitor(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row = delete_monitor_mutation(
            db, monitor_id, user["id"], resolve_incident=resolve_incident,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "monitor_delete", row["name"], str(monitor_id), request.client.host if request.client else None)
    return {"ok": True}''',
)

text = replace_route(
    text,
    '@app.post("/api/monitors/{monitor_id}/run", status_code=202)\ndef run_monitor_now',
    '@app.get("/api/monitors/{monitor_id}/results")\ndef monitor_results',
    '''@app.post("/api/monitors/{monitor_id}/run", status_code=202)
def run_monitor_now(monitor_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        monitor, job_id = queue_monitor_run_mutation(
            db, monitor_id, user["id"], utc_ts(),
            node_stale_after=NODE_STALE_AFTER, decrypt=decrypt,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "monitor_run", monitor["name"], str(job_id), request.client.host if request.client else None)
    return {"job_id": job_id, "status": "queued"}''',
)

text = replace_route(
    text,
    '@app.post("/api/incidents/{incident_id}/notes", status_code=201)\ndef add_incident_note',
    '@app.post("/api/incidents/{incident_id}/action")\ndef incident_action',
    '''@app.post("/api/incidents/{incident_id}/notes", status_code=201)
def add_incident_note(incident_id: int, body: IncidentNoteBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        add_incident_note_mutation(
            db, incident_id, body, user["id"], utc_ts(), append_incident_event=append_incident_event,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], "incident_note", str(incident_id), body.message, request.client.host if request.client else None)
    return {"ok": True}''',
)

text = replace_route(
    text,
    '@app.post("/api/incidents/{incident_id}/action")\ndef incident_action',
    '@app.post("/api/nodes/{node_id}/jobs", status_code=202)\ndef create_job',
    '''@app.post("/api/incidents/{incident_id}/action")
def incident_action(incident_id: int, body: IncidentActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
    try:
        row = incident_action_mutation(
            db, incident_id, body, user["id"], utc_ts(),
            append_incident_event=append_incident_event, resolve_incident=resolve_incident,
        )
    except MonitorIncidentServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    audit(user["id"], f"incident_{body.action}", str(incident_id), row["title"], request.client.host if request.client else None)
    return {"ok": True, "action": body.action}''',
)

anchor = 'del _plugin_deployment_service_name\n'
assert anchor in text
loader = '''

_MONITOR_INCIDENT_SERVICE_PATH = Path(__file__).resolve().with_name('monitor_incident_service.py')
_monitor_incident_service_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitor_incident_service', _MONITOR_INCIDENT_SERVICE_PATH)
if _monitor_incident_service_spec is None or _monitor_incident_service_spec.loader is None:
    raise ImportError(f'Could not load Monitor/Incident service: {_MONITOR_INCIDENT_SERVICE_PATH}')
_monitor_incident_service_module = _realm_support_importlib_util.module_from_spec(_monitor_incident_service_spec)
_monitor_incident_service_spec.loader.exec_module(_monitor_incident_service_module)
for _monitor_incident_service_name in [
    'MonitorIncidentServiceError', 'create_monitor_mutation', 'update_monitor_mutation',
    'delete_monitor_mutation', 'queue_monitor_run_mutation', 'add_incident_note_mutation',
    'incident_action_mutation',
]:
    globals()[_monitor_incident_service_name] = getattr(_monitor_incident_service_module, _monitor_incident_service_name)
del _monitor_incident_service_name
'''
text = text.replace(anchor, anchor + loader, 1)
app_path.write_text(text)

contract = r'''from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
service = (root / "hub/monitor_incident_service.py").read_text()

assert "with_name('monitor_incident_service.py')" in app
assert "_monitor_incident_service_spec.loader.exec_module" in app
for marker in [
    "class MonitorIncidentServiceError",
    "def create_monitor_mutation(",
    "def update_monitor_mutation(",
    "def delete_monitor_mutation(",
    "def queue_monitor_run_mutation(",
    "def add_incident_note_mutation(",
    "def incident_action_mutation(",
]:
    assert marker in service

for call in [
    "create_monitor_mutation(",
    "update_monitor_mutation(",
    "delete_monitor_mutation(",
    "queue_monitor_run_mutation(",
    "add_incident_note_mutation(",
    "incident_action_mutation(",
]:
    assert call in app

for route in [
    '@app.post("/api/monitors", status_code=201)',
    '@app.put("/api/monitors/{monitor_id}")',
    '@app.delete("/api/monitors/{monitor_id}")',
    '@app.post("/api/monitors/{monitor_id}/run", status_code=202)',
    '@app.post("/api/incidents/{incident_id}/notes", status_code=201)',
    '@app.post("/api/incidents/{incident_id}/action")',
]:
    assert route in app

for marker in [
    "INSERT INTO monitors(node_id,name,kind,target,port,secret_enc",
    "UPDATE monitors SET node_id=?,name=?,kind=?,target=?",
    "DELETE FROM monitors WHERE id=?",
    "UPDATE incidents SET status='acknowledged'",
    "UPDATE incidents SET status='open',resolved_at=NULL",
]:
    assert marker in service
    assert marker not in app

assert 'VERSION = "2.9.27"' in app
print("Monitor/Incident mutation service boundary passed")
'''
Path("tests/backend_monitor_incident_service_contract.py").write_text(contract)

identity_paths = [
    Path("hub/app.py"), Path("agent/agent.py"), Path("darknoc"),
    Path("install-hub.sh"), Path("install-node.sh"), Path("upgrade.sh"),
    Path("hub/static/index.html"),
]
identity_paths.extend(Path("tests").glob("*.py"))
for path in identity_paths:
    value = path.read_text()
    if "2.9.26" in value:
        path.write_text(value.replace("2.9.26", "2.9.27"))

notes = '''# DARK NOC v2.9.27 — Monitor & Incident Mutation Service Layer

- Continue the backend write/service layer by extracting Monitor create/update/delete/run transactions and Incident note/action mutations from `hub/app.py` into `hub/monitor_incident_service.py`.
- Keep FastAPI route declarations, audit logging, API response shaping and telemetry-driven Incident automation in `hub/app.py`.
- Preserve transaction boundaries, HTTP status/details, Synthetic Monitoring behavior, Incident lifecycle behavior, Agent behavior and frontend visuals.
- Add permanent Monitor/Incident mutation-service regression coverage.

## فارسی

عملیات نوشتنی Monitoring و Incident از `hub/app.py` به `hub/monitor_incident_service.py` منتقل شدند؛ Routeها، Audit، رفتار Agent و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

'''
path = Path("RELEASE_NOTES.md")
path.write_text(notes + path.read_text())

path = Path("CHANGELOG.md")
old = path.read_text()
first, sep, rest = old.partition("\n")
path.write_text(
    first + sep
    + "\n## 2.9.27 — Monitor and Incident mutation service layer\n\n"
    + "- Extract Monitor create/update/delete/run and Incident note/action write transactions into hub/monitor_incident_service.py.\n"
    + "- Preserve FastAPI route orchestration, audit logging and telemetry automation in hub/app.py.\n"
    + "- Add mutation-service boundary regression coverage.\n\n"
    + rest
)
