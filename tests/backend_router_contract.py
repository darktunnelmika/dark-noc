from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
router_paths = [
    'monitoring_router.py', 'incidents_router.py', 'nodes_router.py', 'tunnels_router.py',
    'plugin_deployments_router.py', 'certificates_router.py', 'fleet_router.py',
    'auth_router.py', 'dashboard_router.py', 'system_router.py', 'jobs_router.py',
    'ssh_file_router.py', 'ssh_terminal_router.py', 'agent_control_router.py', 'live_router.py',
]
for filename in router_paths:
    owner = (root / 'hub' / filename).read_text()
    assert 'APIRouter' in owner, filename
    assert f"with_name('{filename}')" in app, filename
for marker in [
    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',
    '@app.get("/api/plugins")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',
    '@app.post("/api/auth/login")', '@app.get("/api/dashboard")', '@app.get("/healthz")',
    '@app.get("/api/jobs")', '@app.post("/api/ssh/relay")', '@app.websocket("/ws/ssh/{node_id}")',
    '@app.post("/api/agent/heartbeat")', '@app.get("/api/agent/jobs")', '@app.websocket("/ws/live")',
]:
    assert marker not in app, marker
assert 'def agent_node(' in app
assert 'def create_incident(' in app and 'def resolve_incident(' in app
assert 'async def websocket_session_guard(' in app
print('backend router contract ok')
