from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.32'
new_version = '2.9.33'

file_names = [
    'ssh_upload_file', 'ssh_relay_file', 'list_ssh_files', 'read_ssh_file',
    'write_ssh_file', 'ssh_file_action', 'checksum_ssh_file', 'download_ssh_file',
]
terminal_names = ['ssh_terminal']


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


files = route_blocks(text, file_names)
terminal = route_blocks(text, terminal_names)


def render_router(
    register_name: str,
    blocks: list[str],
    aliases: list[str],
    imports: str,
    export_names: list[str],
) -> str:
    body = '\n\n'.join(block.replace('@app.', '@router.').rstrip() for block in blocks)
    indented = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    exports = ',\n'.join(f'        "{name}": {name}' for name in export_names)
    return f'''{imports}\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}\n\n{indented}\n\n    handlers = {{\n{exports}\n    }}\n    app.include_router(router)\n    return router, handlers\n'''


file_runtime_replacements = {
    'SSH_UPLOAD_LIMIT': 'get_ssh_upload_limit()',
    'SSH_RELAY_LIMIT': 'get_ssh_relay_limit()',
    'SSH_TRANSFER_TIMEOUT': 'get_ssh_transfer_timeout()',
    'SSH_TRANSFER_IDLE_TIMEOUT': 'get_ssh_transfer_idle_timeout()',
    'SSH_TRANSFER_SEMAPHORE': 'get_ssh_transfer_semaphore()',
    'SSH_FILE_CHUNK': 'get_ssh_file_chunk()',
    'SSH_EDITOR_LIMIT': 'get_ssh_editor_limit()',
}
file_blocks: list[str] = []
for name in file_names:
    block = files[name]
    for token, replacement in file_runtime_replacements.items():
        block = block.replace(token, replacement)
    file_blocks.append(block)

file_aliases = [
    'current_user', 'ssh_file_node', 'clean_remote_path', 'destructive_remote_path_allowed',
    'disconnect_aware_semaphore', 'ssh_connection_options', 'sftp_path_exists',
    'ssh_destination_lock', 'finalize_sftp_file', 'cleanup_sftp_path', 'ssh_file_error',
    'audit', 'LOGGER', 'SSHRelayBody', 'clean_remote_directory', 'sftp_entry',
    'SSHFileWriteBody', 'SSHFileActionBody',
    'get_ssh_upload_limit', 'get_ssh_relay_limit', 'get_ssh_transfer_timeout',
    'get_ssh_transfer_idle_timeout', 'get_ssh_transfer_semaphore', 'get_ssh_file_chunk',
    'get_ssh_editor_limit',
]
file_src = render_router(
    'ssh_file',
    file_blocks,
    file_aliases,
    'import asyncio\nimport hashlib\nimport posixpath\nimport secrets\nimport sqlite3\nimport stat as statmod\n\nimport asyncssh\nfrom fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile\nfrom fastapi.responses import StreamingResponse',
    file_names,
)
(ROOT / 'hub/ssh_file_router.py').write_text(file_src)

terminal_block = terminal['ssh_terminal']
terminal_runtime_replacements = {
    'SESSION_TTL': 'get_session_ttl()',
    'SSH_KEEPALIVE_INTERVAL': 'get_ssh_keepalive_interval()',
    'SSH_KEEPALIVE_COUNT_MAX': 'get_ssh_keepalive_count_max()',
}
for token, replacement in terminal_runtime_replacements.items():
    terminal_block = terminal_block.replace(token, replacement)
terminal_aliases = [
    'websocket_user', 'db', 'decrypt', 'token_hash', 'utc_ts', 'audit',
    'websocket_session_valid', 'websocket_session_guard', 'LOGGER',
    'get_session_ttl', 'get_ssh_keepalive_interval', 'get_ssh_keepalive_count_max',
]
terminal_src = render_router(
    'ssh_terminal',
    [terminal_block],
    terminal_aliases,
    'import asyncio\nimport hmac\nimport re\nimport shlex\nfrom typing import Any\n\nimport asyncssh\nfrom fastapi import APIRouter, WebSocket, WebSocketDisconnect',
    terminal_names,
)
(ROOT / 'hub/ssh_terminal_router.py').write_text(terminal_src)

