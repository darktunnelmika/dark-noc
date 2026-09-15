// DARK NOC delegated Plugin/deployment action handlers. Classic-script globals are intentional.
function handlePluginActions(event) {
  const createTunnel=event.target.closest('[data-create-tunnel]');
  const installMissing=event.target.closest('.plugin-install-missing');
  const pluginDeploy=event.target.closest('.plugin-deploy');
  const deployRetry=event.target.closest('.deploy-retry');
  const deployRemove=event.target.closest('.deploy-remove');
  const pairRetry=event.target.closest('.pair-retry');
  const pairRemove=event.target.closest('.pair-remove');
  const pairReveal=event.target.closest('.pair-reveal');
  if(createTunnel){const trigger=$('.plugin-deploy');if(trigger)trigger.click();return true;}
  if(installMissing){const pluginId=installMissing.dataset.pluginId,plugin=state.plugins.find(p=>p.id===pluginId),missing=state.nodes.filter(node=>node.status==='online'&&!pluginInventory(node,pluginId)?.installed);Promise.all(missing.map(node=>api(`/api/nodes/${node.id}/plugins/${pluginId}/install`,{method:'POST'}))).then(()=>showToast('CORE INSTALL QUEUED',`${missing.length} Agent${missing.length===1?'':'s'} will install ${plugin?.name||pluginId}.`)).catch(error=>showToast('INSTALL FAILED',error.message,true));return true;}
  if(pluginDeploy){
    state.selectedPlugin=pluginDeploy.dataset.pluginId;
    const plugin=state.plugins.find(item=>item.id===state.selectedPlugin);
    $('#plugin-title').textContent=`Deploy ${plugin?.name||state.selectedPlugin}`;
    const transport=$('#plugin-form').elements.transport;
    transport.innerHTML=(plugin?.transports||[]).map((item,index)=>`<option value="${esc(item)}">${esc(item.toUpperCase())}${index===0?' · Recommended':''}</option>`).join('');
    const online=state.nodes.filter(node=>node.status==='online');
    const iranNodes=online.filter(node=>['edge','hub'].includes(node.role)),kharejNodes=online.filter(node=>node.role==='exit');
    if(!iranNodes.length){showToast('IRAN AGENT REQUIRED','At least one online Iran Edge or Hub Agent is required.',true);return true;}
    $('#plugin-iran-node').innerHTML=iranNodes.map(node=>`<option value="${Number(node.id)}">${esc(node.name)} · ${esc(node.host)}</option>`).join('');
    $('#plugin-kharej-node').innerHTML=kharejNodes.map(node=>`<option value="${Number(node.id)}">${esc(node.name)} · ${esc(node.host)}</option>`).join('');
    const form=$('#plugin-form');form.reset();form.elements.name.value='dark-link';form.elements.tunnel_port.value='3080';form.elements.target_host.value='127.0.0.1';form.elements.ws_path.value=`/dark-${crypto.getRandomValues(new Uint32Array(2)).join('')}`;form.elements.ws_mask.value='skipped';
    const iran=iranNodes[0],kharej=kharejNodes[0];
    $('#plugin-iran-node').value=iran.id;if(kharej){$('#plugin-kharej-node').value=kharej.id;form.elements.kharej_endpoint.value=kharej.host;}$('#plugin-endpoint').value=state.selectedPlugin==='dark-realm'?(kharej?.host||''):iran.host;updatePluginMode();openModal('#plugin-modal');return true;
  }
  if(deployRetry){api(`/api/plugin-deployments/${Number(deployRetry.dataset.deploymentId)}/retry`,{method:'POST'}).then(()=>{showToast('RETRY QUEUED','Only the failed side will be deployed again.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));return true;}
  if(deployRemove){if(confirm('Remove this managed tunnel from both servers?'))api(`/api/plugin-deployments/${Number(deployRemove.dataset.deploymentId)}/remove`,{method:'POST'}).then(()=>{showToast('REMOVAL QUEUED','The tunnel will be removed from both Agents.');refresh();}).catch(error=>showToast('REMOVAL FAILED',error.message,true));return true;}
  if(pairReveal){api(`/api/hybrid-deployments/${Number(pairReveal.dataset.deploymentId)}/pair-code`,{method:'POST'}).then(result=>{const name=result.pair_code.startsWith('DR1.')?'DARK Realm Pro':result.pair_code.startsWith('DGP-')?'DARK Ghost Pro':result.pair_code.startsWith('DPP-N1-')?'DARK Packet Pro':'DARK Backhaul';$('#output-title').textContent='DARK NOC PAIR CODE';$('#job-output').textContent=`KEEP THIS CODE SECRET\n\n${result.pair_code}\n\nOn the KHAREJ server run ${name}, select KHAREJ, choose Connect with DARK NOC Pair Code, and paste this code.`;openModal('#output-modal');}).catch(error=>showToast('PAIR CODE FAILED',error.message,true));return true;}
  if(pairRetry){api(`/api/hybrid-deployments/${Number(pairRetry.dataset.deploymentId)}/retry`,{method:'POST'}).then(()=>{showToast('IRAN RETRY QUEUED','Only the managed Iran side will be retried.');refresh();}).catch(error=>showToast('RETRY FAILED',error.message,true));return true;}
  if(pairRemove){if(confirm('Remove the IRAN side? You must remove the KHAREJ side manually from its script.'))api(`/api/hybrid-deployments/${Number(pairRemove.dataset.deploymentId)}/remove`,{method:'POST'}).then(()=>{showToast('IRAN REMOVAL QUEUED','Remove the foreign side manually using the matching DARK tunnel script.');refresh();}).catch(error=>showToast('REMOVAL FAILED',error.message,true));return true;}
  return false;
}
