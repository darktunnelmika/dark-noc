from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
module=(root/'hub/static/file-transfer.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['function fileSelection(entry=null){','function renderFileManager(){','async function loadFileDirectory(','async function fileAction(']:
    assert marker in module
    assert marker not in app
for marker in ["$('#ssh-upload-form').addEventListener","$('#ssh-relay-form').addEventListener","$('#file-manager-node').addEventListener","$('#file-download').addEventListener","$('#file-delete').addEventListener"]:
    assert marker in module
    assert marker not in app
assert "$('#ssh-connect').addEventListener" in app
assert 'connectSSH(nodeId)' in app
assert index.index('core.js') < index.index('topology.js') < index.index('ssh-terminal.js') < index.index('file-transfer.js') < index.index('app.js')
print('File transfer module boundary passed')
