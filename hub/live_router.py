import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


def register_live_router(app, **deps):
    router = APIRouter()
    websocket_user = deps["websocket_user"]
    token_hash = deps["token_hash"]
    utc_ts = deps["utc_ts"]
    websocket_session_guard = deps["websocket_session_guard"]
    get_session_ttl = deps["get_session_ttl"]
    get_live_clients = deps["get_live_clients"]

    @router.websocket("/ws/live")
    async def live_updates(websocket: WebSocket):
        user = await websocket_user(websocket)
        if not user:
            await websocket.close(code=4401)
            return
        session_digest = token_hash(websocket.cookies.get("dark_noc_session", ""))
        session_deadline = min(
            int(user["session_expires_at"]),
            int(user["session_created_at"]) + get_session_ttl(),
            utc_ts() + get_session_ttl(),
        )
        await websocket.accept()
        get_live_clients().add(websocket)
        try:
            await websocket.send_json({"type": "ready", "server_time": utc_ts()})

            async def receive_live_messages() -> None:
                while True:
                    raw_message = await websocket.receive_text()
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        message = {"type": raw_message}
                    if isinstance(message, dict) and message.get("type") == "ping":
                        await websocket.send_json({"type": "pong", "server_time": utc_ts()})

            auth_task = asyncio.create_task(websocket_session_guard(session_digest, user["id"], session_deadline))
            tasks = {asyncio.create_task(receive_live_messages()), auth_task}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            if auth_task in done and auth_task.result() is False:
                get_live_clients().discard(websocket)
                await websocket.close(code=4401)
        except WebSocketDisconnect:
            pass
        finally:
            get_live_clients().discard(websocket)

    handlers = {
        "live_updates": live_updates
    }
    app.include_router(router)
    return router, handlers
