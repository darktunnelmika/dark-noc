from pathlib import Path
root=Path(__file__).resolve().parents[1]
refresh=(root/'hub/static/refresh-runtime.js').read_text()
state=(root/'hub/static/state.js').read_text()
app=(root/'hub/static/app.js').read_text()
session=(root/'hub/static/session-runtime.js').read_text()
for marker in [
    "const REFRESH_ENDPOINTS = Object.freeze({",
    "const VIEW_REFRESH_PLAN = Object.freeze({",
    "async function refreshActiveView(",
    "fetchRefreshData(['summary','nodes','tunnels', ...VIEW_REFRESH_PLAN[name]])",
    "fetchRefreshData(Object.keys(REFRESH_ENDPOINTS))",
]:
    assert marker in refresh, marker
for marker in [
    "overview: ['incidents', 'traffic']",
    "tunnels: ['plugins', 'deployments', 'certificates']",
    "monitors: ['monitors']",
    "fleet: ['fleetOperations']",
    "certificates: ['certificates']",
    "incidents: ['incidents']",
]:
    assert marker in refresh, marker
assert 'let viewRefreshInFlight = false;' in state
assert 'let refreshPendingFull = false;' in state
assert 'setInterval(()=>refreshActiveView(),15000)' in app
assert "refreshActiveView(activeViewName(),{force:true})" in app
assert 'setTimeout(()=>refreshActiveView(),1200)' in session
assert 'clearTimeout(connectLive.fullRefreshTimer);' in session
assert "if (active === 'overview') { renderLiveTopology(); renderNodeHealth(); }" in refresh
print('View-aware fetch contract passed')
