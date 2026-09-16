import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

LIVE_MESSAGE_LIMIT = 4096


def register_live_router(app, **deps):
    router = APIRouter()
    websocket_user = deps["websocket_user"]
    websocket_session_guard = deps["websocket_session_guard"]
    websocket_origin_allowed = deps["websocket_origin_allowed"]
    get_live_clients = deps["get_live_clients"]
    utc_ts = deps["utc_ts"]
    LOGGER = deps["LOGGER"]

    @router.websocket("/ws/live")
    async def live_updates(websocket: WebSocket):
        if not websocket_origin_allowed(websocket):
            await websocket.close(code=4403)
            return
        auth = websocket_user(websocket)
        if not auth:
            await websocket.close(code=4401)
            return
        session_hash, session_expires_at, _ = auth
        await websocket.accept()
        get_live_clients().add(websocket)
        try:
            await websocket.send_json({"type": "ready", "server_time": utc_ts()})

            async def receiver() -> None:
                while True:
                    raw = await websocket.receive_text()
                    if len(raw.encode("utf-8")) > LIVE_MESSAGE_LIMIT:
                        await websocket.close(code=4409)
                        return
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        message = {"type": raw}
                    if isinstance(message, dict) and message.get("type") == "ping":
                        await websocket.send_json({"type": "pong", "server_time": utc_ts()})

            receive_task = asyncio.create_task(receiver())
            guard_task = asyncio.create_task(websocket_session_guard(websocket, session_hash, session_expires_at))
            done, pending = await asyncio.wait({receive_task, guard_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    await task
                except WebSocketDisconnect:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:
            LOGGER.debug("Live WebSocket ended: %s", exc)
        finally:
            get_live_clients().discard(websocket)

    @router.get("/api/live")
    def live_status():
        return {"ok": True, "server_time": int(time.time())}

    app.include_router(router)
    return router
