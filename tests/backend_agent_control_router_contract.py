from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
agent = (root / "hub/agent_control_router.py").read_text()
live = (root / "hub/live_router.py").read_text()

assert "with_name('agent_control_router.py')" in app
assert "with_name('live_router.py')" in app
assert 'register_agent_control_router(' in app
assert 'register_live_router(' in app
for marker in [
    '@app.post("/api/agent/local-enroll")', '@app.post("/api/agent/pulse")',
    '@app.post("/api/agent/heartbeat")', '@app.get("/api/agent/jobs")',
    '@app.post("/api/agent/jobs/result")', '@app.post("/api/agent/jobs/{job_id}/lease")',
    '@app.websocket("/ws/live")',
]:
    assert marker not in app, marker
for path in [
    '/api/agent/local-enroll', '/api/agent/pulse', '/api/agent/heartbeat', '/api/agent/jobs',
    '/api/agent/jobs/result', '/api/agent/jobs/{job_id}/lease',
]:
    assert path in agent, path
assert '/ws/live' in live
assert 'if row["kind"] == "certificate_issue":' in agent
assert 'if row["kind"] == "monitor_run":' in agent
assert 'plugin_deployments' in agent and 'fleet_operation_items' in agent
for marker in [
    'get_agent_inventory_flags()(', 'get_managed_tunnel_report()(', 'get_create_incident()(',
    'get_resolve_incident()(', 'get_finalize_fleet_operation()(', 'get_telegram_notify()',
    'get_live_clients()',
]:
    assert marker in agent, marker
assert 'get_session_ttl()' in live and 'get_live_clients()' in live
for helper in [
    'def agent_node(', 'def agent_inventory_flags(', 'def managed_tunnel_report(',
    'def create_incident(', 'def resolve_incident(', 'def finalize_fleet_operation(',
    'async def websocket_user(', 'async def websocket_session_guard(',
]:
    assert helper in app, helper
assert 'globals()[_agent_control_name] = _agent_control_handler' in app
assert 'globals()[_live_name] = _live_handler' in app
assert 'VERSION = "2.9.36"' in app
print('Agent Control/Live router boundary passed')
