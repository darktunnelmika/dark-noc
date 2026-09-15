from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.34'
new_version = '2.9.35'

agent_names = [
    'local_agent_enroll', 'agent_pulse', 'agent_heartbeat',
    'agent_jobs', 'agent_job_result', 'renew_agent_job_lease',
]
live_names = ['live_updates']


def route_blocks(source: str, names: list[str]) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(True)
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([item.lineno for item in node.decorator_list] + [node.lineno]) - 1
            end = node.end_lineno
            block = ''.join(lines[start:end]).rstrip() + '\n'
            if '@app.' not in block:
                raise RuntimeError(f'{node.name} is not an app route')
            found[node.name] = block
    missing = sorted(set(names) - set(found))
    if missing:
        raise RuntimeError(f'Missing route functions: {missing}')
    return found


agent = route_blocks(text, agent_names)
live = route_blocks(text, live_names)


def apply_replacements(block: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        block = block.replace(old, new)
    return block


def render_router(register_name: str, blocks: list[str], aliases: list[str], imports: str, export_names: list[str]) -> str:
    body = '\n\n'.join(block.replace('@app.', '@router.').rstrip() for block in blocks)
    indented = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    exports = ',\n'.join(f'        "{name}": {name}' for name in export_names)
    return f'''{imports}\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}\n\n{indented}\n\n    handlers = {{\n{exports}\n    }}\n    app.include_router(router)\n    return router, handlers\n'''


agent_replacements = [
    ('agent_inventory_flags(', 'get_agent_inventory_flags()('),
    ('managed_tunnel_report(', 'get_managed_tunnel_report()('),
    ('create_incident(', 'get_create_incident()('),
    ('resolve_incident(', 'get_resolve_incident()('),
    ('finalize_fleet_operation(', 'get_finalize_fleet_operation()('),
    ('telegram_notify(', 'get_telegram_notify()('),
    ('telegram_notify,', 'get_telegram_notify(),'),
    ('LIVE_CLIENTS', 'get_live_clients()'),
]
agent_blocks = [apply_replacements(agent[name], agent_replacements) for name in agent_names]
agent_aliases = [
    'AgentPulse', 'AgentReport', 'JobResult', 'agent_node', 'db', 'utc_ts', 'token_hash',
    'get_agent_inventory_flags', 'get_managed_tunnel_report', 'get_create_incident',
    'get_resolve_incident', 'get_finalize_fleet_operation', 'get_telegram_notify',
    'get_live_clients',
]
agent_src = render_router(
    'agent_control',
    agent_blocks,
    agent_aliases,
    'import asyncio\nimport hmac\nimport json\nimport os\nimport secrets\nimport socket\nimport sqlite3\n\nfrom fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket',
    agent_names,
)
(ROOT / 'hub/agent_control_router.py').write_text(agent_src)

live_block = apply_replacements(
    live['live_updates'],
    [
        ('SESSION_TTL', 'get_session_ttl()'),
        ('LIVE_CLIENTS', 'get_live_clients()'),
    ],
)
live_aliases = [
    'websocket_user', 'token_hash', 'utc_ts', 'websocket_session_guard',
    'get_session_ttl', 'get_live_clients',
]
live_src = render_router(
    'live',
    [live_block],
    live_aliases,
    'import asyncio\nimport json\n\nfrom fastapi import APIRouter, WebSocket, WebSocketDisconnect',
    live_names,
)
(ROOT / 'hub/live_router.py').write_text(live_src)

# Remove only route declarations. Auth dependencies, incident/tunnel helpers,
# local DB ownership and websocket session guards remain in app.py.
tree = ast.parse(text)
lines = text.splitlines(True)
targets = set(agent_names + live_names)
ranges: list[tuple[int, int]] = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
        start = min([item.lineno for item in node.decorator_list] + [node.lineno]) - 1
        end = node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end))
for start, end in sorted(ranges, reverse=True):
    del lines[start:end]
text = ''.join(lines)

loader_anchor = "ROOT = Path(__file__).resolve().parent\n"
router_loader = '''_AGENT_CONTROL_ROUTER_PATH = Path(__file__).resolve().with_name('agent_control_router.py')\n_agent_control_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_agent_control_router', _AGENT_CONTROL_ROUTER_PATH)\nif _agent_control_router_spec is None or _agent_control_router_spec.loader is None:\n    raise ImportError(f'Could not load Agent Control router: {_AGENT_CONTROL_ROUTER_PATH}')\n_agent_control_router_module = _realm_support_importlib_util.module_from_spec(_agent_control_router_spec)\n_agent_control_router_spec.loader.exec_module(_agent_control_router_module)\nregister_agent_control_router = _agent_control_router_module.register_agent_control_router\n\n_LIVE_ROUTER_PATH = Path(__file__).resolve().with_name('live_router.py')\n_live_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_live_router', _LIVE_ROUTER_PATH)\nif _live_router_spec is None or _live_router_spec.loader is None:\n    raise ImportError(f'Could not load Live router: {_LIVE_ROUTER_PATH}')\n_live_router_module = _realm_support_importlib_util.module_from_spec(_live_router_spec)\n_live_router_spec.loader.exec_module(_live_router_module)\nregister_live_router = _live_router_module.register_live_router\n\n'''
if loader_anchor not in text:
    raise RuntimeError('Router loader anchor not found')
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)

