from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.31'
new_version = '2.9.32'

auth_names = ['login', 'logout', 'me', 'update_account']
dashboard_names = ['dashboard', 'dashboard_traffic']
system_names = ['healthz', 'readyz', 'index']


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


auth = route_blocks(text, auth_names)
dashboard = route_blocks(text, dashboard_names)
system = route_blocks(text, system_names)


def render_router(register_name: str, blocks: list[str], aliases: list[str], imports: str, extras: list[str] | None = None) -> str:
    body = '\n\n'.join(block.replace('@app.', '@router.').rstrip() for block in blocks)
    indented = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    extra_lines = '\n'.join(f'    {line}' for line in (extras or []))
    if extra_lines:
        extra_lines = '\n' + extra_lines
    return f'''{imports}\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}{extra_lines}\n\n{indented}\n\n    app.include_router(router)\n    return router\n'''


auth_aliases = [
    'LoginBody', 'current_user', 'db', 'login_rate_check', 'login_rate_record',
    'verify_password', 'audit', 'token_hash', 'utc_ts',
]
auth_src = render_router(
    'auth',
    [auth[name] for name in auth_names],
    auth_aliases,
    'import os\nimport secrets\nimport sqlite3\nfrom fastapi import APIRouter, Cookie, Depends, HTTPException, Request\nfrom fastapi.responses import JSONResponse',
    ['SESSION_TTL = deps["session_ttl"]'],
)
(ROOT / 'hub/auth_router.py').write_text(auth_src)

dashboard_aliases = ['current_user', 'db', 'utc_ts']
dashboard_src = render_router(
    'dashboard',
    [dashboard[name] for name in dashboard_names],
    dashboard_aliases,
    'import sqlite3\nfrom fastapi import APIRouter, Depends',
    [
        'NODE_STALE_AFTER = deps["node_stale_after"]',
        'VERSION = deps["version"]',
        'SSH_UPLOAD_LIMIT = deps["ssh_upload_limit"]',
        'SSH_RELAY_LIMIT = deps["ssh_relay_limit"]',
        'SSH_UPLOAD_QUEUE_TIMEOUT = deps["ssh_upload_queue_timeout"]',
    ],
)
(ROOT / 'hub/dashboard_router.py').write_text(dashboard_src)

system_aliases = ['db']
system_src = render_router(
    'system',
    [system[name] for name in system_names],
    system_aliases,
    'from fastapi import APIRouter, HTTPException\nfrom fastapi.responses import FileResponse',
    [
        'VERSION = deps["version"]',
        'KEY_PATH = deps["key_path"]',
        'STATIC_DIR = deps["static_dir"]',
        'HUB_INSTANCE_ID = deps["hub_instance_id"]',
    ],
)
(ROOT / 'hub/system_router.py').write_text(system_src)

# Remove only the route declarations; keep auth rate helpers, session dependency,
# middleware, static mount and all runtime state machines in app.py.
tree = ast.parse(text)
lines = text.splitlines(True)
targets = set(auth_names + dashboard_names + system_names)
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
router_loader = '''_AUTH_ROUTER_PATH = Path(__file__).resolve().with_name('auth_router.py')\n_auth_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_auth_router', _AUTH_ROUTER_PATH)\nif _auth_router_spec is None or _auth_router_spec.loader is None:\n    raise ImportError(f'Could not load Auth router: {_AUTH_ROUTER_PATH}')\n_auth_router_module = _realm_support_importlib_util.module_from_spec(_auth_router_spec)\n_auth_router_spec.loader.exec_module(_auth_router_module)\nregister_auth_router = _auth_router_module.register_auth_router\n\n_DASHBOARD_ROUTER_PATH = Path(__file__).resolve().with_name('dashboard_router.py')\n_dashboard_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_dashboard_router', _DASHBOARD_ROUTER_PATH)\nif _dashboard_router_spec is None or _dashboard_router_spec.loader is None:\n    raise ImportError(f'Could not load Dashboard router: {_DASHBOARD_ROUTER_PATH}')\n_dashboard_router_module = _realm_support_importlib_util.module_from_spec(_dashboard_router_spec)\n_dashboard_router_spec.loader.exec_module(_dashboard_router_module)\nregister_dashboard_router = _dashboard_router_module.register_dashboard_router\n\n_SYSTEM_ROUTER_PATH = Path(__file__).resolve().with_name('system_router.py')\n_system_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_system_router', _SYSTEM_ROUTER_PATH)\nif _system_router_spec is None or _system_router_spec.loader is None:\n    raise ImportError(f'Could not load System router: {_SYSTEM_ROUTER_PATH}')\n_system_router_module = _realm_support_importlib_util.module_from_spec(_system_router_spec)\n_system_router_spec.loader.exec_module(_system_router_module)\nregister_system_router = _system_router_module.register_system_router\n\n'''
if loader_anchor not in text:
    raise RuntimeError('Router loader anchor not found')
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)

