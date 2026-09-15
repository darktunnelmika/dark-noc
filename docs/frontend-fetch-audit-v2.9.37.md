# DARK NOC Frontend Fetch Audit — v2.9.37

## Previous behavior
The 15-second timer fetched Dashboard, Nodes, Tunnels, Incidents, Plugins, Deployments, Certificates, Traffic, Monitors and Fleet Operations regardless of which view was visible. Telemetry WebSocket activity also scheduled the same broad refresh every 30 seconds.

## v2.9.37 behavior
Every active-view refresh always updates Dashboard + Nodes + Tunnels. Additional resources are scoped to the visible view:

- Overview: Incidents + 60-minute Traffic
- Servers: shared live base only
- Tunnels: Plugins + Deployments + Certificates
- Monitors: Monitors
- Fleet: Fleet Operations
- Certificates: Certificates
- Incidents: Incidents
- Terminal: shared live base only

Boot/login and explicit post-mutation `refresh()` calls still perform the complete synchronization. Hidden browser tabs pause periodic view polling and immediately refresh the active view when visible again.

The Live Matrix rendering contract is unchanged: a connected path remains continuously visible independent of traffic volume.
