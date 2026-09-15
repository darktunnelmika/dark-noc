from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.29'
new_version = '2.9.30'

node_names = [
    'list_nodes', 'create_node', 'retry_node_provision', 'delete_node', 'update_node',
    'reset_ssh_fingerprint', 'metrics', 'install_plugin', 'create_job',
]
tunnel_names = [
    'list_tunnels', 'tunnel_operations', 'tunnel_action', 'reconfigure_tunnel',
    'remove_tunnel_from_manager',
]

def extract(source, names):
    tree = ast.parse(source)
    lines = source.splitlines(True)
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
            found[node.name] = ''.join(lines[start:node.end_lineno]).rstrip() + '\n'
    missing = sorted(set(names) - set(found))
    if missing:
        raise RuntimeError(f'Missing route functions: {missing}')
    return found

nodes = extract(text, node_names)
tunnels = extract(text, tunnel_names)
for name, source in {**nodes, **tunnels}.items():
    if '@app.' not in source:
        raise RuntimeError(f'{name} is no longer app-owned')


def render_router(register_name, blocks, aliases, imports, extras=()):
    body = '\n\n'.join(block.replace('@app.', '@router.').rstrip() for block in blocks)
    indented = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    extra_lines = ''.join(f'\n    {line}' for line in extras)
    return f'''{imports}\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}{extra_lines}\n\n{indented}\n\n    app.include_router(router)\n    return router\n'''

node_aliases = [
    'current_user', 'db', 'NodeBody', 'NodeUpdateBody', 'JobBody', 'fetch_node_inventory',
    'public_node', 'utc_ts', 'create_node_mutation', 'encrypt', 'token_hash',
    'node_endpoint_conflict', 'NodeTunnelServiceError', 'get_provision_node', 'audit', 'LOGGER',
    'prepare_node_provision_mutation', 'delete_node_mutation', 'update_node_mutation',
    'reset_node_fingerprint_mutation', 'queue_plugin_install_mutation', 'PLUGIN_CATALOG',
]
node_src = render_router(
    'nodes', [nodes[name] for name in node_names], node_aliases,
    'import json\nimport secrets\nimport sqlite3\nfrom fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request',
    ('NODE_STALE_AFTER = deps["node_stale_after"]',
     'METRIC_ROLLUP_RETENTION_DAYS = deps["metric_rollup_retention_days"]'),
)
# Resolve the provision callable at request time so tests/operators can monkeypatch app.provision_node.
node_src = node_src.replace('background.add_task(provision_node,', 'background.add_task(get_provision_node(),')
(ROOT / 'hub/nodes_router.py').write_text(node_src)

tunnel_aliases = [
    'current_user', 'db', 'fetch_tunnel_inventory', 'utc_ts', 'normalize_ip',
    'tunnel_topology_side', 'tunnel_health_score', 'fetch_tunnel_operation_rows',
    'public_job', 'TunnelActionBody', 'queue_tunnel_action_mutation', 'NodeTunnelServiceError',
    'audit', 'TunnelReconfigureBody', 'reconfigure_tunnel_mutation', 'PLUGIN_CATALOG',
    'certificate_for_deployment', 'decrypt', 'plugin_job_payload', 'plugin_pair_code',
    'token_hash', 'remove_tunnel_mutation',
]
tunnel_src = render_router(
    'tunnels', [tunnels[name] for name in tunnel_names], tunnel_aliases,
    'import json\nimport sqlite3\nfrom typing import Any\nfrom fastapi import APIRouter, Depends, HTTPException, Request',
    ('NODE_STALE_AFTER = deps["node_stale_after"]',
     'TUNNEL_SAMPLE_RETENTION_DAYS = deps["tunnel_sample_retention_days"]'),
)
(ROOT / 'hub/tunnels_router.py').write_text(tunnel_src)

# Remove extracted routes from app.py.
tree = ast.parse(text)
lines = text.splitlines(True)
ranges = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in set(node_names + tunnel_names):
        start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
        end = node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end))
for start, end in sorted(ranges, reverse=True):
    del lines[start:end]
text = ''.join(lines)

