// DARK NOC delegated Tunnel action handlers. Classic-script globals are intentional.
function handleTunnelActions(event) {
  const manageTunnel=event.target.closest('.tunnel-manage');
  const tunnelSSH=event.target.closest('[data-tunnel-ssh-node]');
  const tunnelAction=event.target.closest('[data-tunnel-action]');
  const restartTunnel=event.target.closest('.tunnel-restart');
  if(manageTunnel){const tunnel=state.tunnels.find(item=>item.id===Number(manageTunnel.dataset.tunnelId));if(tunnel)openTunnelManager(tunnel);return true;}
  if(tunnelSSH){const nodeId=Number(tunnelSSH.dataset.tunnelSshNode);closeModal($('#tunnel-manage-modal'));connectSSH(nodeId);return true;}
  if(tunnelAction&&state.selectedTunnel){const action=tunnelAction.dataset.tunnelAction;if(action==='stop'&&!confirm(`Stop ${state.selectedTunnel.name}?`))return true;api(`/api/tunnels/${state.selectedTunnel.id}/action`,{method:'POST',body:JSON.stringify({action})}).then(result=>{showToast('TUNNEL JOB QUEUED',`${action.toUpperCase()} sent to ${state.selectedTunnel.node_name}.`);if(['logs','status','test'].includes(action))waitForJob(result.job_id);setTimeout(refresh,2500);}).catch(error=>showToast('TUNNEL ACTION FAILED',error.message,true));return true;}
  if(restartTunnel){if(!restartTunnel.dataset.service){showToast('NO SERVICE DEFINED','Register a systemd service for this tunnel first.',true);return true;}if(confirm(`Restart ${restartTunnel.dataset.service}?`))createJob(Number(restartTunnel.dataset.nodeId),'restart_service',restartTunnel.dataset.service,{},true).then(()=>setTimeout(refresh,3500));return true;}
  return false;
}
