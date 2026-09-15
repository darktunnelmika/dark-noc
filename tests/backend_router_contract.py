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
    'auth': (root / "hub/auth_router.py").read_text(),
    'dashboard': (root / "hub/dashboard_router.py").read_text(),
    'system': (root / "hub/system_router.py").read_text(),
    'ssh_files': (root / "hub/ssh_file_router.py").read_text(),
    'ssh_terminal': (root / "hub/ssh_terminal_router.py").read_text(),
}
for marker in [
    'register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router',
    'register_plugin_deployments_router', 'register_certificates_router', 'register_fleet_router',
    'register_auth_router', 'register_dashboard_router', 'register_system_router',
    'register_ssh_file_router', 'register_ssh_terminal_router',
]:
    assert marker in app, marker
for owner in routers.values():
    assert 'APIRouter' in owner
for marker in [
    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',
    '@app.get("/api/plugins")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',
    '@app.post("/api/auth/login")', '@app.get("/api/dashboard")', '@app.get("/healthz")',
    '@app.post("/api/ssh/relay")', '@app.websocket("/ws/ssh/{node_id}")',
]:
    assert marker not in app, marker
assert 'def agent_heartbeat' in app and 'def agent_job_result' in app
assert '@app.websocket("/ws/live")' in app
assert 'create_incident(' in app and 'resolve_incident(' in app
print('backend router contract ok')
