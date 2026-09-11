const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const viewTitles = { overview: 'Network Command', servers: 'Server Fleet', tunnels: 'Tunnel Matrix', monitors: 'Synthetic Monitoring', fleet: 'Fleet Operations', certificates: 'TLS Vault', incidents: 'Incident Command', terminal: 'SSH Command' };
let state = {
  nodes: [], tunnels: [], incidents: [], plugins: [], deployments: [], certificates: [], traffic: [], monitors: [], fleetOperations: [],
  limits: { ssh_upload_bytes: null, ssh_relay_bytes: null }, sockets: new Map(), terminals: new Map(),
  selectedNode: null, selectedTunnel: null, selectedIncident: null, selectedPlugin: 'dark-backhaul',
  nodeFilter: 'all', topologyFilter: 'all', topologySearch: '', editingNode: null, editingMonitor: null,
  files: { nodeId: null, path: '/root', parent: '/', entries: [], selected: null, editingPath: null }
};
let refreshInFlight = false;
let liveSocket = null;
let activeUpload = null;
let activeRelay = null;
let authenticatedActivityTerminated = false;

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (response.status === 401 && path !== '/api/auth/login') {
    terminateAuthenticatedActivity();
    throw new Error('Authentication required');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail=Array.isArray(payload.detail)?payload.detail.map(item=>item.msg||JSON.stringify(item)).join(' · '):payload.detail;
    throw new Error(typeof detail==='string'?detail:`Request failed (${response.status})`);
  }
  return payload;
}

function terminateAuthenticatedActivity({showLoginGate = true, transferMessage = 'AUTHENTICATION EXPIRED · Sign in again', terminalStatus = '● AUTH EXPIRED'} = {}) {
  if (showLoginGate) $('#login-gate').classList.remove('hidden');
  clearTimeout(connectLive.reconnectTimer);
  clearTimeout(connectLive.refreshTimer);
  if (authenticatedActivityTerminated) return;
  authenticatedActivityTerminated = true;

  if (liveSocket) {
    const socket = liveSocket;
    liveSocket = null;
    socket.intentionalClose = true;
    clearInterval(socket.keepaliveTimer);
    try { socket.close(); } catch {}
  }

  [...state.sockets.keys()].forEach(closeSocket);
  $$('.ssh-online').forEach(status => { status.textContent = terminalStatus; });

  if (activeUpload) {
    const upload = activeUpload;
    activeUpload = null;
    try { upload.abort(); } catch {}
    const form = $('#ssh-upload-form');
    if (form) setTransferControls(form, false);
    transferStatus('#upload-progress', 'error', transferMessage, 0);
  }

  if (activeRelay) {
    const relay = activeRelay;
    activeRelay = null;
    try { relay.abort(); } catch {}
    const form = $('#ssh-relay-form');
    if (form) setTransferControls(form, false);
    transferStatus('#relay-progress', 'error', transferMessage, 0);
  }
}

