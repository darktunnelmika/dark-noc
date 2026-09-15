from pathlib import Path

app_path = Path("hub/app.py")
text = app_path.read_text()

repository = '''from __future__ import annotations

import sqlite3
from collections.abc import Callable

ConnectionFactory = Callable[[], sqlite3.Connection]


def fetch_monitor_inventory(db: ConnectionFactory) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """SELECT monitors.*,nodes.name node_name,nodes.role node_role,nodes.host node_host,
                      nodes.last_seen node_last_seen
               FROM monitors JOIN nodes ON nodes.id=monitors.node_id
               ORDER BY CASE monitors.status WHEN 'down' THEN 0 WHEN 'degraded' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                        monitors.name COLLATE NOCASE"""
        ).fetchall()


def fetch_monitor_result_rows(
    db: ConnectionFactory,
    monitor_id: int,
    limit: int,
) -> tuple[bool, list[sqlite3.Row]]:
    with db() as conn:
        exists = bool(conn.execute("SELECT 1 FROM monitors WHERE id=?", (monitor_id,)).fetchone())
        if not exists:
            return False, []
        rows = conn.execute(
            "SELECT ts,status,latency_ms,detail FROM monitor_results WHERE monitor_id=? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    return True, rows


def fetch_incident_inventory(db: ConnectionFactory) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name,
                      (SELECT COUNT(*) FROM incident_events WHERE incident_id=incidents.id) event_count
               FROM incidents
               LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id
               ORDER BY incidents.status='open' DESC,incidents.status='acknowledged' DESC,incidents.opened_at DESC LIMIT 500"""
        ).fetchall()


def fetch_incident_detail_rows(
    db: ConnectionFactory,
    incident_id: int,
) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    with db() as conn:
        row = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name
               FROM incidents LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id WHERE incidents.id=?""",
            (incident_id,),
        ).fetchone()
        if not row:
            return None, []
        events = conn.execute(
            """SELECT incident_events.*,users.username actor
               FROM incident_events LEFT JOIN users ON users.id=incident_events.actor_id
               WHERE incident_id=? ORDER BY created_at,id""",
            (incident_id,),
        ).fetchall()
    return row, events
'''
Path("hub/monitor_incident_repository.py").write_text(repository)

old_monitors = '''    with db() as conn:
        rows = conn.execute(
            """SELECT monitors.*,nodes.name node_name,nodes.role node_role,nodes.host node_host,
                      nodes.last_seen node_last_seen
               FROM monitors JOIN nodes ON nodes.id=monitors.node_id
               ORDER BY CASE monitors.status WHEN 'down' THEN 0 WHEN 'degraded' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
                        monitors.name COLLATE NOCASE"""
        ).fetchall()
'''
new_monitors = '''    rows = fetch_monitor_inventory(db)
'''
assert old_monitors in text
text = text.replace(old_monitors, new_monitors, 1)

old_results = '''    with db() as conn:
        if not conn.execute("SELECT 1 FROM monitors WHERE id=?", (monitor_id,)).fetchone():
            raise HTTPException(404, "Monitor not found")
        rows = conn.execute(
            "SELECT ts,status,latency_ms,detail FROM monitor_results WHERE monitor_id=? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
'''
new_results = '''    exists, rows = fetch_monitor_result_rows(db, monitor_id, limit)
    if not exists:
        raise HTTPException(404, "Monitor not found")
'''
assert old_results in text
text = text.replace(old_results, new_results, 1)

old_incidents = '''    with db() as conn:
        rows = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name,
                      (SELECT COUNT(*) FROM incident_events WHERE incident_id=incidents.id) event_count
               FROM incidents
               LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id
               ORDER BY incidents.status='open' DESC,incidents.status='acknowledged' DESC,incidents.opened_at DESC LIMIT 500"""
        ).fetchall()
'''
new_incidents = '''    rows = fetch_incident_inventory(db)
'''
assert old_incidents in text
text = text.replace(old_incidents, new_incidents, 1)

old_detail = '''    with db() as conn:
        row = conn.execute(
            """SELECT incidents.*,nodes.name node_name,tunnels.name tunnel_name
               FROM incidents LEFT JOIN nodes ON nodes.id=incidents.node_id
               LEFT JOIN tunnels ON tunnels.id=incidents.tunnel_id WHERE incidents.id=?""",
            (incident_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Incident not found")
        events = conn.execute(
            """SELECT incident_events.*,users.username actor
               FROM incident_events LEFT JOIN users ON users.id=incident_events.actor_id
               WHERE incident_id=? ORDER BY created_at,id""",
            (incident_id,),
        ).fetchall()
'''
new_detail = '''    row, events = fetch_incident_detail_rows(db, incident_id)
    if not row:
        raise HTTPException(404, "Incident not found")
'''
assert old_detail in text
text = text.replace(old_detail, new_detail, 1)

