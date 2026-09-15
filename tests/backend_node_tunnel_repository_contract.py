from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
repo = (root / 'hub/node_tunnel_repository.py').read_text()
nodes = (root / 'hub/nodes_router.py').read_text()
tunnels = (root / 'hub/tunnels_router.py').read_text()
assert "with_name('node_tunnel_repository.py')" in app
assert '_node_tunnel_repository_spec.loader.exec_module' in app
for marker in ['def find_node_endpoint_conflict(', 'def fetch_node_inventory(', 'def fetch_tunnel_inventory(', 'def fetch_tunnel_operation_rows(']: assert marker in repo
assert 'fetch_node_inventory(db,' in nodes
assert 'fetch_tunnel_inventory(db)' in tunnels
assert 'fetch_tunnel_operation_rows(' in tunnels
assert "ORDER BY CASE role WHEN 'hub'" in repo
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' in repo
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' in repo
assert "ORDER BY CASE role WHEN 'hub'" not in app
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' not in app
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' not in app
assert 'VERSION = "2.9.44"' in app
print('Node/Tunnel repository query boundary passed')