function bytesPerSecond(value) {
  const n = Number(value || 0);
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} Gb/s`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} Mb/s`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} Kb/s`;
  return `${n.toFixed(0)} b/s`;
}

function fileSize(value) {
  const bytes=Number(value||0),units=['B','KB','MB','GB','TB'];let size=bytes,index=0;
  while(size>=1024&&index<units.length-1){size/=1024;index+=1;}
  return `${size.toFixed(index?2:0)} ${units[index]}`;
}

function transferStatus(selector, stateName, message, percent = null) {
  const element=$(selector);if(!element)return;
  element.className=`transfer-progress ${stateName||''}`.trim();
  const bar=$('i',element),label=$('span',element);if(percent!==null){bar.style.width=`${Math.max(0,Math.min(100,percent))}%`;bar.style.animation='none';}else{bar.style.width='';bar.style.animation='';}
  label.textContent=message;
  element.setAttribute('aria-valuetext',message);
  element.setAttribute('aria-busy',stateName==='running'?'true':'false');
  if(percent===null){element.removeAttribute('aria-valuenow');element.removeAttribute('aria-valuemin');element.removeAttribute('aria-valuemax');}
  else{element.setAttribute('aria-valuemin','0');element.setAttribute('aria-valuemax','100');element.setAttribute('aria-valuenow',String(Math.max(0,Math.min(100,percent))));}
}

function setTransferControls(form, running) {
  const submit=$('button[type="submit"]',form),cancel=$('.transfer-cancel',form);
  submit.disabled=running;submit.setAttribute('aria-disabled',String(running));
  cancel.hidden=!running;cancel.disabled=!running;
}

function dashboardLimits(summary) {
  const limits=summary.limits||{};
  return {
    ssh_upload_bytes:Number(limits.ssh_upload_bytes??summary.ssh_upload_limit??summary.ssh_upload_bytes)||null,
    ssh_relay_bytes:Number(limits.ssh_relay_bytes??summary.ssh_relay_limit??summary.ssh_relay_bytes)||null
  };
}

function renderTransferLimits() {
  const upload=state.limits.ssh_upload_bytes,relay=state.limits.ssh_relay_bytes;
  if($('#upload-limit'))$('#upload-limit').textContent=upload?`Maximum upload: ${fileSize(upload)}.`:'Maximum upload size is enforced by the Hub.';
  if($('#relay-limit'))$('#relay-limit').textContent=relay?`Maximum relay: ${fileSize(relay)}.`:'Maximum relay size is enforced by the Hub.';
}

function requireClipboard(method, action) {
  if(navigator.clipboard&&typeof navigator.clipboard[method]==='function')return true;
  showToast(`${action} UNAVAILABLE`,'Clipboard access is unavailable in this browser or connection.',true);
  return false;
}

function relativeTime(timestamp) {
  if (!timestamp) return 'never';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (seconds < 10) return 'now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

function duration(timestamp) {
  const total=Math.max(0,Math.floor(Date.now()/1000-Number(timestamp||Date.now()/1000)));
  const hours=Math.floor(total/3600), minutes=Math.floor(total%3600/60), seconds=total%60;
  return [hours,minutes,seconds].map(value=>String(value).padStart(2,'0')).join(':');
}

function elapsedDuration(totalSeconds) {
  const total=Math.max(0,Math.floor(Number(totalSeconds||0))),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),seconds=total%60;
  return [hours,minutes,seconds].map(value=>String(value).padStart(2,'0')).join(':');
}

function switchView(name) {
  if (!viewTitles[name]) return;
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $('#view-title').textContent = viewTitles[name];
  $('#sidebar').classList.remove('open');
  scrollTo({ top: 0, behavior: 'smooth' });
}

function showToast(title, message, error = false) {
  const toast = $('#toast');
  toast.innerHTML = `<span>${error ? '!' : '✓'}</span><p><strong>${esc(title)}</strong><small>${esc(message)}</small></p>`;
  toast.style.borderColor = error ? 'rgba(255,65,93,.35)' : '';
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 3600);
}

function openModal(selector) {
  const modal = $(selector);
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  setTimeout(() => $('input', modal)?.focus(), 60);
}

function closeModal(modal) {
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
}

function resource(label, value, warning = false, display = `${Number(value || 0).toFixed(0)}%`) {
  return `<div class="resource ${warning ? 'warn' : ''}"><div><span>${label}</span><b>${display}</b></div><span><i style="width:${Math.min(Number(value || 0),100)}%"></i></span></div>`;
}

function renderNodes() {
  const target = $('#server-grid');
  if (!state.nodes.length) {
    target.innerHTML = '<div class="empty-state"><strong>No server has been enrolled</strong>Add your first node to start receiving live telemetry.</div>';
    return;
  }
  const visibleNodes = state.nodeFilter === 'all' ? state.nodes : state.nodes.filter(node => node.status !== 'online');
  if (!visibleNodes.length) {
    target.innerHTML = '<div class="empty-state"><strong>No nodes match this filter</strong>All registered nodes are currently healthy.</div>';
    return;
  }
  target.innerHTML = visibleNodes.map(node => {
    const m = node.metric || {}, detail=m.detail||{}, inventory=detail.inventory||{};
    const provisioning=node.provision_status==='provisioning', provisionFailed=node.provision_status==='failed';
    const watch = node.status !== 'online' || provisionFailed || Number(m.cpu) > 80 || Number(m.ram) > 85;
    const throughput = Number(m.rx_bps || 0) + Number(m.tx_bps || 0);
    const tunnels = state.tunnels.filter(t => t.node_id === node.id);
    const healthy = tunnels.filter(t => t.status === 'healthy').length;
    const services = (node.services || []).map(service=>`<button class="service-chip ${service.status==='active'?'':'service-down'}" data-service-node="${node.id}" data-service-name="${esc(service.name)}" title="Restart ${esc(service.name)}"><i></i>${esc(service.name)} · ${esc(service.status)}</button>`).join('');
    const code = node.region.toUpperCase().includes('IRAN') ? 'IR' : node.name.slice(0,2).toUpperCase();
    const docker=inventory.docker||{};
    const intel=[inventory.os||inventory.kernel?`${inventory.os||'Linux'} · ${inventory.kernel||'kernel n/a'}`:'INVENTORY PENDING',inventory.updates!=null?`${Number(inventory.updates)} UPDATES`:'UPDATES N/A',inventory.reboot_required?'REBOOT REQUIRED':'NO REBOOT',docker.available?`DOCKER ${Number(docker.running||0)}/${Number(docker.total||0)}${Number(docker.unhealthy||0)?` · ${Number(docker.unhealthy)} UNHEALTHY`:''}`:'DOCKER N/A'].join(' · ');
    return `<article class="panel server-card">
      <div class="server-head"><div class="server-id"><span class="flag">${esc(code)}</span><div><strong>${esc(node.name)}</strong><small>${esc(node.region)} · IP ${esc(node.observed_ip||node.host)}${node.observed_ip&&node.observed_ip!==node.host?` · SSH ${esc(node.host)}`:''} · AGENT ${esc(node.agent_version||'PENDING')} · AUTO-HEAL ${node.autoheal_enabled?'ON':'OFF'}</small></div></div><span class="status-label ${watch ? 'watch' : ''}">● ${provisioning?'INSTALLING':provisionFailed?'INSTALL FAILED':esc(node.status.toUpperCase())}</span></div>
      <div class="resource-grid">${resource('CPU',m.cpu,Number(m.cpu)>80)}${resource('RAM',m.ram,Number(m.ram)>85)}${resource('DISK',m.disk,Number(m.disk)>85)}${resource('INODES',detail.inode_percent,Number(detail.inode_percent)>85)}${resource('LOAD',Math.min(Number(m.load1||0)*10,100),false,Number(m.load1||0).toFixed(2))}${resource('TEMP',Math.min(Number(detail.temperature_c||0),100),Number(detail.temperature_c)>80,detail.temperature_c==null?'—':`${Number(detail.temperature_c).toFixed(0)}°C`)}</div>
      <div class="server-intel"><span>${esc(intel)}</span><span>DISK ${fileSize(Number(detail.disk_read_bps||0))}/s READ · ${fileSize(Number(detail.disk_write_bps||0))}/s WRITE · NET ERR ${Number(detail.network_errors_delta||0)} · DROP ${Number(detail.network_drops_delta||0)}</span></div>
      <div class="service-list">${services || '<span>No managed services reported</span>'}</div>
      <div class="server-bottom"><div><span>TUNNELS</span><b>${healthy} / ${tunnels.length}</b></div><div><span>LAST SEEN</span><b>${relativeTime(node.last_seen)}</b></div><div class="server-actions">${provisionFailed?`<button class="square-action node-provision-retry needs-setup" data-node-id="${node.id}">↻ RETRY INSTALL</button>`:''}${node.provision_status?`<button class="square-action node-provision-log" data-node-id="${node.id}">≡ INSTALL LOG</button>`:''}${node.role!=='hub'&&node.ssh_configured&&!provisioning&&!provisionFailed?`<button class="square-action node-agent-sync" title="Redeploy the matching DARK NOC Agent while preserving managed configuration and tunnels" data-node-id="${node.id}">↻ SYNC AGENT</button>`:''}<button class="square-action server-ssh ${node.ssh_configured?'':'needs-setup'}" title="${node.ssh_configured?'Open SSH terminal':'Configure SSH credentials first'}" data-node-id="${node.id}">${node.ssh_configured?'›_ SSH':'⚙ SETUP SSH'}</button><button class="square-action node-diagnostics" title="Run diagnostics" data-node-id="${node.id}">✓ CHECKS</button><button class="square-action node-logs" title="View Agent logs" data-node-id="${node.id}">≡ LOGS</button><button class="square-action node-autoheal" title="Toggle Auto-Heal" data-node-id="${node.id}">↻ AUTO-HEAL</button><button class="square-action node-edit" title="Edit server and SSH settings" data-node-id="${node.id}">✎ EDIT</button><button class="square-action node-pin" title="Reset pinned SSH host key" data-node-id="${node.id}">◇ HOST KEY</button><button class="square-action node-delete danger" title="Delete server" data-node-id="${node.id}" data-node-name="${esc(node.name)}">× DELETE</button></div></div>
    </article>`;
  }).join('');
}

function populateSSHServers() {
  const select = $('#ssh-node-select');
  if (!select) return;
  const previous = select.value;
  select.innerHTML = '<option value="">SELECT SERVER</option>' + state.nodes.map(node =>
    `<option value="${Number(node.id)}">${esc(node.role==='hub'?'HUB':node.role==='edge'?'IRAN':'KHAREJ')} · ${esc(node.name)} · ${esc(node.host)}${node.ssh_configured?'':' · SSH NOT SET'}</option>`
  ).join('');
  if (state.nodes.some(node => String(node.id) === previous)) select.value = previous;
  const fileNodes=state.nodes.filter(node=>node.ssh_configured);
  const fill=(selector,placeholder)=>{const element=$(selector);if(!element)return;const selected=element.value;element.innerHTML=`<option value="">${placeholder}</option>`+fileNodes.map(node=>`<option value="${Number(node.id)}">${esc(node.role==='hub'?'HUB':node.role==='edge'?'IRAN':'KHAREJ')} · ${esc(node.name)} · ${esc(node.host)}</option>`).join('');if(fileNodes.some(node=>String(node.id)===selected))element.value=selected;};
  fill('#upload-node','SELECT SERVER');fill('#relay-source-node','SELECT SOURCE');fill('#relay-destination-node','SELECT DESTINATION');fill('#file-manager-node','SELECT SERVER');
  const monitorSelect=$('#monitor-form [name="node_id"]');
  if(monitorSelect){const selected=monitorSelect.value;monitorSelect.innerHTML=state.nodes.filter(node=>node.status==='online').map(node=>`<option value="${Number(node.id)}">${esc(node.name)} · ${esc(node.host)}</option>`).join('');if(state.nodes.some(node=>String(node.id)===selected))monitorSelect.value=selected;}
}

function renderTunnels() {
  const target = $('#tunnel-rows');
  if (!state.tunnels.length) {
    target.innerHTML = '<div class="empty-state"><strong>No DARK tunnel telemetry yet</strong>Deploy a tunnel from a verified plugin above.</div>';
    return;
  }
  target.innerHTML = state.tunnels.map((tunnel, index) => {
    const status = (tunnel.status || 'unknown').toUpperCase();
    const unhealthy = status !== 'HEALTHY';
    const tunnelRate = tunnel.rx_bps == null && tunnel.tx_bps == null ? '—' : bytesPerSecond(Number(tunnel.rx_bps||0)+Number(tunnel.tx_bps||0));
    const statusClass=status==='DOWN'?'down':unhealthy?'degraded':'';
    return `<div class="table-row" role="row"><div class="route-name"><span>${esc((tunnel.region || 'NO').slice(0,2).toUpperCase())}</span><p><strong>${esc(tunnel.name)}</strong><small>${esc(tunnel.node_name)} <code class="node-ip">${esc(tunnel.node_host || 'IP N/A')}</code> → ${esc(tunnel.target || 'local')}</small></p></div><span><em class="method-pill">${esc(tunnel.transport==='unknown'?(tunnel.method||'DARK Backhaul'):tunnel.transport.toUpperCase())}</em></span><span>${tunnel.latency_ms == null ? '—' : `${Number(tunnel.latency_ms).toFixed(1)} ms`}</span><span class="${status==='DOWN' ? 'red' : unhealthy ? 'amber' : ''}">${tunnel.packet_loss == null ? '—' : `${Number(tunnel.packet_loss).toFixed(1)}%`}</span><span>${tunnelRate}</span><span>${Number(tunnel.sessions || 0)}</span><span class="table-status ${statusClass}">${esc(status)}</span><span class="row-actions"><button class="tunnel-manage" data-tunnel-id="${Number(tunnel.id)}">MANAGE</button></span></div>`;
  }).join('');
}

function pluginInventory(node, pluginId){return node.plugins?.[pluginId.replaceAll('-','_')];}
function pluginMethod(pluginId){return pluginId==='dark-ghostpro'?'DARK Ghost Pro':pluginId==='dark-packetpro'?'DARK Packet Pro':'DARK Backhaul';}
function pluginIcon(pluginId){return pluginId==='dark-ghostpro'?'DG':pluginId==='dark-packetpro'?'DP':'DB';}
function tunnelPluginId(tunnel){return tunnel.method==='DARK Ghost Pro'?'dark-ghostpro':tunnel.method==='DARK Packet Pro'?'dark-packetpro':'dark-backhaul';}
function renderPlugins() {
  const grid = $('#plugin-grid');
  const onlineNodes=state.nodes.filter(node=>node.status==='online');
  grid.innerHTML = state.plugins.map(plugin => {const installed=onlineNodes.filter(node=>pluginInventory(node,plugin.id)?.installed),missing=onlineNodes.filter(node=>!pluginInventory(node,plugin.id)?.installed),method=pluginMethod(plugin.id),count=state.tunnels.filter(t=>t.method===method).length;return `<article class="panel plugin-card">
    <div class="plugin-icon">${pluginIcon(plugin.id)}</div><div class="plugin-copy"><div class="plugin-title"><h3>${esc(plugin.name)}</h3><span>v${esc(plugin.version)}</span></div><p>${esc(plugin.description)}</p>
    <div class="plugin-tags">${plugin.transports.map(item=>`<span>${esc(item.toUpperCase())}</span>`).join('')}</div>
    <div class="plugin-meta"><span>CORE <b>${installed.length}/${onlineNodes.length} INSTALLED</b></span><i>→</i><span>TUNNELS <b>${count}</b></span><em>${missing.length?'INSTALL AVAILABLE':'READY'}</em></div></div>
    <span class="plugin-card-actions">${missing.length?`<button class="ghost-btn plugin-install-missing" data-plugin-id="${esc(plugin.id)}">INSTALL CORE (${missing.length})</button>`:''}<button class="primary-btn plugin-deploy" data-plugin-id="${esc(plugin.id)}">CREATE TUNNEL</button></span>
  </article>`}).join('') || '<div class="empty-state"><strong>No plugins available</strong>The local catalog is empty.</div>';
  const strip = $('#deployment-strip');
  strip.innerHTML = state.deployments.slice(0,5).map(item => `<article class="deployment ${esc(item.status)}"><span class="deployment-pulse"></span><div><strong>${esc(item.name)}</strong><small>${esc(item.iran_node)} → ${esc(item.kharej_node)}${item.mode==='pair_code'?' · PAIR CODE':''}</small></div><div class="deploy-side"><span>IRAN</span><b>${esc(item.iran_status || 'queued')}</b></div><div class="deploy-side"><span>KHAREJ</span><b>${esc(item.kharej_status || 'queued')}</b></div><em>${esc(item.status.toUpperCase().replaceAll('_',' '))}</em><span class="deployment-actions">${item.mode==='pair_code'&&item.status!=='removed'?`<button class="pair-reveal" data-deployment-id="${Number(item.id)}">CODE</button>`:''}${['failed','rolled_back','rollback_failed'].includes(item.status)?`<button class="${item.mode==='pair_code'?'pair-retry':'deploy-retry'}" data-deployment-id="${Number(item.id)}">RETRY</button>`:''}${['completed','awaiting_pair'].includes(item.status)?`<button class="${item.mode==='pair_code'?'pair-remove':'deploy-remove'}" data-deployment-id="${Number(item.id)}">REMOVE</button>`:''}</span></article>`).join('');
}

function renderCertificates(){
  const grid=$('#certificate-grid');if(!grid)return;
  if(!state.certificates.length){grid.innerHTML='<div class="empty-state"><strong>TLS Vault is empty</strong>Add a domain and issue its first Let\'s Encrypt certificate.</div>';return;}
  grid.innerHTML=state.certificates.map(cert=>{const days=cert.expires_at?Math.ceil((Number(cert.expires_at)*1000-Date.now())/86400000):null,status=String(cert.status||'pending').toUpperCase(),tone=['FAILED','EXPIRED'].includes(status)?'bad':status==='EXPIRING'?'warn':'good';return `<article class="panel certificate-card ${tone}"><div class="cert-shield">◇</div><div><span class="panel-kicker">${esc(status)}</span><h3>${esc(cert.domain)}</h3><p>${esc(cert.node_name)} · ${esc(cert.node_host)}</p><div class="cert-meta"><span>ISSUER <b>${esc(cert.issuer)}</b></span><span>EXPIRES <b>${days==null?'PENDING':`${days} DAYS`}</b></span></div>${cert.last_error?`<small class="cert-error">${esc(cert.last_error)}</small>`:''}</div><button class="ghost-btn cert-renew" data-cert-id="${Number(cert.id)}">RENEW</button></article>`}).join('');
  $('#cert-count').textContent=state.certificates.filter(c=>c.status==='valid').length;
}

function updateTLSSelector(){
  const form=$('#plugin-form'),transport=form.elements.transport.value,required=['tls','wss','wssmux','h2','grpc','relay+tls','relay+wss','relay+h2','relay+grpc'].includes(transport),nodeId=Number(form.elements.iran_node_id.value),box=$('#plugin-tls'),select=$('#plugin-certificate');
  box.hidden=!required;select.required=required;
  if(!required){select.value='';return;}
  const valid=state.certificates.filter(c=>Number(c.node_id)===nodeId&&c.status==='valid'&&Number(c.expires_at)>Date.now()/1000+86400);
  select.innerHTML='<option value="">SELECT VALID CERTIFICATE</option>'+valid.map(c=>`<option value="${Number(c.id)}">${esc(c.domain)} · ${Math.ceil((c.expires_at-Date.now()/1000)/86400)} days</option>`).join('');
  if(valid.length===1){select.value=valid[0].id;form.elements.iran_endpoint.value=valid[0].domain;}
}

function updatePluginMode(){
  const managed=$('#plugin-mode').value==='managed';
  const packet=state.selectedPlugin==='dark-packetpro';
  $('#plugin-kharej-managed').hidden=!managed; $('#plugin-kharej-pair').hidden=managed;
  $('#plugin-kharej-node').required=managed; $('[name="remote_label"]',$('#plugin-form')).required=!managed;
  $('#plugin-kharej-endpoint').hidden=managed||!packet; $('[name="kharej_endpoint"]',$('#plugin-form')).required=!managed&&packet;
  $('#plugin-iran-role').textContent=packet?'Client · exposes local user ports':'Server · accepts tunnel';
  $('#plugin-remote-help').textContent=managed?(packet?'Server · automatic install':'Client · automatic install'):'Script · paste Pair Code';
  $('#plugin-security-title').textContent=managed?'Zero-copy pairing':'Offline-capable secure pairing';
  $('#plugin-security-copy').textContent=managed?'DARK NOC securely applies matching configuration to both Agents.':'The Hub configures IRAN and generates a compatible code. On KHAREJ select the normal Pair Code flow and paste it.';
  $('#plugin-submit').textContent=managed?'DEPLOY ON BOTH SERVERS':'CONFIGURE IRAN & GENERATE CODE';
}

function renderIncidentDetail(incident) {
  const detail=$('.incident-detail'),timeline=$('.timeline-panel .timeline'),noteForm=$('#incident-note-form');
  if(!detail||!timeline)return;
  if(!incident){
    $('.severity-pill',detail).textContent='NO INCIDENT SELECTED';$('.severity-pill',detail).style.color='var(--green)';$('h2',detail).textContent='All monitored paths are operational';$('p',detail).textContent='DARK NOC is watching Agents, tunnels and synthetic checks.';$('.downtime strong',detail).textContent='00:00:00';$('.diagnostic-grid',detail).innerHTML='<div class="diag ok"><span>✓</span><strong>LIVE STATE</strong><small>No incident selected</small></div>';$('.remediation',detail).innerHTML='<div><span class="live-dot"></span><p><strong>Monitoring active</strong><small>Waiting for the next signal</small></p></div>';timeline.innerHTML='<div class="active"><time>NOW</time><span></span><p><strong>No recovery timeline</strong><small>Select an archived incident to inspect it.</small></p></div>';if(noteForm)noteForm.hidden=true;return;
  }
  let telemetry={};try{telemetry=typeof incident.detail==='string'?JSON.parse(incident.detail||'{}'):(incident.detail||{});}catch{}
  $('h2',detail).textContent=incident.title;$('p',detail).textContent=`${incident.node_name||'System'} · ${incident.tunnel_name||telemetry.monitor_name||'Node incident'}`;
  $('.severity-pill',detail).textContent=`${String(incident.severity||'warning').toUpperCase()} · ${String(incident.status).toUpperCase()}`;$('.severity-pill',detail).style.color='';
  $('.downtime strong',detail).textContent=elapsedDuration(incident.downtime_seconds);
  const checks=telemetry.checks||{},monitor=telemetry.monitor_id!=null;
  $('.diagnostic-grid',detail).innerHTML=monitor
    ?`<div class="diag ${incident.status==='resolved'?'ok':'fail'}"><span>01</span><strong>SYNTHETIC CHECK</strong><small>${esc(telemetry.reason||telemetry.status||'Threshold failed')}</small></div><div class="diag waiting"><span>02</span><strong>MONITOR</strong><small>ID #${Number(telemetry.monitor_id)}</small></div>`
    :`<div class="diag ${checks.process===false?'fail':'ok'}"><span>01</span><strong>PROCESS</strong><small>${checks.process===false?'Service failed':'State recorded'}</small></div><div class="diag ${checks.path===false?'fail':'ok'}"><span>02</span><strong>PATH</strong><small>${telemetry.latency_ms==null?'Agent signal':`${Number(telemetry.latency_ms).toFixed(1)} ms`}</small></div><div class="diag ${Number(telemetry.packet_loss||0)>0?'fail':'ok'}"><span>03</span><strong>LOSS</strong><small>${telemetry.packet_loss==null?'—':`${Number(telemetry.packet_loss).toFixed(1)}%`}</small></div><div class="diag waiting"><span>04</span><strong>EVENTS</strong><small>${Number(incident.events?.length||incident.event_count||0)} timeline records</small></div>`;
  const active=['open','acknowledged'].includes(incident.status);
  $('.remediation',detail).innerHTML=`<div><span class="${active?'spinner':'live-dot'}"></span><p><strong>${active?(incident.status==='acknowledged'?'Operator owns this incident':'Operator attention required'):'Incident resolved'}</strong><small>${incident.root_cause?`Cause: ${esc(incident.root_cause)}`:incident.resolution?`Resolution: ${esc(incident.resolution)}`:`Opened ${relativeTime(incident.opened_at)}`}</small></p></div><span class="incident-command-buttons">${incident.status==='open'?`<button class="ghost-btn incident-ack" data-incident-id="${Number(incident.id)}">ACKNOWLEDGE</button>`:''}${active?`<button class="ghost-btn incident-resolve" data-incident-id="${Number(incident.id)}">RESOLVE</button>`:`<button class="ghost-btn incident-reopen" data-incident-id="${Number(incident.id)}">REOPEN</button>`}${incident.node_id?`<button class="danger-btn" id="take-control" data-node-id="${Number(incident.node_id)}">OPEN SSH</button>`:''}</span>`;
  if(noteForm){noteForm.hidden=false;noteForm.dataset.incidentId=incident.id;if(!noteForm.contains(document.activeElement)){noteForm.elements.root_cause.value=incident.root_cause||'';noteForm.elements.resolution.value=incident.resolution||'';}}
  const events=(incident.events||[]).slice().reverse();
  timeline.innerHTML=events.length?events.map((item,index)=>`<div class="${index===0?'active':''}"><time>${new Date(Number(item.created_at)*1000).toLocaleString('en-GB')}</time><span></span><p><strong>${esc(String(item.event_type||'event').toUpperCase())}</strong><small>${esc(item.message)}${item.actor?` · ${esc(item.actor)}`:''}</small></p></div>`).join(''):`<div class="active"><time>${new Date(incident.opened_at*1000).toLocaleString('en-GB')}</time><span></span><p><strong>DETECTED</strong><small>${esc(incident.title)}</small></p></div>`;
  const kicker=$('.timeline-panel .panel-kicker');if(kicker)kicker.textContent=`INCIDENT #${String(incident.id).padStart(4,'0')}`;
}

