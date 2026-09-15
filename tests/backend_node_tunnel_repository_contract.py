from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
repo = (root / 'hub/node_tunnel_repository.py').read_text()
assert "with_name('node_tunnel_repository.py')" in app
assert '_node_tunnel_repository_spec.loader.exec_module' in app
for marker in ['def find_node_endpoint_conflict(', 'def fetch_node_inventory(', 'def fetch_tunnel_inventory(', 'def fetch_tunnel_operation_rows(']:
    assert marker in repo
assert 'fetch_node_inventory(db,' in app
assert 'fetch_tunnel_inventory(db)' in app
assert 'fetch_tunnel_operation_rows(' in app
assert "ORDER BY CASE role WHEN 'hub'" in repo
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' in repo
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' in repo
assert "ORDER BY CASE role WHEN 'hub'" not in app
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' not in app
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' not in app
assert 'VERSION = "2.9.26"' in app
print('Node/Tunnel repository query boundary passed')
