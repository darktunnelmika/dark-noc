from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
module=(root/'hub/static/tunnel-operations.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['function tunnelHistoryChart(samples){','async function openTunnelManager(tunnel){',"$('#tunnel-edit-form').addEventListener","$('#tunnel-delete').addEventListener"]:
    assert marker in module
    assert marker not in app
for marker in ['manageTunnel','tunnelAction','tunnelSSH']:
    assert marker in app
assert index.index('incident-management.js') < index.index('tunnel-operations.js') < index.index('app.js')
print('Tunnel Operations module boundary passed')
