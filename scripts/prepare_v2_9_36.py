from pathlib import Path
import ast
import re

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.35'
new_version = '2.9.36'

runtime_source = r'''from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Callable

ConnectionFactory = Callable[[], sqlite3.Connection]


def acquire_hub_lease(
    *,
    db: ConnectionFactory,
    utc_ts: Callable[[], int],
    hub_lease_seconds: int,
    hub_instance_id: str,
    name: str,
    now: int | None = None,
) -> bool:
    current = now or utc_ts()
    expiry = current + hub_lease_seconds
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO hub_leases(name,holder,expires_at,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at,updated_at=excluded.updated_at
               WHERE hub_leases.holder=excluded.holder OR hub_leases.expires_at<?""",
            (name, hub_instance_id, expiry, current, current),
        )
        row = conn.execute("SELECT holder,expires_at FROM hub_leases WHERE name=?", (name,)).fetchone()
    return bool(row and row["holder"] == hub_instance_id and int(row["expires_at"]) >= expiry)


def queue_due_monitors(
    conn: sqlite3.Connection,
    now: int,
    online_cutoff: int,
    *,
    decrypt: Callable[[str | None], str | None],
    logger: Any,
    create_incident: Callable[..., int],
) -> None:
    due = conn.execute(
        """SELECT monitors.* FROM monitors JOIN nodes ON nodes.id=monitors.node_id
           WHERE monitors.enabled=1 AND monitors.next_run_at<=? AND nodes.last_seen>=?
           ORDER BY monitors.next_run_at,monitors.id LIMIT 100""",
        (now, online_cutoff),
    ).fetchall()
    for monitor in due:
        active = conn.execute(
            "SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')",
            (monitor["job_id"],),
        ).fetchone() if monitor["job_id"] else None
        if active:
            continue
        payload = {
            "monitor_id": monitor["id"], "kind": monitor["kind"], "target": monitor["target"],
            "port": monitor["port"], "timeout_seconds": monitor["timeout_seconds"],
            "expected_status": monitor["expected_status"], "snmp_oid": monitor["snmp_oid"],
        }
        try:
            if monitor["kind"] == "snmp":
                payload["snmp_community"] = decrypt(monitor["secret_enc"])
        except Exception:
            logger.exception("Could not decrypt synthetic monitor secret for monitor %s", monitor["id"])
            failure_streak = int(monitor["failure_streak"] or 0) + 1
            detail = json.dumps({"error": "Encrypted monitor secret cannot be read; edit and save the monitor again"})
            conn.execute(
                """UPDATE monitors SET status='down',detail=?,failure_streak=?,last_run_at=?,next_run_at=?,updated_at=?
                   WHERE id=?""",
                (detail, failure_streak, now, now + int(monitor["interval_seconds"]), now, monitor["id"]),
            )
            conn.execute(
                "INSERT INTO monitor_results(monitor_id,ts,status,detail) VALUES(?,?,'down',?)",
                (monitor["id"], now, detail),
            )
            title = f"Monitor {monitor['name']} is down"
            active_incident = conn.execute(
                "SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')",
                (monitor["node_id"], title),
            ).fetchone()
            if failure_streak >= 2 and not active_incident:
                create_incident(
                    conn, node_id=monitor["node_id"], tunnel_id=None, severity="critical", title=title,
                    detail={"monitor_id": monitor["id"], "reason": "encrypted_secret_unreadable"}, opened_at=now,
                )
            continue
        job_id = conn.execute(
            "INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)",
            (monitor["node_id"], "monitor_run", json.dumps(payload), now),
        ).lastrowid
        conn.execute(
            "UPDATE monitors SET job_id=?,next_run_at=?,updated_at=? WHERE id=?",
            (job_id, now + int(monitor["interval_seconds"]), now, monitor["id"]),
        )


def queue_due_fleet_operations(conn: sqlite3.Connection, now: int) -> None:
    operations = conn.execute(
        "SELECT * FROM fleet_operations WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at,id LIMIT 20",
        (now,),
    ).fetchall()
    for operation in operations:
        payload = json.loads(operation["payload"] or "{}")
        items = conn.execute(
            "SELECT * FROM fleet_operation_items WHERE operation_id=? ORDER BY id",
            (operation["id"],),
        ).fetchall()
        for item in items:
            if item["job_id"]:
                continue
            job_id = conn.execute(
                "INSERT INTO jobs(node_id,kind,payload,created_by,created_at) VALUES(?,?,?,?,?)",
                (item["node_id"], operation["kind"], json.dumps(payload), operation["created_by"], now),
            ).lastrowid
            conn.execute(
                "UPDATE fleet_operation_items SET job_id=?,status='queued' WHERE id=?",
                (job_id, item["id"]),
            )
        conn.execute("UPDATE fleet_operations SET status='running',started_at=? WHERE id=?", (now, operation["id"]))


def rollup_previous_hour(conn: sqlite3.Connection, now: int) -> None:
    bucket = ((now // 3600) - 1) * 3600
    if bucket < 0:
        return
    conn.execute(
        """INSERT INTO metric_rollups(node_id,bucket,samples,cpu_avg,ram_avg,disk_max,load_avg,rx_avg,tx_avg,connections_max)
           SELECT node_id,?,COUNT(*),AVG(cpu),AVG(ram),MAX(disk),AVG(load1),AVG(rx_bps),AVG(tx_bps),MAX(connections)
           FROM metrics WHERE ts>=? AND ts<? GROUP BY node_id
           ON CONFLICT(node_id,bucket) DO UPDATE SET
             samples=excluded.samples,cpu_avg=excluded.cpu_avg,ram_avg=excluded.ram_avg,
             disk_max=excluded.disk_max,load_avg=excluded.load_avg,rx_avg=excluded.rx_avg,
             tx_avg=excluded.tx_avg,connections_max=excluded.connections_max""",
        (bucket, bucket, bucket + 3600),
    )


def run_maintenance_cycle(
    *,
    db: ConnectionFactory,
    utc_ts: Callable[[], int],
    node_stale_after: int,
    metric_raw_retention_days: int,
    metric_rollup_retention_days: int,
    tunnel_sample_retention_days: int,
    monitor_result_retention_days: int,
    create_incident: Callable[..., int],
    resolve_incident: Callable[..., bool],
    rollup_previous_hour: Callable[[sqlite3.Connection, int], None],
    queue_due_monitors: Callable[[sqlite3.Connection, int, int], None],
    queue_due_fleet_operations: Callable[[sqlite3.Connection, int], None],
) -> None:
    now = utc_ts()
    cutoff = now - node_stale_after
    with db() as conn:
        stale_nodes = conn.execute("SELECT id,name FROM nodes WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,)).fetchall()
        for stale in stale_nodes:
            title = f"Node {stale['name']} is offline"
            if not conn.execute("SELECT 1 FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title=? AND status IN ('open','acknowledged')", (stale["id"], title)).fetchone():
                create_incident(conn, node_id=stale["id"], tunnel_id=None, severity="critical", title=title, detail={"reason": "heartbeat_timeout"})
        online_nodes = conn.execute("SELECT id FROM nodes WHERE last_seen>=?", (cutoff,)).fetchall()
        for online in online_nodes:
            active = conn.execute("SELECT id FROM incidents WHERE node_id=? AND tunnel_id IS NULL AND title LIKE 'Node % is offline' AND status IN ('open','acknowledged')", (online["id"],)).fetchall()
            for incident in active:
                resolve_incident(conn, incident["id"], "Agent heartbeat recovered")
        conn.execute("UPDATE nodes SET status='offline' WHERE last_seen IS NOT NULL AND last_seen < ?", (cutoff,))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        rollup_previous_hour(conn, now)
        conn.execute("DELETE FROM metrics WHERE ts < ?", (now - metric_raw_retention_days * 86400,))
        conn.execute("DELETE FROM metric_rollups WHERE bucket < ?", (now - metric_rollup_retention_days * 86400,))
        conn.execute("DELETE FROM tunnel_samples WHERE ts < ?", (now - tunnel_sample_retention_days * 86400,))
        conn.execute("DELETE FROM monitor_results WHERE ts < ?", (now - monitor_result_retention_days * 86400,))
        conn.execute("UPDATE jobs SET status='queued',started_at=NULL WHERE status='running' AND started_at<?", (now - 5 * 60,))
        conn.execute("DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at<?", (now - 90 * 86400,))
        conn.execute("DELETE FROM audit_logs WHERE created_at<?", (now - 180 * 86400,))
        conn.execute("UPDATE certificates SET status='expired',updated_at=? WHERE expires_at IS NOT NULL AND expires_at<=?", (now, now))
        conn.execute("UPDATE certificates SET status='expiring',updated_at=? WHERE status='valid' AND expires_at BETWEEN ? AND ?", (now, now, now + 14 * 86400))
        renewable = conn.execute("SELECT c.* FROM certificates c JOIN nodes n ON n.id=c.node_id WHERE c.status IN ('valid','expiring') AND c.expires_at BETWEEN ? AND ? AND n.last_seen>=?", (now, now + 30 * 86400, cutoff)).fetchall()
        for cert in renewable:
            pending = conn.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('queued','running')", (cert["job_id"],)).fetchone() if cert["job_id"] else None
            if pending:
                continue
            job_id = conn.execute("INSERT INTO jobs(node_id,kind,payload,created_at) VALUES(?,?,?,?)", (cert["node_id"], "certificate_issue", json.dumps({"certificate_id": cert["id"], "domain": cert["domain"], "renew": True}), now)).lastrowid
            conn.execute("UPDATE certificates SET status='renewing',job_id=?,updated_at=? WHERE id=?", (job_id, now, cert["id"]))
        queue_due_monitors(conn, now, cutoff)
        queue_due_fleet_operations(conn, now)


async def maintenance_loop(
    *,
    acquire_hub_lease: Callable[[str], bool],
    run_cycle: Callable[[], None],
    logger: Any,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> None:
    while True:
        try:
            if not acquire_hub_lease("maintenance"):
                await sleep(15)
                continue
            run_cycle()
        except Exception:
            logger.exception("maintenance loop failed")
        await sleep(30)


def build_lifespan(
    *,
    bootstrap: Callable[[], None],
    fernet_cls: Any,
    key_path: Any,
    maintenance_loop: Callable[[], Any],
    db: ConnectionFactory,
    hub_instance_id: str,
    logger: Any,
):
    @asynccontextmanager
    async def lifespan(application):
        bootstrap()
        application.state.fernet = fernet_cls(key_path.read_bytes())
        task = asyncio.create_task(maintenance_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            try:
                with db() as conn:
                    conn.execute("DELETE FROM hub_leases WHERE holder=?", (hub_instance_id,))
            except Exception:
                logger.exception("Could not release Hub controller leases")

    return lifespan
'''
(ROOT / 'hub/maintenance_runtime.py').write_text(runtime_source)

