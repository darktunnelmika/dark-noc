const { $, $$, esc, bytesPerSecond, fileSize, displayHost, relativeTime, duration, elapsedDuration } = window.DarkNocCore;
const viewTitles = { overview: 'Network Command', servers: 'Server Fleet', tunnels: 'Tunnel Matrix', monitors: 'Synthetic Monitoring', fleet: 'Fleet Operations', certificates: 'TLS Vault', incidents: 'Incident Command', terminal: 'SSH Command' };
let state = {
  nodes: [], tunnels: [], incidents: [], plugins: [], deployments: [], certificates: [], traffic: [], monitors: [], fleetOperations: [],
  limits: { ssh_upload_bytes: null, ssh_relay_bytes: null }, sockets: new Map(), terminals: new Map(),
  hubVersion: null, paneCounter: 2, replayTimer: null,
  selectedNode: null, selectedTunnel: null, selectedIncident: null, selectedPlugin: 'dark-backhaul',
  nodeFilter: 'all', topologyFilter: 'all', topologySearch: '', editingNode: null, editingMonitor: null,
  files: { nodeId: null, path: '/root', parent: '/', entries: [], selected: null, editingPath: null }
};
let refreshInFlight = false;
let liveRefreshInFlight = false;
let liveSocket = null;
let activeUpload = null;
let activeRelay = null;
let authenticatedActivityTerminated = false;

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

