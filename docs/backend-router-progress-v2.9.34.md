# DARK NOC Backend Router Progress — v2.9.34

Generic `/api/jobs` reads now live in `hub/jobs_router.py`, and authenticated `/api/system/status` now lives in `hub/system_router.py`. Agent `/api/agent/jobs`, heartbeat, job-result/lease state machines and `/ws/live` intentionally remain in `hub/app.py` for the final high-risk Agent Control Plane phase. No frontend or Live Matrix visual changes are included.