async function loadIncidentDetail(id) {
  const requestId=Number(id);state.selectedIncident=requestId;
  try{const incident=await api(`/api/incidents/${requestId}`);if(state.selectedIncident!==requestId)return;state.incidentDetail=incident;renderIncidentDetail(incident);renderIncidentList();}
  catch(error){showToast('INCIDENT LOAD FAILED',error.message,true);}
}

function renderIncidentList(){
  const list=$('#incident-list');if(!list)return;
  if(!state.incidents.length){list.innerHTML='<div class="empty-state compact-empty"><strong>No incident history</strong>Detected failures and recoveries will be recorded here.</div>';return;}
  list.innerHTML=state.incidents.map(item=>`<button class="incident-list-row ${Number(item.id)===Number(state.selectedIncident)?'selected':''}" data-incident-select="${Number(item.id)}"><span class="severity-dot ${esc(item.severity)}"></span><p><strong>#${String(item.id).padStart(4,'0')} · ${esc(item.title)}</strong><small>${esc(item.node_name||'SYSTEM')} · ${Number(item.event_count||0)} EVENTS · ${relativeTime(item.last_changed_at||item.opened_at)}</small></p><em class="${esc(item.status)}">${esc(String(item.status).toUpperCase())}</em><b>${Math.floor(Number(item.downtime_seconds||0)/60)}m</b></button>`).join('');
}

function renderIncidents() {
  const open=state.incidents.filter(item=>['open','acknowledged'].includes(item.status));$$('.nav-item b.danger').forEach(item=>item.textContent=open.length);
  const preferred=state.incidents.find(item=>Number(item.id)===Number(state.selectedIncident))||open[0]||state.incidents[0]||null;
  state.selectedIncident=preferred?.id||null;renderIncidentList();
  if(!preferred){state.incidentDetail=null;renderIncidentDetail(null);return;}
  if(Number(state.incidentDetail?.id)===Number(preferred.id))renderIncidentDetail({...preferred,...state.incidentDetail});else renderIncidentDetail(preferred);
}

function renderMonitors(){
  const grid=$('#monitor-grid'),summary=$('#monitor-summary');if(!grid||!summary)return;
  const counts={up:0,down:0,pending:0,disabled:0};state.monitors.forEach(item=>{counts[item.enabled?(item.status||'pending'):'disabled']=(counts[item.enabled?(item.status||'pending'):'disabled']||0)+1;});
  summary.innerHTML=`<article><span>UP</span><strong>${counts.up}</strong></article><article><span>DOWN</span><strong class="red">${counts.down}</strong></article><article><span>PENDING</span><strong class="amber">${counts.pending+(counts.degraded||0)}</strong></article><article><span>DISABLED</span><strong>${counts.disabled}</strong></article>`;
  $('#monitor-count').textContent=state.monitors.length;
  if(!state.monitors.length){grid.innerHTML='<div class="empty-state"><strong>No synthetic monitor configured</strong>Add ICMP, TCP, HTTP, HTTPS, DNS, TLS or SNMP checks and choose the Agent that should run them.</div>';return;}
  grid.innerHTML=state.monitors.map(item=>{const detail=item.detail||{},tone=!item.enabled?'disabled':item.status==='up'?'up':item.status==='down'?'down':'pending';return `<article class="panel monitor-card ${tone}"><header><span>${esc(item.kind.toUpperCase())}</span><em>● ${item.enabled?esc(String(item.status).toUpperCase()):'DISABLED'}</em></header><h3>${esc(item.name)}</h3><p>${esc(item.target)}${item.port?`:${Number(item.port)}`:''}</p><div class="monitor-route"><span>RUNNER · ${esc(String(item.runner_status||'unknown').toUpperCase())}</span><b>${esc(item.node_name)} · ${esc(item.node_host)}</b></div><div class="monitor-metrics"><span>LATENCY <b>${item.latency_ms==null?'—':`${Number(item.latency_ms).toFixed(1)} ms`}</b></span><span>FAIL STREAK <b>${Number(item.failure_streak||0)}</b></span><span>LAST RUN <b>${relativeTime(item.last_run_at)}</b></span></div>${detail.days_left!=null?`<small>TLS expires in ${Number(detail.days_left)} days</small>`:''}<footer><button class="monitor-run" data-monitor-id="${Number(item.id)}">▶ RUN</button><button class="monitor-history" data-monitor-id="${Number(item.id)}">≋ HISTORY</button><button class="monitor-edit" data-monitor-id="${Number(item.id)}">✎ EDIT</button><button class="monitor-delete danger" data-monitor-id="${Number(item.id)}" data-monitor-name="${esc(item.name)}">× DELETE</button></footer></article>`;}).join('');
}

function renderFleetOperations(){
  const list=$('#fleet-list');if(!list)return;
  if(!state.fleetOperations.length){list.innerHTML='<div class="empty-state"><strong>No fleet operation yet</strong>Run one allowlisted playbook across selected Agents.</div>';return;}
  list.innerHTML=state.fleetOperations.map(operation=>{const items=operation.items||[],done=items.filter(item=>item.status==='completed').length,failed=items.filter(item=>item.status==='failed').length,total=items.length,progress=total?Math.round((done+failed)/total*100):0;return `<article class="panel fleet-operation"><header><div><span class="panel-kicker">#${String(operation.id).padStart(4,'0')} · ${esc(operation.kind.toUpperCase().replaceAll('_',' '))}</span><h3>${esc(operation.name)}</h3></div><em class="${esc(operation.status)}">${esc(operation.status.toUpperCase())}</em></header><div class="fleet-progress"><i style="width:${progress}%"></i></div><div class="fleet-operation-meta"><span>${done}/${total} COMPLETE</span><span class="${failed?'red':''}">${failed} FAILED</span><span>${operation.scheduled_at>Date.now()/1000?`SCHEDULED ${new Date(operation.scheduled_at*1000).toLocaleString('en-GB')}`:`STARTED ${relativeTime(operation.started_at||operation.created_at)}`}</span></div><div class="fleet-items">${items.map(item=>`<button class="fleet-item ${esc(item.status)}" data-operation-id="${Number(operation.id)}" data-fleet-output="${Number(item.id)}"><span>${esc(item.node_name)}</span><b>${esc(item.status.toUpperCase())}</b></button>`).join('')}</div>${operation.status==='scheduled'?`<footer><button class="danger-outline fleet-cancel" data-operation-id="${Number(operation.id)}">CANCEL SCHEDULE</button></footer>`:''}</article>`;}).join('');
}

function fileSelection(entry=null){
  state.files.selected=entry;const buttons=['#file-download','#file-edit','#file-checksum','#file-rename','#file-chmod','#file-delete'];buttons.forEach(selector=>{$(selector).disabled=!entry;});
  if(entry?.type==='directory'){$('#file-download').disabled=true;$('#file-edit').disabled=true;$('#file-checksum').disabled=true;}
  $('#file-selection').textContent=entry?`${entry.type.toUpperCase()} · ${entry.path}`:'NO FILE SELECTED';
  $$('.file-row.selected','#file-manager-rows').forEach(row=>row.classList.remove('selected'));
}

function renderFileManager(){
  const rows=$('#file-manager-rows');if(!rows)return;
  if(!state.files.nodeId){rows.innerHTML='<div class="empty-state compact-empty"><strong>Select a server</strong>Browse remote files through pinned SFTP.</div>';return;}
  if(!state.files.entries.length){rows.innerHTML='<div class="empty-state compact-empty"><strong>Directory is empty</strong>Create a directory or upload a file here.</div>';return;}
  rows.innerHTML=state.files.entries.map((entry,index)=>`<button class="file-row" data-file-index="${index}"><span><i>${entry.type==='directory'?'▣':entry.type==='symlink'?'↗':'▤'}</i>${esc(entry.name)}</span><span>${esc(entry.type.toUpperCase())}</span><span>${entry.type==='directory'?'—':fileSize(entry.size)}</span><span>${esc(entry.mode)}</span><span>${entry.modified_at?new Date(entry.modified_at*1000).toLocaleString('en-GB'):'—'}</span></button>`).join('');
}

async function loadFileDirectory(path=state.files.path){
  const nodeId=Number($('#file-manager-node').value||state.files.nodeId);if(!nodeId)return;
  $('#file-manager-rows').innerHTML='<div class="empty-state compact-empty"><strong>Opening secure SFTP</strong>Loading remote directory…</div>';
  try{const result=await api(`/api/ssh/files/${nodeId}?path=${encodeURIComponent(path)}`);state.files={...state.files,nodeId,path:result.path,parent:result.parent,entries:result.entries||[],selected:null};$('#file-manager-path').value=result.path;fileSelection();renderFileManager();}
  catch(error){state.files.entries=[];$('#file-manager-rows').innerHTML=`<div class="empty-state compact-empty error"><strong>Directory unavailable</strong>${esc(error.message)}</div>`;showToast('FILE MANAGER FAILED',error.message,true);}
}

async function fileAction(action,extra={}){
  const selected=state.files.selected,nodeId=state.files.nodeId,path=extra.path||selected?.path;if(!nodeId||!path)return;
  const result=await api(`/api/ssh/files/${nodeId}/action`,{method:'POST',body:JSON.stringify({action,path,...extra})});await loadFileDirectory(state.files.path);return result;
}

function openMonitorEditor(monitor=null){
  if(!state.nodes.some(node=>node.status==='online'))return showToast('ONLINE AGENT REQUIRED','Add or reconnect an Agent before creating a monitor.',true);
  state.editingMonitor=monitor?.id||null;const form=$('#monitor-form');form.reset();form.elements.monitor_id.value=monitor?.id||'';form.elements.interval_seconds.value=monitor?.interval_seconds||60;form.elements.timeout_seconds.value=monitor?.timeout_seconds||5;form.elements.enabled.checked=monitor?Boolean(monitor.enabled):true;
  if(monitor){for(const key of ['name','node_id','kind','target','port','expected_status','snmp_oid'])if(form.elements[key])form.elements[key].value=monitor[key]??'';$('#monitor-title').textContent=`Edit ${monitor.name}`;}else{$('#monitor-title').textContent='Add network monitor';form.elements.node_id.value=state.nodes.find(node=>node.status==='online')?.id||'';form.elements.snmp_oid.value='.1.3.6.1.2.1.1.3.0';}
  $('.snmp-fields',form).hidden=form.elements.kind.value!=='snmp';openModal('#monitor-modal');
}

function openFleetEditor(){
  const online=state.nodes.filter(node=>node.status==='online');if(!online.length)return showToast('ONLINE AGENT REQUIRED','Fleet Operations needs at least one online Agent.',true);
  const form=$('#fleet-form');form.reset();form.elements.name.value='NOC health sweep';form.elements.service.required=false;$('.fleet-autoheal',form).hidden=true;$('#fleet-node-picker').innerHTML=online.map(node=>`<label><input type="checkbox" name="node_ids" value="${Number(node.id)}" checked /><span>${esc(node.name)}</span><small>${esc(node.role.toUpperCase())} · ${esc(node.host)}</small></label>`).join('');openModal('#fleet-modal');
}

