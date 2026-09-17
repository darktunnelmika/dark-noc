from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]

security_spec = importlib.util.spec_from_file_location("dark_noc_security_runtime_test", ROOT / "hub/security_runtime.py")
assert security_spec is not None and security_spec.loader is not None
security = importlib.util.module_from_spec(security_spec)
security_spec.loader.exec_module(security)

old_host = os.environ.pop("DARK_NOC_PUBLIC_HOST", None)
old_port = os.environ.pop("DARK_NOC_PUBLIC_PORT", None)
try:
    same = {"host": "noc.example.com:9090", "origin": "https://noc.example.com:9090", "sec-fetch-site": "same-origin"}
    assert security.browser_origin_allowed(same, "https")
    assert security.browser_origin_allowed({"host": "noc.example.com:9090"}, "https")
    assert not security.browser_origin_allowed({**same, "origin": "https://evil.example"}, "https")
    assert not security.browser_origin_allowed({"host": "noc.example.com:9090", "sec-fetch-site": "cross-site"}, "https")
    assert not security.browser_origin_allowed({"host": "noc.example.com:9090", "origin": "null"}, "https")
    security.enforce_http_origin("GET", "/api/nodes", {"origin": "https://evil.example"}, "https")
    security.enforce_http_origin("POST", "/healthz", {"origin": "https://evil.example"}, "https")
    try:
        security.enforce_http_origin("POST", "/api/nodes", {**same, "origin": "https://evil.example"}, "https")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("cross-origin unsafe API request was accepted")

    os.environ["DARK_NOC_PUBLIC_HOST"] = "noc.example.com"
    os.environ["DARK_NOC_PUBLIC_PORT"] = "9090"
    assert security.browser_origin_allowed(
        {"host": "internal.invalid", "origin": "https://noc.example.com:9090", "sec-fetch-site": "same-origin"},
        "https",
    )
    assert not security.browser_origin_allowed(
        {"host": "internal.invalid", "origin": "https://noc.example.com", "sec-fetch-site": "same-origin"},
        "https",
    )
finally:
    if old_host is None:
        os.environ.pop("DARK_NOC_PUBLIC_HOST", None)
    else:
        os.environ["DARK_NOC_PUBLIC_HOST"] = old_host
    if old_port is None:
        os.environ.pop("DARK_NOC_PUBLIC_PORT", None)
    else:
        os.environ["DARK_NOC_PUBLIC_PORT"] = old_port


file_router_spec = importlib.util.spec_from_file_location("dark_noc_ssh_file_security_test", ROOT / "hub/ssh_file_router.py")
assert file_router_spec is not None and file_router_spec.loader is not None
file_router_module = importlib.util.module_from_spec(file_router_spec)
file_router_spec.loader.exec_module(file_router_module)


class FakeResolvedSFTP:
    async def realpath(self, path: str) -> str:
        return {"/tmp/link": "/etc", "/tmp/work": "/tmp/work"}.get(path, path)


async def check_symlink_guard() -> None:
    safe = await file_router_module.resolved_destructive_path(
        FakeResolvedSFTP(), "/tmp/work/report.txt", lambda path: not path.startswith("/etc")
    )
    assert safe == "/tmp/work/report.txt"
    try:
        await file_router_module.resolved_destructive_path(
            FakeResolvedSFTP(), "/tmp/link/shadow", lambda path: not path.startswith("/etc")
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("symlinked parent escaped the protected-tree guard")


asyncio.run(check_symlink_guard())


def load_hub_app():
    data_dir = tempfile.TemporaryDirectory(prefix="dark-noc-security-")
    os.environ["DARK_NOC_DATA"] = data_dir.name
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "Runtime-Security-Test-1234"
    os.environ["DARK_NOC_PUBLIC_HOST"] = "testserver"
    os.environ["DARK_NOC_PUBLIC_PORT"] = "443"
    module_name = "dark_noc_runtime_security_app"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "hub/app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module._runtime_security_test_data_dir = data_dir
    return module


HUB = load_hub_app()
with TestClient(HUB.app, base_url="https://testserver") as client:
    denied_login = client.post(
        "/api/auth/login",
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
        json={"username": "admin", "password": "Runtime-Security-Test-1234"},
    )
    assert denied_login.status_code == 403

    login = client.post(
        "/api/auth/login",
        headers={"origin": "https://testserver", "sec-fetch-site": "same-origin"},
        json={"username": "admin", "password": "Runtime-Security-Test-1234"},
    )
    assert login.status_code == 200, login.text
    set_cookie = login.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Secure" in set_cookie
    session_cookie = client.cookies.get("dark_noc_session")
    assert session_cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.headers.get("cache-control") == "no-store"
    assert me.headers.get("pragma") == "no-cache"
    assert me.headers.get("cross-origin-opener-policy") == "same-origin"
    assert me.headers.get("cross-origin-resource-policy") == "same-origin"

    denied_logout = client.post(
        "/api/auth/logout",
        headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
    )
    assert denied_logout.status_code == 403

    try:
        with client.websocket_connect(
            "/ws/live",
            headers={"origin": "https://evil.example", "cookie": f"dark_noc_session={session_cookie}"},
        ):
            raise AssertionError("cross-origin WebSocket unexpectedly opened")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403

    # Starlette's in-process WebSocket transport uses ws:// even when the HTTP
    # TestClient has an https:// base URL, so explicitly carry the Secure cookie.
    # Production browsers connect over wss:// through nginx and send it normally.
    with client.websocket_connect(
        "/ws/live",
        headers={"origin": "https://testserver", "cookie": f"dark_noc_session={session_cookie}"},
    ) as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        websocket.send_text("x" * 5000)
        try:
            websocket.receive_json()
        except WebSocketDisconnect as exc:
            assert exc.code == 4409
        else:
            raise AssertionError("oversized live WebSocket message was not rejected")

app_source = (ROOT / "hub/app.py").read_text()
terminal = (ROOT / "hub/ssh_terminal_router.py").read_text()
file_router = (ROOT / "hub/ssh_file_router.py").read_text()
live_router = (ROOT / "hub/live_router.py").read_text()
assert "enforce_http_origin" in app_source
assert "browser_origin_allowed" in app_source
assert "resolved_destructive_path" in file_router
assert "await sftp.realpath" in file_router
assert "websocket_origin_allowed" in terminal
assert "SSH_TERMINAL_MESSAGE_LIMIT" in terminal
assert '"type": "output"' in terminal
assert "tmux new-session -A" in terminal
assert "websocket_origin_allowed" in live_router
assert "LIVE_MESSAGE_LIMIT" in live_router
assert 'VERSION = "2.9.47"' in app_source
print("Runtime security contract passed")
