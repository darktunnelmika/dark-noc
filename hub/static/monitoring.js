// DARK NOC Synthetic Monitoring runtime. Classic-script globals are intentional.
function renderMonitors(){
  const grid=$('#monitor-grid'),summary=$('#monitor-summary');if(!grid||!summary)return;
  const counts={up:0,down:0,pending:0,disabled:0};state.monitors.forEach(item=>{counts[item.enabled?(item.status||'pending'):'disabled']=(counts[item.enabled?(item.status||'pending'):'disabled']||0)+1;});
  summary.innerHTML=`<article><span>UP</span><strong>${counts.up}</strong></article><article><span>DOWN</span><strong class="red">${counts.down}</strong></article><article><span>PENDING</span><strong class="amber">${counts.pending+(counts.degraded||0)}</strong></article><article><span>DISABLED</span><strong>${counts.disabled}</strong></article>`;
  $('#monitor-count').textContent=state.monitors.length;
  if(!state.monitors.length){grid.innerHTML='<div class="empty-state"><strong>No synthetic monitor configured</strong>Add ICMP, TCP, HTTP, HTTPS, DNS, TLS or SNMP checks and choose the Agent that should run them.</div>';return;}
  grid.innerHTML=state.monitors.map(item=>{const detail=item.detail||{},tone=!item.enabled?'disabled':item.status==='up'?'up':item.status==='down'?'down':'pending';return `<article class="panel monitor-card ${tone}"><header><span>${esc(item.kind.toUpperCase())}</span><em>● ${item.enabled?esc(String(item.status).toUpperCase()):'DISABLED'}</em></header><h3>${esc(item.name)}</h3><p>${esc(item.target)}${item.port?`:${Number(item.port)}`:''}</p><div class="monitor-route"><span>RUNNER · ${esc(String(item.runner_status||'unknown').toUpperCase())}</span><b>${esc(item.node_name)} · ${esc(item.node_host)}</b></div><div class="monitor-metrics"><span>LATENCY <b>${item.latency_ms==null?'—':`${Number(item.latency_ms).toFixed(1)} ms`}</b></span><span>FAIL STREAK <b>${Number(item.failure_streak||0)}</b></span><span>LAST RUN <b>${relativeTime(item.last_run_at)}</b></span></div>${detail.days_left!=null?`<small>TLS expires in ${Number(detail.days_left)} days</small>`:''}<footer><button class="monitor-run" data-monitor-id="${Number(item.id)}">▶ RUN</button><button class="monitor-history" data-monitor-id="${Number(item.id)}">≋ HISTORY</button><button class="monitor-edit" data-monitor-id="${Number(item.id)}">✎ EDIT</button><button class="monitor-delete danger" data-monitor-id="${Number(item.id)}" data-monitor-name="${esc(item.name)}">× DELETE</button></footer></article>`;}).join('');
}

function openMonitorEditor(monitor=null){
  if(!state.nodes.some(node=>node.status==='online'))return showToast('ONLINE AGENT REQUIRED','Add or reconnect an Agent before creating a monitor.',true);
  state.editingMonitor=monitor?.id||null;const form=$('#monitor-form');form.reset();form.elements.monitor_id.value=monitor?.id||'';form.elements.interval_seconds.value=monitor?.interval_seconds||60;form.elements.timeout_seconds.value=monitor?.timeout_seconds||5;form.elements.enabled.checked=monitor?Boolean(monitor.enabled):true;
  if(monitor){for(const key of ['name','node_id','kind','target','port','expected_status','snmp_oid'])if(form.elements[key])form.elements[key].value=monitor[key]??'';$('#monitor-title').textContent=`Edit ${monitor.name}`;}else{$('#monitor-title').textContent='Add network monitor';form.elements.node_id.value=state.nodes.find(node=>node.status==='online')?.id||'';form.elements.snmp_oid.value='.1.3.6.1.2.1.1.3.0';}
  $('.snmp-fields',form).hidden=form.elements.kind.value!=='snmp';openModal('#monitor-modal');
}

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
