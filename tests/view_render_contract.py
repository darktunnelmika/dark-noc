from pathlib import Path
app=Path('hub/static/app.js').read_text()
s=Path('hub/static/refresh-runtime.js').read_text()
assert 'function renderCachedView' in s
assert 'renderCachedView(name);' in app
assert 'populateSSHServers();renderTransferLimits();updateNavigationCounts();updateOverview(summary);' in s
assert 'renderNodes(); populateSSHServers(); renderTransferLimits(); renderTunnels(); renderPlugins(); renderCertificates(); renderMonitors();' not in s
print('View-aware render contract passed')
