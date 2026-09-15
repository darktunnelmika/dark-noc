from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
fleet=(root/'hub/static/fleet-operations.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['function renderFleetOperations(){','function renderVersionCompliance(){','function openFleetEditor(){',"$('#add-fleet-operation').addEventListener","$('#fleet-form').addEventListener('submit'"]:
    assert marker in fleet
    assert marker not in app
assert 'data-fleet-output' in app and 'fleetCancel' in app
assert index.index('file-transfer.js') < index.index('fleet-operations.js') < index.index('app.js')
print('Fleet Operations module boundary passed')