# Importlib-safe loader because tests load app.py directly via spec_from_file_location.
loader_anchor = "_MONITORING_ROUTER_PATH = Path(__file__).resolve().with_name('monitoring_router.py')\n"
loader = '''_MAINTENANCE_RUNTIME_PATH = Path(__file__).resolve().with_name('maintenance_runtime.py')\n_maintenance_runtime_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_maintenance_runtime', _MAINTENANCE_RUNTIME_PATH)\nif _maintenance_runtime_spec is None or _maintenance_runtime_spec.loader is None:\n    raise ImportError(f'Could not load maintenance runtime: {_MAINTENANCE_RUNTIME_PATH}')\n_maintenance_runtime_module = _realm_support_importlib_util.module_from_spec(_maintenance_runtime_spec)\n_maintenance_runtime_spec.loader.exec_module(_maintenance_runtime_module)\n_maintenance_acquire_hub_lease = _maintenance_runtime_module.acquire_hub_lease\n_maintenance_queue_due_monitors = _maintenance_runtime_module.queue_due_monitors\n_maintenance_queue_due_fleet_operations = _maintenance_runtime_module.queue_due_fleet_operations\n_maintenance_rollup_previous_hour = _maintenance_runtime_module.rollup_previous_hour\n_maintenance_run_cycle = _maintenance_runtime_module.run_maintenance_cycle\n_maintenance_loop_runner = _maintenance_runtime_module.maintenance_loop\n_build_maintenance_lifespan = _maintenance_runtime_module.build_lifespan\n\n'''
if loader_anchor not in text:
    raise RuntimeError('maintenance loader anchor not found')
