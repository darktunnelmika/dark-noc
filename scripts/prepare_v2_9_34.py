from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
SYSTEM = ROOT / 'hub/system_router.py'
text = APP.read_text()
old_version = '2.9.33'
new_version = '2.9.34'
job_names = ['get_job', 'list_jobs']
system_names = ['system_status']


def route_blocks(source: str, names: list[str]) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(True)
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
            block = ''.join(lines[start:node.end_lineno]).rstrip() + '\n'
            if '@app.' not in block:
                raise RuntimeError(f'{node.name} is not an app route')
            found[node.name] = block
    missing = sorted(set(names) - set(found))
    if missing:
        raise RuntimeError(f'Missing routes: {missing}')
    return found


jobs = route_blocks(text, job_names)
system_routes = route_blocks(text, system_names)
body = '\n\n'.join(jobs[name].replace('@app.', '@router.').rstrip() for name in job_names)
body = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
(ROOT / 'hub/jobs_router.py').write_text(f'''import sqlite3\nfrom fastapi import APIRouter, Depends, HTTPException\n\n\ndef register_jobs_router(app, **deps):\n    router = APIRouter()\n    current_user = deps["current_user"]\n    db = deps["db"]\n    public_job = deps["public_job"]\n\n{body}\n\n    handlers = {{"get_job": get_job, "list_jobs": list_jobs}}\n    app.include_router(router)\n    return router, handlers\n''')

system_text = SYSTEM.read_text()
system_text = system_text.replace('from fastapi import APIRouter, HTTPException', 'import sqlite3\nfrom fastapi import APIRouter, Depends, HTTPException', 1)
anchor = '    HUB_INSTANCE_ID = deps["hub_instance_id"]\n'
extra = (
    '    current_user = deps["current_user"]\n'
    '    METRIC_RAW_RETENTION_DAYS = deps["metric_raw_retention_days"]\n'
    '    METRIC_ROLLUP_RETENTION_DAYS = deps["metric_rollup_retention_days"]\n'
    '    TUNNEL_SAMPLE_RETENTION_DAYS = deps["tunnel_sample_retention_days"]\n'
    '    MONITOR_RESULT_RETENTION_DAYS = deps["monitor_result_retention_days"]\n'
)
if anchor not in system_text:
    raise RuntimeError('system dependency anchor missing')
system_text = system_text.replace(anchor, anchor + extra, 1)
status = system_routes['system_status'].replace('@app.', '@router.').rstrip()
status = '\n'.join(('    ' + line if line else '') for line in status.splitlines())
inc = '\n    app.include_router(router)\n'
if inc not in system_text:
    raise RuntimeError('system include anchor missing')
system_text = system_text.replace(inc, '\n' + status + '\n' + inc, 1)
SYSTEM.write_text(system_text)

# Remove old route declarations, replacing the first generic Job route with registration.
tree = ast.parse(text)
lines = text.splitlines(True)
targets = set(job_names + system_names)
ranges = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
        start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
        end = node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end, node.name))
first_job = min((r for r in ranges if r[2] in job_names), key=lambda r: r[0])
registration = '''_jobs_router, _job_handlers = register_jobs_router(\n    app, current_user=current_user, db=db, public_job=public_job,\n)\nfor _job_handler_name, _job_handler in _job_handlers.items():\n    globals()[_job_handler_name] = _job_handler\ndel _job_handler_name, _job_handler, _job_handlers\n\n\n'''
for start, end, name in sorted(ranges, reverse=True):
    if (start, end, name) == first_job:
        lines[start:end] = [registration]
    else:
        del lines[start:end]
text = ''.join(lines)

loader_anchor = "ROOT = Path(__file__).resolve().parent\n"
loader = '''_JOBS_ROUTER_PATH = Path(__file__).resolve().with_name('jobs_router.py')\n_jobs_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_jobs_router', _JOBS_ROUTER_PATH)\nif _jobs_router_spec is None or _jobs_router_spec.loader is None:\n    raise ImportError(f'Could not load Jobs router: {_JOBS_ROUTER_PATH}')\n_jobs_router_module = _realm_support_importlib_util.module_from_spec(_jobs_router_spec)\n_jobs_router_spec.loader.exec_module(_jobs_router_module)\nregister_jobs_router = _jobs_router_module.register_jobs_router\n\n'''
if loader_anchor not in text:
    raise RuntimeError('jobs loader anchor missing')
text = text.replace(loader_anchor, loader + loader_anchor, 1)
old_reg = '''register_system_router(\n    app,\n    db=db, version=VERSION, key_path=KEY_PATH, static_dir=STATIC_DIR, hub_instance_id=HUB_INSTANCE_ID,\n)'''
new_reg = '''register_system_router(\n    app,\n    db=db, current_user=current_user, version=VERSION, key_path=KEY_PATH, static_dir=STATIC_DIR,\n    hub_instance_id=HUB_INSTANCE_ID, metric_raw_retention_days=METRIC_RAW_RETENTION_DAYS,\n    metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,\n    tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,\n    monitor_result_retention_days=MONITOR_RESULT_RETENTION_DAYS,\n)'''
if old_reg not in text:
    raise RuntimeError('system registration block missing')
APP.write_text(text.replace(old_reg, new_reg, 1))

