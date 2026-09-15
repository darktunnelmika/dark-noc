import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app_path = root / "hub/app.py"
app = app_path.read_text()
service = (root / "hub/certificate_fleet_service.py").read_text()

assert "with_name('certificate_fleet_service.py')" in app
assert "_certificate_fleet_service_spec.loader.exec_module" in app
for marker in [
    "def issue_certificate_mutation(",
    "def renew_certificate_mutation(",
    "def create_fleet_operation_mutation(",
    "def cancel_fleet_operation_mutation(",
]:
    assert marker in service

module = ast.parse(app)
functions = {
    node.name: ast.get_source_segment(app, node) or ""
    for node in ast.walk(module)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
for name, call in {
    "issue_certificate": "issue_certificate_mutation(",
    "renew_certificate": "renew_certificate_mutation(",
    "create_fleet_operation": "create_fleet_operation_mutation(",
    "cancel_fleet_operation": "cancel_fleet_operation_mutation(",
}.items():
    source = functions[name]
    assert call in source
    assert "conn.execute(" not in source
    assert "with db()" not in source

# Orchestration and telemetry ownership stay in app.py.
assert "background.add_task(" in functions["create_fleet_operation"]
assert "provision_node_for_fleet" in functions["create_fleet_operation"]
assert "orchestrate_agent_upgrade" in functions["create_fleet_operation"]
assert 'if row["kind"] == "certificate_issue":' in app
assert "def queue_due_fleet_operations(" in app
assert 'VERSION = "2.9.29"' in app
print("Certificate/Fleet service boundary passed")
