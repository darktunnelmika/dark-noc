from pathlib import Path
import ast

ROOT = Path('.')
APP = ROOT / 'hub/app.py'
text = APP.read_text()
old_version = '2.9.30'
new_version = '2.9.31'

plugin_names = [
    'list_plugins', 'deploy_plugin_pair_code', 'deploy_plugin', 'list_plugin_deployments',
    'recover_hybrid_pair_code', 'retry_hybrid_deployment', 'remove_hybrid_deployment',
    'retry_plugin_deployment', 'remove_plugin_deployment',
]
certificate_names = ['list_certificates', 'issue_certificate', 'renew_certificate']
fleet_names = ['list_fleet_operations', 'create_fleet_operation', 'cancel_fleet_operation']


def route_blocks(source: str, names: list[str]) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(True)
    found = {}
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


plugins = route_blocks(text, plugin_names)
certificates = route_blocks(text, certificate_names)
fleet = route_blocks(text, fleet_names)


def render_router(register_name: str, blocks: list[str], aliases: list[str], imports: str, extras: list[str] | None = None) -> str:
    body = '\n\n'.join(block.replace('@app.', '@router.').rstrip() for block in blocks)
    indented = '\n'.join(('    ' + line if line else '') for line in body.splitlines())
    alias_lines = '\n'.join(f'    {name} = deps["{name}"]' for name in aliases)
    extra_lines = '\n'.join(f'    {line}' for line in (extras or []))
    if extra_lines:
        extra_lines = '\n' + extra_lines
    return f'''{imports}\n\n\ndef register_{register_name}_router(app, **deps):\n    router = APIRouter()\n{alias_lines}{extra_lines}\n\n{indented}\n\n    app.include_router(router)\n    return router\n'''


plugin_aliases = [
    'current_user', 'db', 'PLUGIN_CATALOG', 'PairCodeDeployBody', 'PluginDeployBody',
    'deploy_pair_code_mutation', 'deploy_managed_mutation', 'recover_pair_code_mutation',
    'retry_hybrid_mutation', 'remove_hybrid_mutation', 'retry_managed_mutation',
    'remove_managed_mutation', 'PluginDeploymentServiceError', 'utc_ts',
    'prepare_realm_settings', 'certificate_for_deployment', 'plugin_pair_code',
    'plugin_job_payload', 'encrypt', 'token_hash', 'normalize_ip', 'decrypt', 'audit',
]
plugin_src = render_router(
    'plugin_deployments',
    [plugins[name] for name in plugin_names],
    plugin_aliases,
    'import json\nimport sqlite3\nfrom fastapi import APIRouter, Depends, HTTPException, Request',
    [
        'NODE_STALE_AFTER = deps["node_stale_after"]',
        'PAQET_CORE_TAG = deps["paqet_core_tag"]',
    ],
)
(ROOT / 'hub/plugin_deployments_router.py').write_text(plugin_src)

certificate_aliases = [
    'current_user', 'db', 'CertificateBody', 'issue_certificate_mutation',
    'renew_certificate_mutation', 'CertificateFleetServiceError', 'utc_ts',
    'normalize_ip', 'audit',
]
certificate_src = render_router(
    'certificates',
    [certificates[name] for name in certificate_names],
    certificate_aliases,
    'import sqlite3\nfrom fastapi import APIRouter, Depends, HTTPException, Request',
    ['NODE_STALE_AFTER = deps["node_stale_after"]'],
)
(ROOT / 'hub/certificates_router.py').write_text(certificate_src)