function switchView(name) {
  if (!viewTitles[name]) return;
  $$('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  $('#view-title').textContent = viewTitles[name];
  $('#sidebar').classList.remove('open');
  scrollTo({ top: 0, behavior: 'smooth' });
  renderCachedView(name);
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
function pluginMethod(pluginId){return pluginId==='dark-realm'?'DARK Realm Pro':pluginId==='dark-ghostpro'?'DARK Ghost Pro':pluginId==='dark-packetpro'?'DARK Packet Pro':'DARK Backhaul';}
function pluginIcon(pluginId){return pluginId==='dark-realm'?'DR':pluginId==='dark-ghostpro'?'DG':pluginId==='dark-packetpro'?'DP':'DB';}
function tunnelPluginId(tunnel){return tunnel.method==='DARK Realm Pro'?'dark-realm':tunnel.method==='DARK Ghost Pro'?'dark-ghostpro':tunnel.method==='DARK Packet Pro'?'dark-packetpro':'dark-backhaul';}
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

function realmCertificateNodeId(){
  const form=$('#plugin-form');
  return state.selectedPlugin==='dark-realm'?Number(form.elements.kharej_node_id.value):Number(form.elements.iran_node_id.value);
}

function updateRealmFields(){
  const form=$('#plugin-form'),realm=state.selectedPlugin==='dark-realm',transport=form.elements.transport.value;
  $('#plugin-realm').hidden=!realm;
  $('#plugin-realm-tls').hidden=!realm||!['tls','wss'].includes(transport);
  $('#plugin-realm-ws').hidden=!realm||!['ws','wss'].includes(transport);
  ['target_host','port_mappings'].forEach(name=>{if(form.elements[name])form.elements[name].required=realm;});
  ['tls_domain','sni'].forEach(name=>{if(form.elements[name])form.elements[name].required=realm&&['tls','wss'].includes(transport);});
  ['ws_host','ws_path'].forEach(name=>{if(form.elements[name])form.elements[name].required=realm&&['ws','wss'].includes(transport);});
}

function updateTLSSelector(){
  const form=$('#plugin-form'),transport=form.elements.transport.value,realm=state.selectedPlugin==='dark-realm',pairMode=form.elements.deployment_mode.value==='pair_code',required=['tls','wss','wssmux','h2','grpc','relay+tls','relay+wss','relay+h2','relay+grpc'].includes(transport),nodeId=realmCertificateNodeId(),box=$('#plugin-tls'),select=$('#plugin-certificate');
  updateRealmFields();
  box.hidden=!required||realm&&pairMode;select.required=required&&!(realm&&pairMode);
  if(!required||realm&&pairMode){select.value='';return;}
  const valid=state.certificates.filter(c=>Number(c.node_id)===nodeId&&c.status==='valid'&&Number(c.expires_at)>Date.now()/1000+86400);
  select.innerHTML='<option value="">SELECT VALID CERTIFICATE</option>'+valid.map(c=>`<option value="${Number(c.id)}">${esc(c.domain)} · ${Math.ceil((c.expires_at-Date.now()/1000)/86400)} days</option>`).join('');
  if(valid.length===1){select.value=valid[0].id;if(realm){form.elements.tls_domain.value=valid[0].domain;form.elements.sni.value=valid[0].domain;form.elements.ws_host.value=valid[0].domain;form.elements.iran_endpoint.value=valid[0].domain;}else form.elements.iran_endpoint.value=valid[0].domain;}
}

function updatePluginMode(){
  const form=$('#plugin-form'),managed=$('#plugin-mode').value==='managed',packet=state.selectedPlugin==='dark-packetpro',realm=state.selectedPlugin==='dark-realm';
  $('#plugin-kharej-managed').hidden=!managed; $('#plugin-kharej-pair').hidden=managed;
  $('#plugin-kharej-node').required=managed; $('[name="remote_label"]',form).required=!managed;
  $('#plugin-kharej-endpoint').hidden=managed||!(packet||realm); $('[name="kharej_endpoint"]',form).required=!managed&&(packet||realm);
  $('#plugin-kharej-endpoint strong')?.remove();
  $('#plugin-iran-role').textContent=realm?'Edge · exposes public user ports':packet?'Client · exposes local user ports':'Server · accepts tunnel';
  $('#plugin-remote-help').textContent=managed?(realm?'Gateway · native Realm listener':packet?'Server · automatic install':'Client · automatic install'):'Script · paste Pair Code';
  $('#plugin-security-title').textContent=realm?'Native Realm deployment':managed?'Zero-copy pairing':'Offline-capable secure pairing';
  $('#plugin-security-copy').textContent=realm?'DARK NOC keeps Realm TOML native: Iran Edge listens on public ports, Kharej Gateway listens on backbone ports, and TLS certificates stay on the Gateway.':managed?'DARK NOC securely applies matching configuration to both Agents.':'The Hub configures IRAN and generates a compatible code. On KHAREJ select the normal Pair Code flow and paste it.';
  $('#plugin-submit').textContent=managed?'DEPLOY ON BOTH SERVERS':'CONFIGURE IRAN & GENERATE CODE';
  $('#plugin-endpoint-label').firstChild.textContent=realm?'GATEWAY IP / DOMAIN':'IRAN PUBLIC IP / DOMAIN';
  updateRealmFields();updateTLSSelector();
}

function topologyRouteVisual(link) {
  const width=Math.min(4,1.6+Math.log10(link.rate+1)/4);
  const duration=Math.max(1.25,6.2-Math.log10(link.rate+1));
  const active=(link.tone==='online'||link.tone==='degraded')&&link.rate>0;
  return {width,duration,active,energy:Math.max(0,Math.min(1,(width-1.6)/2.4))};
}

function syncLiveTopologyRoutes(topology,links,nodeIndex) {
  topology._liveLinks=links;
  $$('.route-group',topology).forEach(route=>{
    const index=Number(route.dataset.route),link=links[index];if(!link)return;
    route.classList.remove('online','degraded','stale','down');route.classList.add(link.tone);
    const visual=topologyRouteVisual(link),backbone=$('.cyber-route-backbone',route),flow=$('.cyber-route-flow',route);
    if(backbone)backbone.style.setProperty('--route-width',visual.width.toFixed(2));
    if(flow){flow.style.setProperty('--route-width',visual.width.toFixed(2));flow.style.setProperty('--flow-duration',`${visual.duration.toFixed(2)}s`);}
    route.dataset.matrixFlow=visual.active?'active':'idle';
    route.style.setProperty('--matrix-energy',String(visual.energy));
    $$('animateMotion',route).forEach(motion=>motion.setAttribute('dur',`${visual.duration.toFixed(2)}s`));
    const label=$('.route-label',route);if(label)label.textContent=`${link.tunnel.name} · ${String(link.tunnel.transport||link.tunnel.method).toUpperCase()}`;
    route.setAttribute('aria-label',`${link.tunnel.name||'Tunnel'} · ${link.status} · Open operations`);
  });
  $$('.cyber-node',topology).forEach(card=>{
    const id=Number(card.dataset.nodeId||0),node=id?nodeIndex.get(id):null;if(!node)return;
    const badges=$$('em b',card);if(badges[0]){badges[0].textContent=`AG ${node.status==='online'?'ON':'OFF'}`;badges[0].className=node.status==='online'?'ready':'missing';}
    if(badges[1]){badges[1].textContent=`SSH ${node.ssh_configured?'READY':'NO'}`;badges[1].className=node.ssh_configured?'ready':'missing';}
  });
}

function renderLiveTopology() {
  const topology=$('#topology');
  const links=window.DarkNocTopology.buildLinks({nodes:state.nodes,tunnels:state.tunnels,filter:state.topologyFilter,search:state.topologySearch});

  if(!links.length){
    const emptySignature=`empty|${state.topologyFilter}|${state.topologySearch}`;
    topology.style.setProperty('--topology-canvas-height','414px');
    if(topology.dataset.topologySignature!==emptySignature){topology.innerHTML='<div class="grid-floor"></div><div class="empty-state compact-empty"><strong>No matching tunnel path</strong>Change the filter or wait for a complete Agent inventory.</div>';topology.dataset.topologySignature=emptySignature;}
    topology._liveLinks=[];return;
  }

  const rootMap=new Map(),leafMap=new Map();
  links.forEach(link=>{
    link.rootKey=link.left.node?.id?`n${link.left.node.id}`:`r${link.left.name}|${link.left.host}`;
    link.leafKey=link.right.node?.id?`n${link.right.node.id}`:`e${link.right.name}|${link.right.host}`;
    if(!rootMap.has(link.rootKey))rootMap.set(link.rootKey,link.left);if(!leafMap.has(link.leafKey))leafMap.set(link.leafKey,link.right);
  });
  const roots=[...rootMap.entries()],leaves=[...leafMap.entries()];
  const structureSignature=JSON.stringify({filter:state.topologyFilter,search:state.topologySearch,roots:roots.map(([key,entry])=>[key,entry.name,entry.host,entry.node?.role||'']),leaves:leaves.map(([key,entry])=>[key,entry.name,entry.host,entry.node?.role||'']),routes:links.map(link=>[link.rootKey,link.leafKey,link.tunnel.id||'',link.tunnel.name||'',link.tunnel.transport||link.tunnel.method||''])});
  if(topology.dataset.topologySignature===structureSignature&&$$('.route-group',topology).length===links.length){syncLiveTopologyRoutes(topology,links,nodeIndex);return;}

  const laneCount=Math.max(roots.length,leaves.length,1),canvasHeight=Math.max(414,72+laneCount*92);topology.style.setProperty('--topology-canvas-height',`${canvasHeight}px`);
  const yAt=(index,total)=>total===1?canvasHeight/2:58+(index*((canvasHeight-116)/(total-1)));
  const rootY=Object.fromEntries(roots.map(([key],index)=>[key,yAt(index,roots.length)])),leafY=Object.fromEntries(leaves.map(([key],index)=>[key,yAt(index,leaves.length)]));
  const pairTotals=new Map();links.forEach(link=>{const key=`${link.rootKey}|${link.leafKey}`;pairTotals.set(key,(pairTotals.get(key)||0)+1);});const pairIndexes=new Map();
  const defs=`<defs><filter id="cyber-glow"><feGaussianBlur stdDeviation="2.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter><filter id="packet-glow"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter><marker id="route-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10Z" fill="currentColor"/></marker></defs>`;
  const routes=links.map((link,index)=>{
    const y1=rootY[link.rootKey],y2=leafY[link.leafKey],pairKey=`${link.rootKey}|${link.leafKey}`,ordinal=pairIndexes.get(pairKey)||0,total=pairTotals.get(pairKey)||1;pairIndexes.set(pairKey,ordinal+1);
    const offset=(ordinal-(total-1)/2)*24,visual=topologyRouteVisual(link),path=`M165 ${y1} C330 ${y1+offset},570 ${y2+offset},735 ${y2}`;
    const packetCount=link.tone==='online'?(link.rate>10000000?4:link.rate>1000000?3:2):link.tone==='degraded'?1:0;
    const packets=Array.from({length:packetCount},(_,packetIndex)=>{const begin=-(visual.duration/Math.max(packetCount,1))*packetIndex;return `<circle class="route-packet-halo" r="7"><animateMotion begin="${begin.toFixed(2)}s" dur="${visual.duration.toFixed(2)}s" repeatCount="indefinite"><mpath href="#route-${index}"/></animateMotion></circle><circle class="route-packet-core" r="3.2"><animateMotion begin="${begin.toFixed(2)}s" dur="${visual.duration.toFixed(2)}s" repeatCount="indefinite"><mpath href="#route-${index}"/></animateMotion></circle>`;}).join('');
    return `<g class="route-group ${link.tone}" data-route="${index}" data-matrix-flow="${visual.active?'active':'idle'}" style="--matrix-energy:${visual.energy}" tabindex="0"><path class="route-hit" d="${path}"/><path id="route-${index}" class="cyber-route-backbone" style="--route-width:${visual.width.toFixed(2)}" d="${path}" marker-end="url(#route-arrow)"/><path class="cyber-route-flow" pathLength="100" style="--route-width:${visual.width.toFixed(2)};--flow-duration:${visual.duration.toFixed(2)}s" d="${path}"/>${packets}<text class="route-label" x="450" y="${((y1+y2)/2+offset-7).toFixed(1)}" text-anchor="middle">${esc(link.tunnel.name)} · ${esc(String(link.tunnel.transport||link.tunnel.method).toUpperCase())}</text></g>`;
  }).join('');
  const nodeCard=(entry,y,side)=>{const node=entry.node,agent=node?node.status==='online':null,ssh=node?Boolean(node.ssh_configured):null,badge=side==='root'?(node?.role==='hub'?'HB':'IR'):'EX';return `<button class="cyber-node ${side} ${node?'':'unresolved'}" style="top:${y.toFixed(1)}px" data-node-id="${node?.id||''}" data-endpoint-name="${esc(entry.name)}"><i>${badge}</i><span><strong>${esc(entry.name)}</strong><small>${esc(entry.host)}</small><em><b class="${agent===true?'ready':agent===false?'missing':'neutral'}">AG ${agent===true?'ON':agent===false?'OFF':'N/A'}</b><b class="${ssh===true?'ready':ssh===false?'missing':'neutral'}">SSH ${ssh===true?'READY':ssh===false?'NO':'N/A'}</b></em></span></button>`;};
  const nodes=[...roots.map(([key,entry])=>nodeCard(entry,rootY[key],'root')),...leaves.map(([key,entry])=>nodeCard(entry,leafY[key],'leaf'))].join('');
  topology.innerHTML=`<div class="grid-floor" aria-hidden="true"></div><span class="region-label iran">IRAN / HUB</span><span class="region-label global">GLOBAL EXITS</span><svg class="cyber-links" viewBox="0 0 900 ${canvasHeight}" preserveAspectRatio="none">${defs}${routes}</svg>${nodes}<div class="topology-tooltip" id="topology-tooltip"></div><div class="cyber-scan"></div>`;
  topology.dataset.topologySignature=structureSignature;topology._liveLinks=links;
  const tooltip=$('#topology-tooltip');
  $$('.route-group',topology).forEach(route=>{
    const currentLink=()=>topology._liveLinks?.[Number(route.dataset.route)];
    const show=event=>{const link=currentLink();if(!link)return;const t=link.tunnel,rate=link.rows.every(item=>item.rx_bps==null&&item.tx_bps==null)?'NO TRAFFIC DATA':bytesPerSecond(link.rate),ports=[...new Set(link.rows.flatMap(item=>[...(Array.isArray(item.user_ports)?item.user_ports:[]),item.listen_port,item.target_port]).filter(Boolean))].join(', ')||'—';tooltip.innerHTML=`<strong>${esc(t.name)} <i class="${link.tone}">${esc(link.status)}</i></strong><span>${esc(link.left.host)} → ${esc(link.right.host)}</span><small>${esc(t.method||'DARK')} · ${esc(String(t.transport||'unknown').toUpperCase())} · ${esc(String(t.profile||'unknown').toUpperCase())}</small><small>SERVICE ${esc(link.rows.map(item=>item.service).filter(Boolean).join(' ↔ ')||'—')} · UPTIME ${elapsedDuration(link.uptime)}</small><small>PORTS ${esc(ports)} · SESSIONS ${link.sessions}</small><small>TRAFFIC ${esc(rate)} · LATENCY ${link.latency?`${link.latency.toFixed(1)}ms`:'—'} · LOSS ${link.loss.toFixed(1)}%</small><small>HEALTH SCORE ${link.health}% · CLICK TO OPEN OPERATIONS</small>`;const rect=topology.getBoundingClientRect();tooltip.style.left=`${Math.min(event.clientX-rect.left+14,rect.width-330)}px`;tooltip.style.top=`${Math.max(event.clientY-rect.top-120,8)}px`;tooltip.classList.add('open');};
    route.addEventListener('mousemove',show);route.addEventListener('mouseenter',show);route.addEventListener('mouseleave',()=>tooltip.classList.remove('open'));route.addEventListener('focus',()=>{const rect=topology.getBoundingClientRect();show({clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2});});route.addEventListener('blur',()=>tooltip.classList.remove('open'));route.addEventListener('click',()=>{const link=currentLink();if(link)openTunnelManager(link.tunnel);});route.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();const link=currentLink();if(link)openTunnelManager(link.tunnel);}});
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
  const footer=$$('.topology-footer b');if(footer.length>=4){footer[0].textContent=state.tunnels.filter(t=>t.status==='healthy').length;footer[1].textContent=state.tunnels.filter(t=>t.status==='degraded').length;footer[2].textContent=state.tunnels.filter(t=>t.status==='stale').length;footer[3].textContent=state.tunnels.filter(t=>['down','offline'].includes(t.status)).length;}
}

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
  socket.onmessage=event=>{if(liveSocket!==socket)return;let message={};try{message=JSON.parse(event.data)}catch{}if(message.type==='telemetry'){clearTimeout(connectLive.refreshTimer);connectLive.refreshTimer=setTimeout(refreshLive,350);const now=Date.now();if(now-(connectLive.lastFullRefresh||0)>=30000){connectLive.lastFullRefresh=now;clearTimeout(connectLive.fullRefreshTimer);connectLive.fullRefreshTimer=setTimeout(refresh,1200);}}};
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
$$('.terminal-replay-close').forEach(button=>button.addEventListener('click',()=>{stopTerminalReplay();closeModal($('#terminal-replay-modal'));}));
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
  if (handleNodeActions(event)) return;
  if (handlePluginActions(event)) return;
  if (handleTunnelActions(event)) return;
  handleOpsActions(event);
});

