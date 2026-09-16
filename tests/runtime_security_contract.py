from __future__ import annotations

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
    assert "HttpOnly" in login.headers.get("set-cookie", "")
    assert "SameSite=strict" in login.headers.get("set-cookie", "")
    assert "Path=/" in login.headers.get("set-cookie", "")

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
        with client.websocket_connect("/ws/live", headers={"origin": "https://evil.example"}):
            raise AssertionError("cross-origin WebSocket unexpectedly opened")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403

    with client.websocket_connect("/ws/live", headers={"origin": "https://testserver"}) as websocket:
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
assert "websocket_origin_allowed" in live_router
assert "LIVE_MESSAGE_LIMIT" in live_router
assert 'VERSION = "2.9.46"' in app_source
print("Runtime security contract passed")
