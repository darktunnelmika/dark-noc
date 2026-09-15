(() => {
  'use strict';
  const { displayHost } = window.DarkNocCore;
  const severity = { healthy:0, online:0, unknown:1, pending:1, degraded:2, stale:3, offline:4, down:4 };
  function inferSide(tunnel, node) {
    const explicit=String(tunnel.topology_side||'').toLowerCase();
    if(explicit==='iran'||explicit==='kharej')return explicit;
    if(node?.role==='hub'||node?.role==='edge')return 'iran';
    if(node?.role==='exit')return 'kharej';
    const role=String(tunnel.tunnel_role||'').toLowerCase(),method=String(tunnel.method||'');
    if(method==='DARK Packet Pro')return role==='client'?'iran':role==='server'?'kharej':'unknown';
    if(method==='DARK Realm Pro')return role==='edge'?'iran':role==='gateway'?'kharej':'unknown';
    if(method==='DARK Backhaul'||method==='DARK Ghost Pro')return role==='server'?'iran':role==='client'?'kharej':'unknown';
    return 'unknown';
  }
  function endpoint(node,name,host){return {node,name:node?.name||name||'UNRESOLVED ENDPOINT',host:displayHost(node?.observed_ip||node?.host||host||'IP UNAVAILABLE')};}
  function buildLinks({nodes=[],tunnels=[],filter='all',search=''}) {
    const nodeIndex=new Map(nodes.map(node=>[Number(node.id),node]));
    const groups=new Map();
    tunnels.forEach(tunnel=>{
      const peerId=Number(tunnel.peer_node_id||0),nodeId=Number(tunnel.node_id||0);
      const fallbackKey=peerId?`${tunnel.method||'DARK'}|${tunnel.name}|${Math.min(nodeId,peerId)}|${Math.max(nodeId,peerId)}`:`${tunnel.method||'DARK'}|${tunnel.name}|${nodeId}|${tunnel.service||tunnel.id}`;
      const key=String(tunnel.topology_key||fallbackKey);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(tunnel);
    });
    const links=[];
    groups.forEach(rows=>{
      const records=rows.map(tunnel=>{const node=nodeIndex.get(Number(tunnel.node_id));return {tunnel,node,side:inferSide(tunnel,node)};});
      let iran=records.find(item=>item.side==='iran')||null,kharej=records.find(item=>item.side==='kharej')||null;
      if(!iran&&!kharej&&records.length){const first=records[0];if(first.node?.role==='exit')kharej=first;else iran=first;}
      const representative=iran?.tunnel||kharej?.tunnel||rows[0],peerId=Number(representative.peer_node_id||0),peerNode=nodeIndex.get(peerId);
      const left=iran?endpoint(iran.node,iran.tunnel.node_name,iran.tunnel.node_observed_ip||iran.tunnel.node_host):endpoint(peerNode,kharej?.tunnel.peer_name||'IRAN / HUB',kharej?.tunnel.peer_host);
      const right=kharej?endpoint(kharej.node,kharej.tunnel.node_name,kharej.tunnel.node_observed_ip||kharej.tunnel.node_host):endpoint(peerNode,iran?.tunnel.peer_name||'REMOTE ENDPOINT',iran?.tunnel.peer_host||iran?.tunnel.target_host);
      const statuses=rows.map(item=>String(item.status||'unknown').toLowerCase());if(left.node&&left.node.status!=='online')statuses.push('stale');if(right.node&&right.node.status!=='online')statuses.push('stale');
      const worst=statuses.reduce((current,value)=>(severity[value]??1)>(severity[current]??1)?value:current,'healthy');
      const status=['down','offline'].includes(worst)?'DOWN':worst==='stale'?'STALE':worst==='degraded'?'DEGRADED':'ONLINE',tone=status==='ONLINE'?'online':status==='DEGRADED'?'degraded':status==='STALE'?'stale':'down';
      const haystack=[representative.name,representative.method,left.name,left.host,right.name,right.host].join(' ').toLowerCase();if(filter!=='all'&&filter!==tone)return;if(search&&!haystack.includes(search))return;
      const rates=rows.map(item=>Number(item.rx_bps||0)+Number(item.tx_bps||0));
      links.push({tunnel:representative,rows,left,right,status,tone,rate:rates.length?Math.max(...rates):0,sessions:Math.max(0,...rows.map(item=>Number(item.sessions||0))),health:Math.min(100,...rows.map(item=>Number(item.health_score??100))),latency:Math.max(0,...rows.map(item=>Number(item.latency_ms||0))),loss:Math.max(0,...rows.map(item=>Number(item.packet_loss||0))),uptime:Math.max(0,...rows.map(item=>Number(item.service_uptime||0)))});
    });
    return links;
  }
  window.DarkNocTopology = Object.freeze({ inferSide, endpoint, buildLinks });
})();
