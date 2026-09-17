from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Hub runtime: same-origin browser guard, no-store API responses, websocket
# origin validation, manifest-driven plugin telemetry and v2.9.46 version.
# ---------------------------------------------------------------------------
app_path = "hub/app.py"
app = read(app_path)
app = replace_once(app, 'VERSION = "2.9.45"', 'VERSION = "2.9.46"', "Hub version")

runtime_marker = "del _runtime_name\n\n_SCHEMAS_PATH = Path(__file__).resolve().with_name('schemas.py')"
runtime_block = '''del _runtime_name

_SECURITY_RUNTIME_PATH = Path(__file__).resolve().with_name("security_runtime.py")
_security_runtime_spec = _realm_support_importlib_util.spec_from_file_location("dark_noc_security_runtime", _SECURITY_RUNTIME_PATH)
if _security_runtime_spec is None or _security_runtime_spec.loader is None:
    raise ImportError(f"Could not load runtime security helpers: {_SECURITY_RUNTIME_PATH}")
_security_runtime_module = _realm_support_importlib_util.module_from_spec(_security_runtime_spec)
_security_runtime_spec.loader.exec_module(_security_runtime_module)
browser_origin_allowed = _security_runtime_module.browser_origin_allowed
enforce_http_origin = _security_runtime_module.enforce_http_origin

_SCHEMAS_PATH = Path(__file__).resolve().with_name('schemas.py')'''
app = replace_once(app, runtime_marker, runtime_block, "security runtime loader")

middleware_marker = '''@app.middleware("http")
async def security_headers(request: Request, call_next):'''
middleware_block = '''@app.middleware("http")
async def browser_origin_guard(request: Request, call_next):
    try:
        enforce_http_origin(request.method, request.url.path, request.headers, request.url.scheme)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):'''
app = replace_once(app, middleware_marker, middleware_block, "browser origin middleware")

headers_old = '''    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path in {"/", "/static/app.js", "/static/styles.css"}:
        response.headers["Cache-Control"] = "no-store"
    return response
'''
headers_new = '''    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith("/api/") or request.url.path in {"/", "/static/app.js", "/static/styles.css"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response
'''
app = replace_once(app, headers_old, headers_new, "security response headers")

managed_old = '''def managed_tunnel_report(method: str, service: str) -> bool:
    return (
        (method == "DARK Backhaul" and service.startswith("backhaul@"))
        or (method == "DARK Ghost Pro" and service.startswith("ghostpro@"))
        or (method == "DARK Packet Pro" and service.startswith("paqetpro@"))
        or (method == "DARK Realm Pro" and service.startswith("dark-realm@"))
    )
'''
managed_new = '''def managed_tunnel_report(method: str, service: str) -> bool:
    return any(
        method == str(plugin.get("ui", {}).get("method", ""))
        and service.startswith(str(plugin.get("runtime", {}).get("service_prefix", "")))
        for plugin in PLUGIN_CATALOG
        if str(plugin.get("runtime", {}).get("service_prefix", ""))
    )
'''
app = replace_once(app, managed_old, managed_new, "manifest-driven tunnel telemetry")

ws_marker = '''_ssh_terminal_router, _ssh_terminal_handlers = register_ssh_terminal_router(
    app,
    websocket_user=websocket_user, db=db, decrypt=decrypt, token_hash=token_hash, utc_ts=utc_ts, audit=audit,
    websocket_session_valid=websocket_session_valid, websocket_session_guard=websocket_session_guard, LOGGER=LOGGER,
    get_session_ttl=lambda: SESSION_TTL, get_ssh_keepalive_interval=lambda: SSH_KEEPALIVE_INTERVAL,
    get_ssh_keepalive_count_max=lambda: SSH_KEEPALIVE_COUNT_MAX,
)'''
ws_replacement = '''def websocket_origin_allowed(websocket: WebSocket) -> bool:
    return browser_origin_allowed(websocket.headers, websocket.url.scheme)


_ssh_terminal_router, _ssh_terminal_handlers = register_ssh_terminal_router(
    app,
    websocket_user=websocket_user, db=db, decrypt=decrypt, token_hash=token_hash, utc_ts=utc_ts, audit=audit,
    websocket_session_valid=websocket_session_valid, websocket_session_guard=websocket_session_guard,
    websocket_origin_allowed=websocket_origin_allowed, LOGGER=LOGGER,
    get_session_ttl=lambda: SESSION_TTL, get_ssh_keepalive_interval=lambda: SSH_KEEPALIVE_INTERVAL,
    get_ssh_keepalive_count_max=lambda: SSH_KEEPALIVE_COUNT_MAX,
)'''
app = replace_once(app, ws_marker, ws_replacement, "SSH websocket origin wiring")

