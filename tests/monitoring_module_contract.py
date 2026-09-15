from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/static/app.js').read_text()
ops=(root/'hub/static/ops-actions.js').read_text()
mon=(root/'hub/static/monitoring.js').read_text()
index=(root/'hub/static/index.html').read_text()
for marker in ['function renderMonitors(){','function openMonitorEditor(',"$('#add-monitor').addEventListener","$('#monitor-form').addEventListener('submit'","$('#run-all-monitors').addEventListener"]:
    assert marker in mon
    assert marker not in app
assert 'monitorRun' in ops and 'monitorHistory' in ops and 'monitorEdit' in ops and 'monitorDelete' in ops
assert index.index('fleet-operations.js') < index.index('monitoring.js') < index.index('app.js')
print('Synthetic Monitoring module boundary passed')