text = text.replace(loader_anchor, loader + loader_anchor, 1)

# Remove the old maintenance/lifespan implementations and install thin compatibility wrappers.
targets = {
    'acquire_hub_lease', 'queue_due_monitors', 'queue_due_fleet_operations',
    'rollup_previous_hour', 'maintenance_loop', 'lifespan',
}
tree = ast.parse(text)
lines = text.splitlines(True)
ranges = []
insert_at = None
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
        start = min([item.lineno for item in node.decorator_list] + [node.lineno]) - 1
        end = node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end))
        if node.name == 'acquire_hub_lease':
            insert_at = start
if insert_at is None or len(ranges) != len(targets):
    raise RuntimeError(f'maintenance function boundary mismatch: {len(ranges)}')
for start, end in sorted(ranges, reverse=True):
    del lines[start:end]
    if start < insert_at:
        insert_at -= end - start

wrappers = '''def acquire_hub_lease(name: str, now: int | None = None) -> bool:\n    return _maintenance_acquire_hub_lease(\n        db=db, utc_ts=utc_ts, hub_lease_seconds=HUB_LEASE_SECONDS,\n        hub_instance_id=HUB_INSTANCE_ID, name=name, now=now,\n    )\n\n\ndef queue_due_monitors(conn: sqlite3.Connection, now: int, online_cutoff: int) -> None:\n    _maintenance_queue_due_monitors(\n        conn, now, online_cutoff, decrypt=decrypt, logger=LOGGER, create_incident=create_incident,\n    )\n\n\ndef queue_due_fleet_operations(conn: sqlite3.Connection, now: int) -> None:\n    _maintenance_queue_due_fleet_operations(conn, now)\n\n\ndef rollup_previous_hour(conn: sqlite3.Connection, now: int) -> None:\n    _maintenance_rollup_previous_hour(conn, now)\n\n\ndef run_maintenance_cycle() -> None:\n    _maintenance_run_cycle(\n        db=db, utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER,\n        metric_raw_retention_days=METRIC_RAW_RETENTION_DAYS,\n        metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,\n        tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,\n        monitor_result_retention_days=MONITOR_RESULT_RETENTION_DAYS,\n        create_incident=create_incident, resolve_incident=resolve_incident,\n        rollup_previous_hour=rollup_previous_hour, queue_due_monitors=queue_due_monitors,\n        queue_due_fleet_operations=queue_due_fleet_operations,\n    )\n\n\nasync def maintenance_loop() -> None:\n    await _maintenance_loop_runner(\n        acquire_hub_lease=acquire_hub_lease, run_cycle=run_maintenance_cycle, logger=LOGGER,\n    )\n\n\nlifespan = _build_maintenance_lifespan(\n    bootstrap=lambda: bootstrap(), fernet_cls=Fernet, key_path=KEY_PATH,\n    maintenance_loop=lambda: maintenance_loop(), db=lambda: db(),\n    hub_instance_id=HUB_INSTANCE_ID, logger=LOGGER,\n)\n\n\n'''
lines.insert(insert_at, wrappers)
text = ''.join(lines)

