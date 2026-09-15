import asyncio
from importlib.metadata import version
from pathlib import Path
import socket

import httpx
import uvicorn
import websockets

root = Path(__file__).resolve().parents[1]
hub_requirements = (root / "hub/requirements.txt").read_text().splitlines()
agent_requirements = (root / "agent/requirements.txt").read_text().splitlines()

assert "uvicorn[standard]==0.52.4" in hub_requirements
assert "uvicorn[standard]==0.34.0" not in hub_requirements
assert not any(line.startswith("uvicorn") for line in agent_requirements)
assert version("uvicorn") == "0.52.4"

for name in ["Config", "Server"]:
    assert getattr(uvicorn, name, None) is not None, name

async def asgi_app(scope, receive, send):
    if scope["type"] == "http":
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"dark-noc-uvicorn"})
        return
    if scope["type"] == "websocket":
        event = await receive()
        assert event["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        message = await receive()
        assert message["type"] == "websocket.receive" and message.get("text") == "ping"
        await send({"type": "websocket.send", "text": "pong"})
        await send({"type": "websocket.close", "code": 1000})
        return
    raise AssertionError(scope["type"])

async def run_contract():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    host, port = sock.getsockname()[:2]
    config = uvicorn.Config(
        asgi_app,
        host=host,
        port=port,
        lifespan="off",
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "uvicorn server did not start"
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://{host}:{port}/health", timeout=5)
        assert response.status_code == 200 and response.text == "dark-noc-uvicorn"
        async with websockets.connect(f"ws://{host}:{port}/ws", open_timeout=5, close_timeout=5) as websocket:
            await websocket.send("ping")
            assert await websocket.recv() == "pong"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()

asyncio.run(run_contract())
print("uvicorn dependency contract passed")
