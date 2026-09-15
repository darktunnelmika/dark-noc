# DARK NOC Backend Architecture Audit — v2.9.29

- Pre-split FastAPI/WS route count in `hub/app.py`: **67**.
- Repository and write-service boundaries are established for Nodes/Tunnels, Plugin Deployments, Monitoring/Incidents, Certificates and Fleet Operations.
- The next low-risk architectural boundary is route registration.
- v2.9.29 starts that phase with Monitoring and Incident APIRouters while keeping telemetry automation in `hub/app.py`.

## Route-family inventory before v2.9.29

- **Nodes / Node jobs / plugins:** 9
- **Plugin deployments:** 9
- **SSH / file transfer:** 9
- **Agent control plane:** 6
- **Monitoring:** 6
- **Dashboard / system health:** 5
- **Tunnels:** 5
- **Authentication:** 4
- **Incidents:** 4
- **Certificates:** 3
- **Fleet operations:** 3
- **Other:** 3
- **Live websocket:** 1

## Remaining high-value splits

1. Nodes + Tunnels route registration.
2. Plugin Deployments + Certificates + Fleet Operations route registration.
3. Dashboard/System/Auth routes.
4. SSH/File Transfer routes (higher risk because of streaming/WebSocket lifecycle).
5. Agent control-plane routes (highest risk; keep telemetry/job-result state machine intact until last).

## Invariants

- No API path or payload contract changes during route extraction.
- Agent heartbeat/job-result behavior remains in the Hub core until dedicated regression coverage is expanded.
- Live Matrix visuals and frontend behavior remain outside backend router modularization.
