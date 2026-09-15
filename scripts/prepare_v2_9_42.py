from pathlib import Path

ROOT = Path('.')
old_version = '2.9.41'
new_version = '2.9.42'
old_pin = 'uvicorn[standard]==0.34.0'
new_pin = 'uvicorn[standard]==0.52.4'

requirements = ROOT / 'hub/requirements.txt'
data = requirements.read_text()
if old_pin not in data:
    raise RuntimeError(f'{old_pin} missing from hub/requirements.txt')
requirements.write_text(data.replace(old_pin, new_pin, 1))

contract = ROOT / 'tests/dependency_uvicorn_contract.py'
contract.write_text('''import asyncio\nfrom importlib.metadata import version\nfrom pathlib import Path\nimport socket\n\nimport httpx\nimport uvicorn\nimport websockets\n\nroot = Path(__file__).resolve().parents[1]\nhub_requirements = (root / "hub/requirements.txt").read_text().splitlines()\nagent_requirements = (root / "agent/requirements.txt").read_text().splitlines()\n\nassert "uvicorn[standard]==0.52.4" in hub_requirements\nassert "uvicorn[standard]==0.34.0" not in hub_requirements\nassert not any(line.startswith("uvicorn") for line in agent_requirements)\nassert version("uvicorn") == "0.52.4"\n\nfor name in ["Config", "Server"]:\n    assert getattr(uvicorn, name, None) is not None, name\n\nasync def asgi_app(scope, receive, send):\n    if scope["type"] == "http":\n        await receive()\n        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})\n        await send({"type": "http.response.body", "body": b"dark-noc-uvicorn"})\n        return\n    if scope["type"] == "websocket":\n        event = await receive()\n        assert event["type"] == "websocket.connect"\n        await send({"type": "websocket.accept"})\n        message = await receive()\n        assert message["type"] == "websocket.receive" and message.get("text") == "ping"\n        await send({"type": "websocket.send", "text": "pong"})\n        await send({"type": "websocket.close", "code": 1000})\n        return\n    raise AssertionError(scope["type"])\n\nasync def run_contract():\n    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n    sock.bind(("127.0.0.1", 0))\n    sock.listen(128)\n    sock.setblocking(False)\n    host, port = sock.getsockname()[:2]\n    config = uvicorn.Config(\n        asgi_app,\n        host=host,\n        port=port,\n        lifespan="off",\n        log_level="error",\n        access_log=False,\n    )\n    server = uvicorn.Server(config)\n    task = asyncio.create_task(server.serve(sockets=[sock]))\n    try:\n        for _ in range(200):\n            if server.started:\n                break\n            await asyncio.sleep(0.01)\n        assert server.started, "uvicorn server did not start"\n        async with httpx.AsyncClient() as client:\n            response = await client.get(f"http://{host}:{port}/health", timeout=5)\n        assert response.status_code == 200 and response.text == "dark-noc-uvicorn"\n        async with websockets.connect(f"ws://{host}:{port}/ws", open_timeout=5, close_timeout=5) as websocket:\n            await websocket.send("ping")\n            assert await websocket.recv() == "pong"\n    finally:\n        server.should_exit = True\n        await asyncio.wait_for(task, timeout=5)\n        sock.close()\n\nasyncio.run(run_contract())\nprint("uvicorn dependency contract passed")\n''')

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
release_notes.write_text('''# DARK NOC v2.9.42 — Uvicorn Dependency Hardening\n\n- Upgrade the Hub ASGI runtime from `uvicorn[standard]==0.34.0` to `uvicorn[standard]==0.52.4`.\n- Keep the dependency exactly pinned so production and CI use the same HTTP/WebSocket server implementation.\n- Validate real local ASGI HTTP and WebSocket round trips under Uvicorn 0.52.4 in addition to the existing DARK NOC regression suite.\n- Exercise Hub startup, API, WebSocket, SSH terminal/file-transfer and reproducible release coverage with no other direct dependency changes.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nپکیج `Uvicorn` هاب از نسخه 0.34.0 به 0.52.4 ارتقا داده شد؛ HTTP و WebSocket واقعی روی ASGI، شروع Hub و کل تست‌های پنل بررسی می‌شوند و هیچ dependency مستقیم دیگری تغییر نمی‌کند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.42\n- Dependency hardening: pin `uvicorn[standard]==0.52.4` (from 0.34.0).\n- Add permanent real HTTP/WebSocket ASGI runtime compatibility coverage.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n''' + changelog.read_text())

print('prepared v2.9.42 uvicorn hardening')