function renderLiveTopology() {
  const topology = $('#topology');
  const seen=new Set(), links=[];
  state.tunnels.forEach(tunnel=>{
    const peerId=Number(tunnel.peer_node_id||0);
    const key=peerId?[tunnel.name,Math.min(Number(tunnel.node_id),peerId),Math.max(Number(tunnel.node_id),peerId)].join(':'):`${tunnel.id}:endpoint`;
    if(seen.has(key))return;seen.add(key);
    const peerRow=peerId?state.tunnels.find(item=>item.name===tunnel.name&&Number(item.node_id)===peerId):null;
    const ownNode=state.nodes.find(node=>Number(node.id)===Number(tunnel.node_id));
    const peerNode=state.nodes.find(node=>Number(node.id)===peerId);
    const ownIsExit=ownNode?.role==='exit'||tunnel.tunnel_role==='client';
    const leftNode=ownIsExit?peerNode:ownNode, rightNode=ownIsExit?ownNode:peerNode;
    const leftFallback=ownIsExit?(tunnel.peer_name||'IRAN ENDPOINT'):(tunnel.node_name||'IRAN ENDPOINT');
    const leftHost=leftNode?.host||leftNode?.observed_ip||(ownIsExit?tunnel.peer_host:tunnel.node_host)||'UNKNOWN IP';
    const rightFallback=ownIsExit?(tunnel.node_name||'REMOTE ENDPOINT'):(tunnel.peer_name||'REMOTE ENDPOINT');
    const rightHost=rightNode?.host||rightNode?.observed_ip||(ownIsExit?tunnel.node_host:tunnel.peer_host)||'REMOTE IP UNAVAILABLE';
    const statuses=[tunnel.status,peerRow?.status].filter(Boolean).map(value=>String(value).toLowerCase());
    const status=statuses.includes('down')?'DOWN':statuses.includes('stale')?'STALE':statuses.includes('degraded')?'DEGRADED':'ONLINE';
    const tone=status==='ONLINE'?'online':status==='DEGRADED'||status==='STALE'?'degraded':'down';
    const haystack=[tunnel.name,leftNode?.name,leftHost,rightNode?.name,rightFallback,rightHost].join(' ').toLowerCase();
    if(state.topologyFilter!=='all'&&state.topologyFilter!==tone)return;
    if(state.topologySearch&&!haystack.includes(state.topologySearch))return;
    links.push({tunnel,leftNode,rightNode,leftFallback,leftHost,rightFallback,rightHost,status,tone});
  });
  if(!links.length){topology.innerHTML='<div class="grid-floor"></div><div class="empty-state compact-empty"><strong>No matching DARK Backhaul path</strong>Change the topology filter or wait for Agent telemetry.</div>';return;}
  const rootMap=new Map(),leafMap=new Map();
  links.forEach(link=>{
    link.rootKey=link.leftNode?.id?`n${link.leftNode.id}`:`r${link.leftHost}`;
    link.leafKey=link.rightNode?.id?`n${link.rightNode.id}`:`e${link.rightHost}`;
    if(!rootMap.has(link.rootKey))rootMap.set(link.rootKey,{node:link.leftNode,name:link.leftNode?.name||link.leftFallback,host:link.leftHost});
    if(!leafMap.has(link.leafKey))leafMap.set(link.leafKey,{node:link.rightNode,name:link.rightNode?.name||link.rightFallback,host:link.rightHost});
  });
  const roots=[...rootMap.entries()],leaves=[...leafMap.entries()];
  const yAt=(index,total)=>total===1?210:65+(index*(290/(total-1)));
  const rootY=Object.fromEntries(roots.map(([key],index)=>[key,yAt(index,roots.length)]));
  const leafY=Object.fromEntries(leaves.map(([key],index)=>[key,yAt(index,leaves.length)]));
  const defs=`<defs><filter id="cyber-glow"><feGaussianBlur stdDeviation="2.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter><marker id="route-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="currentColor"/></marker></defs>`;
  const routes=links.map((link,index)=>{const y1=rootY[link.rootKey],y2=leafY[link.leafKey],width=Math.min(4,1.6+Math.log10(Number(link.tunnel.rx_bps||0)+Number(link.tunnel.tx_bps||0)+1)/4);return `<g class="route-group ${link.tone}" data-route="${index}" tabindex="0"><path class="route-hit" d="M165 ${y1} C330 ${y1},570 ${y2},735 ${y2}"/><path class="cyber-route" style="--route-width:${width.toFixed(2)}" d="M165 ${y1} C330 ${y1},570 ${y2},735 ${y2}"/></g>`;}).join('');
  const nodeCard=(entry,y,side)=>{const node=entry.node,agent=node?.status==='online',ssh=Boolean(node?.ssh_configured);return `<button class="cyber-node ${side}" style="top:${(y/420*100).toFixed(2)}%" data-node-id="${node?.id||''}" data-endpoint-name="${esc(entry.name)}"><i>${side==='root'?(node?.role==='hub'?'HB':'IR'):'EX'}</i><span><strong>${esc(entry.name)}</strong><small>${esc(entry.host)}</small><em><b class="${agent?'ready':'missing'}">AG ${agent?'ON':'OFF'}</b><b class="${ssh?'ready':'missing'}">SSH ${ssh?'READY':'NO'}</b></em></span></button>`;};
  const nodes=[...roots.map(([key,entry])=>nodeCard(entry,rootY[key],'root')),...leaves.map(([key,entry])=>nodeCard(entry,leafY[key],'leaf'))].join('');
  topology.innerHTML=`<div class="grid-floor" aria-hidden="true"></div><span class="region-label iran">IRAN / HUB</span><span class="region-label global">GLOBAL EXITS</span><svg class="cyber-links" viewBox="0 0 900 420" preserveAspectRatio="none">${defs}${routes}</svg>${nodes}<div class="topology-tooltip" id="topology-tooltip"></div><div class="cyber-scan"></div>`;
  const tooltip=$('#topology-tooltip');
  $$('.route-group',topology).forEach(route=>{
    const link=links[Number(route.dataset.route)],t=link.tunnel,rate=t.rx_bps==null&&t.tx_bps==null?'NO TRAFFIC DATA':bytesPerSecond(Number(t.rx_bps||0)+Number(t.tx_bps||0)),ports=(t.user_ports||[]).join(', ')||t.listen_port||t.target_port||'—';
    const show=event=>{tooltip.innerHTML=`<strong>${esc(t.name)} <i class="${link.tone}">${esc(link.status)}</i></strong><span>${esc(link.leftHost)} → ${esc(link.rightHost)}</span><small>SERVICE ${esc(t.service||'—')}</small><small>PORTS ${esc(ports)} · SESSIONS ${Number(t.sessions||0)}</small><small>TRAFFIC ${esc(rate)} · LOSS ${t.packet_loss==null?'—':`${Number(t.packet_loss).toFixed(1)}%`}</small>`;const rect=topology.getBoundingClientRect();tooltip.style.left=`${Math.min(event.clientX-rect.left+14,rect.width-330)}px`;tooltip.style.top=`${Math.max(event.clientY-rect.top-90,8)}px`;tooltip.classList.add('open');};
    route.addEventListener('mousemove',show);route.addEventListener('mouseenter',show);route.addEventListener('mouseleave',()=>tooltip.classList.remove('open'));route.addEventListener('focus',()=>{const rect=topology.getBoundingClientRect();show({clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2});});route.addEventListener('blur',()=>tooltip.classList.remove('open'));
  });
}

function renderLiveIncident() {
  const panel=$('.incident-panel'), active=state.incidents.find(item=>['open','acknowledged'].includes(item.status));
  if(!active){panel.innerHTML='<div class="panel-head compact"><div><span class="panel-kicker">INCIDENT FEED</span><h2>All clear</h2></div><button class="text-btn" data-jump="incidents">VIEW ALL</button></div><div class="empty-state compact-empty"><strong>No active incidents</strong>Every reporting path is currently healthy.</div>';return;}
  panel.innerHTML=`<div class="panel-head compact"><div><span class="panel-kicker">INCIDENT FEED</span><h2>Requires attention</h2></div><button class="text-btn" data-jump="incidents">VIEW ALL</button></div><article class="incident critical"><div class="incident-top"><span>${esc(active.status==='acknowledged'?'ACKNOWLEDGED':active.severity.toUpperCase())}</span><time>${relativeTime(active.opened_at)}</time></div><h3>${esc(active.title)}</h3><p>${esc(active.node_name||'Unknown node')} → ${esc(active.tunnel_name||'Node')}</p><div class="incident-actions"><button class="ghost-btn incident-ack" data-incident-id="${Number(active.id)}">ACK</button><button class="ghost-btn incident-resolve" data-incident-id="${Number(active.id)}">RESOLVE</button><button class="danger-btn" id="take-control" data-node-id="${Number(active.node_id)}">SSH</button></div></article>`;
}

function renderNodeHealth() {
  const panel=$('.node-health');
  panel.innerHTML='<div class="panel-head compact"><div><span class="panel-kicker">NODE HEALTH</span><h2>Resource pressure</h2></div><button class="text-btn" data-jump="servers">DETAILS</button></div>'+state.nodes.slice(0,5).map(node=>{const m=node.metric||{},pressure=Math.max(Number(m.cpu||0),Number(m.ram||0),Number(m.disk||0)),watch=node.status!=='online'||pressure>=80;return `<div class="health-row"><div><span class="flag">${esc(node.role==='edge'?'IR':node.role==='exit'?'EX':'HB')}</span><p><strong>${esc(node.name)}</strong><small>CPU ${Number(m.cpu||0).toFixed(0)}% · RAM ${Number(m.ram||0).toFixed(0)}% · DISK ${Number(m.disk||0).toFixed(0)}%</small></p></div><span class="bar ${watch?'amber':''}"><i style="width:${Math.min(pressure,100)}%"></i></span><b class="${watch?'warning-text':'ok'}">${node.status!=='online'?'OFFLINE':watch?'WATCH':'GOOD'}</b></div>`;}).join('');
}

function renderTrafficChart() {
  const values=state.traffic.map(point=>Number(point.rx_bps||0)+Number(point.tx_bps||0));
  const yLabels=$$('.chart-wrap .y-axis span'), xLabels=$$('.chart-wrap .x-axis span');
  if(!values.length){$('#traffic-line').setAttribute('d','M0 200 L900 200');$('#traffic-area').setAttribute('d','M0 200 L900 200 L900 210 L0 210Z');yLabels.forEach((label,index)=>label.textContent=index===4?'0':'—');xLabels.forEach((label,index)=>label.textContent=index===4?'NOW':'—');return;}
  const max=Math.max(...values,1), step=values.length===1?900:900/(values.length-1);
  const points=values.length===1?[`0 ${(200-(values[0]/max)*175).toFixed(1)}`,`900 ${(200-(values[0]/max)*175).toFixed(1)}`]:values.map((value,index)=>`${(index*step).toFixed(1)} ${(200-(value/max)*175).toFixed(1)}`);
  const line=`M${points.join(' L')}`; $('#traffic-line').setAttribute('d',line); $('#traffic-area').setAttribute('d',`${line} L900 210 L0 210Z`);
  [1,.75,.5,.25,0].forEach((factor,index)=>{if(yLabels[index])yLabels[index].textContent=factor?bytesPerSecond(max*factor).replace('/s',''): '0';});
  [0,.25,.5,.75,1].forEach((factor,index)=>{if(!xLabels[index])return;const point=state.traffic[Math.min(Math.round((state.traffic.length-1)*factor),state.traffic.length-1)];xLabels[index].textContent=index===4?'NOW':new Date(Number(point.ts)*1000).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});});
}

function updateOverview(summary) {
  const online = Number(summary.nodes.online || 0);
  const total = Object.values(summary.nodes).reduce((a,b) => a + Number(b), 0);
  const health = Number(summary.health_percent || 0).toFixed(1);
  $('.accent-green .stat-value strong').textContent = health;
  $('#throughput').textContent = bytesPerSecond(summary.throughput_bps).replace(/\s.*$/,'');
  $('.accent-blue .stat-value small').textContent = bytesPerSecond(summary.throughput_bps).split(' ').slice(1).join(' ');
  $('#sessions').textContent = Number(summary.connections || 0).toLocaleString();
  $('#throughput-detail').textContent=`↓ ${bytesPerSecond(summary.rx_bps)} / ↑ ${bytesPerSecond(summary.tx_bps)}`;
  const unhealthyTunnels=Object.entries(summary.tunnels||{}).filter(([status])=>status!=='healthy').reduce((sum,[,count])=>sum+Number(count),0);
  $('#health-detail').textContent=`${online}/${total} agents · ${unhealthyTunnels} tunnel alerts`;
  $('#health-state').textContent=health==='100.0'?'OPERATIONAL':online?'DEGRADED':'NO AGENTS';
  $('#session-nodes').textContent=`${online} LIVE NODE${online===1?'':'S'}`;
  $('#agent-count').textContent=`${online} / ${total}`;
  $('#chart-throughput').textContent=bytesPerSecond(summary.throughput_bps);
  $('.accent-red .stat-value strong').textContent = String(summary.open_incidents || 0).padStart(2,'0');
  $('#connection-banner').innerHTML = '<span class="live-source">LIVE HUB CONNECTED</span> Telemetry and incident data are updating automatically.';
  $$('.nav-item')[1]?.querySelector('b') && ($$('.nav-item')[1].querySelector('b').textContent = total);
  $$('.nav-item')[2]?.querySelector('b') && ($$('.nav-item')[2].querySelector('b').textContent = state.tunnels.length);
  const footer=$$('.topology-footer b');if(footer.length>=3){footer[0].textContent=online;footer[1].textContent=state.nodes.filter(node=>node.status==='online'&&state.tunnels.some(t=>t.node_id===node.id&&t.status!=='healthy')).length;footer[2].textContent=state.nodes.filter(node=>node.status==='offline').length;}
}

async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    const [summary,nodes,tunnels,incidents,plugins,deployments,certificates,traffic,monitors,fleetOperations] = await Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels'),api('/api/incidents'),api('/api/plugins'),api('/api/plugin-deployments'),api('/api/certificates'),api('/api/dashboard/traffic?minutes=60'),api('/api/monitors'),api('/api/fleet/operations?limit=50')]);
    state = { ...state, nodes, tunnels, incidents, plugins, deployments, certificates, traffic, monitors, fleetOperations, limits:dashboardLimits(summary) };
    renderNodes(); populateSSHServers(); renderTransferLimits(); renderTunnels(); renderPlugins(); renderCertificates(); renderMonitors(); renderFleetOperations(); renderIncidents(); renderLiveTopology(); renderLiveIncident(); renderNodeHealth(); renderTrafficChart(); updateOverview(summary);
    if(state.selectedIncident)loadIncidentDetail(state.selectedIncident);
    $('#sync-time').textContent = 'NOW';
  } catch (error) {
    if (error.message !== 'Authentication required') {
      $('#connection-banner').innerHTML = '<span class="offline-source">HUB CONNECTION LOST</span> Retrying automatically.';
    }
  } finally {
    refreshInFlight = false;
  }
}

async function createJob(nodeId, kind, service = null, payload = {}, showOutput = false) {
  const job = await api(`/api/nodes/${nodeId}/jobs`, { method:'POST', body:JSON.stringify({kind,service,payload}) });
  showToast('COMMAND QUEUED', `Job #${job.job_id} will run through the secure Agent channel.`);
  if (showOutput) waitForJob(job.job_id);
  return job;
}

async function waitForJob(jobId) {
  $('#job-output').textContent = `Waiting for Agent to execute job #${jobId}…`;
  openModal('#output-modal');
  for (let attempt = 0; attempt < 180; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 1500));
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (job.status === 'completed' || job.status === 'failed') {
        $('#output-title').textContent = `${job.kind} · ${job.status.toUpperCase()}`;
        $('#job-output').textContent = job.output || 'Command completed without output.';
        return;
      }
      $('#job-output').textContent = `Job #${jobId}\nStatus: ${job.status}\nWaiting for Agent response…`;
    } catch (error) { $('#job-output').textContent = error.message; return; }
  }
  $('#job-output').textContent = 'No terminal result after 4.5 minutes. The job may still be running; check Remote job history.';
}

function activeTerminalSlot() {
  return $('.terminal-card.active-terminal .terminal-screen')?.id || 'terminal-iran';
}

function activeTerminal() { return state.terminals.get(activeTerminalSlot()); }

function terminalText(terminal) {
  const buffer=terminal?.buffer?.active;if(!buffer)return '';
  const lines=[];for(let index=0;index<buffer.length;index++)lines.push(buffer.getLine(index)?.translateToString(true)||'');
  return lines.join('\n').replace(/\n{4,}/g,'\n\n\n');
}