# Register at the original Auth/Dashboard boundary after all auth helpers and
# dependencies exist. System routes are path-independent, so moving registration
# here does not alter static mounting or the Live WebSocket lifecycle.
register_anchor = 'def ssh_file_node(node_id: int) -> tuple[sqlite3.Row, str | None, str | None]:\n'
if register_anchor not in text:
    raise RuntimeError('SSH helper registration anchor not found')
registration = '''register_auth_router(\n    app,\n    LoginBody=LoginBody, current_user=current_user, db=db, login_rate_check=login_rate_check,\n    login_rate_record=login_rate_record, verify_password=verify_password, audit=audit, token_hash=token_hash,\n    utc_ts=utc_ts, session_ttl=SESSION_TTL,\n)\n\nregister_dashboard_router(\n    app,\n    current_user=current_user, db=db, utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER, version=VERSION,\n    ssh_upload_limit=SSH_UPLOAD_LIMIT, ssh_relay_limit=SSH_RELAY_LIMIT,\n    ssh_upload_queue_timeout=SSH_UPLOAD_QUEUE_TIMEOUT,\n)\n\nregister_system_router(\n    app,\n    db=db, version=VERSION, key_path=KEY_PATH, static_dir=STATIC_DIR, hub_instance_id=HUB_INSTANCE_ID,\n)\n\n\n'''
text = text.replace(register_anchor, registration + register_anchor, 1)
APP.write_text(text)

# Permanent router boundary contract.
contract = ROOT / 'tests/backend_auth_dashboard_system_router_contract.py'
contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nauth = (root / "hub/auth_router.py").read_text()\ndashboard = (root / "hub/dashboard_router.py").read_text()\nsystem = (root / "hub/system_router.py").read_text()\n\nfor name in ["auth", "dashboard", "system"]:\n    assert f"with_name('{name}_router.py')" in app\n    assert f"register_{name}_router" in app\nfor marker in [\n    '@app.post("/api/auth/login")', '@app.post("/api/auth/logout")', '@app.get("/api/auth/me")',\n    '@app.put("/api/auth/account")', '@app.get("/api/dashboard")', '@app.get("/api/dashboard/traffic")',\n    '@app.get("/healthz")', '@app.get("/readyz")', '@app.get("/")',\n]:\n    assert marker not in app, marker\nfor path in ['/api/auth/login', '/api/auth/logout', '/api/auth/me', '/api/auth/account']:\n    assert path in auth, path\nfor path in ['/api/dashboard', '/api/dashboard/traffic']:\n    assert path in dashboard, path\nfor path in ['/healthz', '/readyz', '"/"']:\n    assert path in system, path\nassert 'login_rate_check(client_ip)' in auth\nassert 'login_rate_record(client_ip, False)' in auth\nassert 'response.set_cookie("dark_noc_session"' in auth\nassert 'SSH_UPLOAD_LIMIT' in dashboard and 'open_incidents' in dashboard\nassert 'Hub is not ready' in system and 'FileResponse(STATIC_DIR / "index.html")' in system\nassert 'app.mount("/static"' in app\nassert '@app.websocket("/ws/live")' in app\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\nassert 'VERSION = "2.9.32"' in app\nprint('Auth/Dashboard/System router boundary passed')\n''')

# Extend aggregate router contract while leaving high-risk ownership in app.py.
router_contract = ROOT / 'tests/backend_router_contract.py'
router_contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nrouters = {\n    'monitoring': (root / "hub/monitoring_router.py").read_text(),\n    'incidents': (root / "hub/incidents_router.py").read_text(),\n    'nodes': (root / "hub/nodes_router.py").read_text(),\n    'tunnels': (root / "hub/tunnels_router.py").read_text(),\n    'plugins': (root / "hub/plugin_deployments_router.py").read_text(),\n    'certificates': (root / "hub/certificates_router.py").read_text(),\n    'fleet': (root / "hub/fleet_router.py").read_text(),\n    'auth': (root / "hub/auth_router.py").read_text(),\n    'dashboard': (root / "hub/dashboard_router.py").read_text(),\n    'system': (root / "hub/system_router.py").read_text(),\n}\nfor marker in [\n    'register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router',\n    'register_plugin_deployments_router', 'register_certificates_router', 'register_fleet_router',\n    'register_auth_router', 'register_dashboard_router', 'register_system_router',\n]:\n    assert marker in app, marker\nfor owner in routers.values():\n    assert 'APIRouter' in owner\nfor marker in [\n    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',\n    '@app.get("/api/plugins")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',\n    '@app.post("/api/auth/login")', '@app.get("/api/dashboard")', '@app.get("/healthz")',\n]:\n    assert marker not in app, marker\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\nassert '@app.post("/api/ssh/relay")' in app\nassert '@app.websocket("/ws/live")' in app\nassert 'create_incident(' in app and 'resolve_incident(' in app\nprint('backend router contract ok')\n''')

