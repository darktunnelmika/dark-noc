// DARK NOC cached/live refresh orchestration. Classic-script globals are intentional.
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
