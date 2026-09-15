from pathlib import Path

ROOT = Path('.')
old_version = '2.9.36'
new_version = '2.9.37'

refresh = ROOT / 'hub/static/refresh-runtime.js'
refresh.write_text(r'''// DARK NOC cached/live refresh orchestration. Classic-script globals are intentional.
const REFRESH_ENDPOINTS = Object.freeze({
  summary: '/api/dashboard',
  nodes: '/api/nodes',
  tunnels: '/api/tunnels',
  incidents: '/api/incidents',
  plugins: '/api/plugins',
  deployments: '/api/plugin-deployments',
  certificates: '/api/certificates',
  traffic: '/api/dashboard/traffic?minutes=60',
  monitors: '/api/monitors',
  fleetOperations: '/api/fleet/operations?limit=50'
});

const VIEW_REFRESH_PLAN = Object.freeze({
  overview: ['incidents', 'traffic'],
  servers: [],
  tunnels: ['plugins', 'deployments', 'certificates'],
  monitors: ['monitors'],
  fleet: ['fleetOperations'],
  certificates: ['certificates'],
  incidents: ['incidents'],
  terminal: []
});

function activeViewName() {
  return $('.view.active')?.id?.replace(/^view-/, '') || 'overview';
}

async function fetchRefreshData(keys) {
  const unique = [...new Set(keys)];
  const values = await Promise.all(unique.map(key => api(REFRESH_ENDPOINTS[key])));
  return Object.fromEntries(unique.map((key, index) => [key, values[index]]));
}

function applyRefreshData(data) {
  const next = { ...state };
  for (const key of ['nodes','tunnels','incidents','plugins','deployments','certificates','traffic','monitors','fleetOperations']) {
    if (Object.prototype.hasOwnProperty.call(data, key)) next[key] = data[key];
  }
  const summary = data.summary || null;
  if (summary) {
    next.hubVersion = summary.version || next.hubVersion;
    next.limits = dashboardLimits(summary);
  }
  state = next;
  return summary;
}

function refreshFailure(error) {
  if (error.message !== 'Authentication required') {
    $('#connection-banner').innerHTML = '<span class="offline-source">HUB CONNECTION LOST</span> Retrying automatically.';
  }
}

async function refreshLive() {
  if (liveRefreshInFlight || refreshInFlight || viewRefreshInFlight || document.hidden) return;
  liveRefreshInFlight = true;
  try {
    const data = await fetchRefreshData(['summary','nodes','tunnels']);
    const summary = applyRefreshData(data);
    const active = activeViewName();
    if (active === 'overview') { renderLiveTopology(); renderNodeHealth(); }
    else if (active === 'servers') { renderNodes(); populateSSHServers(); }
    else if (active === 'tunnels') { renderTunnels(); renderPlugins(); }
    else if (active === 'terminal') populateSSHServers();
    updateNavigationCounts(summary);
    updateOverview(summary);
    $('#sync-time').textContent='NOW';
  } catch (error) {
    refreshFailure(error);
  } finally { liveRefreshInFlight = false; }
}

function updateNavigationCounts(summary = null) {
  $('#monitor-count').textContent=state.monitors.length;
  $('#cert-count').textContent=state.certificates.filter(item=>item.status==='valid').length;
  const cachedOpen=state.incidents.filter(item=>['open','acknowledged'].includes(item.status)).length;
  const openIncidents=summary&&Number.isFinite(Number(summary.open_incidents))?Number(summary.open_incidents):cachedOpen;
  $$('.nav-item b.danger').forEach(item=>item.textContent=openIncidents);
}

function renderCachedView(name = activeViewName()) {
  if(name==='overview'){renderLiveTopology();renderLiveIncident();renderNodeHealth();renderTrafficChart();return;}
  if(name==='servers'){renderNodes();return;}
  if(name==='tunnels'){renderTunnels();renderPlugins();return;}
  if(name==='certificates'){renderCertificates();return;}
  if(name==='monitors'){renderMonitors();return;}
  if(name==='fleet'){renderFleetOperations();renderVersionCompliance();return;}
  if(name==='incidents'){renderIncidents();return;}
  if(name==='terminal'){populateSSHServers();renderTransferLimits();}
}

function refreshCommonUI(summary) {
  populateSSHServers();
  renderTransferLimits();
  updateNavigationCounts(summary);
  updateOverview(summary);
}

async function refreshActiveView(name = activeViewName(), { force = false } = {}) {
  if (!VIEW_REFRESH_PLAN[name]) name = 'overview';
  if ((!force && document.hidden) || refreshInFlight || viewRefreshInFlight) return;
  viewRefreshInFlight = true;
  try {
    const data = await fetchRefreshData(['summary','nodes','tunnels', ...VIEW_REFRESH_PLAN[name]]);
    const summary = applyRefreshData(data);
    refreshCommonUI(summary);
    renderCachedView(name);
    if (name === 'incidents' && state.selectedIncident) loadIncidentDetail(state.selectedIncident);
    $('#sync-time').textContent = 'NOW';
  } catch (error) {
    refreshFailure(error);
  } finally {
    viewRefreshInFlight = false;
    if (refreshPendingFull) {
      refreshPendingFull = false;
      queueMicrotask(refresh);
    }
  }
}

async function refresh() {
  if (refreshInFlight || viewRefreshInFlight) {
    refreshPendingFull = true;
    return;
  }
  refreshInFlight = true;
  try {
    const data = await fetchRefreshData(Object.keys(REFRESH_ENDPOINTS));
    const summary = applyRefreshData(data);
    refreshCommonUI(summary);
    renderCachedView();
    if(state.selectedIncident&&activeViewName()==='incidents')loadIncidentDetail(state.selectedIncident);
    $('#sync-time').textContent = 'NOW';
  } catch (error) {
    refreshFailure(error);
  } finally {
    refreshInFlight = false;
    if (refreshPendingFull) {
      refreshPendingFull = false;
      queueMicrotask(refresh);
    }
  }
}
''')

