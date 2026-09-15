from pathlib import Path
app=Path('hub/static/app.js').read_text()
refresh=Path('hub/static/refresh-runtime.js').read_text()
session=Path('hub/static/session-runtime.js').read_text()
assert 'function syncLiveTopologyRoutes' in app
assert 'topology.dataset.topologySignature===structureSignature' in app
assert 'setTimeout(refreshLive,350)' in session
assert 'now-(connectLive.lastFullRefresh||0)>=30000' in session
assert 'setTimeout(refresh,250)' not in session
assert "Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels')])" in refresh
print('Topology refresh contract passed')
