import asyncio
import json
import sqlite3

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
    websocket_session_valid = deps["websocket_session_valid"]
    websocket_origin_allowed = deps["websocket_origin_allowed"]
    decrypt = deps["decrypt"]
    db = deps["db"]
    ssh_connection_options = deps["ssh_connection_options"]
    audit = deps["audit"]
    LOGGER = deps["LOGGER"]
    get_session_ttl = deps["get_session_ttl"]
    get_ssh_keepalive_interval = deps["get_ssh_keepalive_interval"]
    get_ssh_keepalive_count_max = deps["get_ssh_keepalive_count_max"]

    @router.websocket("/ws/ssh/{node_id}")
    async def ssh_terminal(websocket: WebSocket, node_id: int):
        if not websocket_origin_allowed(websocket):
            await websocket.close(code=4403)
            return
        auth = websocket_user(websocket)
        if not auth:
            await websocket.close(code=4401)
            return
        session_hash, session_expires_at, _ = auth
        with db() as conn:
            node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            await websocket.close(code=4404)
            return
        if not (node["ssh_password_enc"] or node["ssh_key_enc"]):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        password, key = decrypt(node["ssh_password_enc"]), decrypt(node["ssh_key_enc"])
        ssh = process = None
        try:
            ssh = await asyncssh.connect(**ssh_connection_options(node, password, key))
            process = await ssh.create_process(term_type="xterm-256color", term_size=(120, 34))
            await websocket.send_json({"type": "ready", "node": node["name"]})

            async def output_stream() -> None:
                while True:
                    data = await process.stdout.read(4096)
                    if not data:
                        break
                    await websocket.send_json({"type": "data", "data": data})

            async def error_stream() -> None:
                while True:
                    data = await process.stderr.read(4096)
                    if not data:
                        break
                    await websocket.send_json({"type": "data", "data": data})

            async def input_stream() -> None:
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
                    message_type = message.get("type")
                    if message_type == "input":
                        data = str(message.get("data", ""))
                        if len(data.encode("utf-8")) > SSH_TERMINAL_INPUT_LIMIT:
                            await websocket.close(code=4409)
                            return
                        process.stdin.write(data)
                    elif message_type == "resize":
                        try:
                            cols = int(message.get("cols", 120))
                            rows = int(message.get("rows", 34))
                        except (TypeError, ValueError, OverflowError):
                            continue
                        if not (
                            SSH_TERMINAL_MIN_COLS <= cols <= SSH_TERMINAL_MAX_COLS
                            and SSH_TERMINAL_MIN_ROWS <= rows <= SSH_TERMINAL_MAX_ROWS
                        ):
                            continue
                        process.change_terminal_size(cols, rows)

            async def session_guard() -> None:
                while True:
                    await asyncio.sleep(5)
                    if not websocket_session_valid(session_hash, session_expires_at):
                        try:
                            await websocket.send_json({"type": "error", "message": "Session expired or revoked"})
                        except Exception:
                            pass
                        await websocket.close(code=4401)
                        return

            output_task = asyncio.create_task(output_stream())
            error_task = asyncio.create_task(error_stream())
            input_task = asyncio.create_task(input_stream())
            guard_task = asyncio.create_task(session_guard())
            done, pending = await asyncio.wait(
                {output_task, error_task, input_task, guard_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    await task
                except WebSocketDisconnect:
                    pass
        except (asyncssh.Error, OSError) as exc:
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:
            LOGGER.debug("SSH WebSocket ended: %s", exc)
        finally:
            if process is not None:
                try:
                    process.stdin.write_eof()
                    process.terminate()
                    await asyncio.wait_for(process.wait_closed(), 2)
                except Exception:
                    pass
            if ssh is not None:
                ssh.close()
                try:
                    await asyncio.wait_for(ssh.wait_closed(), 2)
                except Exception:
                    pass
            try:
                audit(auth[2]["id"], "ssh_terminal", node["name"], "Interactive SSH session closed")
            except Exception:
                pass

    app.include_router(router)
    return router, ssh_terminal
