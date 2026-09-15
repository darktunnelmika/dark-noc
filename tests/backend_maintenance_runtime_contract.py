from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
runtime = (root / 'hub/maintenance_runtime.py').read_text()

assert "with_name('maintenance_runtime.py')" in app
assert '_maintenance_runtime_spec.loader.exec_module' in app
for marker in [
    'def acquire_hub_lease(', 'def queue_due_monitors(', 'def queue_due_fleet_operations(',
    'def rollup_previous_hour(', 'def run_maintenance_cycle(', 'async def maintenance_loop(',
    'def build_lifespan(',
]:
    assert marker in runtime, marker
for sql in [
    "DELETE FROM metrics WHERE ts < ?", "DELETE FROM metric_rollups WHERE bucket < ?",
    "UPDATE jobs SET status='queued',started_at=NULL", "UPDATE certificates SET status='expired'",
    "SELECT * FROM fleet_operations WHERE status='scheduled'", "SELECT monitors.* FROM monitors JOIN nodes",
]:
    assert sql in runtime, sql
    assert sql not in app, sql
for wrapper in [
    'return _maintenance_acquire_hub_lease(', '_maintenance_queue_due_monitors(',
    '_maintenance_queue_due_fleet_operations(', '_maintenance_rollup_previous_hour(',
    '_maintenance_run_cycle(', 'await _maintenance_loop_runner(', '_build_maintenance_lifespan(',
]:
    assert wrapper in app, wrapper
assert 'from contextlib import asynccontextmanager' in app
assert 'BackgroundTasks' not in app and 'UploadFile' not in app and 'StreamingResponse' not in app
assert 'from fastapi import Cookie, FastAPI, Header, HTTPException, Request, WebSocket' in app
for method in ['get', 'post', 'put', 'delete', 'patch', 'websocket']:
    assert f'@app.{method}(' not in app, method
assert '@app.middleware("http")' in app
assert 'app.mount("/static"' in app
assert 'VERSION = "2.9.36"' in app
print('Maintenance runtime/final backend composition contract passed')
