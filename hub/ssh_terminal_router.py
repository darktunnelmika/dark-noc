import asyncio
import hmac
import json
import re
import shlex
from typing import Any

import asyncssh
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

SSH_TERMINAL_MESSAGE_LIMIT = 512 * 1024
SSH_TERMINAL_INPUT_LIMIT = 256 * 1024
SSH_TERMINAL_MIN_COLS = 20
SSH_TERMINAL_MAX_COLS = 1000
SSH_TERMINAL_MIN_ROWS = 5
SSH_TERMINAL_MAX_ROWS = 300


def register_ssh_terminal_router(app, **deps):
    router = APIRouter()
    websocket_user = deps["websocket_user"]
    db = deps["db"]
    decrypt = deps["decrypt"]
    token_hash = deps["token_hash"]
    utc_ts = deps["utc_ts"]
    audit = deps["audit"]
    websocket_session_valid = deps["websocket_session_valid"]
    websocket_session_guard = deps["websocket_session_guard"]
    websocket_origin_allowed = deps["websocket_origin_allowed"]
    LOGGER = deps["LOGGER"]
    get_session_ttl = deps["get_session_ttl"]
    get_ssh_keepalive_interval = deps["get_ssh_keepalive_interval"]
    get_ssh_keepalive_count_max = deps["get_ssh_keepalive_count_max"]

    @router.websocket("/ws/ssh/{node_id}")
    async def ssh_terminal(websocket: WebSocket, node_id: int):
        if not websocket_origin_allowed(websocket):
            await websocket.close(code=4403)
            return
        user = await websocket_user(websocket)
        if not user:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        with db() as conn:
            node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            await websocket.send_json({"type": "error", "message": "Node not found"})
            await websocket.close(code=4404)
            return
        password, key = decrypt(node["ssh_password_enc"]), decrypt(node["ssh_key_enc"])
        if not password and not key:
            await websocket.send_json({"type": "error", "message": "SSH credentials are not configured"})
            await websocket.close(code=4403)
            return
        session_digest = token_hash(websocket.cookies.get("dark_noc_session", ""))
        session_deadline = min(
            int(user["session_expires_at"]),
            int(user["session_created_at"]) + get_session_ttl(),
            utc_ts() + get_session_ttl(),
        )
        requested_session = str(websocket.query_params.get("session", "workspace"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", requested_session):
            await websocket.send_json({"type": "error", "message": "Invalid persistent terminal session name"})
            await websocket.close(code=4400)
            return
        terminal_session = f"dn-{user['id']}-{node_id}-{requested_session}"[:64]
        audit(user["id"], "ssh_open", node["name"], "Interactive terminal opened", websocket.client.host if websocket.client else None)
        try:
            expected_fingerprint = node["ssh_host_fingerprint"]

            class PinnedSSHClient(asyncssh.SSHClient):
                def validate_host_public_key(self, host: str, addr: str, port: int, host_key: Any) -> bool:
                    fingerprint = host_key.get_fingerprint("sha256")
                    if expected_fingerprint:
                        return hmac.compare_digest(expected_fingerprint, fingerprint)
                    with db() as pin_conn:
                        current = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                        if not current:
                            return False
                        if current["ssh_host_fingerprint"]:
                            return hmac.compare_digest(current["ssh_host_fingerprint"], fingerprint)
                        changed = pin_conn.execute(
                            "UPDATE nodes SET ssh_host_fingerprint=?,updated_at=? WHERE id=? AND ssh_host_fingerprint IS NULL",
                            (fingerprint, utc_ts(), node_id),
                        ).rowcount
                        if changed == 1:
                            return True
                        saved = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                        return bool(
                            saved
                            and saved["ssh_host_fingerprint"]
                            and hmac.compare_digest(saved["ssh_host_fingerprint"], fingerprint)
                        )

            options: dict[str, Any] = {
                "host": node["host"],
                "port": node["ssh_port"],
                "username": node["ssh_user"],
                "known_hosts": ([], [], [], [], [], [], []),
                "client_factory": PinnedSSHClient,
                "connect_timeout": 12,
                "keepalive_interval": get_ssh_keepalive_interval(),
                "keepalive_count_max": get_ssh_keepalive_count_max(),
            }
            if key:
                options["client_keys"] = [asyncssh.import_private_key(key)]
            if password:
                options["password"] = password
            async with asyncssh.connect(**options) as conn:
                if not websocket_session_valid(session_digest, user["id"], session_deadline):
                    await websocket.close(code=4401)
                    return
                tmux_probe = await conn.run("command -v tmux >/dev/null 2>&1", check=False)
                persistent = tmux_probe.exit_status == 0
                if persistent:
                    command = f"exec tmux new-session -A -s {shlex.quote(terminal_session)}"
                    process = await conn.create_process(command, term_type="xterm-256color", term_size=(120, 32))
                else:
                    process = await conn.create_process(term_type="xterm-256color", term_size=(120, 32))

                async def ssh_to_ws():
                    while not process.stdout.at_eof():
                        chunk = await process.stdout.read(4096)
                        if chunk:
                            await websocket.send_json({"type": "output", "data": chunk})

                async def ws_to_ssh():
                    while True:
                        raw = await websocket.receive_text()
                        if len(raw.encode("utf-8")) > SSH_TERMINAL_MESSAGE_LIMIT:
                            await websocket.close(code=4409)
                            return
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            await websocket.close(code=4400)
                            return
                        if not isinstance(message, dict):
                            await websocket.close(code=4400)
                            return
                        if message.get("type") == "input":
                            data = str(message.get("data", ""))
                            if len(data.encode("utf-8")) > SSH_TERMINAL_INPUT_LIMIT:
                                await websocket.close(code=4409)
                                return
                            process.stdin.write(data)
                        elif message.get("type") == "resize":
                            try:
                                cols = int(message.get("cols", 120))
                                rows = int(message.get("rows", 32))
                            except (TypeError, ValueError, OverflowError):
                                continue
                            if not (
                                SSH_TERMINAL_MIN_COLS <= cols <= SSH_TERMINAL_MAX_COLS
                                and SSH_TERMINAL_MIN_ROWS <= rows <= SSH_TERMINAL_MAX_ROWS
                            ):
                                continue
                            process.change_terminal_size(cols, rows)
                        elif message.get("type") == "ping":
                            await websocket.send_json({"type": "pong", "server_time": utc_ts()})

                with db() as pin_conn:
                    pinned = pin_conn.execute("SELECT ssh_host_fingerprint FROM nodes WHERE id=?", (node_id,)).fetchone()
                await websocket.send_json(
                    {
                        "type": "connected",
                        "node": node["name"],
                        "fingerprint": pinned["ssh_host_fingerprint"] if pinned else None,
                        "persistent": persistent,
                        "session": terminal_session,
                    }
                )
                auth_task = asyncio.create_task(websocket_session_guard(session_digest, user["id"], session_deadline))
                tasks = {asyncio.create_task(ssh_to_ws()), asyncio.create_task(ws_to_ssh()), auth_task}
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, *pending, return_exceptions=True)
                if auth_task in done and auth_task.result() is False:
                    await websocket.close(code=4401)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            try:
                LOGGER.warning("SSH session failed for node %s: %s", node_id, exc)
                detail = str(exc).strip().replace("\n", " ")[:240] or exc.__class__.__name__
                await websocket.send_json({"type": "error", "message": f"SSH connection failed: {detail}"})
            except Exception:
                pass
        finally:
            audit(user["id"], "ssh_close", node["name"], "Interactive terminal closed", websocket.client.host if websocket.client else None)
            try:
                await websocket.close()
            except Exception:
                pass

    handlers = {"ssh_terminal": ssh_terminal}
    app.include_router(router)
    return router, handlers
