import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
agent_control = (root / "hub/agent_control_router.py").read_text()
service = (root / "hub/certificate_fleet_service.py").read_text()
cert_router = (root / "hub/certificates_router.py").read_text()
fleet_router = (root / "hub/fleet_router.py").read_text()

assert "with_name('certificate_fleet_service.py')" in app
assert "_certificate_fleet_service_spec.loader.exec_module" in app
for marker in [
    "def issue_certificate_mutation(", "def renew_certificate_mutation(",
    "def create_fleet_operation_mutation(", "def cancel_fleet_operation_mutation(",
]:
    assert marker in service

def functions(source: str):
    module = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
cert_functions = functions(cert_router)
fleet_functions = functions(fleet_router)
for name, call in {
    "issue_certificate": "issue_certificate_mutation(",
    "renew_certificate": "renew_certificate_mutation(",
}.items():
    source = cert_functions[name]
    assert call in source and "conn.execute(" not in source and "with db()" not in source
for name, call in {
    "create_fleet_operation": "create_fleet_operation_mutation(",
    "cancel_fleet_operation": "cancel_fleet_operation_mutation(",
}.items():
    source = fleet_functions[name]
    assert call in source and "conn.execute(" not in source and "with db()" not in source
assert "background.add_task(" in fleet_functions["create_fleet_operation"]
assert "get_provision_node_for_fleet()" in fleet_functions["create_fleet_operation"]
assert "get_orchestrate_agent_upgrade()" in fleet_functions["create_fleet_operation"]
assert "get_queue_due_fleet_operations()" in fleet_functions["create_fleet_operation"]
assert 'if row["kind"] == "certificate_issue":' in agent_control
assert "def queue_due_fleet_operations(" in app
assert 'VERSION = "2.9.45"' in app
print("Certificate/Fleet service boundary passed")
