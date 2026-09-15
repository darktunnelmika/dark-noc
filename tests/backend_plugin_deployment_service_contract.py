from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
router = (root / 'hub/plugin_deployments_router.py').read_text()
service = (root / 'hub/plugin_deployment_service.py').read_text()
assert "with_name('plugin_deployment_service.py')" in app
assert '_plugin_deployment_service_spec.loader.exec_module' in app
for marker in [
    'def deploy_pair_code_mutation(', 'def deploy_managed_mutation(',
    'def recover_pair_code_mutation(', 'def retry_hybrid_mutation(',
    'def remove_hybrid_mutation(', 'def retry_managed_mutation(', 'def remove_managed_mutation(',
]:
    assert marker in service
for route in [
    '@router.post("/api/plugins/{plugin_id}/pair-code", status_code=202)',
    '@router.post("/api/plugins/{plugin_id}/deploy", status_code=202)',
    '@router.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)',
    '@router.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)',
    '@router.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)',
    '@router.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)',
]:
    assert route in router
    assert route.replace('@router.', '@app.') not in app
for call in [
    'deploy_pair_code_mutation(', 'deploy_managed_mutation(', 'recover_pair_code_mutation(',
    'retry_hybrid_mutation(', 'remove_hybrid_mutation(', 'retry_managed_mutation(', 'remove_managed_mutation(',
]:
    assert call in router
for sql in [
    'INSERT INTO hybrid_deployments(', 'INSERT INTO plugin_deployments(',
    "UPDATE hybrid_deployments SET iran_job_id=?,updated_at=?",
    "UPDATE plugin_deployments SET lifecycle='active'",
    "UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing'",
]:
    assert sql in service
    assert sql not in app
assert '@router.get("/api/plugin-deployments")' in router
assert 'VERSION = "2.9.35"' in app
print('Plugin Deployment service boundary passed')
