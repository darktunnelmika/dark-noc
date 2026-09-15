from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
monitoring = (root / "hub/monitoring_router.py").read_text()
incidents = (root / "hub/incidents_router.py").read_text()
nodes = (root / "hub/nodes_router.py").read_text()
tunnels = (root / "hub/tunnels_router.py").read_text()
for marker in ['register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router']: assert marker in app
for marker in ['@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")']: assert marker not in app
for owner in [monitoring, incidents, nodes, tunnels]: assert 'APIRouter' in owner
for path in ['/api/monitors', '/api/monitors/{monitor_id}/results']: assert path in monitoring
for path in ['/api/incidents', '/api/incidents/{incident_id}/action']: assert path in incidents
for path in ['/api/nodes', '/api/nodes/{node_id}/metrics', '/api/nodes/{node_id}/plugins/{plugin_id}/install', '/api/nodes/{node_id}/jobs']: assert path in nodes, path
for path in ['/api/tunnels', '/api/tunnels/{tunnel_id}/operations', '/api/tunnels/{tunnel_id}/action']: assert path in tunnels, path
assert 'def agent_heartbeat' in app and 'def agent_job_result' in app
assert 'create_incident(' in app and 'resolve_incident(' in app
print('backend router contract ok')