function ensureTerminal(slot) {
  if (state.terminals.has(slot)) return state.terminals.get(slot);
  const container=$(`#${slot}`);
  const terminal=new Terminal({
    cursorBlink:true,cursorStyle:'bar',fontFamily:'"JetBrains Mono","Cascadia Code",Consolas,monospace',fontSize:13,
    lineHeight:1.2,letterSpacing:0,scrollback:12000,convertEol:false,allowTransparency:true,
    theme:{background:'#05080c',foreground:'#c8d3df',cursor:'#28f7a0',cursorAccent:'#05080c',selectionBackground:'#1d604d',black:'#071018',red:'#ff566c',green:'#28f7a0',yellow:'#ffb648',blue:'#4bb9ff',magenta:'#b36cff',cyan:'#51e7ef',white:'#d8e1e9',brightBlack:'#566474'}
  });
  const fitAddon=new FitAddon.FitAddon(), searchAddon=new SearchAddon.SearchAddon();
  terminal.loadAddon(fitAddon);terminal.loadAddon(searchAddon);terminal.loadAddon(new WebLinksAddon.WebLinksAddon());
  terminal.open(container);fitAddon.fit();
  terminal.writeln('\x1b[38;5;48mDARK SSH GATEWAY\x1b[0m  // Select a server and connect');
  terminal.onData(data=>{const socket=connectedSocket(slot);if(socket)socket.send(JSON.stringify({type:'input',data}));});
  terminal.onResize(size=>{const socket=connectedSocket(slot);if(socket)socket.send(JSON.stringify({type:'resize',cols:size.cols,rows:size.rows}));});
  terminal.attachCustomKeyEventHandler(event=>{
    if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='c'&&terminal.hasSelection()){if(requireClipboard('writeText','COPY'))navigator.clipboard.writeText(terminal.getSelection()).catch(()=>showToast('COPY BLOCKED','Allow clipboard access in the browser.',true));return false;}
    if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='v'){const socket=connectedSocket(slot);if(!socket){showToast('SSH NOT CONNECTED','Wait for the active terminal to finish connecting.',true);return false;}if(requireClipboard('readText','PASTE'))navigator.clipboard.readText().then(text=>socket.send(JSON.stringify({type:'input',data:text}))).catch(()=>showToast('PASTE BLOCKED','Allow clipboard access in the browser.',true));return false;}
    return true;
  });
  const entry={terminal,fitAddon,searchAddon};state.terminals.set(slot,entry);
  const observer=new ResizeObserver(()=>{clearTimeout(entry.fitTimer);entry.fitTimer=setTimeout(()=>{try{fitAddon.fit();}catch{}},60);});observer.observe(container.closest('.terminal-card'));
  return entry;
}

function closeSocket(slot) {
  const socket = state.sockets.get(slot);
  if (socket) { socket.intentionalClose=true;clearInterval(socket.keepaliveTimer);state.sockets.delete(slot);socket.close(); }
}

function connectedSocket(slot=activeTerminalSlot()) {
  const socket=state.sockets.get(slot);
  return socket?.readyState===WebSocket.OPEN&&socket.sshConnected?socket:null;
}

function selectTerminalCard(card) {
  if(!card)return;
  $$('.terminal-card').forEach(item=>item.classList.toggle('active-terminal',item===card));
  const slot=$('.terminal-screen',card)?.id,socket=slot&&state.sockets.get(slot);
  if(socket?.nodeId)$('#ssh-node-select').value=String(socket.nodeId);
}

function connectSSH(nodeId, slot = null) {
  const node = state.nodes.find(item => item.id === Number(nodeId));
  if (!node) return;
  if (!node.ssh_configured) {
    openNodeEditor(node, true);
    showToast('SSH SETUP REQUIRED', `Add an SSH password or private key for ${node.name}.`, true);
    return;
  }
  if (!slot) {
    const first=state.sockets.get('terminal-iran'), second=state.sockets.get('terminal-germany');
    slot=!first||first.readyState>=WebSocket.CLOSING?'terminal-iran':(!second||second.readyState>=WebSocket.CLOSING?'terminal-germany':activeTerminalSlot());
  }
  const existing=state.sockets.get(slot);
  if(existing&&existing.readyState<WebSocket.CLOSING){
    const existingNode=state.nodes.find(item=>item.id===Number(existing.nodeId));
    if(!confirm(`Replace the active ${existingNode?.name||'SSH'} session in this pane?`))return;
  }
  switchView('terminal');
  closeSocket(slot);
  const screen = $(`#${slot}`), term=ensureTerminal(slot);
  const card=screen.closest('.terminal-card'), code=node.role==='edge'?'IR':node.role==='exit'?'EX':'HB';
  $('.terminal-server .flag',card).textContent=code;
  $('.terminal-server strong',card).textContent=node.name;
  $('.terminal-server small',card).textContent=`${node.ssh_user}@${node.host}:${node.ssh_port}`;
  selectTerminalCard(card);
  $('#ssh-node-select').value=String(node.id);
  $('.ssh-online',card).textContent='● CONNECTING';
  term.terminal.reset();term.terminal.writeln(`\x1b[38;5;45mConnecting to ${node.name} (${node.host}:${Number(node.ssh_port)})…\x1b[0m\r\n`);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/ssh/${node.id}`);
  socket.nodeId=node.id;socket.sshConnected=false;socket.hadError=false;socket.intentionalClose=false;
  state.sockets.set(slot, socket);
  state.selectedNode = node.id;
  socket.onmessage = event => {
    if(state.sockets.get(slot)!==socket)return;
    let message={};try{message=JSON.parse(event.data);}catch{return;}
    if (message.type === 'output') term.terminal.write(message.data);
    if (message.type === 'connected') { socket.sshConnected=true;$('.ssh-online',card).textContent='● CONNECTED';term.terminal.writeln(`\x1b[38;5;48m✓ SECURE SSH CONNECTED · ${message.node}\x1b[0m`);term.terminal.writeln(`\x1b[38;5;244mHost key: ${message.fingerprint||'unavailable'}\x1b[0m\r\n`);term.fitAddon.fit();socket.send(JSON.stringify({type:'resize',cols:term.terminal.cols,rows:term.terminal.rows}));term.terminal.focus(); }
    if (message.type === 'error') { socket.hadError=true;socket.sshConnected=false;$('.ssh-online',card).textContent='● ERROR';term.terminal.writeln(`\r\n\x1b[38;5;203m${message.message}\x1b[0m`); }
  };
  socket.onopen = () => {if(state.sockets.get(slot)!==socket)return;term.fitAddon.fit();socket.keepaliveTimer=setInterval(()=>{if(state.sockets.get(slot)===socket&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'ping'}));},25000); };
  socket.onerror = () => {if(state.sockets.get(slot)!==socket)return;socket.hadError=true;socket.sshConnected=false;$('.ssh-online',card).textContent='● ERROR';term.terminal.writeln('\r\n\x1b[38;5;203mSSH WebSocket connection failed.\x1b[0m'); };
  socket.onclose = event => {
    clearInterval(socket.keepaliveTimer);
    if(state.sockets.get(slot)!==socket)return;
    state.sockets.delete(slot);socket.sshConnected=false;
    if(event.code===4401){socket.hadError=true;$('.ssh-online',card).textContent='● AUTH EXPIRED';term.terminal.writeln('\r\n\x1b[38;5;203mAuthentication expired. Sign in again to reconnect.\x1b[0m');terminateAuthenticatedActivity();return;}
    const abnormal=socket.hadError||(!socket.intentionalClose&&![1000,1001].includes(event.code));
    $('.ssh-online',card).textContent=abnormal?'● ERROR':'● CLOSED';
    term.terminal.writeln(abnormal?'\r\n\x1b[38;5;203mSession closed unexpectedly.\x1b[0m':'\r\n\x1b[38;5;244mSession closed.\x1b[0m');
  };
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  $('#login-error').textContent = '';
  try {
    const session=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:form.get('username'),password:form.get('password')})});
    authenticatedActivityTerminated=false;
    $('#login-gate').classList.add('hidden');
    $('.operator strong').textContent=session.username;
    connectLive();
    event.target.reset();
    await refresh();
  } catch (error) { $('#login-error').textContent = error.message; }
});

async function boot() {
  try { const me=await api('/api/auth/me'); authenticatedActivityTerminated=false;$('.operator strong').textContent=me.username; $('#login-gate').classList.add('hidden'); await refresh(); connectLive(); }
  catch { $('#login-gate').classList.remove('hidden'); }
}

function connectLive(){
  if(!$('#login-gate').classList.contains('hidden'))return;
  if(liveSocket&&liveSocket.readyState<WebSocket.CLOSING)return;
  clearTimeout(connectLive.reconnectTimer);
  const protocol=location.protocol==='https:'?'wss:':'ws:';
  const socket=new WebSocket(`${protocol}//${location.host}/ws/live`);
  socket.intentionalClose=false;liveSocket=socket;
  socket.onopen=()=>{if(liveSocket!==socket)return;socket.keepaliveTimer=setInterval(()=>{if(liveSocket===socket&&socket.readyState===WebSocket.OPEN)socket.send('ping');},25000);};
  socket.onmessage=event=>{if(liveSocket!==socket)return;let message={};try{message=JSON.parse(event.data)}catch{}if(message.type==='telemetry'){clearTimeout(connectLive.refreshTimer);connectLive.refreshTimer=setTimeout(refresh,250);}};
  socket.onclose=event=>{clearInterval(socket.keepaliveTimer);if(liveSocket===socket)liveSocket=null;if(event.code===4401){terminateAuthenticatedActivity();return;}if(!socket.intentionalClose&&$('#login-gate').classList.contains('hidden'))connectLive.reconnectTimer=setTimeout(connectLive,3000);};
}

$$('.nav-item').forEach(button => button.addEventListener('click',()=>switchView(button.dataset.view)));
$('#menu-toggle').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
function openNewNode(){state.editingNode=null;const form=$('#node-form');$('#node-title').textContent='Install & add Node';$('#node-submit').textContent='INSTALL & ADD NODE';form.reset();form.elements.ssh_port.value='22';form.elements.ssh_user.value='root';form.elements.role.querySelector('option[value="hub"]').disabled=true;openModal('#node-modal');}
function openNodeEditor(node, focusSSH=false){state.editingNode=node.id;$('#node-title').textContent=`Edit ${node.name}`;$('#node-submit').textContent='SAVE SETTINGS';const form=$('#node-form');form.elements.role.querySelector('option[value="hub"]').disabled=false;form.reset();for(const key of ['name','host','ssh_port','region','role','ssh_user']){if(form.elements[key])form.elements[key].value=node[key]??'';}form.elements.ssh_password.value='';form.elements.ssh_private_key.value='';openModal('#node-modal');if(focusSSH)setTimeout(()=>form.elements.ssh_password.focus(),80);}
$('#add-node').addEventListener('click',openNewNode);
$$('[data-add-node]').forEach(button=>button.addEventListener('click',openNewNode));
$('.modal-cancel').addEventListener('click',()=>closeModal($('#node-modal')));
$$('.plugin-close,.plugin-cancel').forEach(button=>button.addEventListener('click',()=>closeModal($('#plugin-modal'))));
$$('.tunnel-manage-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#tunnel-manage-modal'))));
$$('.output-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#output-modal'))));
$$('.account-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#account-modal'))));
$$('.certificate-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#certificate-modal'))));
$$('.monitor-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#monitor-modal'))));
$$('.fleet-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#fleet-modal'))));
$$('.file-editor-close').forEach(button=>button.addEventListener('click',()=>closeModal($('#file-editor-modal'))));
$('#node-modal .modal-close').addEventListener('click',()=>closeModal($('#node-modal')));
$$('.modal-backdrop').forEach(backdrop=>backdrop.addEventListener('mousedown',event=>event.target===backdrop&&closeModal(backdrop)));

$('#node-form').addEventListener('submit', async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  values.ssh_port = Number(values.ssh_port);
  if (!values.ssh_password) delete values.ssh_password;
  if (!values.ssh_private_key) delete values.ssh_private_key;
  try {
    const result = await api(state.editingNode?`/api/nodes/${state.editingNode}`:'/api/nodes',{method:state.editingNode?'PUT':'POST',body:JSON.stringify(values)});
    closeModal($('#node-modal')); event.target.reset();
    if(state.editingNode){showToast('NODE UPDATED',result.ssh_pin_reset?'Connection changed; SSH fingerprint will be pinned again.':'Node settings saved.');state.editingNode=null;}
    else showToast('NODE INSTALLATION STARTED',`${result.name} is being prepared over SSH. Follow its card for live status.`);
    await refresh();
  } catch (error) { showToast('NODE REGISTRATION FAILED',error.message,true); }
});

$('#plugin-iran-node').addEventListener('change',event=>{const node=state.nodes.find(item=>item.id===Number(event.target.value));if(node)$('#plugin-endpoint').value=node.host;updateTLSSelector();});
$('#open-command').addEventListener('click',()=>openModal('#command-modal'));
$('#command-input').addEventListener('input',event=>{$$('.command-option').forEach(button=>button.hidden=!button.textContent.toLowerCase().includes(event.target.value.toLowerCase()));});
$('#alert-button').addEventListener('click',()=>switchView('incidents'));
$('#list-view').addEventListener('click',()=>switchView('tunnels'));
$('#map-view').addEventListener('click',()=>switchView('overview'));
$('#topology-filter').addEventListener('change',event=>{state.topologyFilter=event.target.value;renderLiveTopology();});
$('#topology-search').addEventListener('input',event=>{state.topologySearch=event.target.value.trim().toLowerCase();renderLiveTopology();});
$('#topology-fullscreen').addEventListener('click',async()=>{const panel=$('.topology-panel');try{if(!document.fullscreenElement)await panel.requestFullscreen();else await document.exitFullscreen();}catch(error){showToast('FULLSCREEN UNAVAILABLE',error.message||'The browser blocked fullscreen mode.',true);}});

