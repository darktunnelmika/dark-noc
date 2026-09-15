# DARK NOC v2.9.5

## 2.9.13 — Fleet Operations module extraction

- Extract Fleet Operations rendering/editor/form runtime into fleet-operations.js.
- Preserve rollouts, canary selection, Auto-Heal and scheduled operations.
- Add permanent module-boundary regression coverage.


## 2.9.12 — Secure file transfer module extraction

- Extract File Manager, browser Upload and Hub Relay runtime into file-transfer.js.
- Preserve transfer progress/cancel/limits and remote file actions.
- Add permanent module-boundary regression coverage.


## 2.9.11 — SSH terminal module extraction

- Extract browser SSH terminal runtime into ssh-terminal.js.
- Preserve reconnect, panes, snippets, replay and xterm behavior.
- Add permanent module-boundary CI coverage.


## 2.9.10 — Topology module extraction

- Extract topology modeling from app.js into topology.js.
- Preserve current matrix visuals and interactions.
- Add topology module-boundary regression coverage.


## 2.9.9 — Frontend core modularization

- Extract stable frontend helpers into core.js.
- Preserve current UI and topology behavior.
- Add module-boundary regression coverage.


## 2.9.8 — Runtime hardening and render efficiency

- Realm listener/UFW ownership regression fix ported to current main.
- Active-view-only rendering on full refresh; hidden pages no longer rebuild.
- CI now checks live-matrix.js and runs Realm/topology/view regression contracts.
- No visual redesign.


## 2.9.7 — Smooth Live Matrix Refresh

- Keep the existing matrix visuals unchanged.
- Reuse stable SVG/routes across telemetry refreshes and update live attributes in place.
- Move WebSocket telemetry to a lightweight dashboard/nodes/tunnels refresh path.
- Bound full dashboard refreshes to 30-second cadence while telemetry is active.


## 2.9.6 — Live tunnel matrix only

- Publish isolated live-matrix.css and live-matrix.js; shared frontend
  logic/styles remain unchanged and index.html only loads the assets.
- Keep every connection visible independently of rate, including flat paths.
- Add scoped neon flow, route beacons, card accents and related-route focus.
- Stop stale/offline/idle flow and bound motion for reduced-motion/large maps.
- Synchronize release identifiers, installer handshakes and regression
  expectations only; no backend, Agent or installer logic changes.


- Keeps the repaired local Hub Agent enabled and running after an upgrade, even when the pre-upgrade Agent was crashed or stopped.
- Requires a fresh post-upgrade v2.9.5 pulse before the upgrade can succeed.
- Makes the Hub service pull in its local Agent on service start and boot.
- Expands Hub-local tunnel discovery across loaded units, unit files, stopped instances, metadata-only directories and legacy config file names.
- Restores a solid glowing Iran-to-Kharej route with pulsing energy and moving traffic particles; healthy routes no longer look dotted.

## 2.9.4

- Added an authenticated lightweight Agent pulse endpoint so Hub and Node
  liveness no longer depends on full telemetry acceptance.
- Quarantine incompatible cached inventory and rebuild it while liveness stays
  online.
- Discover filesystem-owned DARK tunnels even when their stopped systemd
  instance cannot be inspected.
- Restored solid glowing topology routes with animated data flow and moving
  packet comets.
- Added permanent pulse, inventory and topology visual regression coverage.

# v2.9.3 — Topology & Inventory Integrity

- Preserves Hub and Node tunnel/service/plugin inventory during lightweight or incomplete heartbeats.
- Persists the last complete Agent payload and restores it after Agent restarts.
- Discovers stopped and disabled local DARK tunnel instances from their managed config directories.
- Prevents transient systemd discovery failures from publishing destructive empty snapshots.
- Normalizes IRAN/HUB versus KHAREJ topology roles for Backhaul, Ghost, Packet and Realm.
- Resolves peers by managed deployment and address before using an unambiguous role fallback.
- Rebuilds the topology canvas with dynamic height and separated parallel routes.
- Adds permanent regression coverage for Hub-local tunnels and authoritative inventory semantics.

# Changelog

All notable changes to DARK NOC are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [2.9.2] - 2026-09-12

### Fixed

- Decoupled the liveness heartbeat from expensive socket, service, plugin and tunnel telemetry collection, preventing a healthy Hub or Node from being marked offline while inventory work is slow.
- Added automatic local Hub enrollment recovery after an Agent token mismatch, without exposing the bootstrap secret outside the root-only Hub environment.
- Added a systemd watchdog, unbuffered structured Agent logging and failure state retention so a wedged loop is restarted and the actual cause is visible in journal/state diagnostics.
- Increased the transient heartbeat grace window from 120 to 180 seconds while retaining prompt offline detection.
- Restored DARK Realm Pro tunnel ingestion in Hub heartbeat processing and stale-tunnel cleanup.
- Made Agent JSON writes use unique fsynced temporary files to avoid state/config replacement races.

