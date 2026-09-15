// DARK NOC delegated cross-feature action handlers. Classic-script globals are intentional.
function handleOpsActions(event) {
  const jump=event.target.closest('[data-jump]');
  const terminalClose=event.target.closest('.terminal-close');
  const incidentAck=event.target.closest('.incident-ack');
  const incidentResolve=event.target.closest('.incident-resolve');
  const incidentReopen=event.target.closest('.incident-reopen');
  const incidentSelect=event.target.closest('[data-incident-select]');
  const monitorRun=event.target.closest('.monitor-run');
  const monitorHistory=event.target.closest('.monitor-history');
  const monitorEdit=event.target.closest('.monitor-edit');
  const monitorDelete=event.target.closest('.monitor-delete');
  const fleetOutput=event.target.closest('[data-fleet-output]');
  const fleetCancel=event.target.closest('.fleet-cancel');
  const fileRow=event.target.closest('[data-file-index]');
  const certRenew=event.target.closest('.cert-renew');
  if(jump){switchView(jump.dataset.jump);const modal=jump.closest('.modal-backdrop');if(modal)closeModal(modal);return true;}
  if(terminalClose){const card=terminalClose.closest('.terminal-card'),slot=$('.terminal-screen',card)?.id;if(slot){closeSocket(slot);if($$('.terminal-card').length>1){state.terminals.get(slot)?.terminal.dispose();state.terminals.delete(slot);card.remove();selectTerminalCard($('.terminal-card'));activeTerminal()?.fitAddon.fit();}else{state.terminals.get(slot)?.terminal.reset();$('.terminal-server strong',card).textContent='NO SESSION';$('.terminal-server small',card).textContent='Select a server and connect';$('.ssh-online',card).textContent='● IDLE';}}return true;}
  if(incidentSelect){loadIncidentDetail(Number(incidentSelect.dataset.incidentSelect));return true;}
  if(incidentAck){const form=$('#incident-note-form');api(`/api/incidents/${Number(incidentAck.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'acknowledge',note:form?.elements.message.value||null,root_cause:form?.elements.root_cause.value||null})}).then(()=>{if(form)form.elements.message.value='';return refresh();}).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));return true;}
  if(incidentResolve){if(confirm('Resolve this incident and record the operator findings?')){const form=$('#incident-note-form');api(`/api/incidents/${Number(incidentResolve.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'resolve',note:form?.elements.message.value||null,root_cause:form?.elements.root_cause.value||null,resolution:form?.elements.resolution.value||null})}).then(()=>{if(form)form.elements.message.value='';return refresh();}).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));}return true;}
  if(incidentReopen){if(confirm('Reopen this incident?'))api(`/api/incidents/${Number(incidentReopen.dataset.incidentId)}/action`,{method:'POST',body:JSON.stringify({action:'reopen'})}).then(refresh).catch(error=>showToast('INCIDENT ACTION FAILED',error.message,true));return true;}
  if(monitorRun){api(`/api/monitors/${Number(monitorRun.dataset.monitorId)}/run`,{method:'POST'}).then(result=>{showToast('MONITOR QUEUED',`Job #${result.job_id} is running from its assigned Agent.`);setTimeout(refresh,1800);}).catch(error=>showToast('MONITOR FAILED',error.message,true));return true;}
  if(monitorHistory){const monitor=state.monitors.find(item=>item.id===Number(monitorHistory.dataset.monitorId));api(`/api/monitors/${Number(monitorHistory.dataset.monitorId)}/results?limit=200`).then(results=>{$('#output-title').textContent=`${monitor?.name||'MONITOR'} · LAST ${results.length} CHECKS`;$('#job-output').textContent=results.map(result=>`${new Date(result.ts*1000).toLocaleString('en-GB')}  ${String(result.status).toUpperCase().padEnd(4)}  ${result.latency_ms==null?'—':`${Number(result.latency_ms).toFixed(1)} ms`}\n${JSON.stringify(result.detail||{})}`).join('\n\n')||'No results yet.';openModal('#output-modal');}).catch(error=>showToast('HISTORY FAILED',error.message,true));return true;}
  if(monitorEdit){const monitor=state.monitors.find(item=>item.id===Number(monitorEdit.dataset.monitorId));if(monitor)openMonitorEditor(monitor);return true;}
  if(monitorDelete){if(confirm(`Delete monitor “${monitorDelete.dataset.monitorName}” and its result history?`))api(`/api/monitors/${Number(monitorDelete.dataset.monitorId)}`,{method:'DELETE'}).then(()=>{showToast('MONITOR DELETED',monitorDelete.dataset.monitorName);refresh();}).catch(error=>showToast('DELETE FAILED',error.message,true));return true;}
  if(fleetOutput){const operation=state.fleetOperations.find(item=>item.id===Number(fleetOutput.dataset.operationId)),item=operation?.items?.find(entry=>entry.id===Number(fleetOutput.dataset.fleetOutput));$('#output-title').textContent=`FLEET OUTPUT · ${item?.node_name||'NODE'}`;$('#job-output').textContent=item?.output||'No output yet.';openModal('#output-modal');return true;}
  if(fleetCancel){if(confirm('Cancel this scheduled fleet operation?'))api(`/api/fleet/operations/${Number(fleetCancel.dataset.operationId)}`,{method:'DELETE'}).then(refresh).catch(error=>showToast('CANCEL FAILED',error.message,true));return true;}
  if(fileRow){const entry=state.files.entries[Number(fileRow.dataset.fileIndex)];if(entry){fileSelection(entry);fileRow.classList.add('selected');}return true;}
  if(certRenew){api(`/api/certificates/${Number(certRenew.dataset.certId)}/renew`,{method:'POST'}).then(result=>{showToast('RENEWAL QUEUED',`Certificate job #${result.job_id} queued.`);refresh();}).catch(error=>showToast('RENEWAL FAILED',error.message,true));return true;}
  return false;
}
