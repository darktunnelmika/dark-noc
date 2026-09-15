from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
state=(root/'hub/static/state.js').read_text()
api=(root/'hub/static/api-client.js').read_text()
refresh=(root/'hub/static/refresh-runtime.js').read_text()
session=(root/'hub/static/session-runtime.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['let state = {','let refreshInFlight = false;','let viewRefreshInFlight = false;','let liveSocket = null;']:
    assert marker in state and marker not in app
for marker in ['async function api(','function dashboardLimits(','async function createJob(','async function waitForJob(']:
    assert marker in api and marker not in app
for marker in ['async function refreshLive()','function updateNavigationCounts(','function renderCachedView(','async function refreshActiveView(','async function refresh()']:
    assert marker in refresh and marker not in app
for marker in ['function terminateAuthenticatedActivity(','async function boot()','function connectLive()']:
    assert marker in session and marker not in app
order=['core.js','state.js','api-client.js','topology.js','refresh-runtime.js','session-runtime.js','ops-actions.js','app.js']
pos=[index.index(item) for item in order]
assert pos==sorted(pos)
assert "$('#login-form').addEventListener" in app
assert 'boot();' in app
print('Frontend runtime ownership contract passed')
