from pathlib import Path
import re

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()

old_version = '2.9.28'
new_version = '2.9.29'

route_pattern = re.compile(r'^@app\.(get|post|put|delete|patch|websocket)\("([^"]+)"', re.M)
routes = route_pattern.findall(text)
counts = {}
for method, path in routes:
    if path.startswith('/api/monitors'):
        family = 'Monitoring'
    elif path.startswith('/api/incidents'):
        family = 'Incidents'
    elif path.startswith('/api/nodes'):
        family = 'Nodes / Node jobs / plugins'
    elif path.startswith('/api/tunnels'):
        family = 'Tunnels'
    elif path.startswith('/api/plugin') or path.startswith('/api/hybrid'):
        family = 'Plugin deployments'
    elif path.startswith('/api/fleet'):
        family = 'Fleet operations'
    elif path.startswith('/api/certificates'):
        family = 'Certificates'
    elif path.startswith('/api/ssh') or path.startswith('/ws/ssh'):
        family = 'SSH / file transfer'
    elif path.startswith('/api/agent'):
        family = 'Agent control plane'
    elif path.startswith('/api/auth'):
        family = 'Authentication'
    elif path.startswith('/api/dashboard') or path.startswith('/api/system') or path in {'/healthz', '/readyz'}:
        family = 'Dashboard / system health'
    elif path.startswith('/ws/live'):
        family = 'Live websocket'
    else:
        family = 'Other'
    counts[family] = counts.get(family, 0) + 1


def block(source: str, start: str, end: str) -> str:
    a = source.index(start)
    b = source.index(end, a)
    return source[a:b].rstrip() + '\n'

monitor_block = block(text, '@app.get("/api/monitors")', '@app.get("/api/fleet/operations")')
incident_block = block(text, '@app.get("/api/incidents")', '@app.post("/api/nodes/{node_id}/jobs", status_code=202)')


def router_source(source_block: str, register_name: str, aliases: list[str], extras: str = '') -> str:
    converted = source_block.replace('@app.', '@router.')
    indented = '\n'.join(('    ' + line if line else '') for line in converted.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    return f'''import json\nimport sqlite3\nfrom fastapi import APIRouter, Depends, HTTPException, Request\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}\n{extras}\n{indented}\n\n    app.include_router(router)\n    return router\n'''

monitor_aliases = [
    'current_user', 'db', 'MonitorBody', 'fetch_monitor_inventory', 'public_monitor',
    'create_monitor_mutation', 'update_monitor_mutation', 'delete_monitor_mutation',
    'queue_monitor_run_mutation', 'MonitorIncidentServiceError', 'monitor_values',
    'resolve_incident', 'utc_ts', 'decrypt', 'audit', 'fetch_monitor_result_rows',
]
monitor_src = router_source(
    monitor_block,
    'monitoring',
    monitor_aliases,
    '    NODE_STALE_AFTER = deps["node_stale_after"]\n',
)
incident_aliases = [
    'current_user', 'db', 'IncidentNoteBody', 'IncidentActionBody',
    'fetch_incident_inventory', 'fetch_incident_detail_rows', 'add_incident_note_mutation',
    'incident_action_mutation', 'MonitorIncidentServiceError', 'append_incident_event',
    'resolve_incident', 'utc_ts', 'audit',
]
incident_src = router_source(incident_block, 'incidents', incident_aliases)
(ROOT / 'hub/monitoring_router.py').write_text(monitor_src)
(ROOT / 'hub/incidents_router.py').write_text(incident_src)

monitor_start = text.index('@app.get("/api/monitors")')
monitor_end = text.index('@app.get("/api/fleet/operations")', monitor_start)
monitor_call = '''register_monitoring_router(\n    app,\n    current_user=current_user, db=db, MonitorBody=MonitorBody, fetch_monitor_inventory=fetch_monitor_inventory,\n    public_monitor=public_monitor, create_monitor_mutation=create_monitor_mutation,\n    update_monitor_mutation=update_monitor_mutation, delete_monitor_mutation=delete_monitor_mutation,\n    queue_monitor_run_mutation=queue_monitor_run_mutation, MonitorIncidentServiceError=MonitorIncidentServiceError,\n    monitor_values=monitor_values, resolve_incident=resolve_incident, utc_ts=utc_ts, decrypt=decrypt, audit=audit,\n    fetch_monitor_result_rows=fetch_monitor_result_rows, node_stale_after=NODE_STALE_AFTER,\n)\n\n\n'''
text = text[:monitor_start] + monitor_call + text[monitor_end:]

incident_start = text.index('@app.get("/api/incidents")')
incident_end = text.index('@app.post("/api/nodes/{node_id}/jobs", status_code=202)', incident_start)
incident_call = '''register_incidents_router(\n    app,\n    current_user=current_user, db=db, IncidentNoteBody=IncidentNoteBody, IncidentActionBody=IncidentActionBody,\n    fetch_incident_inventory=fetch_incident_inventory, fetch_incident_detail_rows=fetch_incident_detail_rows,\n    add_incident_note_mutation=add_incident_note_mutation, incident_action_mutation=incident_action_mutation,\n    MonitorIncidentServiceError=MonitorIncidentServiceError, append_incident_event=append_incident_event,\n    resolve_incident=resolve_incident, utc_ts=utc_ts, audit=audit,\n)\n\n\n'''
text = text[:incident_start] + incident_call + text[incident_end:]

loader_anchor = "ROOT = Path(__file__).resolve().parent\n"
router_loader = '''_MONITORING_ROUTER_PATH = Path(__file__).resolve().with_name('monitoring_router.py')\n_monitoring_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_monitoring_router', _MONITORING_ROUTER_PATH)\nif _monitoring_router_spec is None or _monitoring_router_spec.loader is None:\n    raise ImportError(f'Could not load Monitoring router: {_MONITORING_ROUTER_PATH}')\n_monitoring_router_module = _realm_support_importlib_util.module_from_spec(_monitoring_router_spec)\n_monitoring_router_spec.loader.exec_module(_monitoring_router_module)\nregister_monitoring_router = _monitoring_router_module.register_monitoring_router\n\n_INCIDENTS_ROUTER_PATH = Path(__file__).resolve().with_name('incidents_router.py')\n_incidents_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_incidents_router', _INCIDENTS_ROUTER_PATH)\nif _incidents_router_spec is None or _incidents_router_spec.loader is None:\n    raise ImportError(f'Could not load Incidents router: {_INCIDENTS_ROUTER_PATH}')\n_incidents_router_module = _realm_support_importlib_util.module_from_spec(_incidents_router_spec)\n_incidents_router_spec.loader.exec_module(_incidents_router_module)\nregister_incidents_router = _incidents_router_module.register_incidents_router\n\n'''
assert loader_anchor in text
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)
APP.write_text(text)