live_old = '''_live_router, _live_handlers = register_live_router(
    app,
    websocket_user=websocket_user, token_hash=token_hash, utc_ts=utc_ts,
    websocket_session_guard=websocket_session_guard, get_session_ttl=lambda: SESSION_TTL,
    get_live_clients=lambda: LIVE_CLIENTS,
)'''
live_new = '''_live_router, _live_handlers = register_live_router(
    app,
    websocket_user=websocket_user, token_hash=token_hash, utc_ts=utc_ts,
    websocket_session_guard=websocket_session_guard, websocket_origin_allowed=websocket_origin_allowed,
    get_session_ttl=lambda: SESSION_TTL, get_live_clients=lambda: LIVE_CLIENTS,
)'''
app = replace_once(app, live_old, live_new, "live websocket origin wiring")
write(app_path, app)


# ---------------------------------------------------------------------------
# File Manager: resolve the parent directory server-side before every
# destructive operation. This prevents /tmp/link -> /etc style bypasses.
# ---------------------------------------------------------------------------
file_path = "hub/ssh_file_router.py"
files = read(file_path)
helper_marker = '''from fastapi.responses import StreamingResponse


def register_ssh_file_router(app, **deps):'''
helper_block = '''from fastapi.responses import StreamingResponse


async def resolved_destructive_path(sftp, path: str, destructive_remote_path_allowed) -> str:
    """Resolve a path's parent so symlinked directories cannot bypass protected trees."""
    parent = posixpath.dirname(path) or "/"
    basename = posixpath.basename(path)
    resolved_parent = str(await sftp.realpath(parent))
    resolved = posixpath.normpath(posixpath.join(resolved_parent, basename))
    if not destructive_remote_path_allowed(resolved):
        raise HTTPException(400, "Resolved remote path enters a protected system tree")
    return resolved


def register_ssh_file_router(app, **deps):'''
files = replace_once(files, helper_marker, helper_block, "resolved destructive path helper")

upload_old = '''                                if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                if await sftp_path_exists(sftp, destination) and not overwrite:'''
upload_new = '''                                if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                await resolved_destructive_path(sftp, destination, destructive_remote_path_allowed)
                                if await sftp_path_exists(sftp, destination) and not overwrite:'''
files = replace_once(files, upload_old, upload_new, "upload symlink guard")

relay_old = '''                                    if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                        raise HTTPException(400, "Destination parent is not a directory")
                                    if await sftp_path_exists(destination_sftp, body.destination_path) and not body.overwrite:'''
relay_new = '''                                    if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                        raise HTTPException(400, "Destination parent is not a directory")
                                    await resolved_destructive_path(destination_sftp, body.destination_path, destructive_remote_path_allowed)
                                    if await sftp_path_exists(destination_sftp, body.destination_path) and not body.overwrite:'''
files = replace_once(files, relay_old, relay_new, "relay symlink guard")

write_old = '''                                if parent.permissions is not None and not statmod.S_ISDIR(parent.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                if await sftp_path_exists(sftp, body.path) and not body.overwrite:'''
write_new = '''                                if parent.permissions is not None and not statmod.S_ISDIR(parent.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                await resolved_destructive_path(sftp, body.path, destructive_remote_path_allowed)
                                if await sftp_path_exists(sftp, body.path) and not body.overwrite:'''
files = replace_once(files, write_old, write_new, "editor symlink guard")

action_old = '''                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.destination or body.path)):
                                if body.action == "mkdir":'''