state_path = ROOT / 'hub/static/state.js'
state = state_path.read_text()
old = 'let refreshInFlight = false;\nlet liveRefreshInFlight = false;'
new = 'let refreshInFlight = false;\nlet viewRefreshInFlight = false;\nlet refreshPendingFull = false;\nlet liveRefreshInFlight = false;'
if old not in state:
    raise RuntimeError('state refresh flags anchor not found')
state_path.write_text(state.replace(old, new, 1))

app_path = ROOT / 'hub/static/app.js'
app = app_path.read_text()
old = "  renderCachedView(name);\n}"
new = "  renderCachedView(name);\n  refreshActiveView(name);\n}"
if old not in app:
    raise RuntimeError('switchView render anchor not found')
app = app.replace(old, new, 1)
old = "updateClock(); setInterval(updateClock,1000); setInterval(refresh,15000); boot();"
new = "updateClock(); setInterval(updateClock,1000); setInterval(()=>refreshActiveView(),15000);\ndocument.addEventListener('visibilitychange',()=>{if(!document.hidden&&$('#login-gate').classList.contains('hidden'))refreshActiveView(activeViewName(),{force:true});});\nboot();"
if old not in app:
    raise RuntimeError('periodic refresh anchor not found')
app_path.write_text(app.replace(old, new, 1))

session_path = ROOT / 'hub/static/session-runtime.js'
session = session_path.read_text()
old = '  clearTimeout(connectLive.reconnectTimer);\n  clearTimeout(connectLive.refreshTimer);'
new = '  clearTimeout(connectLive.reconnectTimer);\n  clearTimeout(connectLive.refreshTimer);\n  clearTimeout(connectLive.fullRefreshTimer);'
if old not in session:
    raise RuntimeError('session timeout cleanup anchor not found')
