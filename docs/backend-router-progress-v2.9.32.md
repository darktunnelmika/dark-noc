# DARK NOC Backend Router Progress — v2.9.32

Completed router ownership: Monitoring, Incidents, Nodes, Tunnels, Plugin Deployments, Certificates, Fleet Operations, Authentication, Dashboard and System health/index.

Still intentionally kept in `hub/app.py`:

1. SSH/File Transfer — higher risk because of streaming, cancellation, multipart and SFTP rollback semantics.
2. Agent control plane + job-result state machine — highest risk and should remain last.
3. Generic Job read endpoints and Live WebSocket — keep with control-plane/runtime core until those final splits.

No frontend or Live Matrix visual behavior is part of this backend router phase.