# Update progress documentation.
progress = ROOT / 'docs/backend-router-progress-v2.9.32.md'
progress.write_text('''# DARK NOC Backend Router Progress — v2.9.32\n\nCompleted router ownership: Monitoring, Incidents, Nodes, Tunnels, Plugin Deployments, Certificates, Fleet Operations, Authentication, Dashboard and System health/index.\n\nStill intentionally kept in `hub/app.py`:\n\n1. SSH/File Transfer — higher risk because of streaming, cancellation, multipart and SFTP rollback semantics.\n2. Agent control plane + job-result state machine — highest risk and should remain last.\n3. Generic Job read endpoints and Live WebSocket — keep with control-plane/runtime core until those final splits.\n\nNo frontend or Live Matrix visual behavior is part of this backend router phase.\n''')

# Version synchronization for runtime/installers/tests.
version_paths = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh',
    ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html',
]
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.32 — Backend Router Split: Auth, Dashboard & System\n\n- Continue backend route modularization by extracting Authentication routes into `hub/auth_router.py`.\n- Extract Dashboard routes into `hub/dashboard_router.py` and public health/readiness/index routes into `hub/system_router.py`.\n- Preserve login throttling, secure session cookie behavior, dashboard metrics/limits, health/readiness checks, static mounting, API paths and response contracts.\n- Keep SSH/File Transfer, Agent control plane, generic Job reads, Live WebSocket and all frontend/Live Matrix behavior unchanged.\n- Add permanent Auth/Dashboard/System router-boundary regression coverage.\n\n## فارسی\n\nRouteهای Auth، Dashboard و System health/index از `hub/app.py` به Routerهای مستقل منتقل شدند؛ Session، Dashboard، health check، SSH، Agent و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.32\n- Split Authentication, Dashboard and System health/index FastAPI route registration into dedicated APIRouter modules.\n\n''' + changelog.read_text())

# CI is changed in the working tree for verification but intentionally excluded
# from the Actions push; the connector applies it to the candidate branch later.
ci = ROOT / '.github/workflows/ci.yml'
ci_text = ci.read_text()
needle = '          python tests/backend_operations_router_contract.py\n'
if needle not in ci_text:
    raise RuntimeError('CI operations contract anchor not found')
ci.write_text(ci_text.replace(needle, needle + '          python tests/backend_auth_dashboard_system_router_contract.py\n', 1))

# Sanity check exact route ownership.
updated = APP.read_text()
for name in auth_names + dashboard_names + system_names:
    if f'def {name}(' in updated:
        raise RuntimeError(f'{name} unexpectedly remains in app.py')
for required in ['register_auth_router(', 'register_dashboard_router(', 'register_system_router(']:
    if required not in updated:
        raise RuntimeError(f'Missing registration: {required}')
