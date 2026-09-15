# DARK NOC Backend Router Progress — v2.9.30

Completed router ownership: Monitoring, Incidents, Nodes, and Tunnels.

Still intentionally kept in `hub/app.py`:

1. Plugin Deployments + Certificates + Fleet Operations — next low-risk split.
2. Dashboard/System/Auth — moderate coupling.
3. SSH/File Transfer — higher risk because of streaming and cancellation semantics.
4. Agent control plane + job result state machine — highest risk and should remain last.
5. Live WebSocket — keep with session/runtime core until Agent/SSH route work is complete.

No frontend or Live Matrix behavior is part of this backend router phase.
