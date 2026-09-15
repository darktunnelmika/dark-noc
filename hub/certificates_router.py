import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request


def register_certificates_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    CertificateBody = deps["CertificateBody"]
    issue_certificate_mutation = deps["issue_certificate_mutation"]
    renew_certificate_mutation = deps["renew_certificate_mutation"]
    CertificateFleetServiceError = deps["CertificateFleetServiceError"]
    utc_ts = deps["utc_ts"]
    normalize_ip = deps["normalize_ip"]
    audit = deps["audit"]
    NODE_STALE_AFTER = deps["node_stale_after"]

    @router.get("/api/certificates")
    def list_certificates(_: sqlite3.Row = Depends(current_user)):
        with db() as conn:
            rows = conn.execute("SELECT c.*,n.name node_name,n.host node_host FROM certificates c JOIN nodes n ON n.id=c.node_id ORDER BY c.domain").fetchall()
        return [{key: row[key] for key in row.keys() if key not in {"key_path"}} for row in rows]

    @router.post("/api/certificates", status_code=202)
    def issue_certificate(body: CertificateBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            mutation = issue_certificate_mutation(
                db, body, user["id"], utc_ts=utc_ts,
                node_stale_after=NODE_STALE_AFTER, normalize_ip=normalize_ip,
            )
        except CertificateFleetServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(
            user["id"], "certificate_issue", mutation["domain"], mutation["node_name"],
            request.client.host if request.client else None,
        )
        return {
            "certificate_id": mutation["certificate_id"],
            "job_id": mutation["job_id"],
            "status": "pending",
        }

    @router.post("/api/certificates/{certificate_id}/renew", status_code=202)
    def renew_certificate(certificate_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            cert, job_id = renew_certificate_mutation(
                db, certificate_id, user["id"], utc_ts=utc_ts, node_stale_after=NODE_STALE_AFTER,
            )
        except CertificateFleetServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(
            user["id"], "certificate_renew", cert["domain"], str(cert["node_id"]),
            request.client.host if request.client else None,
        )
        return {"job_id": job_id, "status": "renewing"}

    app.include_router(router)
    return router