action_new = '''                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.destination or body.path)):
                                await resolved_destructive_path(sftp, body.path, destructive_remote_path_allowed)
                                if body.action == "rename" and body.destination:
                                    await resolved_destructive_path(sftp, body.destination, destructive_remote_path_allowed)
                                if body.action == "chmod":
                                    link_attrs = await sftp.lstat(body.path)
                                    if link_attrs.permissions is not None and statmod.S_ISLNK(link_attrs.permissions):
                                        raise HTTPException(400, "CHMOD through a symbolic link is not allowed")
                                if body.action == "mkdir":'''
files = replace_once(files, action_old, action_new, "file action symlink guard")
write(file_path, files)


# ---------------------------------------------------------------------------
# Release/runtime version markers. Tests are current-version contracts, so
# update their exact v2.9.45 expectations without touching historical notes.
# ---------------------------------------------------------------------------
for path in ["agent/agent.py", "darknoc", "install-hub.sh", "install-node.sh", "upgrade.sh", "hub/static/index.html"]:
    text = read(path)
    if "2.9.45" not in text:
        raise SystemExit(f"{path}: v2.9.45 marker missing")
    write(path, text.replace("2.9.45", "2.9.46"))

for path in sorted((ROOT / "tests").glob("*.py")):
    text = path.read_text(encoding="utf-8")
    if "2.9.45" in text:
        path.write_text(text.replace("2.9.45", "2.9.46"), encoding="utf-8")

ci_path = ".github/workflows/ci.yml"
ci = read(ci_path)
ci = replace_once(
    ci,
    "          python tests/plugin_registry_contract.py\n",
    "          python tests/plugin_registry_contract.py\n          python tests/runtime_security_contract.py\n",
    "CI runtime security test",
)
write(ci_path, ci)

release_path = ".github/workflows/release.yml"
release = read(release_path)
release = replace_once(
    release,
    "          python tests/plugin_registry_contract.py\n",
    "          python tests/plugin_registry_contract.py\n          python tests/runtime_security_contract.py\n",
    "release runtime security test",
)
write(release_path, release)

changelog = read("CHANGELOG.md")
if not changelog.startswith("## v2.9.46"):
    changelog = '''## v2.9.46
- Harden browser-origin validation for unsafe API requests and both authenticated WebSocket endpoints.
- Preserve the existing persistent SSH terminal protocol while bounding terminal messages, input size and resize dimensions.
- Prevent File Manager destructive operations from escaping protected trees through symlinked parent directories.
- Mark authenticated API responses no-store and add COOP/CORP response isolation headers.
- Extend Plugin Contract v1 with `runtime.service_prefix` so Hub telemetry acceptance is manifest-driven for future plugins.

''' + changelog
write("CHANGELOG.md", changelog)

notes = read("RELEASE_NOTES.md")
if not notes.startswith("# DARK NOC v2.9.46"):
    notes = '''# DARK NOC v2.9.46 — Runtime Security Hardening

- Enforce same-origin browser metadata on unsafe API requests and authenticated WebSocket handshakes.
- Keep the existing tmux-backed SSH Terminal behavior while limiting WebSocket frame/input size and terminal resize ranges.
- Resolve remote parent directories before destructive File Manager actions so symlinked directories cannot redirect writes, deletes, renames or chmod into protected system trees.
- Send authenticated API responses with `Cache-Control: no-store`, `Pragma: no-cache`, COOP and CORP isolation headers.
- Add `runtime.service_prefix` to Plugin Contract v1 and use it for Hub tunnel telemetry acceptance, removing the last four-plugin hardcode from that path.
- Add permanent runtime-security regression coverage.

## فارسی

در این نسخه امنیت Runtime پنل، WebSocketهای Live/SSH و File Manager سخت‌گیرانه‌تر شده است. مسیرهای تخریبی File Manager قبل از اجرا روی سرور Resolve می‌شوند تا Symlink نتواند محدودیت مسیرهای محافظت‌شده را دور بزند. همچنین Telemetry پلاگین‌ها از `service_prefix` داخل Manifest استفاده می‌کند تا اضافه‌کردن پلاگین جدید نیازمند هاردکد جدید در Hub نباشد.

---

''' + notes
write("RELEASE_NOTES.md", notes)

print("Prepared DARK NOC v2.9.46 Runtime Security Hardening")
