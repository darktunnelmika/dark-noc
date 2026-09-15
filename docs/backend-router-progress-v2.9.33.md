# DARK NOC Backend Router Progress — v2.9.33

Completed router ownership now includes SSH File Transfer/File Manager and the interactive SSH terminal WebSocket, in addition to Monitoring, Incidents, Nodes, Tunnels, Deployments, Certificates, Fleet, Auth, Dashboard and System health/index.

Still intentionally kept in `hub/app.py`:

1. Agent control plane + job-result state machine — highest risk and remains last.
2. Generic Job read endpoints and Live telemetry WebSocket — keep with control-plane/runtime core until the Agent split.
3. `/api/system/status` — small residual system route to fold in during final backend cleanup.
4. SSH/SFTP helper primitives and upload middleware remain centralized for now; this release changes route ownership only.

No frontend or Live Matrix visual behavior is changed.
