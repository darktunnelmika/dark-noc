from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def register_system_router(app, **deps):
    router = APIRouter()
    db = deps["db"]
    VERSION = deps["version"]
    KEY_PATH = deps["key_path"]
    STATIC_DIR = deps["static_dir"]
    HUB_INSTANCE_ID = deps["hub_instance_id"]

    @router.get("/healthz")
    def healthz():
        return {"status": "ok", "version": VERSION}

    @router.get("/readyz")
    def readyz():
        try:
            with db() as conn:
                conn.execute("SELECT 1").fetchone()
            if not KEY_PATH.is_file() or not (STATIC_DIR / "index.html").is_file():
                raise RuntimeError("Hub runtime files are incomplete")
        except Exception as exc:
            raise HTTPException(503, f"Hub is not ready: {str(exc)[:160]}") from exc
        return {"status": "ready", "version": VERSION, "instance": HUB_INSTANCE_ID}

    @router.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.include_router(router)
    return router