contract = ROOT / 'tests/backend_jobs_system_router_contract.py'
contract.write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\njobs = (root / "hub/jobs_router.py").read_text()\nsystem = (root / "hub/system_router.py").read_text()\nassert "with_name('jobs_router.py')" in app and 'register_jobs_router(' in app\nassert '@app.get("/api/jobs/{job_id}")' not in app and '@app.get("/api/jobs")' not in app\nassert '@app.get("/api/system/status")' not in app\nassert '/api/jobs/{job_id}' in jobs and '/api/jobs' in jobs and 'public_job(row)' in jobs\nassert '/api/system/status' in system and 'Depends(current_user)' in system\nfor marker in ['METRIC_RAW_RETENTION_DAYS','METRIC_ROLLUP_RETENTION_DAYS','TUNNEL_SAMPLE_RETENTION_DAYS','MONITOR_RESULT_RETENTION_DAYS']:\n    assert marker in system, marker\nassert '@app.get("/api/agent/jobs")' in app\nassert '@app.post("/api/agent/jobs/result")' in app\nassert '@app.post("/api/agent/jobs/{job_id}/lease")' in app\nassert 'def agent_heartbeat' in app and '@app.websocket("/ws/live")' in app\nassert 'globals()[_job_handler_name] = _job_handler' in app\nassert 'VERSION = "2.9.34"' in app\nprint('Generic Jobs/System status router boundary passed')\n''')

router_contract = ROOT / 'tests/backend_router_contract.py'
rt = router_contract.read_text()
rt = rt.replace("    'ssh_terminal': (root / \"hub/ssh_terminal_router.py\").read_text(),\n", "    'ssh_terminal': (root / \"hub/ssh_terminal_router.py\").read_text(),\n    'jobs': (root / \"hub/jobs_router.py\").read_text(),\n", 1)
rt = rt.replace("    'register_ssh_file_router', 'register_ssh_terminal_router',\n", "    'register_ssh_file_router', 'register_ssh_terminal_router', 'register_jobs_router',\n", 1)
rt = rt.replace("    '@app.post(\"/api/ssh/relay\")', '@app.websocket(\"/ws/ssh/{node_id}\")',\n", "    '@app.post(\"/api/ssh/relay\")', '@app.websocket(\"/ws/ssh/{node_id}\")',\n    '@app.get(\"/api/jobs\")', '@app.get(\"/api/system/status\")',\n", 1)
router_contract.write_text(rt)

sys_contract = ROOT / 'tests/backend_auth_dashboard_system_router_contract.py'
st = sys_contract.read_text()
st = st.replace("for path in ['/healthz', '/readyz', '\"/\"']:\n", "for path in ['/healthz', '/readyz', '\"/\"', '/api/system/status']:\n", 1)
st = st.replace("assert 'Hub is not ready' in system and 'FileResponse(STATIC_DIR / \"index.html\")' in system\n", "assert 'Hub is not ready' in system and 'FileResponse(STATIC_DIR / \"index.html\")' in system\nassert 'Depends(current_user)' in system and 'metric_raw_retention_days' in system\n", 1)
sys_contract.write_text(st)

(ROOT / 'docs/backend-router-progress-v2.9.34.md').write_text('''# DARK NOC Backend Router Progress — v2.9.34\n\nGeneric `/api/jobs` reads now live in `hub/jobs_router.py`, and authenticated `/api/system/status` now lives in `hub/system_router.py`. Agent `/api/agent/jobs`, heartbeat, job-result/lease state machines and `/ws/live` intentionally remain in `hub/app.py` for the final high-risk Agent Control Plane phase. No frontend or Live Matrix visual changes are included.\n''')

version_paths = [ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html'] + sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

notes = ROOT / 'RELEASE_NOTES.md'
notes.write_text('''# DARK NOC v2.9.34 — Backend Router Split: Generic Jobs & System Status\n\n- Extract authenticated Generic Job read routes into `hub/jobs_router.py`.\n- Move `/api/system/status` into `hub/system_router.py`.\n- Preserve job payload redaction, response limits, retention/status counters and API contracts.\n- Keep Agent job queue/result/lease, heartbeat state machine and Live WebSocket in `hub/app.py` for the final control-plane phase.\n- Add permanent Generic Jobs/System Status router-boundary regression coverage.\n\n## فارسی\n\nRouteهای عمومی Job و `/api/system/status` از `hub/app.py` جدا شدند؛ Agent job state machine، heartbeat و Live WebSocket بدون تغییر باقی مانده‌اند.\n\n---\n\n''' + notes.read_text())
changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.34\n- Split Generic Job reads into `jobs_router.py` and move authenticated System Status into `system_router.py`.\n\n''' + changelog.read_text())

ci = ROOT / '.github/workflows/ci.yml'
ct = ci.read_text()
needle = '          python tests/backend_ssh_router_contract.py\n'
if needle not in ct:
    raise RuntimeError('CI SSH contract anchor missing')
ci.write_text(ct.replace(needle, needle + '          python tests/backend_jobs_system_router_contract.py\n', 1))

updated = APP.read_text()
for name in job_names + system_names:
    if f'def {name}(' in updated:
        raise RuntimeError(f'{name} unexpectedly remains in app.py')
print('Prepared DARK NOC v2.9.34 Generic Jobs/System Status router split')