### Tests

- Added Agent liveness, background telemetry, local re-enrollment and watchdog regression coverage to CI and release publication.

## [2.9.1] - 2026-09-12

### Fixed

- Installed the Realm adapter beside the local Hub Agent, fixing the Hub Node remaining `PENDING` after the v2.9.0 upgrade.
- Added a canonical Agent payload for remote synchronization instead of depending on the health of the running local Agent directory.
- Reused a verified local Agent token during upgrades and required a fresh exact-version heartbeat before installation succeeds.
- Added fail-soft optional Realm imports so missing adapter/support files disable Realm operations without taking down Agent heartbeats or the Hub.
- Added Realm adapter backup/rollback to standalone Agent upgrades and created `/etc/dark-realm` in every prerequisite path.
- Updated stale Node and `darknoc` version banners.

### Reliability

- Added local Hub enrollment recovery and installer/package contract tests to CI and release validation.

## [2.9.0] - 2026-09-12

### Added

- DARK Realm Pro v1.0.0 as a first-class DARK NOC plugin with native Realm TCP/TLS/WS/WSS configuration.
- Managed Iran Edge and Kharej Gateway deployment, plus standalone DR1 Pair Code mode.
- Realm Multi-Port mapping, native TOML generation, verified v2.9.6 core installation, per-tunnel systemd services and Agent telemetry.
- Gateway-owned TLS Vault selection for Realm TLS/WSS; private keys remain only on the transport-listener Node.

### Security

- Realm core assets are version-pinned, verified with official GitHub SHA-256 digests and installed atomically.
- DR1 Pair Codes carry validated metadata and corruption checksums only; no certificate key material is included.

## [2.8.0] - 2026-09-11

### Added

- Tunnel Operations Cockpit with retained samples, health score, traffic/session/uptime KPIs, 24-hour graph, endpoint intelligence, recent job timeline and direct SSH actions.
- Animated next-generation tree topology with status-specific routes, live traffic particles, route labels, deep hover telemetry and click-through tunnel management.
- Canary-first Agent Upgrade Orchestrator with bounded batches, configurable pauses, stop-on-failure, exact post-upgrade version verification and Fleet version compliance.
- Advanced SSH Workspace with unlimited panes, grid/column/focus layouts, remote tmux persistence, exponential reconnect, local command snippets and session recording/replay.
- Per-tunnel Agent traffic attribution from kernel TCP counters and systemd service uptime telemetry.

### Changed

- Node and Hub prerequisite installation now includes `tmux` for persistent browser SSH sessions.
- Dashboard API exposes the running Hub version and tunnel retention is independently configurable with `DARK_NOC_TUNNEL_RETENTION_DAYS`.

### Fixed

- Normalized IPv4-mapped IPv6 peer addresses before topology matching and display.
- Made tunnel service-uptime parsing tolerant of malformed or older Agent reports.

## [2.7.0] - 2026-09-11

### Added

- Server-only `darknoc` control center for status, recoverable credential display, username/password reset, public port, panel domain/IP, Let's Encrypt issuance/renewal, service restart, logs, backup and verified update.
- A real configurable public HTTPS listener, defaulting to `9090` on fresh installations, while the FastAPI backend remains isolated on an automatically selected localhost port.
- Atomic environment/Nginx updates, pre-activation validation and automatic rollback for failed public port or address changes.
- Automatic Certbot deploy hook for Nginx reload and self-signed certificate fallback until domain DNS/port 80 is ready.

### Changed

- Removed web-based owner credential mutation; panel access settings now require root access to the Hub server.
- Remote Agent enrollment URLs now include the configured public panel port and pin any self-signed Hub certificate, not only certificates issued to IP addresses.
- Hub upgrades preserve legacy port `443` by default, while fresh installs default to public port `9090`; operators can change either through `darknoc`.

### Fixed

- Prevented public port `9090` from colliding with a legacy Uvicorn backend by moving the private backend before Nginx claims the public listener.
- Preserved the installer credential environment across upgrades instead of silently replacing it with unrelated generated values.

## [2.6.0] - 2026-09-11

### Added

