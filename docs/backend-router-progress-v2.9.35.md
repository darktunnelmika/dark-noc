# DARK NOC Backend Router Progress — v2.9.35

The backend route-registration modularization wave is now complete for the main HTTP/WebSocket surfaces.

New ownership in this release:
- `hub/agent_control_router.py`: local Agent enrollment, liveness pulse, full heartbeat, Agent job polling, terminal result submission and lease renewal.
- `hub/live_router.py`: authenticated `/ws/live` telemetry WebSocket.

Shared state machines and primitives remain centralized in `hub/app.py`: Agent auth, incident transitions, managed tunnel filtering, Fleet finalization, WebSocket session validation, lifecycle/background loops and shared live-client registry.

No frontend or Live Matrix visual behavior changed.
