// DARK NOC delegated Node action handlers. Classic-script globals are intentional.
function handleNodeActions(event) {
  const takeControl=event.target.closest('#take-control');
  const ssh=event.target.closest('.server-ssh');
  const diagnostics=event.target.closest('.node-diagnostics');
  const logs=event.target.closest('.node-logs');
  const deleteNode=event.target.closest('.node-delete');
  const topologyNode=event.target.closest('.node[data-node]');
  const cyberNode=event.target.closest('.cyber-node');
  const editNode=event.target.closest('.node-edit');
  const resetPin=event.target.closest('.node-pin');
  const serviceRestart=event.target.closest('.service-chip');
  const autohealNode=event.target.closest('.node-autoheal');
  const provisionRetry=event.target.closest('.node-provision-retry');
  const syncAgent=event.target.closest('.node-agent-sync');
  const provisionLog=event.target.closest('.node-provision-log');
  if(takeControl){const node=state.nodes.find(item=>item.id===Number(takeControl.dataset.nodeId))||state.nodes.find(item=>item.status!=='online')||state.nodes[0];if(node)connectSSH(node.id);else showToast('NO NODE AVAILABLE','Add a server first.',true);return true;}
  if(ssh){connectSSH(ssh.dataset.nodeId);return true;}
  if(diagnostics){createJob(Number(diagnostics.dataset.nodeId),'diagnostics',null,{},true).catch(error=>showToast('COMMAND FAILED',error.message,true));return true;}
  if(logs){createJob(Number(logs.dataset.nodeId),'logs',null,{},true).catch(error=>showToast('LOG REQUEST FAILED',error.message,true));return true;}
  if(deleteNode){if(confirm(`Delete node “${deleteNode.dataset.nodeName}” and its telemetry from DARK NOC?`))api(`/api/nodes/${Number(deleteNode.dataset.nodeId)}`,{method:'DELETE'}).then(()=>{showToast('NODE DELETED',`${deleteNode.dataset.nodeName} was removed from the Hub.`);refresh();}).catch(error=>showToast('DELETE FAILED',error.message,true));return true;}
  if(topologyNode){const node=state.nodes.find(item=>item.name===topologyNode.dataset.node);switchView('servers');if(node)showToast('NODE LOCATED',`${node.name} · ${node.status}`);return true;}
  if(cyberNode){const node=state.nodes.find(item=>item.id===Number(cyberNode.dataset.nodeId));const related=node?state.tunnels.filter(item=>Number(item.node_id)===node.id||Number(item.peer_node_id)===node.id):[];$('#output-title').textContent=node?`${node.name} · NODE INTELLIGENCE`:cyberNode.dataset.endpointName;$('#job-output').textContent=node?`ROLE: ${node.role}\nHOST: ${node.host}\nOBSERVED IP: ${node.observed_ip||'—'}\nAGENT: ${node.status}\nSSH: ${node.ssh_configured?'READY':'NO ACCESS'}\nLAST SEEN: ${relativeTime(node.last_seen)}\n\nDARK BACKHAUL PATHS:\n${related.map(item=>`• ${item.name} · ${String(item.status).toUpperCase()} · ${item.sessions||0} sessions`).join('\n')||'None'}`:'This endpoint is visible from a live Backhaul TCP peer but is not enrolled as a manageable Node.';openModal('#output-modal');return true;}
  if(editNode){const node=state.nodes.find(item=>item.id===Number(editNode.dataset.nodeId));if(node)openNodeEditor(node);return true;}
  if(resetPin){if(confirm('Forget the pinned SSH fingerprint? Only do this after verifying the server key changed.'))api(`/api/nodes/${Number(resetPin.dataset.nodeId)}/ssh-fingerprint`,{method:'DELETE'}).then(()=>showToast('SSH PIN RESET','The next SSH connection will pin the presented host key.')).catch(error=>showToast('PIN RESET FAILED',error.message,true));return true;}
  if(serviceRestart){if(confirm(`Restart ${serviceRestart.dataset.serviceName}?`))createJob(Number(serviceRestart.dataset.serviceNode),'restart_service',serviceRestart.dataset.serviceName,{},true).then(()=>setTimeout(refresh,2500)).catch(error=>showToast('SERVICE RESTART FAILED',error.message,true));return true;}
  if(autohealNode){const node=state.nodes.find(item=>item.id===Number(autohealNode.dataset.nodeId));if(node){const enabled=!Boolean(node.autoheal_enabled);if(confirm(`${enabled?'Enable':'Disable'} Auto-Heal on ${node.name}?`))createJob(node.id,'configure_autoheal',null,{enabled,cooldown_seconds:Number(node.autoheal_cooldown||300),max_restarts_per_hour:Number(node.autoheal_max_restarts||3)},true).then(()=>setTimeout(refresh,3000)).catch(error=>showToast('AUTO-HEAL UPDATE FAILED',error.message,true));}return true;}
  if(provisionRetry){api(`/api/nodes/${Number(provisionRetry.dataset.nodeId)}/provision`,{method:'POST'}).then(()=>{showToast('INSTALLATION RETRIED','The Hub is reconnecting and installing the Agent.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));return true;}
  if(syncAgent){const node=state.nodes.find(item=>item.id===Number(syncAgent.dataset.nodeId));if(node&&confirm(`Redeploy and synchronize the DARK NOC Agent on “${node.name}”?\n\nManaged configuration and tunnels will be preserved.`))api(`/api/nodes/${node.id}/provision`,{method:'POST'}).then(()=>{showToast('AGENT SYNC STARTED',`${node.name} is receiving the matching Agent build; managed configuration and tunnels are preserved.`);refresh();}).catch(error=>showToast('AGENT SYNC FAILED',error.message,true));return true;}
  if(provisionLog){const node=state.nodes.find(item=>item.id===Number(provisionLog.dataset.nodeId));if(node){$('#output-title').textContent=`${node.name} · installation ${String(node.provision_status||'unknown').toUpperCase()}`;$('#job-output').textContent=node.provision_output||'Installation is running; refresh in a few seconds.';openModal('#output-modal');}return true;}
  return false;
}
