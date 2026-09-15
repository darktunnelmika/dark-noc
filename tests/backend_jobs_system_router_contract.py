from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / "hub/app.py").read_text()
jobs = (root / "hub/jobs_router.py").read_text()
system = (root / "hub/system_router.py").read_text()
assert "with_name('jobs_router.py')" in app and 'register_jobs_router(' in app
assert '@app.get("/api/jobs/{job_id}")' not in app and '@app.get("/api/jobs")' not in app
assert '@app.get("/api/system/status")' not in app
assert '/api/jobs/{job_id}' in jobs and '/api/jobs' in jobs and 'public_job(row)' in jobs
assert '/api/system/status' in system and 'Depends(current_user)' in system
for marker in ['METRIC_RAW_RETENTION_DAYS','METRIC_ROLLUP_RETENTION_DAYS','TUNNEL_SAMPLE_RETENTION_DAYS','MONITOR_RESULT_RETENTION_DAYS']:
    assert marker in system, marker
assert "with_name('agent_control_router.py')" in app
assert "with_name('agent_control_router.py')" in app
assert "with_name('agent_control_router.py')" in app
assert "with_name('agent_control_router.py')" in app and "with_name('live_router.py')" in app
assert 'globals()[_job_handler_name] = _job_handler' in app
assert 'VERSION = "2.9.41"' in app
print('Generic Jobs/System status router boundary passed')
