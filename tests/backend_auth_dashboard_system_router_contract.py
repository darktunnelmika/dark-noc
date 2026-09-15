from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
auth = (root / "hub/auth_router.py").read_text()
dashboard = (root / "hub/dashboard_router.py").read_text()
system = (root / "hub/system_router.py").read_text()

for name in ["auth", "dashboard", "system"]:
    assert f"with_name('{name}_router.py')" in app
    assert f"register_{name}_router" in app
for marker in [
    '@app.post("/api/auth/login")', '@app.post("/api/auth/logout")', '@app.get("/api/auth/me")',
    '@app.put("/api/auth/account")', '@app.get("/api/dashboard")', '@app.get("/api/dashboard/traffic")',
    '@app.get("/healthz")', '@app.get("/readyz")', '@app.get("/")',
]:
    assert marker not in app, marker
for path in ['/api/auth/login', '/api/auth/logout', '/api/auth/me', '/api/auth/account']:
    assert path in auth, path
for path in ['/api/dashboard', '/api/dashboard/traffic']:
    assert path in dashboard, path
for path in ['/healthz', '/readyz', '"/"', '/api/system/status']:
    assert path in system, path
assert 'login_rate_check(client_ip)' in auth
assert 'login_rate_record(client_ip, False)' in auth
assert 'response.set_cookie("dark_noc_session"' in auth
assert 'SSH_UPLOAD_LIMIT' in dashboard and 'open_incidents' in dashboard
assert 'Hub is not ready' in system and 'FileResponse(STATIC_DIR / "index.html")' in system
assert 'Depends(current_user)' in system and 'metric_raw_retention_days' in system
assert 'app.mount("/static"' in app
assert "with_name('live_router.py')" in app
assert "with_name('agent_control_router.py')" in app and "with_name('agent_control_router.py')" in app
assert 'VERSION = "2.9.36"' in app
print('Auth/Dashboard/System router boundary passed')
