from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
service = (root / "hub/monitor_incident_service.py").read_text()
monitoring = (root / "hub/monitoring_router.py").read_text()
incidents = (root / "hub/incidents_router.py").read_text()

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

for call, owner in [
    ("create_monitor_mutation(", monitoring),
    ("update_monitor_mutation(", monitoring),
    ("delete_monitor_mutation(", monitoring),
    ("queue_monitor_run_mutation(", monitoring),
    ("add_incident_note_mutation(", incidents),
    ("incident_action_mutation(", incidents),
]:
    assert call in owner

for route, owner in [
    ('@router.post("/api/monitors", status_code=201)', monitoring),
    ('@router.put("/api/monitors/{monitor_id}")', monitoring),
    ('@router.delete("/api/monitors/{monitor_id}")', monitoring),
    ('@router.post("/api/monitors/{monitor_id}/run", status_code=202)', monitoring),
    ('@router.post("/api/incidents/{incident_id}/notes", status_code=201)', incidents),
    ('@router.post("/api/incidents/{incident_id}/action")', incidents),
]:
    assert route in owner

for marker in [
    "INSERT INTO monitors(node_id,name,kind,target,port,secret_enc",
    "UPDATE monitors SET node_id=?,name=?,kind=?,target=?",
    "DELETE FROM monitors WHERE id=?",
    "UPDATE incidents SET status='acknowledged'",
    "UPDATE incidents SET status='open',resolved_at=NULL",
]:
    assert marker in service
    assert marker not in app

assert 'VERSION = "2.9.45"' in app
print("Monitor/Incident mutation service boundary passed")
