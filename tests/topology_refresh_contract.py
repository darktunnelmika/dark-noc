from pathlib import Path

source = Path("hub/static/app.js").read_text()
assert "function syncLiveTopologyRoutes" in source
assert "topology.dataset.topologySignature===structureSignature" in source
assert "setTimeout(refreshLive,350)" in source
assert "now-(connectLive.lastFullRefresh||0)>=30000" in source
assert "setTimeout(refresh,250)" not in source
assert "Promise.all([api('/api/dashboard'),api('/api/nodes'),api('/api/tunnels')])" in source
print("Topology refresh contract passed")
