from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
nodes = (root / 'hub/nodes_router.py').read_text()
tunnels = (root / 'hub/tunnels_router.py').read_text()
assert "with_name('nodes_router.py')" in app and "with_name('tunnels_router.py')" in app
assert 'register_nodes_router(' in app and 'register_tunnels_router(' in app
for path in ['/api/nodes', '/api/nodes/{node_id}/provision', '/api/nodes/{node_id}', '/api/nodes/{node_id}/ssh-fingerprint', '/api/nodes/{node_id}/metrics', '/api/nodes/{node_id}/plugins/{plugin_id}/install', '/api/nodes/{node_id}/jobs']: assert path in nodes, path
for path in ['/api/tunnels', '/api/tunnels/{tunnel_id}/operations', '/api/tunnels/{tunnel_id}/action', '/api/tunnels/{tunnel_id}']: assert path in tunnels, path
assert '@app.get("/api/nodes")' not in app and '@app.get("/api/tunnels")' not in app
assert "with_name('ssh_file_router.py')" in app and "with_name('agent_control_router.py')" in app
assert 'background.add_task(get_provision_node(),' in nodes
assert 'VERSION = "2.9.43"' in app
print('Node/Tunnel router boundary passed')