fleet_aliases = [
    'current_user', 'db', 'FleetOperationBody', 'create_fleet_operation_mutation',
    'cancel_fleet_operation_mutation', 'CertificateFleetServiceError', 'utc_ts', 'audit',
    'get_queue_due_fleet_operations', 'get_provision_node_for_fleet', 'get_orchestrate_agent_upgrade',
]
fleet_src = render_router(
    'fleet',
    [fleet[name] for name in fleet_names],
    fleet_aliases,
    'import json\nimport secrets\nimport sqlite3\nfrom fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request',
    ['VERSION = deps["version"]'],
)
fleet_src = fleet_src.replace('queue_due_fleet_operations=queue_due_fleet_operations,', 'queue_due_fleet_operations=get_queue_due_fleet_operations(),')
fleet_src = fleet_src.replace('background.add_task(\n                    provision_node_for_fleet,', 'background.add_task(\n                    get_provision_node_for_fleet(),')
fleet_src = fleet_src.replace('background.add_task(\n            orchestrate_agent_upgrade,', 'background.add_task(\n            get_orchestrate_agent_upgrade(),')
(ROOT / 'hub/fleet_router.py').write_text(fleet_src)

# Remove route blocks from app.py while preserving helpers and orchestration state machines.
tree = ast.parse(text)
lines = text.splitlines(True)
targets = set(plugin_names + certificate_names + fleet_names)
ranges = []
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
router_loader = '''_PLUGIN_DEPLOYMENTS_ROUTER_PATH = Path(__file__).resolve().with_name('plugin_deployments_router.py')\n_plugin_deployments_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_plugin_deployments_router', _PLUGIN_DEPLOYMENTS_ROUTER_PATH)\nif _plugin_deployments_router_spec is None or _plugin_deployments_router_spec.loader is None:\n    raise ImportError(f'Could not load Plugin Deployments router: {_PLUGIN_DEPLOYMENTS_ROUTER_PATH}')\n_plugin_deployments_router_module = _realm_support_importlib_util.module_from_spec(_plugin_deployments_router_spec)\n_plugin_deployments_router_spec.loader.exec_module(_plugin_deployments_router_module)\nregister_plugin_deployments_router = _plugin_deployments_router_module.register_plugin_deployments_router\n\n_CERTIFICATES_ROUTER_PATH = Path(__file__).resolve().with_name('certificates_router.py')\n_certificates_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_certificates_router', _CERTIFICATES_ROUTER_PATH)\nif _certificates_router_spec is None or _certificates_router_spec.loader is None:\n    raise ImportError(f'Could not load Certificates router: {_CERTIFICATES_ROUTER_PATH}')\n_certificates_router_module = _realm_support_importlib_util.module_from_spec(_certificates_router_spec)\n_certificates_router_spec.loader.exec_module(_certificates_router_module)\nregister_certificates_router = _certificates_router_module.register_certificates_router\n\n_FLEET_ROUTER_PATH = Path(__file__).resolve().with_name('fleet_router.py')\n_fleet_router_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_fleet_router', _FLEET_ROUTER_PATH)\nif _fleet_router_spec is None or _fleet_router_spec.loader is None:\n    raise ImportError(f'Could not load Fleet router: {_FLEET_ROUTER_PATH}')\n_fleet_router_module = _realm_support_importlib_util.module_from_spec(_fleet_router_spec)\n_fleet_router_spec.loader.exec_module(_fleet_router_module)\nregister_fleet_router = _fleet_router_module.register_fleet_router\n\n'''
if loader_anchor not in text:
    raise RuntimeError('Router loader anchor not found')
text = text.replace(loader_anchor, router_loader + loader_anchor, 1)

register_anchor = 'register_nodes_router(\n'
if register_anchor not in text:
    raise RuntimeError('Node router registration anchor not found')
