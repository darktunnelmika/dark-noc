// DARK NOC secure file manager + browser upload/relay runtime. Classic-script globals are intentional.
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