agent_anchor = 'async def websocket_user(websocket: WebSocket) -> sqlite3.Row | None:\n'
if agent_anchor not in text:
    raise RuntimeError('websocket_user anchor not found')
agent_registration = '''_agent_control_router, _agent_control_handlers = register_agent_control_router(\n    app,\n    AgentPulse=AgentPulse, AgentReport=AgentReport, JobResult=JobResult, agent_node=agent_node,\n    db=db, utc_ts=utc_ts, token_hash=token_hash,\n    get_agent_inventory_flags=lambda: agent_inventory_flags,\n    get_managed_tunnel_report=lambda: managed_tunnel_report,\n    get_create_incident=lambda: create_incident, get_resolve_incident=lambda: resolve_incident,\n    get_finalize_fleet_operation=lambda: finalize_fleet_operation,\n    get_telegram_notify=lambda: telegram_notify, get_live_clients=lambda: LIVE_CLIENTS,\n)\nfor _agent_control_name, _agent_control_handler in _agent_control_handlers.items():\n    globals()[_agent_control_name] = _agent_control_handler\ndel _agent_control_name, _agent_control_handler, _agent_control_handlers\n\n\n'''
text = text.replace(agent_anchor, agent_registration + agent_anchor, 1)

live_anchor = 'app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")\n'
if live_anchor not in text:
    raise RuntimeError('static mount anchor not found')
live_registration = '''_live_router, _live_handlers = register_live_router(\n    app,\n    websocket_user=websocket_user, token_hash=token_hash, utc_ts=utc_ts,\n    websocket_session_guard=websocket_session_guard, get_session_ttl=lambda: SESSION_TTL,\n    get_live_clients=lambda: LIVE_CLIENTS,\n)\nfor _live_name, _live_handler in _live_handlers.items():\n    globals()[_live_name] = _live_handler\ndel _live_name, _live_handler, _live_handlers\n\n\n'''
text = text.replace(live_anchor, live_registration + live_anchor, 1)
APP.write_text(text)

for path in sorted((ROOT / 'tests').glob('*.py')):
    data = path.read_text()
    data = data.replace("'def agent_heartbeat' in app", "\"with_name('agent_control_router.py')\" in app")
    data = data.replace("'def agent_job_result' in app", "\"with_name('agent_control_router.py')\" in app")
    data = data.replace("'@app.websocket(\\\"/ws/live\\\")' in app", "\"with_name('live_router.py')\" in app")
    data = data.replace("'@app.websocket(\"/ws/live\")' in app", "\"with_name('live_router.py')\" in app")
    path.write_text(data)

