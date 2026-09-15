from pathlib import Path
import ast

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
service = (root / "hub/node_tunnel_service.py").read_text()

assert "with_name('node_tunnel_service.py')" in app
assert "_node_tunnel_service_spec.loader.exec_module" in app
assert 'VERSION = "2.9.26"' in app

for marker in [
    "def create_node_mutation(",
    "def prepare_node_provision_mutation(",
    "def delete_node_mutation(",
    "def update_node_mutation(",
    "def reset_node_fingerprint_mutation(",
    "def queue_tunnel_action_mutation(",
    "def queue_plugin_install_mutation(",
    "def reconfigure_tunnel_mutation(",
    "def remove_tunnel_mutation(",
]:
    assert marker in service

source_tree = ast.parse(app)
lines = app.splitlines(True)

def source(name: str) -> str:
    node = next(item for item in source_tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    return "".join(lines[node.lineno - 1:node.end_lineno])

expected_calls = {
    "create_node": "create_node_mutation(",
    "retry_node_provision": "prepare_node_provision_mutation(",
    "delete_node": "delete_node_mutation(",
    "update_node": "update_node_mutation(",
    "reset_ssh_fingerprint": "reset_node_fingerprint_mutation(",
    "tunnel_action": "queue_tunnel_action_mutation(",
    "install_plugin": "queue_plugin_install_mutation(",
    "reconfigure_tunnel": "reconfigure_tunnel_mutation(",
    "remove_tunnel_from_manager": "remove_tunnel_mutation(",
}
for name, call in expected_calls.items():
    route = source(name)
    assert call in route, (name, call)
    assert "INSERT INTO jobs" not in route, name
    assert "UPDATE plugin_deployments SET" not in route, name

for name in ["create_node", "delete_node", "update_node", "reset_ssh_fingerprint"]:
    route = source(name)
    assert "INSERT INTO nodes" not in route, name
    assert "DELETE FROM nodes" not in route, name
    assert "UPDATE nodes SET name=" not in route, name

for marker in [
    "INSERT INTO nodes(name,region,role,host",
    "DELETE FROM nodes WHERE id=?",
    "UPDATE nodes SET name=?,region=?,role=?",
    "INSERT INTO jobs(node_id,kind,payload,created_by,created_at)",
    "UPDATE plugin_deployments SET settings=?,iran_job_id=?,kharej_job_id=?",
    "UPDATE hybrid_deployments SET settings=?,iran_job_id=?,pair_code_hash=?",
]:
    assert marker in service

for decorator in [
    '@app.post("/api/nodes", status_code=202)',
    '@app.put("/api/nodes/{node_id}")',
    '@app.delete("/api/nodes/{node_id}")',
    '@app.post("/api/tunnels/{tunnel_id}/action", status_code=202)',
    '@app.put("/api/tunnels/{tunnel_id}", status_code=202)',
    '@app.delete("/api/tunnels/{tunnel_id}", status_code=202)',
]:
    assert decorator in app

print("Node/Tunnel mutation service boundary passed")
