from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
routers = {
    'monitoring': (root / "hub/monitoring_router.py").read_text(),
    'incidents': (root / "hub/incidents_router.py").read_text(),
    'nodes': (root / "hub/nodes_router.py").read_text(),
    'tunnels': (root / "hub/tunnels_router.py").read_text(),
    'plugins': (root / "hub/plugin_deployments_router.py").read_text(),
    'certificates': (root / "hub/certificates_router.py").read_text(),
    'fleet': (root / "hub/fleet_router.py").read_text(),
}
for marker in [
    'register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router',
    'register_plugin_deployments_router', 'register_certificates_router', 'register_fleet_router',
]:
    assert marker in app
for marker in [
    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',
    '@app.get("/api/plugin-deployments")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',
]:
    assert marker not in app
for owner in routers.values():
    assert 'APIRouter' in owner
assert '/api/monitors' in routers['monitoring'] and '/api/incidents' in routers['incidents']
assert '/api/nodes' in routers['nodes'] and '/api/tunnels' in routers['tunnels']
assert '/api/plugin-deployments' in routers['plugins'] and '/api/hybrid-deployments/{deployment_id}/retry' in routers['plugins']
assert '/api/certificates/{certificate_id}/renew' in routers['certificates']
assert '/api/fleet/operations/{operation_id}' in routers['fleet']
assert 'def agent_heartbeat' in app and 'def agent_job_result' in app
assert 'create_incident(' in app and 'resolve_incident(' in app
print('backend router contract ok')