document.addEventListener('click', event => {
  const jump = event.target.closest('[data-jump]');
  const takeControl = event.target.closest('#take-control');
  const pluginDeploy = event.target.closest('.plugin-deploy');
  const installMissing = event.target.closest('.plugin-install-missing');
  const createTunnel = event.target.closest('[data-create-tunnel]');
  const manageTunnel = event.target.closest('.tunnel-manage');
  const tunnelAction = event.target.closest('[data-tunnel-action]');
  const ssh = event.target.closest('.server-ssh');
  const diagnostics = event.target.closest('.node-diagnostics');
  const logs = event.target.closest('.node-logs');
  const deleteNode = event.target.closest('.node-delete');
  const restartTunnel = event.target.closest('.tunnel-restart');
  const deployRetry = event.target.closest('.deploy-retry');
  const deployRemove = event.target.closest('.deploy-remove');
  const pairRetry = event.target.closest('.pair-retry');
  const pairRemove = event.target.closest('.pair-remove');
  const pairReveal = event.target.closest('.pair-reveal');
  const topologyNode = event.target.closest('.node[data-node]');
  const cyberNode = event.target.closest('.cyber-node');
  const editNode = event.target.closest('.node-edit');
  const resetPin = event.target.closest('.node-pin');
  const serviceRestart = event.target.closest('.service-chip');
  const incidentAck = event.target.closest('.incident-ack');
  const incidentResolve = event.target.closest('.incident-resolve');
  const incidentReopen = event.target.closest('.incident-reopen');
  const incidentSelect = event.target.closest('[data-incident-select]');
  const monitorRun = event.target.closest('.monitor-run');
  const monitorHistory = event.target.closest('.monitor-history');
  const monitorEdit = event.target.closest('.monitor-edit');
  const monitorDelete = event.target.closest('.monitor-delete');
  const fleetOutput = event.target.closest('[data-fleet-output]');
  const fleetCancel = event.target.closest('.fleet-cancel');
  const fileRow = event.target.closest('[data-file-index]');
  const autohealNode = event.target.closest('.node-autoheal');
  const provisionRetry = event.target.closest('.node-provision-retry');
  const syncAgent = event.target.closest('.node-agent-sync');
  const provisionLog = event.target.closest('.node-provision-log');
  const certRenew = event.target.closest('.cert-renew');
  if (jump) { switchView(jump.dataset.jump); const modal=jump.closest('.modal-backdrop'); if(modal)closeModal(modal); }
  if (takeControl) { const node=state.nodes.find(item=>item.id===Number(takeControl.dataset.nodeId))||state.nodes.find(item=>item.status!=='online')||state.nodes[0]; if(node)connectSSH(node.id); else showToast('NO NODE AVAILABLE','Add a server first.',true); }
  if(createTunnel){const trigger=$('.plugin-deploy');if(trigger)trigger.click();}
  if(installMissing){const pluginId=installMissing.dataset.pluginId,plugin=state.plugins.find(p=>p.id===pluginId),missing=state.nodes.filter(node=>node.status==='online'&&!pluginInventory(node,pluginId)?.installed);Promise.all(missing.map(node=>api(`/api/nodes/${node.id}/plugins/${pluginId}/install`,{method:'POST'}))).then(()=>showToast('CORE INSTALL QUEUED',`${missing.length} Agent${missing.length===1?'':'s'} will install ${plugin?.name||pluginId}.`)).catch(error=>showToast('INSTALL FAILED',error.message,true));}
  if (pluginDeploy) {
    state.selectedPlugin=pluginDeploy.dataset.pluginId;
    const plugin=state.plugins.find(item=>item.id===state.selectedPlugin);
    $('#plugin-title').textContent=`Deploy ${plugin?.name||state.selectedPlugin}`;
    const transport=$('#plugin-form').elements.transport;
    transport.innerHTML=(plugin?.transports||[]).map((item,index)=>`<option value="${esc(item)}">${esc(item.toUpperCase())}${index===0?' · Recommended':''}</option>`).join('');
    const online = state.nodes.filter(node=>node.status==='online');
    const iranNodes=online.filter(node=>['edge','hub'].includes(node.role)), kharejNodes=online.filter(node=>node.role==='exit');
    if (!iranNodes.length) return showToast('IRAN AGENT REQUIRED','At least one online Iran Edge or Hub Agent is required.',true);
    $('#plugin-iran-node').innerHTML = iranNodes.map(node=>`<option value="${Number(node.id)}">${esc(node.name)} · ${esc(node.host)}</option>`).join('');
    $('#plugin-kharej-node').innerHTML = kharejNodes.map(node=>`<option value="${Number(node.id)}">${esc(node.name)} · ${esc(node.host)}</option>`).join('');
    const iran = iranNodes[0];
    $('#plugin-iran-node').value=iran.id; if(kharejNodes[0]){$('#plugin-kharej-node').value=kharejNodes[0].id;$('[name="kharej_endpoint"]',$('#plugin-form')).value=kharejNodes[0].host;} $('#plugin-endpoint').value=iran.host; updatePluginMode();updateTLSSelector();
    openModal('#plugin-modal');
  }
  if (ssh) connectSSH(ssh.dataset.nodeId);
  if(manageTunnel){const tunnel=state.tunnels.find(item=>item.id===Number(manageTunnel.dataset.tunnelId));if(tunnel){state.selectedTunnel=tunnel;const pluginId=tunnelPluginId(tunnel),plugin=state.plugins.find(item=>item.id===pluginId);$('#tunnel-manage-title').textContent=`${tunnel.name} · ${String(tunnel.status).toUpperCase()}`;$('#tunnel-manage-summary').innerHTML=`<span>${pluginIcon(pluginId)}</span><p><strong>${esc(tunnel.node_name)} · ${esc(tunnel.tunnel_role||'unknown role')}</strong><small>${esc(tunnel.node_host)} · ${esc(tunnel.transport)} · ${esc(tunnel.profile)} · ${tunnel.node_agent_online?'AGENT ONLINE':'AGENT OFFLINE'}</small></p>`;const form=$('#tunnel-edit-form');form.elements.user_ports.value=(tunnel.user_ports||[]).join(', ');form.elements.transport.innerHTML=(plugin?.transports||[tunnel.transport]).map(item=>`<option value="${esc(item)}">${esc(item.toUpperCase())}</option>`).join('');form.elements.transport.value=tunnel.transport;form.elements.profile.value=['stable','balanced','lowping','turbo'].includes(tunnel.profile)?tunnel.profile:'balanced';form.elements.restart_every.value=tunnel.restart_every||'off';openModal('#tunnel-manage-modal');}}
  if(tunnelAction&&state.selectedTunnel){const action=tunnelAction.dataset.tunnelAction;if(action==='stop'&&!confirm(`Stop ${state.selectedTunnel.name}?`))return;api(`/api/tunnels/${state.selectedTunnel.id}/action`,{method:'POST',body:JSON.stringify({action})}).then(result=>{showToast('TUNNEL JOB QUEUED',`${action.toUpperCase()} sent to ${state.selectedTunnel.node_name}.`);if(['logs','status','test'].includes(action))waitForJob(result.job_id);setTimeout(refresh,2500);}).catch(error=>showToast('TUNNEL ACTION FAILED',error.message,true));}
  if (diagnostics) createJob(Number(diagnostics.dataset.nodeId),'diagnostics',null,{},true).catch(error=>showToast('COMMAND FAILED',error.message,true));
  if (logs) createJob(Number(logs.dataset.nodeId),'logs',null,{},true).catch(error=>showToast('LOG REQUEST FAILED',error.message,true));
  if (deleteNode && confirm(`Delete node “${deleteNode.dataset.nodeName}” and its telemetry from DARK NOC?`)) api(`/api/nodes/${Number(deleteNode.dataset.nodeId)}`,{method:'DELETE'}).then(()=>{showToast('NODE DELETED',`${deleteNode.dataset.nodeName} was removed from the Hub.`);refresh();}).catch(error=>showToast('DELETE FAILED',error.message,true));
  if (restartTunnel) { if(!restartTunnel.dataset.service)return showToast('NO SERVICE DEFINED','Register a systemd service for this tunnel first.',true); if(confirm(`Restart ${restartTunnel.dataset.service}?`))createJob(Number(restartTunnel.dataset.nodeId),'restart_service',restartTunnel.dataset.service,{},true).then(()=>setTimeout(refresh,3500)); }
  if (deployRetry) api(`/api/plugin-deployments/${Number(deployRetry.dataset.deploymentId)}/retry`,{method:'POST'}).then(()=>{showToast('RETRY QUEUED','Only the failed side will be deployed again.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));
  if (deployRemove && confirm('Remove this managed tunnel from both servers?')) api(`/api/plugin-deployments/${Number(deployRemove.dataset.deploymentId)}/remove`,{method:'POST'}).then(()=>{showToast('REMOVAL QUEUED','The tunnel will be removed from both Agents.');refresh();}).catch(error=>showToast('REMOVAL FAILED',error.message,true));
  if(pairReveal)api(`/api/hybrid-deployments/${Number(pairReveal.dataset.deploymentId)}/pair-code`,{method:'POST'}).then(result=>{const name=result.pair_code.startsWith('DGP-')?'DARK Ghost Pro':result.pair_code.startsWith('DPP-N1-')?'DARK Packet Pro':'DARK Backhaul';$('#output-title').textContent='DARK NOC PAIR CODE';$('#job-output').textContent=`KEEP THIS CODE SECRET\n\n${result.pair_code}\n\nOn the KHAREJ server run ${name}, select KHAREJ, choose Connect with DARK NOC Pair Code, and paste this code.`;openModal('#output-modal');}).catch(error=>showToast('PAIR CODE FAILED',error.message,true));
  if(pairRetry)api(`/api/hybrid-deployments/${Number(pairRetry.dataset.deploymentId)}/retry`,{method:'POST'}).then(()=>{showToast('IRAN RETRY QUEUED','Only the managed Iran side will be retried.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));
  if(pairRemove&&confirm('Remove the IRAN side? You must remove the KHAREJ side manually from its script.'))api(`/api/hybrid-deployments/${Number(pairRemove.dataset.deploymentId)}/remove`,{method:'POST'}).then(()=>{showToast('IRAN REMOVAL QUEUED','Remove the foreign side manually using the matching DARK tunnel script.');refresh();}).catch(error=>showToast('REMOVAL FAILED',error.message,true));
  if (topologyNode) { const node=state.nodes.find(item=>item.name===topologyNode.dataset.node); switchView('servers'); if(node) showToast('NODE LOCATED',`${node.name} · ${node.status}`); }
  if(cyberNode){const node=state.nodes.find(item=>item.id===Number(cyberNode.dataset.nodeId));const related=node?state.tunnels.filter(item=>Number(item.node_id)===node.id||Number(item.peer_node_id)===node.id):[];$('#output-title').textContent=node?`${node.name} · NODE INTELLIGENCE`:cyberNode.dataset.endpointName;$('#job-output').textContent=node?`ROLE: ${node.role}\nHOST: ${node.host}\nOBSERVED IP: ${node.observed_ip||'—'}\nAGENT: ${node.status}\nSSH: ${node.ssh_configured?'READY':'NO ACCESS'}\nLAST SEEN: ${relativeTime(node.last_seen)}\n\nDARK BACKHAUL PATHS:\n${related.map(item=>`• ${item.name} · ${String(item.status).toUpperCase()} · ${item.sessions||0} sessions`).join('\n')||'None'}`:'This endpoint is visible from a live Backhaul TCP peer but is not enrolled as a manageable Node.';openModal('#output-modal');}
  if(editNode){const node=state.nodes.find(item=>item.id===Number(editNode.dataset.nodeId));if(node)openNodeEditor(node);}
  if(resetPin&&confirm('Forget the pinned SSH fingerprint? Only do this after verifying the server key changed.'))api(`/api/nodes/${Number(resetPin.dataset.nodeId)}/ssh-fingerprint`,{method:'DELETE'}).then(()=>showToast('SSH PIN RESET','The next SSH connection will pin the presented host key.')).catch(error=>showToast('PIN RESET FAILED',error.message,true));
  if(serviceRestart&&confirm(`Restart ${serviceRestart.dataset.serviceName}?`))createJob(Number(serviceRestart.dataset.serviceNode),'restart_service',serviceRestart.dataset.serviceName,{},true).then(()=>setTimeout(refresh,2500)).catch(error=>showToast('SERVICE RESTART FAILED',error.message,true));
  if(incidentSelect)loadIncidentDetail(Number(incidentSelect.dataset.incidentSelect));
  if(incidentAck){const form=$('#incident-note-form');api(`/api/incidents/${Number(incidentAck.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'acknowledge',note:form?.elements.message.value||null,root_cause:form?.elements.root_cause.value||null})}).then(()=>{if(form)form.elements.message.value='';return refresh();}).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));}
  if(incidentResolve&&confirm('Resolve this incident and record the operator findings?')){const form=$('#incident-note-form');api(`/api/incidents/${Number(incidentResolve.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'resolve',note:form?.elements.message.value||null,root_cause:form?.elements.root_cause.value||null,resolution:form?.elements.resolution.value||null})}).then(()=>{if(form)form.elements.message.value='';return refresh();}).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));}
  if(incidentReopen&&confirm('Reopen this incident?'))api(`/api/incidents/${Number(incidentReopen.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'reopen'})}).then(refresh).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));
  if(monitorRun)api(`/api/monitors/${Number(monitorRun.dataset.monitorId)}/run`,{method:'POST'}).then(result=>{showToast('MONITOR QUEUED',`Job #${result.job_id} is running from its assigned Agent.`);setTimeout(refresh,1800);}).catch(error=>showToast('MONITOR FAILED',error.message,true));
  if(monitorHistory){const monitor=state.monitors.find(item=>item.id===Number(monitorHistory.dataset.monitorId));api(`/api/monitors/${Number(monitorHistory.dataset.monitorId)}/results?limit=200`).then(results=>{$('#output-title').textContent=`${monitor?.name||'MONITOR'} · LAST ${results.length} CHECKS`;$('#job-output').textContent=results.map(result=>`${new Date(result.ts*1000).toLocaleString('en-GB')}  ${String(result.status).toUpperCase().padEnd(4)}  ${result.latency_ms==null?'—':`${Number(result.latency_ms).toFixed(1)} ms`}\n${JSON.stringify(result.detail||{})}`).join('\n\n')||'No results yet.';openModal('#output-modal');}).catch(error=>showToast('HISTORY FAILED',error.message,true));}
  if(monitorEdit){const monitor=state.monitors.find(item=>item.id===Number(monitorEdit.dataset.monitorId));if(monitor)openMonitorEditor(monitor);}
  if(monitorDelete&&confirm(`Delete monitor “${monitorDelete.dataset.monitorName}” and its result history?`))api(`/api/monitors/${Number(monitorDelete.dataset.monitorId)}`,{method:'DELETE'}).then(()=>{showToast('MONITOR DELETED',monitorDelete.dataset.monitorName);refresh();}).catch(error=>showToast('DELETE FAILED',error.message,true));
  if(fleetOutput){const operation=state.fleetOperations.find(item=>item.id===Number(fleetOutput.dataset.operationId)),item=operation?.items?.find(entry=>entry.id===Number(fleetOutput.dataset.fleetOutput));$('#output-title').textContent=`FLEET OUTPUT · ${item?.node_name||'NODE'}`;$('#job-output').textContent=item?.output||'No output yet.';openModal('#output-modal');}
  if(fleetCancel&&confirm('Cancel this scheduled fleet operation?'))api(`/api/fleet/operations/${Number(fleetCancel.dataset.operationId)}`,{method:'DELETE'}).then(refresh).catch(error=>showToast('CANCEL FAILED',error.message,true));
  if(fileRow){const entry=state.files.entries[Number(fileRow.dataset.fileIndex)];if(entry){fileSelection(entry);fileRow.classList.add('selected');}}
  if(autohealNode){const node=state.nodes.find(item=>item.id===Number(autohealNode.dataset.nodeId));if(node){const enabled=!Boolean(node.autoheal_enabled);if(confirm(`${enabled?'Enable':'Disable'} Auto-Heal on ${node.name}?`))createJob(node.id,'configure_autoheal',null,{enabled,cooldown_seconds:Number(node.autoheal_cooldown||300),max_restarts_per_hour:Number(node.autoheal_max_restarts||3)},true).then(()=>setTimeout(refresh,3000)).catch(error=>showToast('AUTO-HEAL UPDATE FAILED',error.message,true));}}
  if(provisionRetry)api(`/api/nodes/${Number(provisionRetry.dataset.nodeId)}/provision`,{method:'POST'}).then(()=>{showToast('INSTALLATION RETRIED','The Hub is reconnecting and installing the Agent.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));
  if(syncAgent){const node=state.nodes.find(item=>item.id===Number(syncAgent.dataset.nodeId));if(node&&confirm(`Redeploy and synchronize the DARK NOC Agent on “${node.name}”?\n\nManaged configuration and tunnels will be preserved.`))api(`/api/nodes/${node.id}/provision`,{method:'POST'}).then(()=>{showToast('AGENT SYNC STARTED',`${node.name} is receiving the matching Agent build; managed configuration and tunnels are preserved.`);refresh();}).catch(error=>showToast('AGENT SYNC FAILED',error.message,true));}
  if(provisionLog){const node=state.nodes.find(item=>item.id===Number(provisionLog.dataset.nodeId));if(node){$('#output-title').textContent=`${node.name} · installation ${String(node.provision_status||'unknown').toUpperCase()}`;$('#job-output').textContent=node.provision_output||'Installation is running; refresh in a few seconds.';openModal('#output-modal');}}
  if(certRenew)api(`/api/certificates/${Number(certRenew.dataset.certId)}/renew`,{method:'POST'}).then(result=>{showToast('RENEWAL QUEUED',`Certificate job #${result.job_id} queued.`);refresh();}).catch(error=>showToast('RENEWAL FAILED',error.message,true));
});

$('#plugin-mode').addEventListener('change',updatePluginMode);
$('#plugin-form').elements.transport.addEventListener('change',updateTLSSelector);
$('#plugin-certificate').addEventListener('change',event=>{const cert=state.certificates.find(item=>item.id===Number(event.target.value));if(cert)$('#plugin-endpoint').value=cert.domain;});
function openCertificateModal(){const select=$('#certificate-node');select.innerHTML=state.nodes.filter(n=>n.status==='online'&&['edge','hub'].includes(n.role)).map(n=>`<option value="${Number(n.id)}">${esc(n.name)} · ${esc(n.host)}</option>`).join('');const chosen=Number($('#plugin-iran-node').value);if(chosen)select.value=chosen;openModal('#certificate-modal');}
$('#add-certificate').addEventListener('click',openCertificateModal);
$('#plugin-get-certificate').addEventListener('click',openCertificateModal);
$('#certificate-form').addEventListener('submit',async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));values.node_id=Number(values.node_id);try{const result=await api('/api/certificates',{method:'POST',body:JSON.stringify(values)});closeModal($('#certificate-modal'));event.target.reset();showToast('CERTIFICATE QUEUED',`DNS matched. Job #${result.job_id} is requesting Let's Encrypt.`);await refresh();}catch(error){showToast('CERTIFICATE FAILED',error.message,true);}});
$('#tunnel-edit-form').addEventListener('submit',async event=>{event.preventDefault();if(!state.selectedTunnel)return;const values=Object.fromEntries(new FormData(event.target));values.user_ports=String(values.user_ports).split(/[ ,]+/).filter(Boolean).map(Number);try{const result=await api(`/api/tunnels/${state.selectedTunnel.id}`,{method:'PUT',body:JSON.stringify(values)});closeModal($('#tunnel-manage-modal'));if(result.pair_code){$('#output-title').textContent='UPDATED PAIR CODE';$('#job-output').textContent=`Iran update queued. Apply this NEW code on KHAREJ:\n\n${result.pair_code}`;openModal('#output-modal');}else showToast('REDEPLOY QUEUED','Updated configuration is being applied to both managed sides.');await refresh();}catch(error){showToast('TUNNEL UPDATE FAILED',error.message,true);}});
$('#tunnel-delete').addEventListener('click',async()=>{if(!state.selectedTunnel||!confirm(`Permanently remove ${state.selectedTunnel.method||'DARK'} tunnel “${state.selectedTunnel.name}”?`))return;try{const result=await api(`/api/tunnels/${state.selectedTunnel.id}`,{method:'DELETE'});closeModal($('#tunnel-manage-modal'));showToast('REMOVAL QUEUED',result.foreign_action_required?'Iran removal queued; remove KHAREJ manually.':'Managed tunnel removal queued.');await refresh();}catch(error){showToast('TUNNEL REMOVAL FAILED',error.message,true);}});
$('#plugin-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const values=Object.fromEntries(new FormData(event.target));
  const pairMode=values.deployment_mode==='pair_code'; delete values.deployment_mode;
  values.iran_node_id=Number(values.iran_node_id); values.tunnel_port=Number(values.tunnel_port);
  if(values.certificate_id)values.certificate_id=Number(values.certificate_id);else delete values.certificate_id;
  values.user_ports=String(values.user_ports).split(/[ ,]+/).filter(Boolean).map(Number);
  if(pairMode)delete values.kharej_node_id;else{values.kharej_node_id=Number(values.kharej_node_id);delete values.remote_label;if(!values.kharej_node_id)return showToast('KHAREJ AGENT REQUIRED','Select an online Global Exit Agent or use Pair Code mode.',true);if(values.iran_node_id===values.kharej_node_id)return showToast('INVALID NODE PAIR','Iran and Kharej must be different servers.',true);}
  try{
    const pluginId=state.selectedPlugin||'dark-backhaul', plugin=state.plugins.find(item=>item.id===pluginId);
    const deployment=await api(pairMode?`/api/plugins/${pluginId}/pair-code`:`/api/plugins/${pluginId}/deploy`,{method:'POST',body:JSON.stringify(values)});
    closeModal($('#plugin-modal'));
    if(pairMode){$('#output-title').textContent='PAIR CODE READY';$('#job-output').textContent=`IRAN installation queued as job #${deployment.iran_job_id}.\nWait for IRAN to become READY, then on the foreign server:\n\n1. Run the ${plugin?.name||pluginId} script\n2. Select KHAREJ\n3. Choose Connect with DARK NOC Pair Code\n4. Paste this secret code:\n\n${deployment.pair_code}`;openModal('#output-modal');showToast('PAIR CODE CREATED','IRAN is being configured; copy the code for KHAREJ.');}else showToast('DUAL DEPLOYMENT QUEUED',`Deployment #${deployment.deployment_id} is running on both Agents.`);
    event.target.reset(); updatePluginMode(); await refresh();
  }catch(error){showToast('PLUGIN DEPLOYMENT FAILED',error.message,true);}
});

$('#ssh-upload-form').addEventListener('submit',event=>{
  event.preventDefault();const form=event.target,file=form.elements.file.files[0],nodeId=Number(form.elements.node_id.value);
  if(!file||!nodeId)return showToast('UPLOAD NOT READY','Select a destination server and local file.',true);
  const limit=state.limits.ssh_upload_bytes;
  if(limit&&file.size>limit){const message=`${file.name} is ${fileSize(file.size)}; the Hub limit is ${fileSize(limit)}.`;transferStatus('#upload-progress','error',`LIMIT EXCEEDED · ${message}`,0);return showToast('FILE TOO LARGE',message,true);}
  const payload=new FormData();payload.append('file',file,file.name);payload.append('remote_path',form.elements.remote_path.value);payload.append('overwrite',form.elements.overwrite.checked?'true':'false');
  const xhr=new XMLHttpRequest();xhr.open('POST',`/api/ssh/upload/${nodeId}`);xhr.withCredentials=true;xhr.timeout=24*60*60*1000;activeUpload=xhr;setTransferControls(form,true);
  const finish=()=>{if(activeUpload!==xhr)return false;activeUpload=null;setTransferControls(form,false);return true;};
  transferStatus('#upload-progress','running',`BROWSER → HUB · ${file.name} · 0%`,0);
  xhr.upload.onprogress=progress=>{if(activeUpload===xhr&&progress.lengthComputable){const percent=Math.round(progress.loaded/progress.total*100);transferStatus('#upload-progress','running',`BROWSER → HUB · ${fileSize(progress.loaded)} / ${fileSize(progress.total)} · ${percent}%`,percent);}};
  xhr.upload.onload=()=>{if(activeUpload===xhr)transferStatus('#upload-progress','running','HUB RECEIVED · Opening secure SFTP session');};
  xhr.onload=()=>{if(!finish())return;let result={};try{result=JSON.parse(xhr.responseText||'{}');}catch{}if(xhr.status>=200&&xhr.status<300){transferStatus('#upload-progress','done',`COMPLETE · ${result.path} · ${fileSize(result.bytes)}`,100);showToast('FILE UPLOADED',`${file.name} → ${result.node}:${result.path}`);form.elements.file.value='';}else{const detail=Array.isArray(result.detail)?result.detail.map(item=>item.msg).join(' · '):result.detail,message=typeof detail==='string'?detail:`Upload failed (${xhr.status})`;if(xhr.status===401){terminateAuthenticatedActivity();transferStatus('#upload-progress','error','AUTHENTICATION EXPIRED · Sign in again',0);return;}transferStatus('#upload-progress','error',message,0);showToast('UPLOAD FAILED',message,true);}};
  xhr.onerror=()=>{if(!finish())return;transferStatus('#upload-progress','error','NETWORK ERROR · Upload interrupted',0);showToast('UPLOAD FAILED','Network connection was interrupted.',true);};
  xhr.ontimeout=()=>{if(!finish())return;transferStatus('#upload-progress','error','TIMEOUT · Server did not finish within 24 hours',0);showToast('UPLOAD TIMED OUT','The transfer was cancelled after 24 hours.',true);};
  xhr.onabort=()=>{if(!finish())return;transferStatus('#upload-progress','cancelled','CANCEL REQUESTED · Verify the destination before retrying',0);showToast('UPLOAD CANCEL REQUESTED','The request was closed; verify the remote destination before retrying.');};
  xhr.send(payload);
});

$('#upload-cancel').addEventListener('click',()=>{if(activeUpload)activeUpload.abort();});
$('#ssh-upload-form [name="file"]').addEventListener('change',event=>{const file=event.target.files[0],limit=state.limits.ssh_upload_bytes;if(!file)return transferStatus('#upload-progress','','READY · Select a file');if(limit&&file.size>limit)transferStatus('#upload-progress','error',`LIMIT EXCEEDED · ${fileSize(file.size)} selected; maximum ${fileSize(limit)}`,0);else transferStatus('#upload-progress','',`READY · ${file.name} · ${fileSize(file.size)}`,0);});

$('#ssh-relay-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,values=Object.fromEntries(new FormData(form));values.source_node_id=Number(values.source_node_id);values.destination_node_id=Number(values.destination_node_id);values.overwrite=form.elements.overwrite.checked;
  if(values.source_node_id===values.destination_node_id&&values.source_path===values.destination_path)return showToast('INVALID TRANSFER','Source and destination are the same file.',true);
  const controller=new AbortController();activeRelay=controller;setTransferControls(form,true);transferStatus('#relay-progress','running','CONNECTING · Validating source and destination');
  const phaseTimer=setTimeout(()=>{if(activeRelay===controller)transferStatus('#relay-progress','running',`RELAY ACTIVE · Hub streaming (limit ${state.limits.ssh_relay_bytes?fileSize(state.limits.ssh_relay_bytes):'server enforced'})`);},300);
  try{const result=await api('/api/ssh/relay',{method:'POST',body:JSON.stringify(values),signal:controller.signal});if(activeRelay!==controller)return;transferStatus('#relay-progress','done',`COMPLETE · ${fileSize(result.bytes)} · ${result.source_node} → ${result.destination_node}`,100);showToast('TRANSFER COMPLETE',`${result.source_node}:${result.source_path} → ${result.destination_node}:${result.destination_path}`);}
  catch(error){if(activeRelay!==controller)return;if(error.name==='AbortError'){transferStatus('#relay-progress','cancelled','CANCEL REQUESTED · Verify the destination before retrying',0);showToast('TRANSFER CANCEL REQUESTED','The request was closed; verify the remote destination before retrying.');}else{transferStatus('#relay-progress','error',error.message,0);showToast('TRANSFER FAILED',error.message,true);}}
  finally{clearTimeout(phaseTimer);if(activeRelay===controller){activeRelay=null;setTransferControls(form,false);}}
});

$('#relay-cancel').addEventListener('click',()=>{if(activeRelay)activeRelay.abort();});

$('#add-monitor').addEventListener('click',()=>openMonitorEditor());
$('#monitor-form [name="kind"]').addEventListener('change',event=>{$('.snmp-fields',$('#monitor-form')).hidden=event.target.value!=='snmp';});
$('#monitor-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,values=Object.fromEntries(new FormData(form));const monitorId=Number(values.monitor_id||0);delete values.monitor_id;
  values.node_id=Number(values.node_id);values.interval_seconds=Number(values.interval_seconds);values.timeout_seconds=Number(values.timeout_seconds);values.enabled=form.elements.enabled.checked;
  for(const key of ['port','expected_status']){if(values[key])values[key]=Number(values[key]);else delete values[key];}
  for(const key of ['snmp_community','snmp_oid'])if(!values[key])delete values[key];
  if(!['http','https'].includes(values.kind))delete values.expected_status;
  if(values.kind!=='snmp'){delete values.snmp_community;delete values.snmp_oid;}
  try{await api(monitorId?`/api/monitors/${monitorId}`:'/api/monitors',{method:monitorId?'PUT':'POST',body:JSON.stringify(values)});closeModal($('#monitor-modal'));state.editingMonitor=null;showToast(monitorId?'MONITOR UPDATED':'MONITOR CREATED',`${values.name} will run from the selected Agent.`);await refresh();}
  catch(error){showToast('MONITOR SAVE FAILED',error.message,true);}
});
$('#run-all-monitors').addEventListener('click',async()=>{const enabled=state.monitors.filter(item=>item.enabled);if(!enabled.length)return showToast('NO ENABLED MONITORS','Add or enable a monitor first.',true);const results=await Promise.allSettled(enabled.map(item=>api(`/api/monitors/${item.id}/run`,{method:'POST'}))),queued=results.filter(item=>item.status==='fulfilled').length,failed=results.length-queued;showToast(failed?'MONITORS PARTIALLY QUEUED':'MONITORS QUEUED',`${queued} queued · ${failed} skipped${failed?' (offline or already running)':''}`,failed>0&&queued===0);setTimeout(refresh,1800);});

$('#add-fleet-operation').addEventListener('click',openFleetEditor);
$('#fleet-form [name="kind"]').addEventListener('change',event=>{const needsService=['restart_service','service_status'].includes(event.target.value),service=$('#fleet-form [name="service"]');service.required=needsService;service.closest('label').classList.toggle('required-field',needsService);$('.fleet-autoheal',$('#fleet-form')).hidden=event.target.value!=='configure_autoheal';if(event.target.value==='sync_agent')$$('#fleet-node-picker input').forEach(input=>{const node=state.nodes.find(item=>item.id===Number(input.value));if(node?.role==='hub')input.checked=false;});});
$('#fleet-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,nodeIds=$$('#fleet-node-picker input:checked').map(input=>Number(input.value));if(!nodeIds.length)return showToast('SELECT NODES','Choose at least one online Agent.',true);
  const values=Object.fromEntries(new FormData(form));const payload={};if(values.kind==='configure_autoheal')Object.assign(payload,{enabled:values.autoheal_enabled==='true',cooldown_seconds:Number(values.cooldown_seconds),max_restarts_per_hour:Number(values.max_restarts_per_hour)});
  const body={name:values.name,kind:values.kind,node_ids:nodeIds,payload};if(values.service)body.service=values.service;if(values.scheduled_at)body.scheduled_at=Math.floor(new Date(values.scheduled_at).getTime()/1000);
  try{const result=await api('/api/fleet/operations',{method:'POST',body:JSON.stringify(body)});closeModal($('#fleet-modal'));showToast('FLEET OPERATION QUEUED',`${result.nodes} Agent${result.nodes===1?'':'s'} assigned.`);await refresh();}
  catch(error){showToast('FLEET OPERATION FAILED',error.message,true);}
});

