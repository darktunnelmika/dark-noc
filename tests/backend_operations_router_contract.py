from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
plugins = (root / 'hub/plugin_deployments_router.py').read_text()
certs = (root / 'hub/certificates_router.py').read_text()
fleet = (root / 'hub/fleet_router.py').read_text()

for path in [
    '/api/plugins', '/api/plugins/{plugin_id}/pair-code', '/api/plugins/{plugin_id}/deploy',
    '/api/plugin-deployments', '/api/plugin-deployments/{deployment_id}/retry',
    '/api/plugin-deployments/{deployment_id}/remove', '/api/hybrid-deployments/{deployment_id}/pair-code',
    '/api/hybrid-deployments/{deployment_id}/retry', '/api/hybrid-deployments/{deployment_id}/remove',
]:
    assert path in plugins, path
for path in ['/api/certificates', '/api/certificates/{certificate_id}/renew']:
    assert path in certs, path
for path in ['/api/fleet/operations', '/api/fleet/operations/{operation_id}']:
    assert path in fleet, path
for route_prefix in ['/api/plugins', '/api/plugin-deployments', '/api/hybrid-deployments', '/api/certificates', '/api/fleet/operations']:
    assert f'@app.get("{route_prefix}' not in app and f'@app.post("{route_prefix}' not in app and f'@app.delete("{route_prefix}' not in app
assert 'certificate_for_deployment' in app
assert 'def plugin_pair_code(' in app and 'def plugin_job_payload(' in app
assert 'lambda: queue_due_fleet_operations' in app
assert 'lambda: provision_node_for_fleet' in app
assert 'lambda: orchestrate_agent_upgrade' in app
assert "with_name('agent_control_router.py')" in app and "with_name('ssh_terminal_router.py')" in app
print('Plugin/Certificate/Fleet router boundary passed')
