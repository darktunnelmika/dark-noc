// DARK NOC Incident Command runtime. Classic-script globals are intentional.
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

$('#incident-note-form').addEventListener('submit',async event=>{event.preventDefault();const incidentId=Number(event.target.dataset.incidentId);if(!incidentId)return;const message=event.target.elements.message.value.trim();try{await api(`/api/incidents/${incidentId}/notes`,{method:'POST',body:JSON.stringify({message,event_type:'note'})});event.target.elements.message.value='';showToast('TIMELINE UPDATED','Operator note was recorded.');await loadIncidentDetail(incidentId);await refresh();}catch(error){showToast('NOTE FAILED',error.message,true);}});

$('#export-incidents').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(state.incidents,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`dark-noc-incidents-${Date.now()}.json`;a.click();URL.revokeObjectURL(a.href);});
