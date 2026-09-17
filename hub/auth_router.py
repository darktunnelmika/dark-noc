import os
import secrets
import sqlite3
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse


def register_auth_router(app, **deps):
    router = APIRouter()
    LoginBody = deps["LoginBody"]
    current_user = deps["current_user"]
    db = deps["db"]
    login_rate_check = deps["login_rate_check"]
    login_rate_record = deps["login_rate_record"]
    verify_password = deps["verify_password"]
    audit = deps["audit"]
    token_hash = deps["token_hash"]
    utc_ts = deps["utc_ts"]
    SESSION_TTL = deps["session_ttl"]

    def cookie_secure(request: Request) -> bool:
        if os.getenv("DARK_NOC_COOKIE_SECURE", "0") == "1":
            return True
        forwarded_proto = str(request.headers.get("x-forwarded-proto", "")).split(",", 1)[0].strip().casefold()
        return forwarded_proto == "https" or request.url.scheme.casefold() == "https"

    @router.post("/api/auth/login")
    def login(body: LoginBody, request: Request):
        client_ip = request.client.host if request.client else "unknown"
        login_rate_check(client_ip)
        now = utc_ts()
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at<=? OR created_at<=?", (now, now - SESSION_TTL))
            user = conn.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
            if not user or not verify_password(body.password, user["password_hash"]):
                login_rate_record(client_ip, False)
                audit(user["id"] if user else None, "login_failed", body.username, "Invalid credentials", client_ip)
                raise HTTPException(401, "Invalid username or password")
            token = secrets.token_urlsafe(42)
            conn.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at,ip,created_at) VALUES(?,?,?,?,?)",
                (token_hash(token), user["id"], now + SESSION_TTL, client_ip, now),
            )
        response = JSONResponse({"ok": True, "username": user["username"], "role": user["role"]})
        response.set_cookie(
            "dark_noc_session",
            token,
            httponly=True,
            secure=cookie_secure(request),
            samesite="strict",
            path="/",
            max_age=SESSION_TTL,
        )
        login_rate_record(client_ip, True)
        audit(user["id"], "login", user["username"], "Session created", client_ip)
        return response

    @router.post("/api/auth/logout")
    def logout(
        request: Request,
        user: sqlite3.Row = Depends(current_user),
        dark_noc_session: str | None = Cookie(default=None),
    ):
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(dark_noc_session or ""),))
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            "dark_noc_session",
            httponly=True,
            secure=cookie_secure(request),
            samesite="strict",
            path="/",
        )
        audit(user["id"], "logout", user["username"], "Session revoked", request.client.host if request.client else None)
        return response

    @router.get("/api/auth/me")
    def me(user: sqlite3.Row = Depends(current_user)):
        return {"username": user["username"], "role": user["role"]}

    @router.put("/api/auth/account")
    def update_account(_: sqlite3.Row = Depends(current_user)):
        raise HTTPException(403, "Account changes are server-only. Run sudo darknoc on the Hub server.")

    app.include_router(router)
    return router