registration = '''register_plugin_deployments_router(\n    app,\n    current_user=current_user, db=db, PLUGIN_CATALOG=PLUGIN_CATALOG, PairCodeDeployBody=PairCodeDeployBody,\n    PluginDeployBody=PluginDeployBody, deploy_pair_code_mutation=deploy_pair_code_mutation,\n    deploy_managed_mutation=deploy_managed_mutation, recover_pair_code_mutation=recover_pair_code_mutation,\n    retry_hybrid_mutation=retry_hybrid_mutation, remove_hybrid_mutation=remove_hybrid_mutation,\n    retry_managed_mutation=retry_managed_mutation, remove_managed_mutation=remove_managed_mutation,\n    PluginDeploymentServiceError=PluginDeploymentServiceError, utc_ts=utc_ts,\n    prepare_realm_settings=prepare_realm_settings, certificate_for_deployment=certificate_for_deployment,\n    plugin_pair_code=plugin_pair_code, plugin_job_payload=plugin_job_payload, encrypt=encrypt, token_hash=token_hash,\n    normalize_ip=normalize_ip, decrypt=decrypt, audit=audit, node_stale_after=NODE_STALE_AFTER, paqet_core_tag=PAQET_CORE_TAG,\n)\n\nregister_certificates_router(\n    app,\n    current_user=current_user, db=db, CertificateBody=CertificateBody, issue_certificate_mutation=issue_certificate_mutation,\n    renew_certificate_mutation=renew_certificate_mutation, CertificateFleetServiceError=CertificateFleetServiceError,\n    utc_ts=utc_ts, normalize_ip=normalize_ip, audit=audit, node_stale_after=NODE_STALE_AFTER,\n)\n\nregister_fleet_router(\n    app,\n    current_user=current_user, db=db, FleetOperationBody=FleetOperationBody,\n    create_fleet_operation_mutation=create_fleet_operation_mutation,\n    cancel_fleet_operation_mutation=cancel_fleet_operation_mutation,\n    CertificateFleetServiceError=CertificateFleetServiceError, utc_ts=utc_ts, audit=audit, version=VERSION,\n    get_queue_due_fleet_operations=lambda: queue_due_fleet_operations,\n    get_provision_node_for_fleet=lambda: provision_node_for_fleet,\n    get_orchestrate_agent_upgrade=lambda: orchestrate_agent_upgrade,\n)\n\n'''
text = text.replace(register_anchor, registration + register_anchor, 1)
APP.write_text(text)

# Update existing service contracts to recognize router ownership.
plugin_contract = ROOT / 'tests/backend_plugin_deployment_service_contract.py'
plugin_contract.write_text('''from pathlib import Path\nroot = Path(__file__).resolve().parents[1]\napp = (root / 'hub/app.py').read_text()\nrouter = (root / 'hub/plugin_deployments_router.py').read_text()\nservice = (root / 'hub/plugin_deployment_service.py').read_text()\nassert "with_name('plugin_deployment_service.py')" in app\nassert '_plugin_deployment_service_spec.loader.exec_module' in app\nfor marker in [\n    'def deploy_pair_code_mutation(', 'def deploy_managed_mutation(',\n    'def recover_pair_code_mutation(', 'def retry_hybrid_mutation(',\n    'def remove_hybrid_mutation(', 'def retry_managed_mutation(', 'def remove_managed_mutation(',\n]:\n    assert marker in service\nfor route in [\n    '@router.post("/api/plugins/{plugin_id}/pair-code", status_code=202)',\n    '@router.post("/api/plugins/{plugin_id}/deploy", status_code=202)',\n    '@router.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)',\n    '@router.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)',\n    '@router.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)',\n    '@router.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)',\n]:\n    assert route in router\n    assert route.replace('@router.', '@app.') not in app\nfor call in [\n    'deploy_pair_code_mutation(', 'deploy_managed_mutation(', 'recover_pair_code_mutation(',\n    'retry_hybrid_mutation(', 'remove_hybrid_mutation(', 'retry_managed_mutation(', 'remove_managed_mutation(',\n]:\n    assert call in router\nfor sql in [\n    'INSERT INTO hybrid_deployments(', 'INSERT INTO plugin_deployments(',\n    "UPDATE hybrid_deployments SET iran_job_id=?,updated_at=?",\n    "UPDATE plugin_deployments SET lifecycle='active'",\n    "UPDATE plugin_deployments SET iran_job_id=?,kharej_job_id=?,lifecycle='removing'",\n]:\n    assert sql in service\n    assert sql not in app\nassert '@router.get("/api/plugin-deployments")' in router\nassert 'VERSION = "2.9.31"' in app\nprint('Plugin Deployment service boundary passed')\n''')