- Synthetic ICMP, TCP, HTTP, HTTPS, DNS, TLS and SNMP monitoring assigned to any online Agent, with encrypted SNMP communities, scheduled execution, manual runs, result history and automatic recovery incidents.
- Full Incident Center with a durable event timeline, operator notes, acknowledgement, root cause, resolution, reopen and active/resolved archive views.
- Expanded host telemetry for inode pressure, temperature, disk I/O, per-interface counters, packet errors/drops, operating-system inventory, pending updates, reboot state and Docker health.
- Fleet Operations for allowlisted diagnostics, tunnel tests, log collection, service checks/restarts, Auto-Heal configuration and bounded Agent synchronization across selected Nodes.
- Remote SFTP File Manager with directory browsing, UTF-8 editor, atomic saves, download, SHA-256, mkdir, rename, chmod and guarded deletion in addition to existing upload and cross-server relay.
- Hourly metric rollups, configurable raw/result retention, `/readyz`, authenticated runtime status and a renewable maintenance-controller lease for safe multi-process coordination.

### Changed

- Limited simultaneous remote Agent provisioning and preserved all new NOC/retention tuning values across Hub upgrades.
- Made release publication resumable and commit-owned: annotated markers, draft recovery, immutable asset verification and asset-ID downloads protect against partial or racing GitHub Actions runs.

### Security

- Protected critical operating-system paths and DARK NOC runtime/credential paths from File Manager mutation, upload and relay replacement.
- Kept synthetic secrets out of browser/API responses and redacted sensitive Fleet job payload fields.

## [2.5.1] - 2026-09-11

### Fixed

- Added a dedicated authenticated upload gateway so the advertised 1 GiB browser-to-server limit is no longer blocked by Nginx's general 1 MiB API ceiling.
- Reworked SFTP replacement as a fail-safe staged swap which restores the previous destination if finalization fails.
- Added bounded transfer concurrency, SSH keepalives, transfer/idle timeouts, cancellation cleanup and browser-side cancel/error states.
- Prevented stale WebSocket callbacks from corrupting replacement SSH panes and kept SSH connect controls usable on mobile.
- Added the Agent sandbox paths and Certbot prerequisites required by DARK Ghost Pro, DARK Packet Pro and TLS Vault.
- Made the Hub's public self-signed certificate readable to its unprivileged service without exposing the private key.
- Extended Hub upgrade rollback to cover TLS and the local Agent, and included the Agent upgrade entrypoint in Node packages.
- Made the public Hub command automatically select the verified rollback upgrade path on existing installations.
- Added fail-safe `SYNC AGENT` rollout for SSH-enabled Nodes while preserving their existing managed services, tunnels and Auto-Heal settings.
- Revoked live and SSH WebSocket activity immediately after logout, password rotation or session expiry.
- Made release archives reproducible and publication immutable, with tests required before a version is released.
- Refreshed first-party static asset cache keys so upgraded panels cannot retain stale SSH UI files.

## [2.5.0] - 2026-09-11

### Added

- Browser-to-server SFTP upload with real client progress and configurable size limits.
- Hub-relayed server-to-server streaming when Nodes cannot SSH to each other directly.
- Atomic temporary writes, explicit overwrite protection and audit events for file operations.
- Cyber file-operation cards integrated into the SSH workspace.

## [2.4.0] - 2026-09-11

### Added

- Native DARK Packet Pro v5.6.0 plugin in the Tunnel Plugin Store.
- Correct Packet Pro roles: IRAN client and KHAREJ server.
- Managed dual-Agent PAQET deployment and offline-capable `DPP-N1` Pair Code mode.
- Exact PAQET core tag pinning with release SHA-256 verification.
- Packet Pro inventory, discovery, topology telemetry, controls, reconfiguration and removal.

### Fixed

- Plugin retry and reconfiguration now use each plugin's declared role map instead of assuming Backhaul roles.
- Packet Pair Codes preserve their Pair ID and creation timestamp so code recovery passes integrity validation.

## [2.3.1] - 2026-09-11

### Added

- Native DARK Backhaul `wss` and `wssmux` transports in managed and Pair Code modes.
- TLS Vault certificate injection into the Backhaul IRAN/server configuration.
- Exact Backhaul B2 Pair Code transport indexes for WSS (5) and WSSMUX (6).

### Fixed

- Backhaul TLS deployments now fail safely when the selected certificate files are missing on the Agent.
- Certificate changes are included in deployment identity so certificate rotation triggers a real redeploy.

## [2.3.0] - 2026-09-10

### Added

- TLS Vault with per-Node domain and certificate inventory.
- DNS-to-Node preflight before ACME issuance.
- Agent-side Let's Encrypt issuance through Nginx or standalone validation.
- Automatic renewal jobs beginning 30 days before expiry.
- Certificate selection inside tunnel creation for TLS transports.
- Hard deployment guard preventing TLS tunnels without a valid certificate.
- Synced DARK Ghost Pro's expanded transport catalog, including direct TLS/WSS/H2/gRPC, QUIC, H2C, DTLS, ICMP and relay+QUIC.

### Security

- Certificate private keys remain on the Agent and are never returned to the browser.
- Managed Ghost Pro TLS clients verify the selected certificate instead of using insecure mode.

