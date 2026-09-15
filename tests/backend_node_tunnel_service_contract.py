from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
service = (root / "hub/node_tunnel_service.py").read_text()
nodes = (root / "hub/nodes_router.py").read_text()
tunnels = (root / "hub/tunnels_router.py").read_text()
assert "with_name('node_tunnel_service.py')" in app
assert "_node_tunnel_service_spec.loader.exec_module" in app
assert 'VERSION = "2.9.35"' in app
for marker in ["def create_node_mutation(", "def prepare_node_provision_mutation(", "def delete_node_mutation(", "def update_node_mutation(", "def reset_node_fingerprint_mutation(", "def queue_tunnel_action_mutation(", "def queue_plugin_install_mutation(", "def reconfigure_tunnel_mutation(", "def remove_tunnel_mutation("]: assert marker in service
for call in ["create_node_mutation(", "prepare_node_provision_mutation(", "delete_node_mutation(", "update_node_mutation(", "reset_node_fingerprint_mutation(", "queue_plugin_install_mutation("]: assert call in nodes, call
for call in ["queue_tunnel_action_mutation(", "reconfigure_tunnel_mutation(", "remove_tunnel_mutation("]: assert call in tunnels, call
for marker in ["INSERT INTO nodes(name,region,role,host", "DELETE FROM nodes WHERE id=?", "UPDATE nodes SET name=?,region=?,role=?", "INSERT INTO jobs(node_id,kind,payload,created_by,created_at)", "UPDATE plugin_deployments SET settings=?,iran_job_id=?,kharej_job_id=?", "UPDATE hybrid_deployments SET settings=?,iran_job_id=?,pair_code_hash=?"]: assert marker in service
for decorator, owner in [('@router.post("/api/nodes", status_code=202)', nodes), ('@router.put("/api/nodes/{node_id}")', nodes), ('@router.delete("/api/nodes/{node_id}")', nodes), ('@router.post("/api/tunnels/{tunnel_id}/action", status_code=202)', tunnels), ('@router.put("/api/tunnels/{tunnel_id}", status_code=202)', tunnels), ('@router.delete("/api/tunnels/{tunnel_id}", status_code=202)', tunnels)]:
    assert decorator in owner
    assert decorator.replace('@router.', '@app.') not in app
assert 'background.add_task(get_provision_node(),' in nodes
assert 'get_provision_node=lambda: provision_node' in app
print("Node/Tunnel mutation service boundary passed")