# Remove only route declarations. SSH/SFTP helpers, upload middleware, websocket
# session guards and Live WebSocket remain owned by app.py.
tree = ast.parse(text)
lines = text.splitlines(True)
targets = set(file_names + terminal_names)
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
router_loader = '''_SSH_FILE_ROUTER_PATH = Path(__file__).resolve().with_name('ssh_file_router.py')\n_ssh_file_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_ssh_file_router', _SSH_FILE_ROUTER_PATH)\nif _ssh_file_router_spec is None or _ssh_file_router_spec.loader is None:\n    raise ImportError(f'Could not load SSH File router: {_SSH_FILE_ROUTER_PATH}')\n_ssh_file_router_module = _realm_support_importlib_util.module_from_spec(_ssh_file_router_spec)\n_ssh_file_router_spec.loader.exec_module(_ssh_file_router_module)\nregister_ssh_file_router = _ssh_file_router_module.register_ssh_file_router\n\n_SSH_TERMINAL_ROUTER_PATH = Path(__file__).resolve().with_name('ssh_terminal_router.py')\n_ssh_terminal_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_ssh_terminal_router', _SSH_TERMINAL_ROUTER_PATH)\nif _ssh_terminal_router_spec is None or _ssh_terminal_router_spec.loader is None:\n    raise ImportError(f'Could not load SSH Terminal router: {_SSH_TERMINAL_ROUTER_PATH}')\n_ssh_terminal_router_module = _realm_support_importlib_util.module_from_spec(_ssh_terminal_router_spec)\n_ssh_terminal_router_spec.loader.exec_module(_ssh_terminal_router_module)\nregister_ssh_terminal_router = _ssh_terminal_router_module.register_ssh_terminal_router\n\n'''
if loader_anchor not in text:
    raise RuntimeError('Router loader anchor not found')
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)

# Register File Transfer only after all SFTP helpers including sftp_entry exist.
file_anchor = 'def tunnel_topology_side(node_role: str, method: str, tunnel_role: str) -> str:\n'
if file_anchor not in text:
    raise RuntimeError('Tunnel topology anchor not found')
file_registration = '''_ssh_file_router, _ssh_file_handlers = register_ssh_file_router(\n    app,\n    current_user=current_user, ssh_file_node=ssh_file_node, clean_remote_path=clean_remote_path,\n    destructive_remote_path_allowed=destructive_remote_path_allowed,\n    disconnect_aware_semaphore=disconnect_aware_semaphore, ssh_connection_options=ssh_connection_options,\n    sftp_path_exists=sftp_path_exists, ssh_destination_lock=ssh_destination_lock,\n    finalize_sftp_file=finalize_sftp_file, cleanup_sftp_path=cleanup_sftp_path, ssh_file_error=ssh_file_error,\n    audit=audit, LOGGER=LOGGER, SSHRelayBody=SSHRelayBody, clean_remote_directory=clean_remote_directory,\n    sftp_entry=sftp_entry, SSHFileWriteBody=SSHFileWriteBody, SSHFileActionBody=SSHFileActionBody,\n    get_ssh_upload_limit=lambda: SSH_UPLOAD_LIMIT, get_ssh_relay_limit=lambda: SSH_RELAY_LIMIT,\n    get_ssh_transfer_timeout=lambda: SSH_TRANSFER_TIMEOUT,\n    get_ssh_transfer_idle_timeout=lambda: SSH_TRANSFER_IDLE_TIMEOUT,\n    get_ssh_transfer_semaphore=lambda: SSH_TRANSFER_SEMAPHORE, get_ssh_file_chunk=lambda: SSH_FILE_CHUNK,\n    get_ssh_editor_limit=lambda: SSH_EDITOR_LIMIT,\n)\nfor _ssh_file_name, _ssh_file_handler in _ssh_file_handlers.items():\n    globals()[_ssh_file_name] = _ssh_file_handler\ndel _ssh_file_name, _ssh_file_handler, _ssh_file_handlers\n\n\n'''
text = text.replace(file_anchor, file_registration + file_anchor, 1)

# Register terminal after shared websocket auth/session guards exist, while the
# generic Live WebSocket stays in app.py.
terminal_anchor = '@app.websocket("/ws/live")\n'
if terminal_anchor not in text:
    raise RuntimeError('Live websocket anchor not found')
