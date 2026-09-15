// DARK NOC SSH terminal runtime. Classic-script globals are intentional.
function activeTerminalSlot() {
  return $('.terminal-card.active-terminal .terminal-screen')?.id || $('.terminal-screen')?.id || 'terminal-iran';
}

function activeTerminal() { return state.terminals.get(activeTerminalSlot()); }

function terminalText(terminal) {
  const buffer=terminal?.buffer?.active;if(!buffer)return '';
  const lines=[];for(let index=0;index<buffer.length;index++)lines.push(buffer.getLine(index)?.translateToString(true)||'');
  return lines.join('\n').replace(/\n{4,}/g,'\n\n\n');
}

function createTerminalPane(){
  state.paneCounter+=1;const slot=`terminal-pane-${state.paneCounter}`,card=document.createElement('article');card.className='terminal-card active-terminal';card.innerHTML=`<header><div class="terminal-lights"><i></i><i></i><i></i></div><div class="terminal-server"><span class="flag">${String(state.paneCounter).padStart(2,'0')}</span><div><strong>NO SESSION</strong><small>Select a server and connect</small></div></div><span class="ssh-online">● IDLE</span><button class="terminal-close" title="Close pane">×</button></header><div class="terminal-screen" id="${slot}" tabindex="0"></div>`;$$('.terminal-card').forEach(item=>item.classList.remove('active-terminal'));$('.terminal-layout').appendChild(card);card.addEventListener('pointerdown',()=>selectTerminalCard(card));card.addEventListener('focusin',()=>selectTerminalCard(card));ensureTerminal(slot);return slot;
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
  const closeButton=container.closest('.terminal-card')?.querySelector('.terminal-close');if(!closeButton){const button=document.createElement('button');button.className='terminal-close';button.title='Close pane';button.textContent='×';container.closest('.terminal-card')?.querySelector('header')?.appendChild(button);}
  const entry={terminal,fitAddon,searchAddon,sessionName:slot.replace(/[^A-Za-z0-9_-]/g,'-').slice(0,32),recording:[],reconnectAttempts:0,reconnectTimer:null};state.terminals.set(slot,entry);
  const observer=new ResizeObserver(()=>{clearTimeout(entry.fitTimer);entry.fitTimer=setTimeout(()=>{try{fitAddon.fit();}catch{}},60);});observer.observe(container.closest('.terminal-card'));
  return entry;
}