# Remove route-only/lifespan imports which are now owned by router/runtime modules.
text = text.replace('from contextlib import asynccontextmanager\n', '')
old_fastapi = 'from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect\n'
new_fastapi = 'from fastapi import Cookie, FastAPI, Header, HTTPException, Request, WebSocket\n'
if old_fastapi not in text:
    raise RuntimeError('FastAPI import cleanup anchor not found')
text = text.replace(old_fastapi, new_fastapi, 1)
old_responses = 'from fastapi.responses import FileResponse, JSONResponse, StreamingResponse\n'
if old_responses not in text:
    raise RuntimeError('response import cleanup anchor not found')
text = text.replace(old_responses, 'from fastapi.responses import JSONResponse\n', 1)

# Remove extraction-era vertical whitespace around the remaining schema shim.
text = re.sub(r'\n{4,}(class NodeUpdateBody)', r'\n\n\1', text, count=1)
text = re.sub(r'(class NodeUpdateBody\(NodeBody\):\n    pass)\n{4,}', r'\1\n\n\n', text, count=1)
APP.write_text(text)

# Permanent runtime/composition contract.
contract = ROOT / 'tests/backend_maintenance_runtime_contract.py'
contract.write_text(r'''from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
runtime = (root / 'hub/maintenance_runtime.py').read_text()

assert "with_name('maintenance_runtime.py')" in app
assert '_maintenance_runtime_spec.loader.exec_module' in app
for marker in [
    'def acquire_hub_lease(', 'def queue_due_monitors(', 'def queue_due_fleet_operations(',
    'def rollup_previous_hour(', 'def run_maintenance_cycle(', 'async def maintenance_loop(',
    'def build_lifespan(',
]:
    assert marker in runtime, marker
for sql in [
    "DELETE FROM metrics WHERE ts < ?", "DELETE FROM metric_rollups WHERE bucket < ?",
    "UPDATE jobs SET status='queued',started_at=NULL", "UPDATE certificates SET status='expired'",
    "SELECT * FROM fleet_operations WHERE status='scheduled'", "SELECT monitors.* FROM monitors JOIN nodes",
]:
    assert sql in runtime, sql
    assert sql not in app, sql
for wrapper in [
    'return _maintenance_acquire_hub_lease(', '_maintenance_queue_due_monitors(',
    '_maintenance_queue_due_fleet_operations(', '_maintenance_rollup_previous_hour(',
    '_maintenance_run_cycle(', 'await _maintenance_loop_runner(', '_build_maintenance_lifespan(',
]:
    assert wrapper in app, wrapper
assert 'from contextlib import asynccontextmanager' not in app
assert 'BackgroundTasks' not in app and 'UploadFile' not in app and 'StreamingResponse' not in app
assert 'from fastapi import Cookie, FastAPI, Header, HTTPException, Request, WebSocket' in app
for method in ['get', 'post', 'put', 'delete', 'patch', 'websocket']:
    assert f'@app.{method}(' not in app, method
assert '@app.middleware("http")' in app
assert 'app.mount("/static"' in app
assert 'VERSION = "2.9.36"' in app
print('Maintenance runtime/final backend composition contract passed')
''')

