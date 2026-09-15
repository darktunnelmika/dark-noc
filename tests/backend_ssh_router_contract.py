from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
files = (root / "hub/ssh_file_router.py").read_text()
terminal = (root / "hub/ssh_terminal_router.py").read_text()

assert "with_name('ssh_file_router.py')" in app
assert "with_name('ssh_terminal_router.py')" in app
assert 'register_ssh_file_router(' in app
assert 'register_ssh_terminal_router(' in app
for marker in [
    '@app.post("/api/ssh/upload/{node_id}")', '@app.post("/api/ssh/relay")',
    '@app.get("/api/ssh/files/{node_id}")', '@app.get("/api/ssh/files/{node_id}/read")',
    '@app.put("/api/ssh/files/{node_id}/write")', '@app.post("/api/ssh/files/{node_id}/action")',
    '@app.get("/api/ssh/files/{node_id}/checksum")', '@app.get("/api/ssh/files/{node_id}/download")',
    '@app.websocket("/ws/ssh/{node_id}")',
]:
    assert marker not in app, marker
for path in [
    '/api/ssh/upload/{node_id}', '/api/ssh/relay', '/api/ssh/files/{node_id}',
    '/api/ssh/files/{node_id}/read', '/api/ssh/files/{node_id}/write',
    '/api/ssh/files/{node_id}/action', '/api/ssh/files/{node_id}/checksum',
    '/api/ssh/files/{node_id}/download',
]:
    assert path in files, path
assert '/ws/ssh/{node_id}' in terminal
for marker in [
    'get_ssh_upload_limit()', 'get_ssh_transfer_timeout()', 'get_ssh_transfer_semaphore()',
    'get_ssh_file_chunk()', 'get_ssh_editor_limit()',
]:
    assert marker in files, marker
for marker in ['get_session_ttl()', 'get_ssh_keepalive_interval()', 'get_ssh_keepalive_count_max()']:
    assert marker in terminal, marker
# High-risk helpers and middleware remain centralized in app.py for this slice.
for helper in [
    'def ssh_file_node(', 'async def finalize_sftp_file(', 'def ssh_file_error(',
    'async def disconnect_aware_semaphore(', 'def ssh_connection_options(',
    'async def websocket_session_guard(',
]:
    assert helper in app, helper
assert 'request.url.path.startswith("/api/ssh/upload/")' in app
assert '@app.websocket("/ws/live")' in app
assert 'def agent_heartbeat' in app and 'def agent_job_result' in app
# Compatibility aliases are retained for regression tests and internal callers.
assert 'globals()[_ssh_file_name] = _ssh_file_handler' in app
assert 'globals()[_ssh_terminal_name] = _ssh_terminal_handler' in app
assert 'VERSION = "2.9.34"' in app
print('SSH/File Transfer router boundary passed')
