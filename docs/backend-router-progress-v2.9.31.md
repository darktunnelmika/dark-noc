# DARK NOC Backend Router Progress — v2.9.31

Completed router ownership:

- Monitoring
- Incidents
- Nodes
- Tunnels
- Plugin Deployments
- Certificates
- Fleet Operations

Still intentionally kept in `hub/app.py`:

1. Dashboard/System/Auth — next moderate-risk split.
2. SSH/File Transfer — higher risk due streaming, cancellation and SFTP rollback semantics.
3. Agent control plane + job-result state machine — highest risk and should remain last.
4. Live WebSocket — keep with runtime core until Agent/SSH route work is complete.

No frontend or Live Matrix behavior is part of this backend router phase.
