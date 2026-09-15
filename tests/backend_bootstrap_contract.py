from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
bootstrap = (root / 'hub/database_bootstrap.py').read_text()
assert "with_name('database_bootstrap.py')" in app
assert '_database_bootstrap_spec.loader.exec_module' in app
assert 'def bootstrap() -> None:' in app
assert 'bootstrap_database(' in app
assert 'def bootstrap_database(' in bootstrap
assert 'ALTER TABLE plugin_deployments' in bootstrap
assert 'ALTER TABLE nodes' in bootstrap
assert 'interrupted_operations' in bootstrap
assert 'DARK_NOC_ADMIN_PASSWORD' in bootstrap
assert 'ALTER TABLE plugin_deployments' not in app
assert 'interrupted_operations = conn.execute' not in app
assert 'VERSION = \"2.9.41\"' in app
print('Backend database bootstrap module boundary passed')