session = session.replace(old, new, 1)
old = 'connectLive.fullRefreshTimer=setTimeout(refresh,1200);'
new = 'connectLive.fullRefreshTimer=setTimeout(()=>refreshActiveView(),1200);'
if old not in session:
    raise RuntimeError('WebSocket full refresh anchor not found')
session_path.write_text(session.replace(old, new, 1))

# Update contracts whose ownership/fetch expectations intentionally changed.
topology_contract = ROOT / 'tests/topology_refresh_contract.py'
topology_contract.write_text('''from pathlib import Path\napp=Path('hub/static/app.js').read_text()\nrefresh=Path('hub/static/refresh-runtime.js').read_text()\nsession=Path('hub/static/session-runtime.js').read_text()\nassert 'function syncLiveTopologyRoutes' in app\nassert 'topology.dataset.topologySignature===structureSignature' in app\nassert 'setTimeout(refreshLive,350)' in session\nassert 'now-(connectLive.lastFullRefresh||0)>=30000' in session\nassert 'setTimeout(()=>refreshActiveView(),1200)' in session\nassert "fetchRefreshData(['summary','nodes','tunnels'])" in refresh\nassert "if (active === 'overview') { renderLiveTopology(); renderNodeHealth(); }" in refresh\nprint('Topology refresh contract passed')\n''')

view_contract = ROOT / 'tests/view_render_contract.py'
view_contract.write_text('''from pathlib import Path\napp=Path('hub/static/app.js').read_text()\ns=Path('hub/static/refresh-runtime.js').read_text()\nassert 'function renderCachedView' in s\nassert 'renderCachedView(name);' in app\nassert 'refreshActiveView(name);' in app\nassert 'function refreshCommonUI(summary)' in s\nassert 'renderCachedView(name);' in s\nassert 'renderNodes(); populateSSHServers(); renderTransferLimits(); renderTunnels(); renderPlugins(); renderCertificates(); renderMonitors();' not in s\nprint('View-aware render contract passed')\n''')

fetch_contract = ROOT / 'tests/frontend_fetch_contract.py'
fetch_contract.write_text('''from pathlib import Path\nroot=Path(__file__).resolve().parents[1]\nrefresh=(root/'hub/static/refresh-runtime.js').read_text()\nstate=(root/'hub/static/state.js').read_text()\napp=(root/'hub/static/app.js').read_text()\nsession=(root/'hub/static/session-runtime.js').read_text()\nfor marker in [\n    "const REFRESH_ENDPOINTS = Object.freeze({",\n    "const VIEW_REFRESH_PLAN = Object.freeze({",\n    "async function refreshActiveView(",\n    "fetchRefreshData(['summary','nodes','tunnels', ...VIEW_REFRESH_PLAN[name]])",\n    "fetchRefreshData(Object.keys(REFRESH_ENDPOINTS))",\n]:\n    assert marker in refresh, marker\nfor marker in [\n    "overview: ['incidents', 'traffic']",\n    "tunnels: ['plugins', 'deployments', 'certificates']",\n    "monitors: ['monitors']",\n    "fleet: ['fleetOperations']",\n    "certificates: ['certificates']",\n    "incidents: ['incidents']",\n]:\n    assert marker in refresh, marker\nassert 'let viewRefreshInFlight = false;' in state\nassert 'let refreshPendingFull = false;' in state\nassert 'setInterval(()=>refreshActiveView(),15000)' in app\nassert "refreshActiveView(activeViewName(),{force:true})" in app\nassert 'setTimeout(()=>refreshActiveView(),1200)' in session\nassert 'clearTimeout(connectLive.fullRefreshTimer);' in session\nassert "if (active === 'overview') { renderLiveTopology(); renderNodeHealth(); }" in refresh\nassert "renderLiveTopology();\n    updateOverview" not in refresh\nprint('View-aware fetch contract passed')\n''')