$('#plugin-mode').addEventListener('change',updatePluginMode);
$('#plugin-form').elements.transport.addEventListener('change',updateTLSSelector);
$('#plugin-kharej-node').addEventListener('change',event=>{if(state.selectedPlugin==='dark-realm'){const node=state.nodes.find(item=>item.id===Number(event.target.value));if(node)$('#plugin-endpoint').value=node.observed_ip||node.host;updateTLSSelector();}});
$('#plugin-certificate').addEventListener('change',event=>{const cert=state.certificates.find(item=>item.id===Number(event.target.value));if(cert){const form=$('#plugin-form');$('#plugin-endpoint').value=cert.domain;if(state.selectedPlugin==='dark-realm'){form.elements.tls_domain.value=cert.domain;form.elements.sni.value=cert.domain;form.elements.ws_host.value=cert.domain;}}});
function openCertificateModal(){const select=$('#certificate-node');select.innerHTML=state.nodes.filter(n=>n.status==='online').map(n=>`<option value="${Number(n.id)}">${esc(n.role.toUpperCase())} · ${esc(n.name)} · ${esc(n.host)}</option>`).join('');const chosen=realmCertificateNodeId()||Number($('#plugin-iran-node').value);if(chosen)select.value=chosen;openModal('#certificate-modal');}
$('#add-certificate').addEventListener('click',openCertificateModal);
$('#plugin-get-certificate').addEventListener('click',openCertificateModal);
$('#certificate-form').addEventListener('submit',async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));values.node_id=Number(values.node_id);try{const result=await api('/api/certificates',{method:'POST',body:JSON.stringify(values)});closeModal($('#certificate-modal'));event.target.reset();showToast('CERTIFICATE QUEUED',`DNS matched. Job #${result.job_id} is requesting Let's Encrypt.`);await refresh();}catch(error){showToast('CERTIFICATE FAILED',error.message,true);}});
$('#plugin-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.target,values=Object.fromEntries(new FormData(form));
  const pairMode=values.deployment_mode==='pair_code',realm=state.selectedPlugin==='dark-realm';delete values.deployment_mode;
  values.iran_node_id=Number(values.iran_node_id);values.tunnel_port=Number(values.tunnel_port);
  if(values.certificate_id)values.certificate_id=Number(values.certificate_id);else delete values.certificate_id;
  values.port_mappings=realm?String(values.port_mappings||'').split(/[ ,]+/).filter(Boolean):[];
  values.user_ports=(realm&&values.port_mappings.length?values.port_mappings.map(item=>Number(item.split('>')[0])):String(values.user_ports).split(/[ ,]+/).filter(Boolean).map(Number));
  values.tls_insecure=form.elements.tls_insecure?.checked||false;
  if(!realm){for(const key of ['target_host','port_mappings','tls_domain','tls_insecure','sni','alpn','ws_host','ws_path','ws_mask'])delete values[key];}
  if(pairMode){delete values.kharej_node_id;if(realm&&!values.kharej_endpoint)return showToast('GATEWAY REQUIRED','Enter the Kharej Realm Gateway IP or domain.',true);}else{values.kharej_node_id=Number(values.kharej_node_id);delete values.remote_label;if(!values.kharej_node_id)return showToast('KHAREJ AGENT REQUIRED','Select an online Global Exit Agent or use Pair Code mode.',true);if(values.iran_node_id===values.kharej_node_id)return showToast('INVALID NODE PAIR','Iran and Kharej must be different servers.',true);}
  try{
    const pluginId=state.selectedPlugin||'dark-backhaul',plugin=state.plugins.find(item=>item.id===pluginId);
    const deployment=await api(pairMode?`/api/plugins/${pluginId}/pair-code`:`/api/plugins/${pluginId}/deploy`,{method:'POST',body:JSON.stringify(values)});
    closeModal($('#plugin-modal'));
    if(pairMode){$('#output-title').textContent='PAIR CODE READY';$('#job-output').textContent=`IRAN installation queued as job #${deployment.iran_job_id}.\nWait for IRAN to become READY, then on the foreign server:\n\n1. Run the ${plugin?.name||pluginId} script\n2. Select KHAREJ\n3. Choose Connect with DARK NOC Pair Code\n4. Paste this secret code:\n\n${deployment.pair_code}`;openModal('#output-modal');showToast('PAIR CODE CREATED','IRAN is being configured; copy the code for KHAREJ.');}else showToast('DUAL DEPLOYMENT QUEUED',`Deployment #${deployment.deployment_id} is running on both Agents.`);
    event.target.reset();updatePluginMode();await refresh();
  }catch(error){showToast('PLUGIN DEPLOYMENT FAILED',error.message,true);}
});

