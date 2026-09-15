// DARK NOC cached/live refresh orchestration. Classic-script globals are intentional.
async function refreshLive() {
  if (liveRefreshInFlight || refreshInFlight || document.hidden) return;
  liveRefreshInFlight = true;
  try {
    const [summary,nodes,tunnels] = await Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels')]);
    state = { ...state, nodes, tunnels, hubVersion:summary.version||state.hubVersion, limits:dashboardLimits(summary) };
    renderLiveTopology();
    updateOverview(summary);
    const active=$('.view.active')?.id;
    if(active==='view-servers'){renderNodes();populateSSHServers();}
    else if(active==='view-tunnels')renderTunnels();
    $('#sync-time').textContent='NOW';
  } catch (error) {
    if (error.message !== 'Authentication required') $('#connection-banner').innerHTML = '<span class="offline-source">HUB CONNECTION LOST</span> Retrying automatically.';
  } finally { liveRefreshInFlight = false; }
}

function updateNavigationCounts() {
  $('#monitor-count').textContent=state.monitors.length;
  $('#cert-count').textContent=state.certificates.filter(item=>item.status==='valid').length;
  const openIncidents=state.incidents.filter(item=>['open','acknowledged'].includes(item.status)).length;
  $$('.nav-item b.danger').forEach(item=>item.textContent=openIncidents);
}

function renderCachedView(name = $('.view.active')?.id?.replace(/^view-/, '') || 'overview') {
  if(name==='overview'){renderLiveTopology();renderLiveIncident();renderNodeHealth();renderTrafficChart();return;}
  if(name==='servers'){renderNodes();return;}
  if(name==='tunnels'){renderTunnels();renderPlugins();return;}
  if(name==='certificates'){renderCertificates();return;}
  if(name==='monitors'){renderMonitors();return;}
  if(name==='fleet'){renderFleetOperations();renderVersionCompliance();return;}
  if(name==='incidents'){renderIncidents();return;}
  if(name==='terminal'){populateSSHServers();renderTransferLimits();}
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const [summary,nodes,tunnels,incidents,plugins,deployments,certificates,traffic,monitors,fleetOperations] = await Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels'),api('/api/incidents'),api('/api/plugins'),api('/api/plugin-deployments'),api('/api/certificates'),api('/api/dashboard/traffic?minutes=60'),api('/api/monitors'),api('/api/fleet/operations?limit=50')]);
    state = { ...state, nodes, tunnels, incidents, plugins, deployments, certificates, traffic, monitors, fleetOperations, hubVersion:summary.version||state.hubVersion, limits:dashboardLimits(summary) };
    populateSSHServers();renderTransferLimits();updateNavigationCounts();updateOverview(summary);
    renderCachedView();
    if(state.selectedIncident&&$('.view.active')?.id==='view-incidents')loadIncidentDetail(state.selectedIncident);
    $('#sync-time').textContent = 'NOW';
  } catch (error) {
    if (error.message !== 'Authentication required') {
      $('#connection-banner').innerHTML = '<span class="offline-source">HUB CONNECTION LOST</span> Retrying automatically.';
    }
  } finally {
    refreshInFlight = false;
  }
}