certificate_contract = ROOT / 'tests/backend_certificate_fleet_service_contract.py'
certificate_contract.write_text('''import ast\nfrom pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nservice = (root / "hub/certificate_fleet_service.py").read_text()\ncert_router = (root / "hub/certificates_router.py").read_text()\nfleet_router = (root / "hub/fleet_router.py").read_text()\n\nassert "with_name('certificate_fleet_service.py')" in app\nassert "_certificate_fleet_service_spec.loader.exec_module" in app\nfor marker in [\n    "def issue_certificate_mutation(", "def renew_certificate_mutation(",\n    "def create_fleet_operation_mutation(", "def cancel_fleet_operation_mutation(",\n]:\n    assert marker in service\n\ndef functions(source: str):\n    module = ast.parse(source)\n    return {\n        node.name: ast.get_source_segment(source, node) or ""\n        for node in ast.walk(module)\n        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))\n    }\ncert_functions = functions(cert_router)\nfleet_functions = functions(fleet_router)\nfor name, call in {\n    "issue_certificate": "issue_certificate_mutation(",\n    "renew_certificate": "renew_certificate_mutation(",\n}.items():\n    source = cert_functions[name]\n    assert call in source and "conn.execute(" not in source and "with db()" not in source\nfor name, call in {\n    "create_fleet_operation": "create_fleet_operation_mutation(",\n    "cancel_fleet_operation": "cancel_fleet_operation_mutation(",\n}.items():\n    source = fleet_functions[name]\n    assert call in source and "conn.execute(" not in source and "with db()" not in source\nassert "background.add_task(" in fleet_functions["create_fleet_operation"]\nassert "get_provision_node_for_fleet()" in fleet_functions["create_fleet_operation"]\nassert "get_orchestrate_agent_upgrade()" in fleet_functions["create_fleet_operation"]\nassert "get_queue_due_fleet_operations()" in fleet_functions["create_fleet_operation"]\nassert 'if row["kind"] == "certificate_issue":' in app\nassert "def queue_due_fleet_operations(" in app\nassert 'VERSION = "2.9.31"' in app\nprint("Certificate/Fleet service boundary passed")\n''')