## [2.2.0] - 2026-09-10

### Added

- DARK Ghost Pro as a native second tunnel plugin.
- Exact `DGP-GPC1` Pair Code generation compatible with the official script.
- Automatic official GOST core installation and Ghost service discovery.
- Managed creation, restart scheduling, UFW tracking, telemetry and lifecycle controls.
- Per-plugin install state, transport selection and tunnel counts in the UI.

## [2.1.0] - 2026-09-10

### Added

- Automatic DARK Backhaul core detection and version reporting per Agent.
- One-click core installation for online nodes where Backhaul is missing.
- A dedicated Create Tunnel action with managed and Agent-optional Pair Code modes.
- Per-tunnel Start, Stop, Restart, Status, Logs and path-test controls.
- Port, transport, performance profile and restart-schedule editing with safe redeployment.
- Updated Pair Code output when a hybrid tunnel configuration changes.

## [2.0.0] - 2026-09-10

### Added

- Hybrid DARK Backhaul deployments for foreign servers without SSH or an Agent.
- Native `DBH-B2` Pair Codes compatible with the normal KHAREJ script flow.
- Iran-only status, retry, Pair Code recovery and removal controls.

### Security

- Pair tokens are encrypted at rest and excluded from inventory and audit output.

## [1.9.0] - 2026-09-10

### Added

- Dynamic cyber topology based on the original floating-node v1.3 design.
- Animated curved SVG paths whose color and thickness reflect health and traffic.
- Tunnel hover intelligence, topology search, health filters and fullscreen mode.
- Clickable endpoint intelligence without triggering SSH.

### Fixed

- IPv4-mapped IPv6 peers such as `::ffff:192.0.2.1` are normalized to IPv4.
- Loopback targets are never displayed as foreign endpoints.
- Registered Node host addresses take priority over proxy-observed addresses.

## [1.8.2] - 2026-09-10

### Added

- Interactive branching tunnel topology rooted at each Iran/Hub node.
- Hover and keyboard-focus diagnostics for every tunnel line.
- Real remote peer IP discovery from established Backhaul TCP connections.

### Changed

- Active tunnel lines are green, degraded/stale lines amber and disconnected lines red.
- Local loopback targets are never presented as a foreign server IP.

## [1.8.1] - 2026-09-10

### Added

- Actual Iran/Hub-to-remote DARK Backhaul path cards on the Overview.
- Read-only Agent and SSH readiness indicators for both tunnel endpoints.
- Peer resolution from managed deployments, matching Backhaul names and target IPs.

### Changed

- Tunnel paths remain visible when the remote endpoint is not enrolled or has no SSH credentials.
- Overview SSH badges no longer trigger sessions; management remains in Servers and SSH Command.

## [1.8.0] - 2026-09-10

### Added

- Zero-touch Node provisioning from the Hub using root SSH credentials.
- Automatic remote prerequisite, Agent, systemd, TLS and private enrollment-token configuration.
- Per-node installation progress, actionable logs and retry controls.
- First-heartbeat verification before a Node installation is marked complete.

### Changed

- The Node installer is now a non-interactive prerequisites-only bootstrap and asks for no Hub URL or token.
- `install-agent.sh` is retained as a compatibility alias for the Node bootstrap.

### Security

- Enrollment tokens are generated and delivered directly from Hub to Node and are never exposed to the browser.
- Existing Agent rules and tunnel configuration are preserved during provisioning.

## [1.7.0] - 2026-09-10

### Added

- A self-hosted xterm.js terminal engine with complete ANSI/VT rendering.
- Two concurrent interactive SSH sessions with server selection and quick tab opening.
- Terminal search, selection-aware copy, clipboard paste, fullscreen mode and log export.
- Automatic PTY resizing and clickable web links.

### Fixed

- Bracketed-paste and control escape sequences are no longer printed as visible text.
- Arrow keys, Tab completion, Ctrl shortcuts and full-screen TUI programs now reach the remote PTY correctly.
- Automatically generated Hub names are accepted by node validation.
- Structured API validation errors are rendered as readable messages.

### Security

- All terminal dependencies are vendored locally with their licenses; SSH sessions do not load code from a CDN.

## [1.6.3] - 2026-09-10

### Fixed

- The automatically enrolled Hub now shows its configured public IP/domain instead of `127.0.0.1`.
- Server inventory ordering is deterministic: Hub, Iran Edge, then Global Exit.
- SSH no longer opens a guaranteed-to-fail session when credentials are missing.
- SSH failures now return an actionable connection error to the authenticated operator.

### Changed

- Server actions use readable labels instead of ambiguous abbreviations.
- The SSH workspace includes a direct server selector and connect button.
- Agent-observed IP and configured SSH destination are shown separately when they differ.

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
