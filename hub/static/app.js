const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const viewTitles = { overview: 'Network Command', servers: 'Server Fleet', tunnels: 'Tunnel Matrix', certificates: 'TLS Vault', incidents: 'Incident Command', terminal: 'SSH Command' };
let state = { nodes: [], tunnels: [], incidents: [], plugins: [], deployments: [], certificates: [], traffic: [], sockets: new Map(), terminals: new Map(), selectedNode: null, selectedTunnel: null, selectedPlugin: 'dark-backhaul', nodeFilter: 'all', topologyFilter: 'all', topologySearch: '', editingNode: null };
let refreshInFlight = false;
let liveSocket = null;

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (response.status === 401) {
    $('#login-gate').classList.remove('hidden');
    throw new Error('Authentication required');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail=Array.isArray(payload.detail)?payload.detail.map(item=>item.msg||JSON.stringify(item)).join(' · '):payload.detail;
    throw new Error(typeof detail==='string'?detail:`Request failed (${response.status})`);
  }
  return payload;
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
    const m = node.metric || {};
    const provisioning=node.provision_status==='provisioning', provisionFailed=node.provision_status==='failed';
    const watch = node.status !== 'online' || provisionFailed || Number(m.cpu) > 80 || Number(m.ram) > 85;
    const throughput = Number(m.rx_bps || 0) + Number(m.tx_bps || 0);
    const tunnels = state.tunnels.filter(t => t.node_id === node.id);
    const healthy = tunnels.filter(t => t.status === 'healthy').length;
    const services = (node.services || []).map(service=>`<button class="service-chip ${service.status==='active'?'':'service-down'}" data-service-node="${node.id}" data-service-name="${esc(service.name)}" title="Restart ${esc(service.name)}"><i></i>${esc(service.name)} · ${esc(service.status)}</button>`).join('');
    const code = node.region.toUpperCase().includes('IRAN') ? 'IR' : node.name.slice(0,2).toUpperCase();
    return `<article class="panel server-card">
      <div class="server-head"><div class="server-id"><span class="flag">${esc(code)}</span><div><strong>${esc(node.name)}</strong><small>${esc(node.region)} · IP ${esc(node.observed_ip||node.host)}${node.observed_ip&&node.observed_ip!==node.host?` · SSH ${esc(node.host)}`:''} · AGENT ${esc(node.agent_version||'PENDING')} · AUTO-HEAL ${node.autoheal_enabled?'ON':'OFF'}</small></div></div><span class="status-label ${watch ? 'watch' : ''}">● ${provisioning?'INSTALLING':provisionFailed?'INSTALL FAILED':esc(node.status.toUpperCase())}</span></div>
      <div class="resource-grid">${resource('CPU',m.cpu,Number(m.cpu)>80)}${resource('RAM',m.ram,Number(m.ram)>85)}${resource('DISK',m.disk,Number(m.disk)>85)}${resource('LOAD',Math.min(Number(m.load1||0)*10,100),false,Number(m.load1||0).toFixed(2))}</div>
      <div class="service-list">${services || '<span>No managed services reported</span>'}</div>
      <div class="server-bottom"><div><span>TUNNELS</span><b>${healthy} / ${tunnels.length}</b></div><div><span>LAST SEEN</span><b>${relativeTime(node.last_seen)}</b></div><div class="server-actions">${provisionFailed?`<button class="square-action node-provision-retry needs-setup" data-node-id="${node.id}">↻ RETRY INSTALL</button>`:''}${node.provision_status?`<button class="square-action node-provision-log" data-node-id="${node.id}">≡ INSTALL LOG</button>`:''}<button class="square-action server-ssh ${node.ssh_configured?'':'needs-setup'}" title="${node.ssh_configured?'Open SSH terminal':'Configure SSH credentials first'}" data-node-id="${node.id}">${node.ssh_configured?'›_ SSH':'⚙ SETUP SSH'}</button><button class="square-action node-diagnostics" title="Run diagnostics" data-node-id="${node.id}">✓ CHECKS</button><button class="square-action node-logs" title="View Agent logs" data-node-id="${node.id}">≡ LOGS</button><button class="square-action node-autoheal" title="Toggle Auto-Heal" data-node-id="${node.id}">↻ AUTO-HEAL</button><button class="square-action node-edit" title="Edit server and SSH settings" data-node-id="${node.id}">✎ EDIT</button><button class="square-action node-pin" title="Reset pinned SSH host key" data-node-id="${node.id}">◇ HOST KEY</button><button class="square-action node-delete danger" title="Delete server" data-node-id="${node.id}" data-node-name="${esc(node.name)}">× DELETE</button></div></div>
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
  fill('#upload-node','SELECT SERVER');fill('#relay-source-node','SELECT SOURCE');fill('#relay-destination-node','SELECT DESTINATION');
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

function renderIncidents() {
  const open = state.incidents.filter(item => ['open','acknowledged'].includes(item.status));
  $$('.nav-item b.danger').forEach(item => item.textContent = open.length);
  const active = open[0];
  const detail = $('.incident-detail');
  if (!active) {
    if(detail){$('.severity-pill',detail).textContent='NO ACTIVE INCIDENT';$('.severity-pill',detail).style.color='var(--green)';$('h2',detail).textContent='All monitored paths are operational';$('p',detail).textContent='DARK NOC is watching every enrolled tunnel.';$('.downtime strong',detail).textContent='00:00:00';$('.diagnostic-grid',detail).innerHTML='<div class="diag ok"><span>✓</span><strong>LIVE STATE</strong><small>No failed checks</small></div>';$('.remediation',detail).innerHTML='<div><span class="live-dot"></span><p><strong>Monitoring active</strong><small>Waiting for the next Agent heartbeat</small></p></div>';}
    const timeline=$('.timeline-panel .timeline');if(timeline)timeline.innerHTML='<div class="active"><time>NOW</time><span></span><p><strong>No active recovery timeline</strong><small>Resolved incidents remain available in exported history.</small></p></div>';
    return;
  }
  if (detail) {
    let telemetry={};try{telemetry=JSON.parse(active.detail||'{}')}catch{}
    $('h2', detail).textContent = active.title;
    $('p', detail).textContent = `${active.node_name || 'Unknown node'} · ${active.tunnel_name || 'Node incident'}`;
    $('.severity-pill',detail).textContent=`${String(active.severity||'warning').toUpperCase()} · ACTIVE`;$('.severity-pill',detail).style.color='';
    $('.downtime strong',detail).textContent=duration(active.opened_at);
    const checks=telemetry.checks||{};
    $('.diagnostic-grid',detail).innerHTML=`<div class="diag ${checks.process?'ok':'fail'}"><span>01</span><strong>PROCESS</strong><small>${checks.process?'Service active':'Service failed'}</small></div><div class="diag ${checks.path?'ok':'fail'}"><span>02</span><strong>TUNNEL PATH</strong><small>${telemetry.latency_ms==null?'No response':`${Number(telemetry.latency_ms).toFixed(1)} ms`}</small></div><div class="diag ${Number(telemetry.packet_loss||0)>0?'fail':'ok'}"><span>03</span><strong>PACKET LOSS</strong><small>${Number(telemetry.packet_loss||0).toFixed(1)}%</small></div><div class="diag waiting"><span>04</span><strong>AGENT</strong><small>Last report ${relativeTime(telemetry.last_check||active.opened_at)}</small></div>`;
    $('.remediation',detail).innerHTML=`<div><span class="spinner"></span><p><strong>${active.status==='acknowledged'?'Incident acknowledged':'Operator attention required'}</strong><small>Incident opened ${relativeTime(active.opened_at)}</small></p></div><span class="incident-command-buttons">${active.status==='open'?`<button class="ghost-btn incident-ack" data-incident-id="${Number(active.id)}">ACKNOWLEDGE</button>`:''}<button class="ghost-btn incident-resolve" data-incident-id="${Number(active.id)}">RESOLVE</button><button class="danger-btn" id="take-control" data-node-id="${Number(active.node_id)}">OPEN SSH</button></span>`;
  }
  const timeline=$('.timeline-panel .timeline');if(timeline)timeline.innerHTML=`<div class="active"><time>${new Date(active.opened_at*1000).toLocaleTimeString('en-GB')}</time><span></span><p><strong>${esc(active.title)}</strong><small>Detected by ${esc(active.node_name||'Agent')}</small></p></div>`;
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
    const [summary,nodes,tunnels,incidents,plugins,deployments,certificates,traffic] = await Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels'),api('/api/incidents'),api('/api/plugins'),api('/api/plugin-deployments'),api('/api/certificates'),api('/api/dashboard/traffic?minutes=60')]);
    state = { ...state, nodes, tunnels, incidents, plugins, deployments, certificates, traffic };
    renderNodes(); populateSSHServers(); renderTunnels(); renderPlugins(); renderCertificates(); renderIncidents(); renderLiveTopology(); renderLiveIncident(); renderNodeHealth(); renderTrafficChart(); updateOverview(summary);
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
  terminal.onData(data=>{const socket=state.sockets.get(slot);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'input',data}));});
  terminal.onResize(size=>{const socket=state.sockets.get(slot);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'resize',cols:size.cols,rows:size.rows}));});
  terminal.attachCustomKeyEventHandler(event=>{
    if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='c'&&terminal.hasSelection()){navigator.clipboard.writeText(terminal.getSelection()).catch(()=>{});return false;}
    if((event.ctrlKey||event.metaKey)&&event.shiftKey&&event.key.toLowerCase()==='v'){navigator.clipboard.readText().then(text=>{const socket=state.sockets.get(slot);if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'input',data:text}));}).catch(()=>{});return false;}
    return true;
  });
  const entry={terminal,fitAddon,searchAddon};state.terminals.set(slot,entry);
  const observer=new ResizeObserver(()=>{clearTimeout(entry.fitTimer);entry.fitTimer=setTimeout(()=>{try{fitAddon.fit();}catch{}},60);});observer.observe(container.closest('.terminal-card'));
  return entry;
}

function closeSocket(slot) {
  const socket = state.sockets.get(slot);
  if (socket) { clearInterval(socket.keepaliveTimer); socket.close(); }
  state.sockets.delete(slot);
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
    slot=!first||first.readyState>=WebSocket.CLOSING?'terminal-iran':(!second||second.readyState>=WebSocket.CLOSING?'terminal-germany':'terminal-iran');
  }
  switchView('terminal');
  closeSocket(slot);
  const screen = $(`#${slot}`), term=ensureTerminal(slot);
  const card=screen.closest('.terminal-card'), code=node.role==='edge'?'IR':node.role==='exit'?'EX':'HB';
  $('.terminal-server .flag',card).textContent=code;
  $('.terminal-server strong',card).textContent=node.name;
  $('.terminal-server small',card).textContent=`${node.ssh_user}@${node.host}:${node.ssh_port}`;
  $$('.terminal-card').forEach(item=>item.classList.remove('active-terminal'));
  card.classList.add('active-terminal');
  $('.ssh-online',card).textContent='● CONNECTING';
  term.terminal.reset();term.terminal.writeln(`\x1b[38;5;45mConnecting to ${node.name} (${node.host}:${Number(node.ssh_port)})…\x1b[0m\r\n`);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/ssh/${node.id}`);
  state.sockets.set(slot, socket);
  state.selectedNode = node.id;
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === 'output') term.terminal.write(message.data);
    if (message.type === 'connected') { $('.ssh-online',card).textContent='● CONNECTED';term.terminal.writeln(`\x1b[38;5;48m✓ SECURE SSH CONNECTED · ${message.node}\x1b[0m`);term.terminal.writeln(`\x1b[38;5;244mHost key: ${message.fingerprint||'unavailable'}\x1b[0m\r\n`);term.fitAddon.fit();term.terminal.focus(); }
    if (message.type === 'error') { $('.ssh-online',card).textContent='● ERROR';term.terminal.writeln(`\r\n\x1b[38;5;203m${message.message}\x1b[0m`); }
  };
  socket.onopen = () => {term.fitAddon.fit();socket.send(JSON.stringify({type:'resize',cols:term.terminal.cols,rows:term.terminal.rows}));socket.keepaliveTimer=setInterval(()=>{if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type:'ping'}));},25000); };
  socket.onerror = () => { $('.ssh-online',card).textContent='● ERROR';term.terminal.writeln('\r\n\x1b[38;5;203mSSH WebSocket connection failed.\x1b[0m'); };
  socket.onclose = () => { clearInterval(socket.keepaliveTimer); $('.ssh-online',card).textContent='● CLOSED';term.terminal.writeln('\r\n\x1b[38;5;244mSession closed.\x1b[0m'); };
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  $('#login-error').textContent = '';
  try {
    const session=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:form.get('username'),password:form.get('password')})});
    $('#login-gate').classList.add('hidden');
    $('.operator strong').textContent=session.username;
    connectLive();
    event.target.reset();
    await refresh();
  } catch (error) { $('#login-error').textContent = error.message; }
});

async function boot() {
  try { const me=await api('/api/auth/me'); $('.operator strong').textContent=me.username; $('#login-gate').classList.add('hidden'); await refresh(); connectLive(); }
  catch { $('#login-gate').classList.remove('hidden'); }
}

function connectLive(){
  if(liveSocket&&liveSocket.readyState<WebSocket.CLOSING)return;
  const protocol=location.protocol==='https:'?'wss:':'ws:';
  liveSocket=new WebSocket(`${protocol}//${location.host}/ws/live`);
  liveSocket.onopen=()=>{liveSocket.keepaliveTimer=setInterval(()=>{if(liveSocket?.readyState===WebSocket.OPEN)liveSocket.send('ping');},25000);};
  liveSocket.onmessage=event=>{let message={};try{message=JSON.parse(event.data)}catch{}if(message.type==='telemetry'){clearTimeout(connectLive.refreshTimer);connectLive.refreshTimer=setTimeout(refresh,250);}};
  liveSocket.onclose=()=>{clearInterval(liveSocket?.keepaliveTimer);liveSocket=null;if($('#login-gate').classList.contains('hidden'))setTimeout(connectLive,3000);};
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
$('#topology-fullscreen').addEventListener('click',async()=>{const panel=$('.topology-panel');if(!document.fullscreenElement)await panel.requestFullscreen();else await document.exitFullscreen();});

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
  const autohealNode = event.target.closest('.node-autoheal');
  const provisionRetry = event.target.closest('.node-provision-retry');
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
  if(incidentAck)api(`/api/incidents/${Number(incidentAck.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'acknowledge'})}).then(refresh).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));
  if(incidentResolve&&confirm('Resolve this incident manually?'))api(`/api/incidents/${Number(incidentResolve.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'resolve'})}).then(refresh).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));
  if(autohealNode){const node=state.nodes.find(item=>item.id===Number(autohealNode.dataset.nodeId));if(node){const enabled=!Boolean(node.autoheal_enabled);if(confirm(`${enabled?'Enable':'Disable'} Auto-Heal on ${node.name}?`))createJob(node.id,'configure_autoheal',null,{enabled,cooldown_seconds:Number(node.autoheal_cooldown||300),max_restarts_per_hour:Number(node.autoheal_max_restarts||3)},true).then(()=>setTimeout(refresh,3000)).catch(error=>showToast('AUTO-HEAL UPDATE FAILED',error.message,true));}}
  if(provisionRetry)api(`/api/nodes/${Number(provisionRetry.dataset.nodeId)}/provision`,{method:'POST'}).then(()=>{showToast('INSTALLATION RETRIED','The Hub is reconnecting and installing the Agent.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));
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
  event.preventDefault();const form=event.target,file=form.elements.file.files[0],nodeId=Number(form.elements.node_id.value),button=$('button[type="submit"]',form);
  if(!file||!nodeId)return showToast('UPLOAD NOT READY','Select a destination server and local file.',true);
  const payload=new FormData();payload.append('file',file,file.name);payload.append('remote_path',form.elements.remote_path.value);payload.append('overwrite',form.elements.overwrite.checked?'true':'false');
  const xhr=new XMLHttpRequest();xhr.open('POST',`/api/ssh/upload/${nodeId}`);xhr.withCredentials=true;button.disabled=true;
  transferStatus('#upload-progress','running',`UPLOADING · ${file.name} · 0%`,0);
  xhr.upload.onprogress=progress=>{if(progress.lengthComputable){const percent=Math.round(progress.loaded/progress.total*100);transferStatus('#upload-progress','running',`UPLOADING · ${fileSize(progress.loaded)} / ${fileSize(progress.total)} · ${percent}%`,percent);}};
  xhr.onload=()=>{button.disabled=false;let result={};try{result=JSON.parse(xhr.responseText||'{}');}catch{}if(xhr.status>=200&&xhr.status<300){transferStatus('#upload-progress','done',`COMPLETE · ${result.path} · ${fileSize(result.bytes)}`,100);showToast('FILE UPLOADED',`${file.name} → ${result.node}:${result.path}`);form.elements.file.value='';}else{const detail=Array.isArray(result.detail)?result.detail.map(item=>item.msg).join(' · '):result.detail;transferStatus('#upload-progress','error',typeof detail==='string'?detail:`Upload failed (${xhr.status})`,0);showToast('UPLOAD FAILED',typeof detail==='string'?detail:`Request failed (${xhr.status})`,true);}};
  xhr.onerror=()=>{button.disabled=false;transferStatus('#upload-progress','error','NETWORK ERROR · Upload interrupted',0);showToast('UPLOAD FAILED','Network connection was interrupted.',true);};xhr.send(payload);
});

$('#ssh-relay-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target,values=Object.fromEntries(new FormData(form)),button=$('button[type="submit"]',form);values.source_node_id=Number(values.source_node_id);values.destination_node_id=Number(values.destination_node_id);values.overwrite=form.elements.overwrite.checked;
  if(values.source_node_id===values.destination_node_id&&values.source_path===values.destination_path)return showToast('INVALID TRANSFER','Source and destination are the same file.',true);
  button.disabled=true;transferStatus('#relay-progress','running','SECURE RELAY ACTIVE · Streaming chunks through Hub');
  try{const result=await api('/api/ssh/relay',{method:'POST',body:JSON.stringify(values)});transferStatus('#relay-progress','done',`COMPLETE · ${fileSize(result.bytes)} · ${result.source_node} → ${result.destination_node}`,100);showToast('TRANSFER COMPLETE',`${result.source_node}:${result.source_path} → ${result.destination_node}:${result.destination_path}`);}
  catch(error){transferStatus('#relay-progress','error',error.message,0);showToast('TRANSFER FAILED',error.message,true);}finally{button.disabled=false;}
});

$('#clear-terminal').addEventListener('click',()=>{const entry=activeTerminal();if(entry){entry.terminal.clear();entry.terminal.focus();}});
$('#ssh-connect').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose a Hub, Iran or Kharej server first.',true);connectSSH(nodeId);});
$('#terminal-new-tab').addEventListener('click',()=>{const nodeId=$('#ssh-node-select').value;if(!nodeId)return showToast('SELECT A SERVER','Choose the server for the new tab.',true);const alternate=activeTerminalSlot()==='terminal-iran'?'terminal-germany':'terminal-iran';connectSSH(nodeId,alternate);});
$('#terminal-copy').addEventListener('click',async()=>{const entry=activeTerminal();if(!entry)return;const text=entry.terminal.getSelection()||terminalText(entry.terminal);try{await navigator.clipboard.writeText(text);showToast('COPIED',entry.terminal.hasSelection()?'Selection copied.':'Terminal buffer copied.');}catch{showToast('COPY BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-paste').addEventListener('click',async()=>{const slot=activeTerminalSlot(),socket=state.sockets.get(slot);if(!socket||socket.readyState!==WebSocket.OPEN)return showToast('SSH NOT CONNECTED','Connect the active terminal first.',true);try{const text=await navigator.clipboard.readText();socket.send(JSON.stringify({type:'input',data:text}));activeTerminal()?.terminal.focus();}catch{showToast('PASTE BLOCKED','Allow clipboard access in the browser.',true);}});
$('#terminal-fullscreen').addEventListener('click',async()=>{const card=$('.terminal-card.active-terminal')||$('.terminal-card');if(!document.fullscreenElement)await card.requestFullscreen();else await document.exitFullscreen();setTimeout(()=>activeTerminal()?.fitAddon.fit(),100);});
$('#terminal-search-input').addEventListener('input',event=>activeTerminal()?.searchAddon.findNext(event.target.value,{incremental:true,decorations:{matchBackground:'#5d401d',activeMatchBackground:'#b36cff'}}));
$('#terminal-search-next').addEventListener('click',()=>activeTerminal()?.searchAddon.findNext($('#terminal-search-input').value));
$('#terminal-search-prev').addEventListener('click',()=>activeTerminal()?.searchAddon.findPrevious($('#terminal-search-input').value));
$('#terminal-download-log').addEventListener('click',()=>{const entry=activeTerminal();if(!entry)return;const blob=new Blob([terminalText(entry.terminal)],{type:'text/plain'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-ssh-${Date.now()}.log`;a.click();URL.revokeObjectURL(a.href);});
$('#disconnect').addEventListener('click',()=>{[...state.sockets.keys()].forEach(closeSocket);showToast('SSH DISCONNECTED','All interactive sessions were closed.');});
$('#run-all-tests').addEventListener('click',async()=>{const online=state.nodes.filter(node=>node.status==='online');if(!online.length)return showToast('NO ONLINE AGENTS','Tunnel tests were not queued.',true);await Promise.all(online.map(node=>createJob(node.id,'tunnel_test')));showToast('TUNNEL TESTS QUEUED',`${online.length} online Agent${online.length===1?'':'s'} will test every configured tunnel.`);});
$('#filter-nodes').addEventListener('click',event=>{state.nodeFilter=state.nodeFilter==='all'?'attention':'all';event.target.textContent=`FILTER: ${state.nodeFilter==='all'?'ALL':'ATTENTION'}`;renderNodes();});
$('#export-incidents').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(state.incidents,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-incidents-${Date.now()}.json`;a.click();URL.revokeObjectURL(a.href);});
$('#session-log').addEventListener('click',async()=>{try{const jobs=await api('/api/jobs?limit=100');$('#output-title').textContent='Remote job history';$('#job-output').textContent=jobs.map(job=>`#${job.id} [${job.status}] ${job.node_name} · ${job.kind}\n${job.output||''}`).join('\n\n');openModal('#output-modal');}catch(error){showToast('HISTORY FAILED',error.message,true);}});
$('#copy-output').addEventListener('click',async()=>{await navigator.clipboard.writeText($('#job-output').textContent);showToast('OUTPUT COPIED','Command output copied to clipboard.');});
$$('.terminal-card').forEach(card=>card.addEventListener('mousedown',()=>{$$('.terminal-card').forEach(item=>item.classList.remove('active-terminal'));card.classList.add('active-terminal');}));
$$('[data-command]').forEach(button=>button.addEventListener('click',()=>{const socket=state.sockets.get(activeTerminalSlot());if(!socket||socket.readyState!==WebSocket.OPEN)return showToast('SSH NOT CONNECTED','Connect the active terminal first.',true);socket.send(JSON.stringify({type:'input',data:`${button.dataset.command}\n`}));activeTerminal()?.terminal.focus();}));
$('.operator').addEventListener('click',async()=>{try{const me=await api('/api/auth/me');$('#account-form [name="username"]').value=me.username;$('#account-form').reset();$('#account-form [name="username"]').value=me.username;openModal('#account-modal');}catch(error){showToast('ACCOUNT ERROR',error.message,true);}});
$('#account-form').addEventListener('submit',async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));if(values.new_password!==values.confirm_password)return showToast('PASSWORD MISMATCH','New password confirmation does not match.',true);delete values.confirm_password;if(!values.new_password)delete values.new_password;try{const result=await api('/api/auth/account',{method:'PUT',body:JSON.stringify(values)});closeModal($('#account-modal'));event.target.reset();showToast('ACCOUNT UPDATED',result.reauthenticate?'Sign in again with the new credentials.':'Username saved.');if(result.reauthenticate){$('#login-gate').classList.remove('hidden');if(liveSocket)liveSocket.close();}else $('.operator strong').textContent=result.username;}catch(error){showToast('ACCOUNT UPDATE FAILED',error.message,true);}});
$('#logout-button').addEventListener('click',async()=>{await api('/api/auth/logout',{method:'POST'});$('#login-gate').classList.remove('hidden');if(liveSocket)liveSocket.close();closeModal($('#account-modal'));});

document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openModal('#command-modal');}
  if(event.key==='Escape'){$$('.modal-backdrop.open').forEach(closeModal);}
});

function updateClock(){ $('#clock').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Tehran',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()); }
ensureTerminal('terminal-iran');ensureTerminal('terminal-germany');$('.terminal-card').classList.add('active-terminal');
updateClock(); setInterval(updateClock,1000); setInterval(refresh,15000); boot();
