from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
monitoring = (root / "hub/monitoring_router.py").read_text()
incidents = (root / "hub/incidents_router.py").read_text()
audit = (root / "docs/backend-architecture-audit-v2.9.29.md").read_text()

assert 'register_monitoring_router' in app
assert 'register_incidents_router' in app
assert '@app.get("/api/monitors")' not in app
assert '@app.get("/api/incidents")' not in app
assert 'APIRouter' in monitoring and 'APIRouter' in incidents
for path in [
    '/api/monitors', '/api/monitors/{monitor_id}', '/api/monitors/{monitor_id}/run',
    '/api/monitors/{monitor_id}/results',
]:
    assert path in monitoring, path
for path in ['/api/incidents', '/api/incidents/{incident_id}', '/api/incidents/{incident_id}/notes', '/api/incidents/{incident_id}/action']:
    assert path in incidents, path
assert 'def agent_heartbeat' in app
assert 'def agent_job_result' in app
assert 'create_incident(' in app and 'resolve_incident(' in app
assert 'Remaining high-value splits' in audit
print('backend router contract ok')
