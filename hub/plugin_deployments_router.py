import json
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request


def register_plugin_deployments_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    db = deps["db"]
    PLUGIN_CATALOG = deps["PLUGIN_CATALOG"]
    PairCodeDeployBody = deps["PairCodeDeployBody"]
    PluginDeployBody = deps["PluginDeployBody"]
    deploy_pair_code_mutation = deps["deploy_pair_code_mutation"]
    deploy_managed_mutation = deps["deploy_managed_mutation"]
    recover_pair_code_mutation = deps["recover_pair_code_mutation"]
    retry_hybrid_mutation = deps["retry_hybrid_mutation"]
    remove_hybrid_mutation = deps["remove_hybrid_mutation"]
    retry_managed_mutation = deps["retry_managed_mutation"]
    remove_managed_mutation = deps["remove_managed_mutation"]
    PluginDeploymentServiceError = deps["PluginDeploymentServiceError"]
    utc_ts = deps["utc_ts"]
    prepare_realm_settings = deps["prepare_realm_settings"]
    certificate_for_deployment = deps["certificate_for_deployment"]
    plugin_pair_code = deps["plugin_pair_code"]
    plugin_job_payload = deps["plugin_job_payload"]
    encrypt = deps["encrypt"]
    token_hash = deps["token_hash"]
    normalize_ip = deps["normalize_ip"]
    decrypt = deps["decrypt"]
    audit = deps["audit"]
    NODE_STALE_AFTER = deps["node_stale_after"]
    PAQET_CORE_TAG = deps["paqet_core_tag"]

    @router.get("/api/plugins")
    def list_plugins(_: sqlite3.Row = Depends(current_user)):
        return PLUGIN_CATALOG

    @router.post("/api/plugins/{plugin_id}/pair-code", status_code=202)
    def deploy_plugin_pair_code(plugin_id: str, body: PairCodeDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            mutation = deploy_pair_code_mutation(
                db, plugin_id, body, user["id"], plugin_catalog=PLUGIN_CATALOG,
                node_stale_after=NODE_STALE_AFTER, paqet_core_tag=PAQET_CORE_TAG, utc_ts=utc_ts,
                prepare_realm_settings=prepare_realm_settings,
                certificate_for_deployment=certificate_for_deployment,
                plugin_pair_code=plugin_pair_code, plugin_job_payload=plugin_job_payload,
                encrypt=encrypt, token_hash=token_hash,
            )
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_pair_create", body.name, f"{mutation['iran_name']} -> pair-code:{body.remote_label}", request.client.host if request.client else None)
        return {
            "deployment_id": mutation["deployment_id"], "mode": "pair_code", "status": "queued",
            "iran_job_id": mutation["iran_job_id"], "pair_code": mutation["pair_code"],
            "instructions": ["Wait until the IRAN side shows READY.", f"Run {mutation['catalog_name']} on the foreign server.", "Select KHAREJ, then choose Connect with DARK NOC Pair Code.", "Paste the Pair Code exactly as shown."],
        }

    @router.post("/api/plugins/{plugin_id}/deploy", status_code=202)
    def deploy_plugin(plugin_id: str, body: PluginDeployBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            mutation = deploy_managed_mutation(
                db, plugin_id, body, user["id"], plugin_catalog=PLUGIN_CATALOG,
                node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, normalize_ip=normalize_ip,
                prepare_realm_settings=prepare_realm_settings,
                certificate_for_deployment=certificate_for_deployment,
                plugin_job_payload=plugin_job_payload, encrypt=encrypt,
            )
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_deploy", body.name, f"{plugin_id}: {mutation['iran_name']} -> {mutation['kharej_name']}", request.client.host if request.client else None)
        return {"deployment_id": mutation["deployment_id"], "status": "queued", "jobs": mutation["jobs"]}

    @router.get("/api/plugin-deployments")
    def list_plugin_deployments(_: sqlite3.Row = Depends(current_user)):
        with db() as conn:
            rows = conn.execute("""
              SELECT d.*, i.name iran_node, k.name kharej_node,
                     ji.status iran_status, ji.output iran_output,
                     jk.status kharej_status, jk.output kharej_output
              FROM plugin_deployments d
              JOIN nodes i ON i.id=d.iran_node_id JOIN nodes k ON k.id=d.kharej_node_id
              LEFT JOIN jobs ji ON ji.id=d.iran_job_id LEFT JOIN jobs jk ON jk.id=d.kharej_job_id
              ORDER BY d.id DESC LIMIT 100
            """).fetchall()
            hybrid_rows = conn.execute("""
              SELECT d.*,i.name iran_node,j.status iran_status,j.output iran_output
              FROM hybrid_deployments d JOIN nodes i ON i.id=d.iran_node_id
              LEFT JOIN jobs j ON j.id=d.iran_job_id ORDER BY d.id DESC LIMIT 100
            """).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("pair_token_enc", None)
            statuses = {item.get("iran_status"), item.get("kharej_status")}
            lifecycle = item.get("lifecycle")
            if lifecycle in {"rolling_back", "rolled_back", "rollback_failed"}:
                item["status"] = lifecycle
            elif "failed" in statuses:
                item["status"] = "failed"
            elif statuses == {"completed"}:
                item["status"] = "removed" if lifecycle == "removing" else "completed"
            else:
                item["status"] = "running" if "running" in statuses or "completed" in statuses else "queued"
            item["settings"] = json.loads(item["settings"])
            item["mode"] = "managed"
            result.append(item)
        for row in hybrid_rows:
            item = dict(row)
            item.pop("pair_token_enc", None)
            iran_status = item.get("iran_status") or "queued"
            lifecycle = item.get("lifecycle")
            if lifecycle == "removing" and iran_status == "completed":
                status = "removed"
            elif iran_status == "failed":
                status = "failed"
            elif iran_status == "completed":
                status = "awaiting_pair"
            else:
                status = iran_status if iran_status in {"queued", "running"} else "queued"
            item.update({"mode": "pair_code", "kharej_node": item["remote_label"], "kharej_status": "pair code", "status": status, "settings": json.loads(item["settings"])})
            result.append(item)
        return sorted(result, key=lambda item: (item["created_at"], item["mode"] == "pair_code", item["id"]), reverse=True)[:100]

    @router.post("/api/hybrid-deployments/{deployment_id}/pair-code")
    def recover_hybrid_pair_code(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row, pair_code = recover_pair_code_mutation(
                db, deployment_id, decrypt=decrypt, plugin_pair_code=plugin_pair_code, token_hash=token_hash,
            )
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_pair_reveal", row["name"], str(deployment_id), request.client.host if request.client else None)
        return {"deployment_id": deployment_id, "pair_code": pair_code}

    @router.post("/api/hybrid-deployments/{deployment_id}/retry", status_code=202)
    def retry_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row, job_id = retry_hybrid_mutation(
                db, deployment_id, user["id"], plugin_catalog=PLUGIN_CATALOG,
                node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, decrypt=decrypt,
            )
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_pair_retry", row["name"], str(deployment_id), request.client.host if request.client else None)
        return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id}

    @router.post("/api/hybrid-deployments/{deployment_id}/remove", status_code=202)
    def remove_hybrid_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row, job_id = remove_hybrid_mutation(db, deployment_id, user["id"], utc_ts=utc_ts)
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_pair_remove", row["name"], "Iran side only; foreign side is script-managed", request.client.host if request.client else None)
        return {"deployment_id": deployment_id, "status": "queued", "iran_job_id": job_id, "foreign_action_required": True}

    @router.post("/api/plugin-deployments/{deployment_id}/retry", status_code=202)
    def retry_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row, new_jobs = retry_managed_mutation(
                db, deployment_id, user["id"], plugin_catalog=PLUGIN_CATALOG,
                node_stale_after=NODE_STALE_AFTER, utc_ts=utc_ts, decrypt=decrypt,
                plugin_job_payload=plugin_job_payload,
            )
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_retry", str(deployment_id), json.dumps(new_jobs), request.client.host if request.client else None)
        return {"deployment_id": deployment_id, "status": "queued", "jobs": new_jobs}

    @router.post("/api/plugin-deployments/{deployment_id}/remove", status_code=202)
    def remove_plugin_deployment(deployment_id: int, request: Request, user: sqlite3.Row = Depends(current_user)):
        try:
            row, jobs = remove_managed_mutation(db, deployment_id, user["id"], utc_ts=utc_ts)
        except PluginDeploymentServiceError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc
        audit(user["id"], "plugin_remove", row["name"], str(deployment_id), request.client.host if request.client else None)
        return {"deployment_id": deployment_id, "status": "queued", "jobs": jobs}

    app.include_router(router)
    return router
