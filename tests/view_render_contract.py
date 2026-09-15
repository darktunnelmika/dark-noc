from pathlib import Path
s=Path("hub/static/app.js").read_text()
assert "function renderCachedView" in s
assert "renderCachedView(name);" in s
assert "populateSSHServers();renderTransferLimits();updateNavigationCounts();updateOverview(summary);" in s
assert "renderNodes(); populateSSHServers(); renderTransferLimits(); renderTunnels(); renderPlugins(); renderCertificates(); renderMonitors();" not in s
print("View-aware render contract passed")