(ROOT / 'docs').mkdir(exist_ok=True)
audit_lines = [
    '# DARK NOC Backend Architecture Audit — v2.9.29',
    '',
    f'- Pre-split FastAPI/WS route count in `hub/app.py`: **{len(routes)}**.',
    '- Repository and write-service boundaries are established for Nodes/Tunnels, Plugin Deployments, Monitoring/Incidents, Certificates and Fleet Operations.',
    '- The next low-risk architectural boundary is route registration.',
    '- v2.9.29 starts that phase with Monitoring and Incident APIRouters while keeping telemetry automation in `hub/app.py`.',
    '',
    '## Route-family inventory before v2.9.29',
    '',
]
for family, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
    audit_lines.append(f'- **{family}:** {count}')
audit_lines += [
    '',
    '## Remaining high-value splits',
    '',
    '1. Nodes + Tunnels route registration.',
    '2. Plugin Deployments + Certificates + Fleet Operations route registration.',
    '3. Dashboard/System/Auth routes.',
    '4. SSH/File Transfer routes (higher risk because of streaming/WebSocket lifecycle).',
    '5. Agent control-plane routes (highest risk; keep telemetry/job-result state machine intact until last).',
    '',
    '## Invariants',
    '',
    '- No API path or payload contract changes during route extraction.',
    '- Agent heartbeat/job-result behavior remains in the Hub core until dedicated regression coverage is expanded.',
    '- Live Matrix visuals and frontend behavior remain outside backend router modularization.',
]
(ROOT / 'docs/backend-architecture-audit-v2.9.29.md').write_text('\n'.join(audit_lines) + '\n')

version_files = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh',
    ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html',
]
for path in version_files:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))
for path in (ROOT / 'tests').glob('*.py'):
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

notes = '''# DARK NOC v2.9.29 — Backend Router Split: Monitoring & Incidents\n\n- Complete a backend architecture audit after the Repository + Service modularization wave.\n- Start route-registration modularization by extracting Monitoring routes into `hub/monitoring_router.py` and Incident routes into `hub/incidents_router.py`.\n- Use FastAPI `APIRouter` modules with explicit dependency injection while preserving API paths, request schemas, HTTP status/details and audit behavior.\n- Keep telemetry-driven Incident automation, Agent heartbeat/job-result state machines, SSH/File Transfer, Live WebSocket and frontend visuals unchanged.\n- Add a permanent backend router-boundary regression contract and architecture audit document.\n\n## فارسی\n\nAudit نهایی بک‌اند انجام شد و مرحله Routerها شروع شد: Routeهای Monitoring و Incident از `hub/app.py` به Routerهای مستقل منتقل شدند، بدون تغییر API، Agent یا ظاهر پنل.\n\n---\n\n'''
release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text(notes + release_notes.read_text())
changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('## v2.9.29\n- Backend architecture audit and first FastAPI router split for Monitoring and Incidents.\n\n' + changelog.read_text())

contract = r'''from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
monitoring = (root / "hub/monitoring_router.py").read_text()
incidents = (root / "hub/incidents_router.py").read_text()
audit = (root / "docs/backend-architecture-audit-v2.9.29.md").read_text()

assert 'register_monitoring_router' in app
assert 'register_incidents_router' in app
assert '@app.get("/api/monitors")' not in app
assert '@app.get("/api/incidents")' not in app
assert 'APIRouter' in monitoring and 'APIRouter' in incidents
for path in [
    '/api/monitors', '/api/monitors/{monitor_id}', '/api/monitors/{monitor_id}/run',
    '/api/monitors/{monitor_id}/results',
]:
    assert path in monitoring, path
for path in ['/api/incidents', '/api/incidents/{incident_id}', '/api/incidents/{incident_id}/notes', '/api/incidents/{incident_id}/action']:
    assert path in incidents, path
assert 'def agent_heartbeat' in app
assert 'def agent_job_result' in app
assert 'create_incident(' in app and 'resolve_incident(' in app
assert 'Remaining high-value splits' in audit
print('backend router contract ok')
'''
(ROOT / 'tests/backend_router_contract.py').write_text(contract)