terminal_registration = '''_ssh_terminal_router, _ssh_terminal_handlers = register_ssh_terminal_router(\n    app,\n    websocket_user=websocket_user, db=db, decrypt=decrypt, token_hash=token_hash, utc_ts=utc_ts, audit=audit,\n    websocket_session_valid=websocket_session_valid, websocket_session_guard=websocket_session_guard, LOGGER=LOGGER,\n    get_session_ttl=lambda: SESSION_TTL, get_ssh_keepalive_interval=lambda: SSH_KEEPALIVE_INTERVAL,\n    get_ssh_keepalive_count_max=lambda: SSH_KEEPALIVE_COUNT_MAX,\n)\nfor _ssh_terminal_name, _ssh_terminal_handler in _ssh_terminal_handlers.items():\n    globals()[_ssh_terminal_name] = _ssh_terminal_handler\ndel _ssh_terminal_name, _ssh_terminal_handler, _ssh_terminal_handlers\n\n\n'''
text = text.replace(terminal_anchor, terminal_registration + terminal_anchor, 1)
APP.write_text(text)

# Permanent SSH router boundary contract.
contract = ROOT / 'tests/backend_ssh_router_contract.py'
contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nfiles = (root / "hub/ssh_file_router.py").read_text()\nterminal = (root / "hub/ssh_terminal_router.py").read_text()\n\nassert "with_name('ssh_file_router.py')" in app\nassert "with_name('ssh_terminal_router.py')" in app\nassert 'register_ssh_file_router(' in app\nassert 'register_ssh_terminal_router(' in app\nfor marker in [\n    '@app.post("/api/ssh/upload/{node_id}")', '@app.post("/api/ssh/relay")',\n    '@app.get("/api/ssh/files/{node_id}")', '@app.get("/api/ssh/files/{node_id}/read")',\n    '@app.put("/api/ssh/files/{node_id}/write")', '@app.post("/api/ssh/files/{node_id}/action")',\n    '@app.get("/api/ssh/files/{node_id}/checksum")', '@app.get("/api/ssh/files/{node_id}/download")',\n    '@app.websocket("/ws/ssh/{node_id}")',\n]:\n    assert marker not in app, marker\nfor path in [\n    '/api/ssh/upload/{node_id}', '/api/ssh/relay', '/api/ssh/files/{node_id}',\n    '/api/ssh/files/{node_id}/read', '/api/ssh/files/{node_id}/write',\n    '/api/ssh/files/{node_id}/action', '/api/ssh/files/{node_id}/checksum',\n    '/api/ssh/files/{node_id}/download',\n]:\n    assert path in files, path\nassert '/ws/ssh/{node_id}' in terminal\nfor marker in [\n    'get_ssh_upload_limit()', 'get_ssh_transfer_timeout()', 'get_ssh_transfer_semaphore()',\n    'get_ssh_file_chunk()', 'get_ssh_editor_limit()',\n]:\n    assert marker in files, marker\nfor marker in ['get_session_ttl()', 'get_ssh_keepalive_interval()', 'get_ssh_keepalive_count_max()']:\n    assert marker in terminal, marker\n# High-risk helpers and middleware remain centralized in app.py for this slice.\nfor helper in [\n    'def ssh_file_node(', 'async def finalize_sftp_file(', 'def ssh_file_error(',\n    'async def disconnect_aware_semaphore(', 'def ssh_connection_options(',\n    'async def websocket_session_guard(',\n]:\n    assert helper in app, helper\nassert 'request.url.path.startswith("/api/ssh/upload/")' in app\nassert '@app.websocket("/ws/live")' in app\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\n# Compatibility aliases are retained for regression tests and internal callers.\nassert 'globals()[_ssh_file_name] = _ssh_file_handler' in app\nassert 'globals()[_ssh_terminal_name] = _ssh_terminal_handler' in app\nassert 'VERSION = "2.9.33"' in app\nprint('SSH/File Transfer router boundary passed')\n''')

