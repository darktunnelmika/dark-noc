from pathlib import Path
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
assert 'VERSION = "2.9.25"' in app
print('Monitor/Incident repository query boundary passed')
