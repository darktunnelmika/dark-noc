from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
incident=(root/'hub/static/incident-management.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['function renderIncidentDetail(incident) {','async function loadIncidentDetail(','function renderIncidentList(){','function renderIncidents() {',"$('#incident-note-form').addEventListener","$('#export-incidents').addEventListener"]:
    assert marker in incident
    assert marker not in app
for marker in ['incidentAck','incidentResolve','incidentReopen','incidentSelect']:
    assert marker in app
assert index.index('monitoring.js') < index.index('incident-management.js') < index.index('app.js')
print('Incident Command module boundary passed')
