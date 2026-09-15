# DARK NOC Backend Final Audit — v2.9.36

The Repository + Service + Router modularization wave is complete. This release performs the final low-risk backend composition cleanup.

## Runtime ownership

- `hub/maintenance_runtime.py` owns maintenance scheduling primitives, retention/rollup work, scheduled Monitor/Fleet queueing, certificate renewal scheduling, the maintenance loop and FastAPI lifespan cleanup.
- `hub/app.py` keeps shared domain helpers, provisioning/orchestration primitives, middleware, router wiring and compatibility wrappers used by tests/internal callers.
- Route registration remains fully outside `app.py`; the only FastAPI decorator intentionally left there is the global HTTP security/upload middleware.
- `/static` mounting remains in `app.py` because it is application composition rather than an API route.

## Hygiene

Route-only FastAPI imports and the old lifespan context-manager import were removed from `app.py`. Extraction-era blank-line debris around the schema compatibility shim was normalized.

## Preserved behavior

Transaction scopes, retention windows, lease semantics, Agent/Monitor/Fleet/Certificate scheduling, upload security, WebSocket behavior, API contracts, frontend and Live Matrix visuals are unchanged.