# Frontend runtime contract now owns the active-view polling function too.
frontend_contract = ROOT / 'tests/frontend_runtime_contract.py'
fc = frontend_contract.read_text()
fc = fc.replace("'async function refreshLive()','function updateNavigationCounts()','function renderCachedView(','async function refresh()'", "'async function refreshLive()','function updateNavigationCounts(','function renderCachedView(','async function refreshActiveView(','async function refresh()'")
fc = fc.replace("'let refreshInFlight = false;','let liveSocket = null;'", "'let refreshInFlight = false;','let viewRefreshInFlight = false;','let liveSocket = null;'")
frontend_contract.write_text(fc)

version_paths = [ROOT/'hub/app.py', ROOT/'agent/agent.py', ROOT/'darknoc', ROOT/'install-hub.sh', ROOT/'install-node.sh', ROOT/'upgrade.sh', ROOT/'hub/static/index.html']
version_paths += sorted((ROOT/'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.37 — View-Aware Fetch Scheduling\n\n- Replace the broad 10-endpoint 15-second frontend poll with active-view fetch plans.\n- Keep Dashboard, Nodes and Tunnels as the lightweight shared live base; fetch Incidents/Traffic, Plugins/Deployments/Certificates, Monitors or Fleet data only when the owning view is active.\n- Keep full refresh for boot/login and explicit post-mutation synchronization.\n- Make WebSocket catch-up use the active-view plan instead of triggering a broad full refresh every 30 seconds.\n- Skip periodic view polling while the document is hidden and refresh the active view immediately when it becomes visible again.\n- Coalesce a requested full refresh behind an in-flight view refresh so post-mutation state is not lost.\n- Stop rebuilding hidden Live Matrix DOM on telemetry updates; the approved topology appearance and always-visible connected path invariant are unchanged.\n- Add permanent view-aware fetch regression coverage.\n\n## فارسی\n\nPolling پنل سبک‌تر شد: به‌جای گرفتن همه endpointها هر ۱۵ ثانیه، فقط داده‌های مشترک و داده‌های صفحه فعال دریافت می‌شوند. هنگام برگشت به تب مرورگر صفحه فعال فوراً sync می‌شود و ظاهر Live Matrix هیچ تغییری نکرده است.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.37\n- Performance: active-view frontend fetch scheduling replaces broad periodic polling.\n- WebSocket catch-up now refreshes only the active view dependencies.\n- Hidden tabs pause periodic view polling and sync immediately on visibility restore.\n- Full refreshes remain serialized and are queued behind active-view refreshes when required.\n- No Live Matrix visual changes.\n\n''' + changelog.read_text())

doc = ROOT / 'docs/frontend-fetch-audit-v2.9.37.md'
doc.write_text('''# DARK NOC Frontend Fetch Audit — v2.9.37\n\n## Previous behavior\nThe 15-second timer fetched Dashboard, Nodes, Tunnels, Incidents, Plugins, Deployments, Certificates, Traffic, Monitors and Fleet Operations regardless of which view was visible. Telemetry WebSocket activity also scheduled the same broad refresh every 30 seconds.\n\n## v2.9.37 behavior\nEvery active-view refresh always updates Dashboard + Nodes + Tunnels. Additional resources are scoped to the visible view:\n\n- Overview: Incidents + 60-minute Traffic\n- Servers: shared live base only\n- Tunnels: Plugins + Deployments + Certificates\n- Monitors: Monitors\n- Fleet: Fleet Operations\n- Certificates: Certificates\n- Incidents: Incidents\n- Terminal: shared live base only\n\nBoot/login and explicit post-mutation `refresh()` calls still perform the complete synchronization. Hidden browser tabs pause periodic view polling and immediately refresh the active view when visible again.\n\nThe Live Matrix rendering contract is unchanged: a connected path remains continuously visible independent of traffic volume.\n''')

print('prepared v2.9.37 view-aware fetch scheduling')