router_contract = ROOT / 'tests/backend_router_contract.py'
router_contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / "hub/app.py").read_text()\nrouters = {\n    'monitoring': (root / "hub/monitoring_router.py").read_text(),\n    'incidents': (root / "hub/incidents_router.py").read_text(),\n    'nodes': (root / "hub/nodes_router.py").read_text(),\n    'tunnels': (root / "hub/tunnels_router.py").read_text(),\n    'plugins': (root / "hub/plugin_deployments_router.py").read_text(),\n    'certificates': (root / "hub/certificates_router.py").read_text(),\n    'fleet': (root / "hub/fleet_router.py").read_text(),\n}\nfor marker in [\n    'register_monitoring_router', 'register_incidents_router', 'register_nodes_router', 'register_tunnels_router',\n    'register_plugin_deployments_router', 'register_certificates_router', 'register_fleet_router',\n]:\n    assert marker in app\nfor marker in [\n    '@app.get("/api/monitors")', '@app.get("/api/incidents")', '@app.get("/api/nodes")', '@app.get("/api/tunnels")',\n    '@app.get("/api/plugin-deployments")', '@app.get("/api/certificates")', '@app.get("/api/fleet/operations")',\n]:\n    assert marker not in app\nfor owner in routers.values():\n    assert 'APIRouter' in owner\nassert '/api/monitors' in routers['monitoring'] and '/api/incidents' in routers['incidents']\nassert '/api/nodes' in routers['nodes'] and '/api/tunnels' in routers['tunnels']\nassert '/api/plugin-deployments' in routers['plugins'] and '/api/hybrid-deployments/{deployment_id}/retry' in routers['plugins']\nassert '/api/certificates/{certificate_id}/renew' in routers['certificates']\nassert '/api/fleet/operations/{operation_id}' in routers['fleet']\nassert 'def agent_heartbeat' in app and 'def agent_job_result' in app\nassert 'create_incident(' in app and 'resolve_incident(' in app\nprint('backend router contract ok')\n''')

operations_contract = ROOT / 'tests/backend_operations_router_contract.py'
operations_contract.write_text('''from pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\napp = (root / 'hub/app.py').read_text()\nplugins = (root / 'hub/plugin_deployments_router.py').read_text()\ncerts = (root / 'hub/certificates_router.py').read_text()\nfleet = (root / 'hub/fleet_router.py').read_text()\n\nfor path in [\n    '/api/plugins', '/api/plugins/{plugin_id}/pair-code', '/api/plugins/{plugin_id}/deploy',\n    '/api/plugin-deployments', '/api/plugin-deployments/{deployment_id}/retry',\n    '/api/plugin-deployments/{deployment_id}/remove', '/api/hybrid-deployments/{deployment_id}/pair-code',\n    '/api/hybrid-deployments/{deployment_id}/retry', '/api/hybrid-deployments/{deployment_id}/remove',\n]:\n    assert path in plugins, path\nfor path in ['/api/certificates', '/api/certificates/{certificate_id}/renew']:\n    assert path in certs, path\nfor path in ['/api/fleet/operations', '/api/fleet/operations/{operation_id}']:\n    assert path in fleet, path\nfor route_prefix in ['/api/plugins', '/api/plugin-deployments', '/api/hybrid-deployments', '/api/certificates', '/api/fleet/operations']:\n    assert f'@app.get("{route_prefix}' not in app and f'@app.post("{route_prefix}' not in app and f'@app.delete("{route_prefix}' not in app\nassert 'certificate_for_deployment' in app\nassert 'def plugin_pair_code(' in app and 'def plugin_job_payload(' in app\nassert 'lambda: queue_due_fleet_operations' in app\nassert 'lambda: provision_node_for_fleet' in app\nassert 'lambda: orchestrate_agent_upgrade' in app\nassert 'def agent_heartbeat' in app and '@app.websocket("/ws/ssh/{node_id}")' in app\nprint('Plugin/Certificate/Fleet router boundary passed')\n''')

# Version bump across shipped runtime and regression expectations.
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

notes = '''# DARK NOC v2.9.31 — Backend Router Split: Deployments, Certificates & Fleet\n\n- Continue backend route modularization by extracting Plugin Deployment routes into `hub/plugin_deployments_router.py`.\n- Extract Certificate routes into `hub/certificates_router.py` and Fleet Operations routes into `hub/fleet_router.py`.\n- Preserve Plugin Deployment and Certificate/Fleet service ownership, Pair Code behavior, TLS validation, Fleet scheduling/canary semantics and audit responses.\n- Preserve Fleet BackgroundTasks through runtime callback lookup so test/runtime overrides remain compatible.\n- Keep Dashboard/System/Auth, SSH/File Transfer, Agent control plane, Live WebSocket and all frontend/Live Matrix behavior unchanged.\n- Add permanent Plugin/Certificate/Fleet router-boundary regression coverage.\n\n## فارسی\n\nRouteهای Plugin Deployment، Certificate و Fleet Operations از `hub/app.py` به Routerهای مستقل منتقل شدند؛ Pair Code، TLS، Fleet rollout، Agent و ظاهر پنل بدون تغییر باقی مانده‌اند.\n\n---\n\n'''
release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text(notes + release_notes.read_text())
changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('## v2.9.31\n- Split Plugin Deployment, Certificate and Fleet Operations FastAPI routes into dedicated APIRouter modules.\n\n' + changelog.read_text())

doc = ROOT / 'docs/backend-router-progress-v2.9.31.md'
doc.write_text('''# DARK NOC Backend Router Progress — v2.9.31\n\nCompleted router ownership:\n\n- Monitoring\n- Incidents\n- Nodes\n- Tunnels\n- Plugin Deployments\n- Certificates\n- Fleet Operations\n\nStill intentionally kept in `hub/app.py`:\n\n1. Dashboard/System/Auth — next moderate-risk split.\n2. SSH/File Transfer — higher risk due streaming, cancellation and SFTP rollback semantics.\n3. Agent control plane + job-result state machine — highest risk and should remain last.\n4. Live WebSocket — keep with runtime core until Agent/SSH route work is complete.\n\nNo frontend or Live Matrix behavior is part of this backend router phase.\n''')
