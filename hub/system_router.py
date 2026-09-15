import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse


def register_system_router(app, **deps):
    router = APIRouter()
    db = deps["db"]
    VERSION = deps["version"]
    KEY_PATH = deps["key_path"]
    STATIC_DIR = deps["static_dir"]
    HUB_INSTANCE_ID = deps["hub_instance_id"]
    current_user = deps["current_user"]
    METRIC_RAW_RETENTION_DAYS = deps["metric_raw_retention_days"]
    METRIC_ROLLUP_RETENTION_DAYS = deps["metric_rollup_retention_days"]
    TUNNEL_SAMPLE_RETENTION_DAYS = deps["tunnel_sample_retention_days"]
    MONITOR_RESULT_RETENTION_DAYS = deps["monitor_result_retention_days"]

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

    @router.get("/api/system/status")
    def system_status(_: sqlite3.Row = Depends(current_user)):
        with db() as conn:
            leader = conn.execute("SELECT holder,expires_at,updated_at FROM hub_leases WHERE name='maintenance'").fetchone()
            counts = {
                "nodes": conn.execute("SELECT COUNT(*) count FROM nodes").fetchone()["count"],
                "monitors": conn.execute("SELECT COUNT(*) count FROM monitors").fetchone()["count"],
                "queued_jobs": conn.execute("SELECT COUNT(*) count FROM jobs WHERE status='queued'").fetchone()["count"],
                "metric_samples": conn.execute("SELECT COUNT(*) count FROM metrics").fetchone()["count"],
                "rollup_samples": conn.execute("SELECT COUNT(*) count FROM metric_rollups").fetchone()["count"],
                "tunnel_samples": conn.execute("SELECT COUNT(*) count FROM tunnel_samples").fetchone()["count"],
            }
        return {
            "version": VERSION, "instance": HUB_INSTANCE_ID,
            "maintenance_leader": dict(leader) if leader else None,
            "retention": {
                "raw_metrics_days": METRIC_RAW_RETENTION_DAYS,
                "rollups_days": METRIC_ROLLUP_RETENTION_DAYS,
                "tunnel_samples_days": TUNNEL_SAMPLE_RETENTION_DAYS,
                "monitor_results_days": MONITOR_RESULT_RETENTION_DAYS,
            },
            "counts": counts,
        }

    app.include_router(router)
    return router