$('#incident-note-form').addEventListener('submit',async event=>{event.preventDefault();const incidentId=Number(event.target.dataset.incidentId);if(!incidentId)return;const message=event.target.elements.message.value.trim();try{await api(`/api/incidents/${incidentId}/notes`,{method:'POST',body:JSON.stringify({message,event_type:'note'})});event.target.elements.message.value='';showToast('TIMELINE UPDATED','Operator note was recorded.');await loadIncidentDetail(incidentId);await refresh();}catch(error){showToast('NOTE FAILED',error.message,true);}});

$('#file-manager-node').addEventListener('change',event=>{state.files.nodeId=Number(event.target.value)||null;state.files.path='/root';loadFileDirectory('/root');});
$('#file-manager-go').addEventListener('click',()=>loadFileDirectory($('#file-manager-path').value));
$('#file-manager-refresh').addEventListener('click',()=>loadFileDirectory(state.files.path));
$('#file-manager-up').addEventListener('click',()=>{if(state.files.parent)loadFileDirectory(state.files.parent);});
$('#file-manager-path').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();loadFileDirectory(event.target.value);}});
$('#file-manager-rows').addEventListener('dblclick',event=>{const row=event.target.closest('[data-file-index]');if(!row)return;const entry=state.files.entries[Number(row.dataset.fileIndex)];if(entry?.type==='directory')loadFileDirectory(entry.path);else if(entry?.type==='file')$('#file-edit').click();});
$('#file-new-directory').addEventListener('click',async()=>{if(!state.files.nodeId)return showToast('SELECT A SERVER','Choose an SSH-enabled server first.',true);const name=prompt('New directory name');if(!name)return;const path=`${state.files.path==='/'?'':state.files.path}/${name}`;try{await fileAction('mkdir',{path});showToast('DIRECTORY CREATED',path);}catch(error){showToast('CREATE FAILED',error.message,true);}});
$('#file-download').addEventListener('click',()=>{const entry=state.files.selected;if(!entry||entry.type!=='file')return;const link=document.createElement('a');link.href=`/api/ssh/files/${state.files.nodeId}/download?path=${encodeURIComponent(entry.path)}`;link.download=entry.name;document.body.appendChild(link);link.click();link.remove();});
$('#file-edit').addEventListener('click',async()=>{const entry=state.files.selected;if(!entry||entry.type!=='file')return;try{const result=await api(`/api/ssh/files/${state.files.nodeId}/read?path=${encodeURIComponent(entry.path)}`);state.files.editingPath=result.path;$('#file-editor-title').textContent=`Edit ${entry.name}`;$('#file-editor-content').value=result.content;$('#file-editor-meta').textContent=`${result.path} · ${fileSize(result.bytes)} · UTF-8`;openModal('#file-editor-modal');}catch(error){showToast('EDITOR FAILED',error.message,true);}});
$('#file-editor-save').addEventListener('click',async()=>{if(!state.files.editingPath)return;try{const result=await api(`/api/ssh/files/${state.files.nodeId}/write`,{method:'PUT',body:JSON.stringify({path:state.files.editingPath,content:$('#file-editor-content').value,overwrite:true})});closeModal($('#file-editor-modal'));showToast('FILE SAVED',`${result.path} · ${fileSize(result.bytes)}`);await loadFileDirectory(state.files.path);}catch(error){showToast('SAVE FAILED',error.message,true);}});
$('#file-checksum').addEventListener('click',async()=>{const entry=state.files.selected;if(!entry||entry.type!=='file')return;try{const result=await api(`/api/ssh/files/${state.files.nodeId}/checksum?path=${encodeURIComponent(entry.path)}`);$('#output-title').textContent=`SHA-256 · ${entry.name}`;$('#job-output').textContent=`${result.sha256}  ${result.path}\n\n${fileSize(result.bytes)} verified through SFTP.`;openModal('#output-modal');}catch(error){showToast('CHECKSUM FAILED',error.message,true);}});
$('#file-rename').addEventListener('click',async()=>{const entry=state.files.selected;if(!entry)return;const name=prompt('New name',entry.name);if(!name||name===entry.name)return;const destination=`${state.files.path==='/'?'':state.files.path}/${name}`;try{await fileAction('rename',{destination});showToast('RENAMED',`${entry.name} → ${name}`);}catch(error){showToast('RENAME FAILED',error.message,true);}});
$('#file-chmod').addEventListener('click',async()=>{const entry=state.files.selected;if(!entry)return;const mode=prompt('Octal permissions (example: 0644)',entry.mode||'0644');if(!mode)return;try{await fileAction('chmod',{mode});showToast('PERMISSIONS UPDATED',`${entry.path} · ${mode}`);}catch(error){showToast('CHMOD FAILED',error.message,true);}});
$('#file-delete').addEventListener('click',async()=>{const entry=state.files.selected;if(!entry||!confirm(`Delete ${entry.type} “${entry.path}”?\n\nDirectories must be empty. This action cannot be undone.`))return;try{await fileAction('delete');showToast('REMOTE ITEM DELETED',entry.path);}catch(error){showToast('DELETE FAILED',error.message,true);}});