loader_anchor = "ROOT = Path(__file__).resolve().parent\n"
router_loader = '''_NODES_ROUTER_PATH = Path(__file__).resolve().with_name('nodes_router.py')\n_nodes_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_nodes_router', _NODES_ROUTER_PATH)\nif _nodes_router_spec is None or _nodes_router_spec.loader is None:\n    raise ImportError(f'Could not load Nodes router: {_NODES_ROUTER_PATH}')\n_nodes_router_module = _realm_support_importlib_util.module_from_spec(_nodes_router_spec)\n_nodes_router_spec.loader.exec_module(_nodes_router_module)\nregister_nodes_router = _nodes_router_module.register_nodes_router\n\n_TUNNELS_ROUTER_PATH = Path(__file__).resolve().with_name('tunnels_router.py')\n_tunnels_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_tunnels_router', _TUNNELS_ROUTER_PATH)\nif _tunnels_router_spec is None or _tunnels_router_spec.loader is None:\n    raise ImportError(f'Could not load Tunnels router: {_TUNNELS_ROUTER_PATH}')\n_tunnels_router_module = _realm_support_importlib_util.module_from_spec(_tunnels_router_spec)\n_tunnels_router_spec.loader.exec_module(_tunnels_router_module)\nregister_tunnels_router = _tunnels_router_module.register_tunnels_router\n\n'''
assert loader_anchor in text
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)

anchor = '@app.post("/api/plugins/{plugin_id}/pair-code", status_code=202)\n'
assert anchor in text
registration = '''register_nodes_router(\n    app,\n    current_user=current_user, db=db, NodeBody=NodeBody, NodeUpdateBody=NodeUpdateBody, JobBody=JobBody,\n    fetch_node_inventory=fetch_node_inventory, public_node=public_node, utc_ts=utc_ts,\n    create_node_mutation=create_node_mutation, encrypt=encrypt, token_hash=token_hash,\n    node_endpoint_conflict=node_endpoint_conflict, NodeTunnelServiceError=NodeTunnelServiceError,\n    get_provision_node=lambda: provision_node, audit=audit, LOGGER=LOGGER,\n    prepare_node_provision_mutation=prepare_node_provision_mutation, delete_node_mutation=delete_node_mutation,\n    update_node_mutation=update_node_mutation, reset_node_fingerprint_mutation=reset_node_fingerprint_mutation,\n    queue_plugin_install_mutation=queue_plugin_install_mutation, PLUGIN_CATALOG=PLUGIN_CATALOG,\n    node_stale_after=NODE_STALE_AFTER, metric_rollup_retention_days=METRIC_ROLLUP_RETENTION_DAYS,\n)\n\nregister_tunnels_router(\n    app,\n    current_user=current_user, db=db, fetch_tunnel_inventory=fetch_tunnel_inventory, utc_ts=utc_ts,\n    normalize_ip=normalize_ip, tunnel_topology_side=tunnel_topology_side, tunnel_health_score=tunnel_health_score,\n    fetch_tunnel_operation_rows=fetch_tunnel_operation_rows, public_job=public_job,\n    TunnelActionBody=TunnelActionBody, queue_tunnel_action_mutation=queue_tunnel_action_mutation,\n    NodeTunnelServiceError=NodeTunnelServiceError, audit=audit, TunnelReconfigureBody=TunnelReconfigureBody,\n    reconfigure_tunnel_mutation=reconfigure_tunnel_mutation, PLUGIN_CATALOG=PLUGIN_CATALOG,\n    certificate_for_deployment=certificate_for_deployment, decrypt=decrypt, plugin_job_payload=plugin_job_payload,\n    plugin_pair_code=plugin_pair_code, token_hash=token_hash, remove_tunnel_mutation=remove_tunnel_mutation,\n    node_stale_after=NODE_STALE_AFTER, tunnel_sample_retention_days=TUNNEL_SAMPLE_RETENTION_DAYS,\n)\n\n\n'''
text = text.replace(anchor, registration + anchor, 1)
APP.write_text(text)

