# Changelog

All notable changes to DARK NOC are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.6.2] - 2026-09-09

### Added

- Separate Hub and lightweight Node installation packages.
- Automatic Hub-node enrollment and one-time Agent tokens.
- Coordinated DARK Backhaul deployment, removal, retry and rollback.
- Job lease renewal for long-running remote operations.
- Persistent SSH host-key pinning and WebSocket keepalive.
- Automated GitHub CI and release packaging.

### Changed

- Tunnel inventory is intentionally limited to DARK Backhaul.
- Agent connection telemetry uses one socket snapshot per heartbeat.
- Existing Backhaul core binaries are preserved during new deployments.
- Hub upgrades now back up the SQLite database and restart the new service.
- Remote Node enrollment requires HTTPS.

### Security

- Backhaul release assets require a matching GitHub SHA-256 digest.
- Release archives are checked for traversal, links and special files.
- Nginx no longer trusts a client-supplied forwarded-address chain.
- Node deletion is blocked while an active deployment exists.

