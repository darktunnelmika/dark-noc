from pathlib import Path

ROOT = Path('.')
old_version = '2.9.42'
new_version = '2.9.43'
old_pin = 'fastapi==0.115.6'
new_pin = 'fastapi==0.141.1'

requirements_path = ROOT / 'hub/requirements.txt'
requirements = requirements_path.read_text()
if old_pin not in requirements:
    raise RuntimeError(f'{old_pin} missing from hub/requirements.txt')
requirements_path.write_text(requirements.replace(old_pin, new_pin, 1))

contract = ROOT / 'tests/dependency_fastapi_contract.py'
contract.write_text('''from importlib.metadata import version\nfrom pathlib import Path\n\nfrom fastapi import APIRouter, Cookie, Depends, FastAPI, Header, HTTPException, WebSocket\nfrom fastapi.testclient import TestClient\nfrom pydantic import BaseModel\n\nroot = Path(__file__).resolve().parents[1]\nhub_requirements = (root / "hub/requirements.txt").read_text().splitlines()\nagent_requirements = (root / "agent/requirements.txt").read_text().splitlines()\n\nassert "fastapi==0.141.1" in hub_requirements\nassert "fastapi==0.115.6" not in hub_requirements\nassert not any(line.startswith("fastapi") for line in agent_requirements)\nassert version("fastapi") == "0.141.1"\n\nclass ContractBody(BaseModel):\n    value: int\n\ndef operator_context(\n    x_dark_noc: str = Header(...),\n    dark_noc_session: str | None = Cookie(default=None),\n):\n    if x_dark_noc != "contract-token":\n        raise HTTPException(401, "invalid contract token")\n    return {"header": x_dark_noc, "session": dark_noc_session}\n\napp = FastAPI()\nrouter = APIRouter()\n\n@router.post("/api/contract/{item_id}")\ndef contract_http(\n    item_id: int,\n    body: ContractBody,\n    context: dict = Depends(operator_context),\n):\n    return {\n        "item_id": item_id,\n        "value": body.value,\n        "session": context["session"],\n    }\n\n@router.websocket("/ws/contract")\nasync def contract_websocket(websocket: WebSocket):\n    await websocket.accept()\n    payload = await websocket.receive_json()\n    await websocket.send_json({"echo": payload, "query": websocket.query_params.get("source")})\n    await websocket.close(code=1000)\n\napp.include_router(router)\n\nwith TestClient(app) as client:\n    unauthorized = client.post("/api/contract/7", json={"value": 9})\n    assert unauthorized.status_code == 422\n\n    denied = client.post(\n        "/api/contract/7",\n        headers={"X-Dark-Noc": "wrong"},\n        json={"value": 9},\n    )\n    assert denied.status_code == 401 and denied.json()["detail"] == "invalid contract token"\n\n    client.cookies.set("dark_noc_session", "session-contract")\n    response = client.post(\n        "/api/contract/7",\n        headers={"X-Dark-Noc": "contract-token"},\n        json={"value": 9},\n    )\n    assert response.status_code == 200\n    assert response.json() == {"item_id": 7, "value": 9, "session": "session-contract"}\n\n    with client.websocket_connect("/ws/contract?source=dark-noc") as websocket:\n        websocket.send_json({"type": "ping"})\n        assert websocket.receive_json() == {\n            "echo": {"type": "ping"},\n            "query": "dark-noc",\n        }\n\nprint("fastapi dependency contract passed")\n''')

version_paths = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc',
    ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh',
    ROOT / 'hub/static/index.html',
]
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.43 — FastAPI Dependency Hardening\n\n- Upgrade the Hub API framework from `fastapi==0.115.6` to `fastapi==0.141.1`.\n- Keep the dependency exactly pinned so production and CI use the same API framework release.\n- Validate APIRouter registration, Pydantic request bodies, path parameters, Header/Cookie dependencies, HTTPException responses and WebSocket routing under FastAPI 0.141.1.\n- Exercise the complete Hub/Auth/API/WebSocket/Agent regression suite and reproducible release build with no other direct dependency changes.\n- No API contract, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nپکیج `FastAPI` هاب از نسخه 0.115.6 به 0.141.1 ارتقا داده شد؛ Routerها، مدل‌های درخواست، Auth dependencyها، خطاهای HTTP و WebSocket با تست کامل بررسی می‌شوند و هیچ dependency مستقیم دیگری تغییر نمی‌کند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.43\n- Dependency hardening: pin `fastapi==0.141.1` (from 0.115.6).\n- Add permanent APIRouter/request/auth/WebSocket compatibility coverage.\n- No API contract, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n''' + changelog.read_text())

print('prepared v2.9.43 FastAPI hardening')