(ROOT / 'tests/backend_node_tunnel_repository_contract.py').write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / 'hub/app.py').read_text()\nrepo = (root / 'hub/node_tunnel_repository.py').read_text()\nnodes = (root / 'hub/nodes_router.py').read_text()\ntunnels = (root / 'hub/tunnels_router.py').read_text()\nassert "with_name('node_tunnel_repository.py')" in app\nassert '_node_tunnel_repository_spec.loader.exec_module' in app\nfor marker in ['def find_node_endpoint_conflict(', 'def fetch_node_inventory(', 'def fetch_tunnel_inventory(', 'def fetch_tunnel_operation_rows(']: assert marker in repo\nassert 'fetch_node_inventory(db,' in nodes\nassert 'fetch_tunnel_inventory(db)' in tunnels\nassert 'fetch_tunnel_operation_rows(' in tunnels\nassert "ORDER BY CASE role WHEN 'hub'" in repo\nassert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' in repo\nassert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' in repo\nassert "ORDER BY CASE role WHEN 'hub'" not in app\nassert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' not in app\nassert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' not in app\nassert 'VERSION = "2.9.30"' in app\nprint('Node/Tunnel repository query boundary passed')\n''')

(ROOT / 'tests/backend_node_tunnel_service_contract.py').write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nservice = (root / "hub/node_tunnel_service.py").read_text()\nnodes = (root / "hub/nodes_router.py").read_text()\ntunnels = (root / "hub/tunnels_router.py").read_text()\nassert "with_name('node_tunnel_service.py')" in app\nassert "_node_tunnel_service_spec.loader.exec_module" in app\nassert 'VERSION = "2.9.30"' in app\nfor marker in ["def create_node_mutation(", "def prepare_node_provision_mutation(", "def delete_node_mutation(", "def update_node_mutation(", "def reset_node_fingerprint_mutation(", "def queue_tunnel_action_mutation(", "def queue_plugin_install_mutation(", "def reconfigure_tunnel_mutation(", "def remove_tunnel_mutation("]: assert marker in service\nfor call in ["create_node_mutation(", "prepare_node_provision_mutation(", "delete_node_mutation(", "update_node_mutation(", "reset_node_fingerprint_mutation(", "queue_plugin_install_mutation("]: assert call in nodes, call\nfor call in ["queue_tunnel_action_mutation(", "reconfigure_tunnel_mutation(", "remove_tunnel_mutation("]: assert call in tunnels, call\nfor marker in ["INSERT INTO nodes(name,region,role,host", "DELETE FROM nodes WHERE id=?", "UPDATE nodes SET name=?,region=?,role=?", "INSERT INTO jobs(node_id,kind,payload,created_by,created_at)", "UPDATE plugin_deployments SET settings=?,iran_job_id=?,kharej_job_id=?", "UPDATE hybrid_deployments SET settings=?,iran_job_id=?,pair_code_hash=?"]: assert marker in service\nfor decorator, owner in [('@router.post("/api/nodes", status_code=202)', nodes), ('@router.put("/api/nodes/{node_id}")', nodes), ('@router.delete("/api/nodes/{node_id}")', nodes), ('@router.post("/api/tunnels/{tunnel_id}/action", status_code=202)', tunnels), ('@router.put("/api/tunnels/{tunnel_id}", status_code=202)', tunnels), ('@router.delete("/api/tunnels/{tunnel_id}", status_code=202)', tunnels)]:\n    assert decorator in owner\n    assert decorator.replace('@router.', '@app.') not in app\nassert 'background.add_task(get_provision_node(),' in nodes\nassert 'get_provision_node=lambda: provision_node' in app\nprint("Node/Tunnel mutation service boundary passed")\n''')