# Final backend ownership audit document.
doc = ROOT / 'docs/backend-final-audit-v2.9.36.md'
doc.write_text('''# DARK NOC Backend Final Audit — v2.9.36\n\nThe Repository + Service + Router modularization wave is complete. This release performs the final low-risk backend composition cleanup.\n\n## Runtime ownership\n\n- `hub/maintenance_runtime.py` owns maintenance scheduling primitives, retention/rollup work, scheduled Monitor/Fleet queueing, certificate renewal scheduling, the maintenance loop and FastAPI lifespan cleanup.\n- `hub/app.py` keeps shared domain helpers, provisioning/orchestration primitives, middleware, router wiring and compatibility wrappers used by tests/internal callers.\n- Route registration remains fully outside `app.py`; the only FastAPI decorator intentionally left there is the global HTTP security/upload middleware.\n- `/static` mounting remains in `app.py` because it is application composition rather than an API route.\n\n## Hygiene\n\nRoute-only FastAPI imports and the old lifespan context-manager import were removed from `app.py`. Extraction-era blank-line debris around the schema compatibility shim was normalized.\n\n## Preserved behavior\n\nTransaction scopes, retention windows, lease semantics, Agent/Monitor/Fleet/Certificate scheduling, upload security, WebSocket behavior, API contracts, frontend and Live Matrix visuals are unchanged.\n''')

# Update versions across shipped/runtime/test files.
version_paths = [ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html']
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.36 — Backend Final Audit & Maintenance Runtime Cleanup\n\n- Extract maintenance scheduling, retention/rollup work, scheduled Monitor/Fleet queueing, certificate renewal scheduling and FastAPI lifespan cleanup into `hub/maintenance_runtime.py`.\n- Keep thin compatibility wrappers in `hub/app.py` so existing tests/internal callers and runtime overrides remain stable.\n- Remove route-only FastAPI imports and obsolete lifespan imports from `hub/app.py`.\n- Enforce the final composition boundary: no API/WebSocket route decorators remain in `hub/app.py`; only global middleware and static mounting stay there.\n- Add a permanent maintenance/runtime composition regression contract and final backend ownership audit.\n- Preserve transaction scopes, lease semantics, retention windows, Agent behavior, API contracts and all frontend/Live Matrix visuals.\n\n## فارسی\n\nAudit نهایی بک‌اند انجام شد؛ maintenance/lifecycle و retention scheduling به `maintenance_runtime.py` منتقل شدند، importهای اضافی پاک شدند و `app.py` حالا فقط composition، middleware و helperهای مشترک را نگه می‌دارد؛ رفتار پنل و Agent تغییر نکرده است.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.36\n\n- Final backend composition audit and maintenance/lifespan runtime extraction.\n- Removed route-only imports and locked the no-routes-in-app boundary with regression coverage.\n\n''' + changelog.read_text())

print('prepared v2.9.36 backend final cleanup')
