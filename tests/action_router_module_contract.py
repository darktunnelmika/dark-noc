from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
index=(root/'hub/static/index.html').read_text()
modules={name:(root/'hub/static'/name).read_text() for name in ['node-actions.js','plugin-actions.js','tunnel-actions.js','ops-actions.js']}
assert "handleNodeActions(event)" in app and "handlePluginActions(event)" in app and "handleTunnelActions(event)" in app and "handleOpsActions(event)" in app
router=app[app.index("document.addEventListener('click'"):app.index("$('#plugin-mode')")]
for marker in ['pluginDeploy','incidentAck','autohealNode','certRenew','terminalClose']:
    assert marker not in router
assert 'function handleNodeActions(event)' in modules['node-actions.js']
assert 'function handlePluginActions(event)' in modules['plugin-actions.js']
assert 'function handleTunnelActions(event)' in modules['tunnel-actions.js']
assert 'function handleOpsActions(event)' in modules['ops-actions.js']
order=['tunnel-operations.js','node-actions.js','plugin-actions.js','tunnel-actions.js','ops-actions.js','app.js']
positions=[index.index(item) for item in order]
assert positions==sorted(positions)
print('Delegated action router module boundary passed')
