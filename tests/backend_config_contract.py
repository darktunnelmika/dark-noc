from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/app.py').read_text()
config=(root/'hub/runtime_config.py').read_text()
for marker in ['def bounded_env_int(', 'def configured_public_hub_url(', 'SSH_UPLOAD_LIMIT =', 'SSH_RELAY_LIMIT =', 'NODE_PROVISION_TIMEOUT =', 'HUB_LEASE_SECONDS =']:
    assert marker in config
    assert marker not in app
assert "with_name('runtime_config.py')" in app
assert '_runtime_config_spec.loader.exec_module' in app
assert 'VERSION = \"2.9.20\"' in app
print('Backend runtime configuration module boundary passed')