$('#ssh-connect').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose a Hub, Iran or Kharej server first.',true);connectSSH(nodeId);});
$('#terminal-new-tab').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose the server for the new pane.',true);connectSSH(nodeId,createTerminalPane());});
$('#terminal-layout-mode').addEventListener('change',event=>{const layout=$('.terminal-layout');layout.classList.remove('grid','columns','focus');layout.classList.add(event.target.value);setTimeout(()=>state.terminals.forEach(entry=>entry.fitAddon.fit()),100);});
$('#terminal-copy').addEventListener('click',async()=>{const entry=activeTerminal();if(!entry)return showToast('NO TERMINAL','Select a terminal pane first.',true);if(!requireClipboard('writeText','COPY'))return;const text=entry.terminal.getSelection()||terminalText(entry.terminal);try{await navigator.clipboard.writeText(text);showToast('COPIED',entry.terminal.hasSelection()?'Selection copied.':'Terminal buffer copied.');}catch{showToast('COPY BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-paste').addEventListener('click',async()=>{const socket=connectedSocket();if(!socket)return showToast('SSH NOT CONNECTED','Wait for the active terminal to finish connecting.',true);if(!requireClipboard('readText','PASTE'))return;try{const text=await navigator.clipboard.readText();socket.send(JSON.stringify({type:'input',data:text}));activeTerminal()?.terminal.focus();}catch{showToast('PASTE BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-fullscreen').addEventListener('click',async()=>{const card=$('.terminal-card.active-terminal')||$('.terminal-card');try{if(!document.fullscreenElement)await card.requestFullscreen();else await document.exitFullscreen();setTimeout(()=>activeTerminal()?.fitAddon.fit(),100);}catch(error){showToast('FULLSCREEN UNAVAILABLE',error.message||'The browser blocked fullscreen mode.',true);}});
$('#terminal-search-input').addEventListener('input',event=>activeTerminal()?.searchAddon.findNext(event.target.value,{incremental:true,decorations:{matchBackground:'#5d401d',activeMatchBackground:'#b36cff'}}));
$('#terminal-search-next').addEventListener('click',()=>activeTerminal()?.searchAddon.findNext($('#terminal-search-input').value));
$('#terminal-search-prev').addEventListener('click',()=>activeTerminal()?.searchAddon.findPrevious($('#terminal-search-input').value));
$('#terminal-download-log').addEventListener('click',()=>{const entry=activeTerminal();if(!entry)return;const blob=new Blob([terminalText(entry.terminal)],{type:'text/plain'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-ssh-${Date.now()}.log`;a.click();URL.revokeObjectURL(a.href);});
$('#terminal-replay').addEventListener('click',startTerminalReplay);$('#terminal-replay-stop').addEventListener('click',stopTerminalReplay);
$('#terminal-snippet-select').addEventListener('change',event=>{const item=savedSnippets()[Number(event.target.value)];if(item){$('#terminal-snippet-name').value=item.name;$('#terminal-snippet-command').value=item.command;}});
$('#terminal-snippet-save').addEventListener('click',()=>{const name=$('#terminal-snippet-name').value.trim(),command=$('#terminal-snippet-command').value.trim();if(!name||!command)return showToast('SNIPPET INCOMPLETE','Enter both a name and a command.',true);if(name.length>48||command.length>2000||/[\0\r\n]/.test(command))return showToast('INVALID SNIPPET','Use a single command up to 2000 characters.',true);const snippets=savedSnippets(),existing=snippets.find(item=>item.name.toLowerCase()===name.toLowerCase());if(existing)existing.command=command;else snippets.push({name,command});localStorage.setItem('darkNocSshSnippets',JSON.stringify(snippets.slice(-100)));renderSnippets();showToast('SNIPPET SAVED',`${name} is available in this browser.`);});
$('#terminal-snippet-run').addEventListener('click',()=>{const socket=connectedSocket(),command=$('#terminal-snippet-command').value.trim();if(!socket)return showToast('SSH NOT CONNECTED','Connect the active pane first.',true);if(!command)return showToast('EMPTY SNIPPET','Select or enter a command first.',true);socket.send(JSON.stringify({type:'input',data:`${command}\n`}));activeTerminal()?.terminal.focus();});
$('#disconnect').addEventListener('click',()=>{[...state.sockets.keys()].forEach(closeSocket);$$('.ssh-online').forEach(status=>status.textContent='● CLOSED');showToast('SSH DISCONNECTED','All interactive sessions were closed.');});
$('#run-all-tests').addEventListener('click',async()=>{const online=state.nodes.filter(node=>node.status==='online');if(!online.length)return showToast('NO ONLINE AGENTS','Tunnel tests were not queued.',true);await Promise.all(online.map(node=>createJob(node.id,'tunnel_test')));showToast('TUNNEL TESTS QUEUED',`${online.length} online Agent${online.length===1?'':'s'} will test every configured tunnel.`);});
$('#filter-nodes').addEventListener('click',event=>{state.nodeFilter=state.nodeFilter==='all'?'attention':'all';event.target.textContent=`FILTER: ${state.nodeFilter==='all'?'ALL':'ATTENTION'}`;renderNodes();});
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
ensureTerminal('terminal-iran');ensureTerminal('terminal-germany');$('.terminal-card').classList.add('active-terminal');renderSnippets();
updateClock(); setInterval(updateClock,1000); setInterval(refresh,15000); boot();
