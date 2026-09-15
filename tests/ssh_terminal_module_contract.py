from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
ssh=(root/'hub/static/ssh-terminal.js').read_text()
index=(root/'hub/static/index.html').read_text()
markers=['function activeTerminalSlot()','function ensureTerminal(slot)','function closeSocket(slot)','function connectSSH(nodeId, slot = null, options = {})','function savedSnippets()','function startTerminalReplay()']
for marker in markers:
    assert marker in ssh
    assert marker not in app
assert "$('#ssh-connect').addEventListener" in app
assert 'connectSSH(nodeId)' in app
assert index.index('core.js') < index.index('topology.js') < index.index('ssh-terminal.js') < index.index('app.js')
print('SSH terminal module boundary passed')
