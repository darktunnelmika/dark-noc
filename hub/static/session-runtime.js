// DARK NOC authentication and live WebSocket lifecycle. Classic-script globals are intentional.
function terminateAuthenticatedActivity({showLoginGate = true, transferMessage = 'AUTHENTICATION EXPIRED · Sign in again', terminalStatus = '● AUTH EXPIRED'} = {}) {
  if (showLoginGate) $('#login-gate').classList.remove('hidden');
  clearTimeout(connectLive.reconnectTimer);
  clearTimeout(connectLive.refreshTimer);
  clearTimeout(connectLive.fullRefreshTimer);
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
  socket.onmessage=event=>{if(liveSocket!==socket)return;let message={};try{message=JSON.parse(event.data)}catch{}if(message.type==='telemetry'){clearTimeout(connectLive.refreshTimer);connectLive.refreshTimer=setTimeout(refreshLive,350);const now=Date.now();if(now-(connectLive.lastFullRefresh||0)>=30000){connectLive.lastFullRefresh=now;clearTimeout(connectLive.fullRefreshTimer);connectLive.fullRefreshTimer=setTimeout(()=>refreshActiveView(),1200);}}};
  socket.onclose=event=>{clearInterval(socket.keepaliveTimer);if(liveSocket===socket)liveSocket=null;if(event.code===4401){terminateAuthenticatedActivity();return;}if(!socket.intentionalClose&&$('#login-gate').classList.contains('hidden'))connectLive.reconnectTimer=setTimeout(connectLive,3000);};
}