$('#clear-terminal').addEventListener('click',()=>{const entry=activeTerminal();if(entry){entry.terminal.clear();entry.terminal.focus();}});
$('#ssh-connect').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose a Hub, Iran or Kharej server first.',true);connectSSH(nodeId);});
$('#terminal-new-tab').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose the server for the new tab.',true);const alternate=activeTerminalSlot()==='terminal-iran'?'terminal-germany':'terminal-iran';connectSSH(nodeId,alternate);});
$('#terminal-copy').addEventListener('click',async()=>{const entry=activeTerminal();if(!entry)return showToast('NO TERMINAL','Select a terminal pane first.',true);if(!requireClipboard('writeText','COPY'))return;const text=entry.terminal.getSelection()||terminalText(entry.terminal);try{await navigator.clipboard.writeText(text);showToast('COPIED',entry.terminal.hasSelection()?'Selection copied.':'Terminal buffer copied.');}catch{showToast('COPY BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-paste').addEventListener('click',async()=>{const socket=connectedSocket();if(!socket)return showToast('SSH NOT CONNECTED','Wait for the active terminal to finish connecting.',true);if(!requireClipboard('readText','PASTE'))return;try{const text=await navigator.clipboard.readText();socket.send(JSON.stringify({type:'input',data:text}));activeTerminal()?.terminal.focus();}catch{showToast('PASTE BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-fullscreen').addEventListener('click',async()=>{const card=$('.terminal-card.active-terminal')||$('.terminal-card');try{if(!document.fullscreenElement)await card.requestFullscreen();else await document.exitFullscreen();setTimeout(()=>activeTerminal()?.fitAddon.fit(),100);}catch(error){showToast('FULLSCREEN UNAVAILABLE',error.message||'The browser blocked fullscreen mode.',true);}});
$('#terminal-search-input').addEventListener('input',event=>activeTerminal()?.searchAddon.findNext(event.target.value,{incremental:true,decorations:{matchBackground:'#5d401d',activeMatchBackground:'#b36cff'}}));
$('#terminal-search-next').addEventListener('click',()=>activeTerminal()?.searchAddon.findNext($('#terminal-search-input').value));
$('#terminal-search-prev').addEventListener('click',()=>activeTerminal()?.searchAddon.findPrevious($('#terminal-search-input').value));
$('#terminal-download-log').addEventListener('click',()=>{const entry=activeTerminal();if(!entry)return;const blob=new Blob([terminalText(entry.terminal)],{type:'text/plain'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-ssh-${Date.now()}.log`;a.click();URL.revokeObjectURL(a.href);});
$('#disconnect').addEventListener('click',()=>{[...state.sockets.keys()].forEach(closeSocket);$$('.ssh-online').forEach(status=>status.textContent='● CLOSED');showToast('SSH DISCONNECTED','All interactive sessions were closed.');});
$('#run-all-tests').addEventListener('click',async()=>{const online=state.nodes.filter(node=>node.status==='online');if(!online.length)return showToast('NO ONLINE AGENTS','Tunnel tests were not queued.',true);await Promise.all(online.map(node=>createJob(node.id,'tunnel_test')));showToast('TUNNEL TESTS QUEUED',`${online.length} online Agent${online.length===1?'':'s'} will test every configured tunnel.`);});
$('#filter-nodes').addEventListener('click',event=>{state.nodeFilter=state.nodeFilter==='all'?'attention':'all';event.target.textContent=`FILTER: ${state.nodeFilter==='all'?'ALL':'ATTENTION'}`;renderNodes();});
$('#export-incidents').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(state.incidents,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-incidents-${Date.now()}.json`;a.click();URL.revokeObjectURL(a.href);});
$('#session-log').addEventListener('click',async()=>{try{const jobs=await api('/api/jobs?limit=100');$('#output-title').textContent='Remote job history';$('#job-output').textContent=jobs.map(job=>`#${job.id} [${job.status}] ${job.node_name} · ${job.kind}\n${job.output||''}`).join('\n\n');openModal('#output-modal');}catch(error){showToast('HISTORY FAILED',error.message,true);}});
$('#copy-output').addEventListener('click',async()=>{if(!requireClipboard('writeText','COPY'))return;try{await navigator.clipboard.writeText($('#job-output').textContent);showToast('OUTPUT COPIED','Command output copied to clipboard.');}catch{showToast('COPY BLOCKED','Allow clipboard access in the browser.',true);}});
$$('.terminal-card').forEach(card=>{card.addEventListener('pointerdown',()=>selectTerminalCard(card));card.addEventListener('focusin',()=>selectTerminalCard(card));});
$$('[data-command]').forEach(button=>button.addEventListener('click',()=>{const socket=connectedSocket();if(!socket)return showToast('SSH NOT CONNECTED','Wait for the active terminal to finish connecting.',true);socket.send(JSON.stringify({type:'input',data:`${button.dataset.command}\n`}));activeTerminal()?.terminal.focus();}));
$('.operator').addEventListener('click',async()=>{try{const me=await api('/api/auth/me');$('#account-current-user').textContent=me.username;openModal('#account-modal');}catch(error){showToast('ACCOUNT ERROR',error.message,true);}});
$('#logout-button').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});}catch(error){if(error.message!=='Authentication required')showToast('LOGOUT FAILED',error.message,true);}finally{terminateAuthenticatedActivity({transferMessage:'SIGNED OUT · Transfer stopped',terminalStatus:'● SIGNED OUT'});closeModal($('#account-modal'));}});

document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openModal('#command-modal');}
  if(event.key==='Escape'){$$('.modal-backdrop.open').forEach(closeModal);}
});

function updateClock(){ $('#clock').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()); }
ensureTerminal('terminal-iran');ensureTerminal('terminal-germany');$('.terminal-card').classList.add('active-terminal');
updateClock(); setInterval(updateClock,1000); setInterval(refresh,15000); boot();