anchor = "del _repository_name\n"
assert anchor in text
loader = '''
_MONITOR_INCIDENT_REPOSITORY_PATH = Path(__file__).resolve().with_name('monitor_incident_repository.py')
_monitor_incident_repository_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitor_incident_repository', _MONITOR_INCIDENT_REPOSITORY_PATH)
if _monitor_incident_repository_spec is None or _monitor_incident_repository_spec.loader is None:
    raise ImportError(f'Could not load Monitor/Incident repository: {_MONITOR_INCIDENT_REPOSITORY_PATH}')
_monitor_incident_repository_module = _realm_support_importlib_util.module_from_spec(_monitor_incident_repository_spec)
_monitor_incident_repository_spec.loader.exec_module(_monitor_incident_repository_module)
for _monitor_incident_repository_name in ['fetch_monitor_inventory', 'fetch_monitor_result_rows', 'fetch_incident_inventory', 'fetch_incident_detail_rows']:
    globals()[_monitor_incident_repository_name] = getattr(_monitor_incident_repository_module, _monitor_incident_repository_name)
del _monitor_incident_repository_name
'''
text = text.replace(anchor, anchor + loader, 1)
app_path.write_text(text)

contract = '''from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
repo = (root / 'hub/monitor_incident_repository.py').read_text()
assert "with_name('monitor_incident_repository.py')" in app
assert '_monitor_incident_repository_spec.loader.exec_module' in app
for marker in ['def fetch_monitor_inventory(', 'def fetch_monitor_result_rows(', 'def fetch_incident_inventory(', 'def fetch_incident_detail_rows(']:
    assert marker in repo
assert 'fetch_monitor_inventory(db)' in app
assert 'fetch_monitor_result_rows(db, monitor_id, limit)' in app
assert 'fetch_incident_inventory(db)' in app
assert 'fetch_incident_detail_rows(db, incident_id)' in app
for marker in [
    'SELECT monitors.*,nodes.name node_name,nodes.role node_role,nodes.host node_host',
    'SELECT ts,status,latency_ms,detail FROM monitor_results WHERE monitor_id=?',
    '(SELECT COUNT(*) FROM incident_events WHERE incident_id=incidents.id) event_count',
    'SELECT incident_events.*,users.username actor',
]:
    assert marker in repo
    assert marker not in app
for mutation_marker in [
    '@app.post("/api/monitors", status_code=201)',
    '@app.put("/api/monitors/{monitor_id}")',
    '@app.post("/api/incidents/{incident_id}/notes", status_code=201)',
    '@app.post("/api/incidents/{incident_id}/action")',
]:
    assert mutation_marker in app
assert 'VERSION = "2.9.24"' in app
print('Monitor/Incident repository query boundary passed')
'''
Path("tests/backend_monitor_incident_repository_contract.py").write_text(contract)

identity_paths = [
    Path("hub/app.py"),
    Path("agent/agent.py"),
    Path("darknoc"),
    Path("install-hub.sh"),
    Path("install-node.sh"),
    Path("upgrade.sh"),
    Path("hub/static/index.html"),
]
identity_paths.extend(Path("tests").glob("*.py"))
for path in identity_paths:
    value = path.read_text()
    if "2.9.23" in value:
        path.write_text(value.replace("2.9.23", "2.9.24"))

ci = Path(".github/workflows/ci.yml")
ci_text = ci.read_text()
ci_anchor = "          python tests/backend_node_tunnel_repository_contract.py\n"
assert ci_anchor in ci_text
ci.write_text(
    ci_text.replace(
        ci_anchor,
        ci_anchor + "          python tests/backend_monitor_incident_repository_contract.py\n",
        1,
    )
)

notes = '''# DARK NOC v2.9.24 — Monitor & Incident Repository Query Layer

- Continue the backend repository/query layer by extracting Synthetic Monitor inventory/history reads and Incident list/detail/timeline reads from `hub/app.py` into `hub/monitor_incident_repository.py`.
- Keep Monitor/Incident mutation transactions, API response shaping and HTTP error behavior in `hub/app.py` to preserve behavior.
- Preserve SQLite semantics, Agent behavior and frontend visuals.
- Add permanent Monitor/Incident repository-boundary regression coverage.

## فارسی

Queryهای خواندنی Monitoring و Incident از `hub/app.py` به `hub/monitor_incident_repository.py` منتقل شدند؛ عملیات Create/Edit/Delete/Action و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

'''
path = Path("RELEASE_NOTES.md")
path.write_text(notes + path.read_text())

path = Path("CHANGELOG.md")
old = path.read_text()
first, sep, rest = old.partition("\n")
path.write_text(
    first
    + sep
    + "\n## 2.9.24 — Monitor and Incident repository query layer\n\n"
    + "- Extract Monitor inventory/history and Incident list/detail reads into hub/monitor_incident_repository.py.\n"
    + "- Preserve all mutation transactions and API response shaping in hub/app.py.\n"
    + "- Add repository-boundary regression coverage.\n\n"
    + rest
)