function closeSocket(slot) {
  const entry=state.terminals.get(slot);if(entry?.reconnectTimer){clearTimeout(entry.reconnectTimer);entry.reconnectTimer=null;}if(entry)entry.reconnectAttempts=0;
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

function connectSSH(nodeId, slot = null, options = {}) {
  const node = state.nodes.find(item => item.id === Number(nodeId));
  if (!node) return;
  if (!node.ssh_configured) {
    openNodeEditor(node, true);
    showToast('SSH SETUP REQUIRED', `Add an SSH password or private key for ${node.name}.`, true);
    return;
  }
  if (!slot) {
    const available=$$('.terminal-screen').find(screen=>{const socket=state.sockets.get(screen.id);return !socket||socket.readyState>=WebSocket.CLOSING;});slot=available?.id||createTerminalPane();
  }
  const existing=state.sockets.get(slot);
  if(existing&&existing.readyState<WebSocket.CLOSING&&!options.reconnect){
    const existingNode=state.nodes.find(item=>item.id===Number(existing.nodeId));
    if(!confirm(`Replace the active ${existingNode?.name||'SSH'} session in this pane?`))return;
  }
  switchView('terminal');
  const screen = $(`#${slot}`), term=ensureTerminal(slot);
  if(!screen)return;
  if(!options.reconnect)closeSocket(slot);else if(term.reconnectTimer){clearTimeout(term.reconnectTimer);term.reconnectTimer=null;}
  const card=screen.closest('.terminal-card'), code=node.role==='edge'?'IR':node.role==='exit'?'EX':'HB';
  $('.terminal-server .flag',card).textContent=code;
  $('.terminal-server strong',card).textContent=node.name;
  $('.terminal-server small',card).textContent=`${node.ssh_user}@${node.host}:${node.ssh_port}`;
  selectTerminalCard(card);
  $('#ssh-node-select').value=String(node.id);
  $('.ssh-online',card).textContent='● CONNECTING';
  if(!options.reconnect){term.terminal.reset();term.recording=[];term.reconnectAttempts=0;}term.terminal.writeln(`\x1b[38;5;45m${options.reconnect?'Reconnecting':'Connecting'} to ${node.name} (${node.host}:${Number(node.ssh_port)})…\x1b[0m\r\n`);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/ssh/${node.id}?session=${encodeURIComponent(term.sessionName)}`);
  socket.nodeId=node.id;socket.sshConnected=false;socket.hadError=false;socket.intentionalClose=false;
  state.sockets.set(slot, socket);
  state.selectedNode = node.id;
  socket.onmessage = event => {
    if(state.sockets.get(slot)!==socket)return;
    let message={};try{message=JSON.parse(event.data);}catch{return;}
    if (message.type === 'output') {term.recording.push({at:Date.now(),data:String(message.data||'')});if(term.recording.length>12000)term.recording.splice(0,term.recording.length-12000);term.terminal.write(message.data);}
    if (message.type === 'connected') { socket.sshConnected=true;term.reconnectAttempts=0;$('.ssh-online',card).textContent=message.persistent?'● PERSISTENT':'● CONNECTED';term.terminal.writeln(`\x1b[38;5;48m✓ SECURE SSH CONNECTED · ${message.node}${message.persistent?' · TMUX PERSISTENT':''}\x1b[0m`);term.terminal.writeln(`\x1b[38;5;244mHost key: ${message.fingerprint||'unavailable'} · Session: ${message.session||term.sessionName}\x1b[0m\r\n`);term.fitAddon.fit();socket.send(JSON.stringify({type:'resize',cols:term.terminal.cols,rows:term.terminal.rows}));term.terminal.focus(); }
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
    $('.ssh-online',card).textContent=abnormal?'● RECONNECTING':'● CLOSED';
    term.terminal.writeln(abnormal?'\r\n\x1b[38;5;203mSession interrupted. Automatic reconnect armed.\x1b[0m':'\r\n\x1b[38;5;244mSession closed.\x1b[0m');
    if(abnormal&&!socket.intentionalClose&&!authenticatedActivityTerminated&&term.reconnectAttempts<5){term.reconnectAttempts+=1;const delay=Math.min(20000,1000*2**(term.reconnectAttempts-1));$('.ssh-online',card).textContent=`● RETRY ${term.reconnectAttempts}/5`;term.reconnectTimer=setTimeout(()=>connectSSH(node.id,slot,{reconnect:true}),delay);}else if(abnormal){$('.ssh-online',card).textContent='● ERROR';}
  };
}

function savedSnippets(){try{const value=JSON.parse(localStorage.getItem('darkNocSshSnippets')||'[]');return Array.isArray(value)?value:[];}catch{return [];}}
function renderSnippets(){const select=$('#terminal-snippet-select'),snippets=savedSnippets();select.innerHTML='<option value="">SAVED SNIPPETS</option>'+snippets.map((item,index)=>`<option value="${index}">${esc(item.name)} · ${esc(item.command)}</option>`).join('');}
function stopTerminalReplay(){if(state.replayTimer){clearTimeout(state.replayTimer);state.replayTimer=null;}if($('#terminal-replay-status'))$('#terminal-replay-status').textContent='STOPPED';}
function startTerminalReplay(){const entry=activeTerminal();if(!entry||!entry.recording.length)return showToast('NO RECORDING','Run an SSH session in the active pane first.',true);stopTerminalReplay();const records=entry.recording.slice(),output=$('#terminal-replay-output');output.textContent='';$('#terminal-replay-title').textContent=`${entry.sessionName} · ${records.length} frames`;$('#terminal-replay-status').textContent='PLAYING';openModal('#terminal-replay-modal');let index=0,previous=records[0].at;const next=()=>{if(index>=records.length){state.replayTimer=null;$('#terminal-replay-status').textContent='REPLAY COMPLETE';return;}const record=records[index++],clean=record.data.replace(/\x1B\[[0-?]*[ -/]*[@-~]/g,'').replace(/\r/g,'');output.textContent+=clean;output.scrollTop=output.scrollHeight;const delay=Math.min(160,Math.max(12,record.at-previous));previous=record.at;state.replayTimer=setTimeout(next,delay);};next();}