contract = ROOT / 'tests/backend_agent_control_router_contract.py'
contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nagent = (root / "hub/agent_control_router.py").read_text()\nlive = (root / "hub/live_router.py").read_text()\n\nassert "with_name('agent_control_router.py')" in app\nassert "with_name('live_router.py')" in app\nassert 'register_agent_control_router(' in app\nassert 'register_live_router(' in app\nfor marker in [\n    '@app.post("/api/agent/local-enroll")', '@app.post("/api/agent/pulse")',\n    '@app.post("/api/agent/heartbeat")', '@app.get("/api/agent/jobs")',\n    '@app.post("/api/agent/jobs/result")', '@app.post("/api/agent/jobs/{job_id}/lease")',\n    '@app.websocket("/ws/live")',\n]:\n    assert marker not in app, marker\nfor path in [\n    '/api/agent/local-enroll', '/api/agent/pulse', '/api/agent/heartbeat', '/api/agent/jobs',\n    '/api/agent/jobs/result', '/api/agent/jobs/{job_id}/lease',\n]:\n    assert path in agent, path\nassert '/ws/live' in live\nfor marker in [\n    'get_agent_inventory_flags()(', 'get_managed_tunnel_report()(', 'get_create_incident()(',\n    'get_resolve_incident()(', 'get_finalize_fleet_operation()(', 'get_telegram_notify()',\n    'get_live_clients()',\n]:\n    assert marker in agent, marker\nassert 'get_session_ttl()' in live and 'get_live_clients()' in live\nfor helper in [\n    'def agent_node(', 'def agent_inventory_flags(', 'def managed_tunnel_report(',\n    'def create_incident(', 'def resolve_incident(', 'def finalize_fleet_operation(',\n    'async def websocket_user(', 'async def websocket_session_guard(',\n]:\n    assert helper in app, helper\nassert 'globals()[_agent_control_name] = _agent_control_handler' in app\nassert 'globals()[_live_name] = _live_handler' in app\nassert 'VERSION = "2.9.35"' in app\nprint('Agent Control/Live router boundary passed')\n''')

router_contract = ROOT / 'tests/backend_router_contract.py'
router_contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nrouter_paths = [\n    'monitoring_router.py', 'incidents_router.py', 'nodes_router.py', 'tunnels_router.py',\n    'plugin_deployments_router.py', 'certificates_router.py', 'fleet_router.py',\n    'auth_router.py', 'dashboard_router.py', 'system_router.py', 'jobs_router.py',\n    'ssh_file_router.py', 'ssh_terminal_router.py', 'agent_control_router.py', 'live_router.py',\n]\nfor filename in router_paths:\n    owner = (root / 'hub' / filename).read_text()\n    assert 'APIRouter' in owner, filename\n    assert f"with_name('{filename}')" in app, filename\nfor marker in [\n    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',\n    '@app.get("/api/plugins")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',\n    '@app.post("/api/auth/login")', '@app.get("/api/dashboard")', '@app.get("/healthz")',\n    '@app.get("/api/jobs")', '@app.post("/api/ssh/relay")', '@app.websocket("/ws/ssh/{node_id}")',\n    '@app.post("/api/agent/heartbeat")', '@app.get("/api/agent/jobs")', '@app.websocket("/ws/live")',\n]:\n    assert marker not in app, marker\nassert 'def agent_node(' in app\nassert 'def create_incident(' in app and 'def resolve_incident(' in app\nassert 'async def websocket_session_guard(' in app\nprint('backend router contract ok')\n''')

progress = ROOT / 'docs/backend-router-progress-v2.9.35.md'
progress.write_text('''# DARK NOC Backend Router Progress — v2.9.35\n\nThe backend route-registration modularization wave is now complete for the main HTTP/WebSocket surfaces.\n\nNew ownership in this release:\n- `hub/agent_control_router.py`: local Agent enrollment, liveness pulse, full heartbeat, Agent job polling, terminal result submission and lease renewal.\n- `hub/live_router.py`: authenticated `/ws/live` telemetry WebSocket.\n\nShared state machines and primitives remain centralized in `hub/app.py`: Agent auth, incident transitions, managed tunnel filtering, Fleet finalization, WebSocket session validation, lifecycle/background loops and shared live-client registry.\n\nNo frontend or Live Matrix visual behavior changed.\n''')

version_paths = [ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html']
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.35 — Agent Control Plane & Live Router Split\n\n- Extract local Agent enrollment, liveness pulse, full heartbeat and Agent job poll/result/lease routes into `hub/agent_control_router.py`.\n- Extract authenticated `/ws/live` telemetry WebSocket into `hub/live_router.py`.\n- Preserve Agent authentication, inventory freshness semantics, Incident/Monitor/Fleet/Plugin result transitions, heartbeat broadcasts, local enrollment recovery and job idempotency.\n- Preserve runtime callback/live-client lookup so shared state and test/runtime overrides remain compatible.\n- Keep shared Agent/WebSocket state-machine primitives and Hub lifecycle loops centralized in `hub/app.py`.\n- Add permanent Agent Control Plane/Live router-boundary regression coverage.\n- No frontend or Live Matrix visual changes.\n\n## فارسی\n\nRouteهای Agent Control Plane شامل enrollment، pulse، heartbeat، job poll/result/lease و WebSocket زنده `/ws/live` به Routerهای مستقل منتقل شدند؛ منطق state machine، Incident/Fleet/Plugin، Session و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.35\n- Split Agent Control Plane HTTP routes and the Live telemetry WebSocket into dedicated APIRouter modules while preserving shared state-machine ownership.\n\n''' + changelog.read_text())

ci = ROOT / '.github/workflows/ci.yml'
ci_text = ci.read_text()
needle = '          python tests/backend_jobs_system_router_contract.py\n'
if needle not in ci_text:
    raise RuntimeError('CI jobs/system contract anchor not found')
ci.write_text(ci_text.replace(needle, needle + '          python tests/backend_agent_control_router_contract.py\n', 1))

updated = APP.read_text()
for name in agent_names + live_names:
    if f'def {name}(' in updated or f'async def {name}(' in updated:
        raise RuntimeError(f'{name} unexpectedly remains defined in app.py')
for required in ['register_agent_control_router(', 'register_live_router(']:
    if required not in updated:
        raise RuntimeError(f'Missing registration: {required}')
print('prepared v2.9.35 Agent Control Plane + Live router split')