# Aggregate router contract now treats SSH routes as modular while keeping
# Agent control-plane and Live telemetry websocket in app.py.
router_contract = ROOT / 'tests/backend_router_contract.py'
router_contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nrouters = {\n    'monitoring': (root / "hub/monitoring_router.py").read_text(),\n    'incidents': (root / "hub/incidents_router.py").read_text(),\n    'nodes': (root / "hub/nodes_router.py").read_text(),\n    'tunnels': (root / "hub/tunnels_router.py").read_text(),\n    'plugins': (root / "hub/plugin_deployments_router.py").read_text(),\n    'certificates': (root / "hub/certificates_router.py").read_text(),\n    'fleet': (root / "hub/fleet_router.py").read_text(),\n    'auth': (root / "hub/auth_router.py").read_text(),\n    'dashboard': (root / "hub/dashboard_router.py").read_text(),\n    'system': (root / "hub/system_router.py").read_text(),\n    'ssh_files': (root / "hub/ssh_file_router.py").read_text(),\n    'ssh_terminal': (root / "hub/ssh_terminal_router.py").read_text(),\n}\nfor marker in [\n    'register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router',\n    'register_plugin_deployments_router', 'register_certificates_router', 'register_fleet_router',\n    'register_auth_router', 'register_dashboard_router', 'register_system_router',\n    'register_ssh_file_router', 'register_ssh_terminal_router',\n]:\n    assert marker in app, marker\nfor owner in routers.values():\n    assert 'APIRouter' in owner\nfor marker in [\n    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',\n    '@app.get("/api/plugins")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',\n    '@app.post("/api/auth/login")', '@app.get("/api/dashboard")', '@app.get("/healthz")',\n    '@app.post("/api/ssh/relay")', '@app.websocket("/ws/ssh/{node_id}")',\n]:\n    assert marker not in app, marker\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\nassert '@app.websocket("/ws/live")' in app\nassert 'create_incident(' in app and 'resolve_incident(' in app\nprint('backend router contract ok')\n''')

progress = ROOT / 'docs/backend-router-progress-v2.9.33.md'
progress.write_text('''# DARK NOC Backend Router Progress — v2.9.33\n\nCompleted router ownership now includes SSH File Transfer/File Manager and the interactive SSH terminal WebSocket, in addition to Monitoring, Incidents, Nodes, Tunnels, Deployments, Certificates, Fleet, Auth, Dashboard and System health/index.\n\nStill intentionally kept in `hub/app.py`:\n\n1. Agent control plane + job-result state machine — highest risk and remains last.\n2. Generic Job read endpoints and Live telemetry WebSocket — keep with control-plane/runtime core until the Agent split.\n3. `/api/system/status` — small residual system route to fold in during final backend cleanup.\n4. SSH/SFTP helper primitives and upload middleware remain centralized for now; this release changes route ownership only.\n\nNo frontend or Live Matrix visual behavior is changed.\n''')

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
release_notes.write_text('''# DARK NOC v2.9.33 — Backend Router Split: SSH & File Transfer\n\n- Extract all SSH File Transfer and File Manager HTTP routes into `hub/ssh_file_router.py`.\n- Extract the interactive `/ws/ssh/{node_id}` terminal into `hub/ssh_terminal_router.py`.\n- Preserve multipart upload authentication, streaming download behavior, disconnect-aware semaphores, timeouts, SFTP atomic overwrite/rollback, checksum/editor limits, host-key pinning and persistent tmux sessions.\n- Preserve runtime/test overrides for SSH limits, chunking, transfer semaphore and keepalive settings through dynamic lookup.\n- Keep SSH/SFTP helper primitives, upload middleware, Agent control plane and Live telemetry WebSocket in `hub/app.py`.\n- Add permanent SSH router-boundary regression coverage.\n\n## فارسی\n\nRouteهای SSH File Manager، Upload/Relay/Download و WebSocket ترمینال SSH به Routerهای مستقل منتقل شدند؛ محدودیت‌ها، rollback اتمیک، قطع Client، Session، host-key pinning و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.33\n- Split SSH File Transfer/File Manager and interactive SSH terminal route ownership into dedicated backend routers.\n\n''' + changelog.read_text())

# CI is verified in the workspace but applied to the release branch separately,
# because the Actions token cannot modify workflow files.
ci = ROOT / '.github/workflows/ci.yml'
ci_text = ci.read_text()
needle = '          python tests/backend_auth_dashboard_system_router_contract.py\n'
if needle not in ci_text:
    raise RuntimeError('CI auth/dashboard/system contract anchor not found')
ci.write_text(ci_text.replace(needle, needle + '          python tests/backend_ssh_router_contract.py\n', 1))

updated = APP.read_text()
for name in file_names + terminal_names:
    # Compatibility aliases may expose names, but original definitions must be gone.
    if f'def {name}(' in updated or f'async def {name}(' in updated:
        raise RuntimeError(f'{name} unexpectedly remains defined in app.py')
for required in ['register_ssh_file_router(', 'register_ssh_terminal_router(']:
    if required not in updated:
        raise RuntimeError(f'Missing router registration: {required}')
if '@app.websocket("/ws/live")' not in updated:
    raise RuntimeError('Live telemetry websocket moved unexpectedly')