(ROOT / 'tests/backend_router_contract.py').write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nmonitoring = (root / "hub/monitoring_router.py").read_text()\nincidents = (root / "hub/incidents_router.py").read_text()\nnodes = (root / "hub/nodes_router.py").read_text()\ntunnels = (root / "hub/tunnels_router.py").read_text()\nfor marker in ['register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router']: assert marker in app\nfor marker in ['@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")']: assert marker not in app\nfor owner in [monitoring, incidents, nodes, tunnels]: assert 'APIRouter' in owner\nfor path in ['/api/monitors', '/api/monitors/{monitor_id}/results']: assert path in monitoring\nfor path in ['/api/incidents', '/api/incidents/{incident_id}/action']: assert path in incidents\nfor path in ['/api/nodes', '/api/nodes/{node_id}/metrics', '/api/nodes/{node_id}/plugins/{plugin_id}/install', '/api/nodes/{node_id}/jobs']: assert path in nodes, path\nfor path in ['/api/tunnels', '/api/tunnels/{tunnel_id}/operations', '/api/tunnels/{tunnel_id}/action']: assert path in tunnels, path\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\nassert 'create_incident(' in app and 'resolve_incident(' in app\nprint('backend router contract ok')\n''')

(ROOT / 'tests/backend_node_tunnel_router_contract.py').write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / 'hub/app.py').read_text()\nnodes = (root / 'hub/nodes_router.py').read_text()\ntunnels = (root / 'hub/tunnels_router.py').read_text()\nassert "with_name('nodes_router.py')" in app and "with_name('tunnels_router.py')" in app\nassert 'register_nodes_router(' in app and 'register_tunnels_router(' in app\nfor path in ['/api/nodes', '/api/nodes/{node_id}/provision', '/api/nodes/{node_id}', '/api/nodes/{node_id}/ssh-fingerprint', '/api/nodes/{node_id}/metrics', '/api/nodes/{node_id}/plugins/{plugin_id}/install', '/api/nodes/{node_id}/jobs']: assert path in nodes, path\nfor path in ['/api/tunnels', '/api/tunnels/{tunnel_id}/operations', '/api/tunnels/{tunnel_id}/action', '/api/tunnels/{tunnel_id}']: assert path in tunnels, path\nassert '@app.get("/api/nodes")' not in app and '@app.get("/api/tunnels")' not in app\nassert 'def ssh_upload_file' in app and 'def agent_heartbeat' in app\nassert 'background.add_task(get_provision_node(),' in nodes\nassert 'VERSION = "2.9.30"' in app\nprint('Node/Tunnel router boundary passed')\n''')

version_files = [ROOT/'hub/app.py', ROOT/'agent/agent.py', ROOT/'darknoc', ROOT/'install-hub.sh', ROOT/'install-node.sh', ROOT/'upgrade.sh', ROOT/'hub/static/index.html']
for path in version_files:
    data = path.read_text()
    if old_version in data: path.write_text(data.replace(old_version, new_version))
for path in (ROOT/'tests').glob('*.py'):
    data = path.read_text()
    if old_version in data: path.write_text(data.replace(old_version, new_version))

ci = ROOT/'.github/workflows/ci.yml'
ci_text = ci.read_text()
needle = '          python tests/backend_router_contract.py\n'
if 'backend_node_tunnel_router_contract.py' not in ci_text:
    assert needle in ci_text
    ci_text = ci_text.replace(needle, needle + '          python tests/backend_node_tunnel_router_contract.py\n', 1)
ci.write_text(ci_text)

notes = '''# DARK NOC v2.9.30 — Backend Router Split: Nodes & Tunnels\n\n- Continue backend route modularization by extracting all `/api/nodes...` routes into `hub/nodes_router.py`.\n- Extract all `/api/tunnels...` routes into `hub/tunnels_router.py`.\n- Preserve Node provisioning monkeypatch/runtime indirection, BackgroundTasks, Repository + Service ownership, API paths, audit behavior, tunnel topology semantics, Agent control plane, SSH/File Transfer and frontend visuals.\n- Add permanent Node/Tunnel router-boundary regression coverage.\n\n## فارسی\n\nتمام Routeهای Node و Tunnel از `hub/app.py` به `nodes_router.py` و `tunnels_router.py` منتقل شدند؛ API، Agent، SSH و ظاهر پنل بدون تغییر باقی مانده‌اند.\n\n---\n\n'''
release_notes = ROOT/'RELEASE_NOTES.md'
release_notes.write_text(notes + release_notes.read_text())
changelog = ROOT/'CHANGELOG.md'
changelog.write_text('## v2.9.30\n- Split Node and Tunnel FastAPI route registration into dedicated APIRouter modules.\n\n' + changelog.read_text())
(ROOT/'docs/backend-router-progress-v2.9.30.md').write_text('''# DARK NOC Backend Router Progress — v2.9.30\n\nCompleted router ownership: Monitoring, Incidents, Nodes, and Tunnels.\n\nStill intentionally kept in `hub/app.py`:\n\n1. Plugin Deployments + Certificates + Fleet Operations — next low-risk split.\n2. Dashboard/System/Auth — moderate coupling.\n3. SSH/File Transfer — higher risk because of streaming and cancellation semantics.\n4. Agent control plane + job result state machine — highest risk and should remain last.\n5. Live WebSocket — keep with session/runtime core until Agent/SSH route work is complete.\n\nNo frontend or Live Matrix behavior is part of this backend router phase.\n''')
