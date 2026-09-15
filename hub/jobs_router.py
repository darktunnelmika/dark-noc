import sqlite3
from fastapi import APIRouter, Depends, HTTPException


def register_jobs_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    public_job = deps["public_job"]

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: int, _: sqlite3.Row = Depends(current_user)):
        with db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        return public_job(row)

    @router.get("/api/jobs")
    def list_jobs(limit: int = 100, _: sqlite3.Row = Depends(current_user)):
        limit = min(max(limit, 1), 500)
        with db() as conn:
            rows = conn.execute("SELECT jobs.*,nodes.name node_name FROM jobs JOIN nodes ON nodes.id=jobs.node_id ORDER BY jobs.id DESC LIMIT ?", (limit,)).fetchall()
        return [public_job(row) for row in rows]

    handlers = {"get_job": get_job, "list_jobs": list_jobs}
    app.include_router(router)
    return router, handlers
