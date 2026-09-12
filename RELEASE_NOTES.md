# DARK NOC v2.9.3 — Topology & Inventory Integrity

This maintenance release restores Hub-local tunnel visibility and makes the cyber topology deterministic. Lightweight liveness heartbeats can no longer erase the last complete tunnel, service or plugin inventory. Agent discovery now includes stopped/disabled managed tunnel directories, preserves the last good payload across restarts and marks incomplete discovery snapshots as non-authoritative.

The topology renderer now understands the native side semantics of Backhaul, Ghost Pro, Packet Pro and Realm Pro, groups paired rows safely, separates parallel paths and expands vertically as the fleet grows.

---

# DARK NOC v2.9.2 — Heartbeat Resilience

This maintenance release keeps Hub and Node liveness independent from expensive telemetry collection. Full socket, service, plugin and tunnel inventory now runs in a background worker while a lightweight heartbeat continues at the configured interval.

- A slow `apt`, Docker, systemd, socket or tunnel probe can no longer make an otherwise healthy server appear offline.
- The local Hub Agent can securely re-enroll itself through loopback when its stored token no longer matches the Hub record.
- systemd watchdog notifications restart a genuinely wedged Agent loop, and heartbeat/telemetry errors are written to journal plus the Agent state file.
- DARK Realm Pro telemetry is accepted and retained by the Hub alongside Backhaul, Ghost Pro and Packet Pro.
- The offline grace window is 180 seconds, while normal heartbeats remain every 15 seconds.

---

# DARK NOC v2.9.1 — Hub Agent Recovery

This maintenance release repairs the local Hub monitoring Agent and hardens every Agent delivery path introduced with Realm integration.

- Installs `realm_plugin.py` beside the local Hub Agent and keeps a canonical, compile-checked Agent payload for remote **SYNC AGENT** operations.
- Reuses a verified local enrollment token during routine upgrades, so a healthy Hub Node is not reset to `PENDING`.
- Requires a new exact-version heartbeat before the installer reports success and prints actionable service logs on failure.
- Keeps the monitoring Agent online when an optional Realm adapter is missing or damaged; only Realm operations are disabled.
- Aligns standalone Agent upgrades and Node prerequisites with `/etc/dark-realm`, Realm adapter backup/rollback and version `2.9.1`.
- Adds local-enrollment recovery and installer-contract regression tests to both CI and the release transaction.

---

# DARK NOC v2.9.0 — DARK Realm Integration

DARK Realm Pro v1.0.0 is now a first-class plugin with native Realm TCP/TLS/WS/WSS semantics, managed Iran Edge and Kharej Gateway deployment, DR1 Pair Code mode, Gateway-owned TLS Vault certificates, native multi-endpoint TOML generation and Agent telemetry.

The integration preserves Realm's direct-relay architecture. It does not inject Backhaul pool, channel or multiplexing controls into Realm.

---

# DARK NOC v2.8.0 — Operations Cockpit

Maintainer: **@mikakhadm**

This release turns tunnel, topology, Agent upgrade and browser SSH workflows into a deeper day-to-day NOC operations cockpit.

## Tunnel Operations and topology

Every managed tunnel now has a dedicated Operations Cockpit with a health score, live traffic, sessions, latency/loss, service uptime, a retained 24-hour graph, endpoint/SSH intelligence and a relevant job timeline. The Agent attributes kernel TCP byte counters to tunnel ports and reports systemd uptime; the Hub retains these samples independently from host metrics.

The topology remains the cyber tree layout, but routes now have explicit healthy, degraded, stale and down states. Animated packets communicate traffic flow, each route is labeled, richer hover details expose its exact transport and telemetry, and clicking the line opens that tunnel's cockpit. IPv4-mapped peer addresses are normalized before matching or display.

## Controlled Agent upgrades

Fleet Operations now includes a version-aware Upgrade Agents playbook. Operators select a canary, batch size, pause and stop-on-failure policy. The canary runs alone, subsequent batches are bounded, and a Node is successful only after it sends a heartbeat with the exact Hub version. The Fleet page shows current, outdated and unknown Agent counts.

## Advanced SSH Workspace

Browser SSH is no longer limited to two temporary panes. Operators can create unlimited panes, switch grid/column/focus layouts, save and run local snippets, record/replay output and automatically reconnect with exponential backoff. When `tmux` is available, closing or losing the browser keeps the remote shell alive and reconnecting reattaches to the same pinned session. Hub and Node installers now include `tmux`.

## Previous v2.7.0 server control center

### `darknoc` server command

Run `sudo darknoc` on the Hub to view the active panel URL and recoverable credentials, change the owner username/password, select the real public HTTPS port, register a domain or IP, issue/renew Let's Encrypt, restart services, follow logs, create a SQLite/config/TLS backup or start a verified update. These sensitive settings are no longer editable from the web panel.

Fresh installations default to `https://HOST:9090`. Nginx owns the selected public port while FastAPI remains on an automatically isolated `127.0.0.1` backend. If a legacy backend already occupies `9090`, the installer moves it before exposing the panel. Existing installations retain their current public port during upgrade unless the operator chooses another one.

Domain certificate requests use ACME webroot validation on port 80, install an automatic Nginx reload hook and retain an encrypted self-signed fallback when validation is not ready. Public port/address changes validate Nginx first and restore the previous configuration on failure.

Remote Node provisioning now writes the configured public port into the Agent Hub URL and securely pins every self-signed panel certificate.

## Previous v2.6.0 operations suite

## Synthetic monitoring

Create ICMP, TCP, HTTP, HTTPS, DNS, TLS-expiry and SNMP v2c checks and choose the exact Agent that runs each check. Monitors support automatic schedules, manual execution, latency and failure streaks, retained history and automatic incident creation after repeated failures. Recovery resolves the matching incident. SNMP communities are encrypted at rest and never returned to the browser.

## Incident Command

Incidents now have a durable event timeline rather than a single status row. Operators can add diagnostic notes, acknowledge ownership, record root cause and resolution, resolve an incident and reopen it. Automatic Agent, tunnel, resource and synthetic-monitor recoveries write their own timeline events. The archive exposes both active and resolved cases with downtime and event counts.

## Deeper server visibility

Agent telemetry now includes inode pressure, temperature, disk read/write rate, per-interface counters, network errors and drops, OS/kernel inventory, pending package updates, reboot state and Docker container health. Fixed safety thresholds feed the Incident Center. Hourly rollups and configurable retention preserve useful long-term history without keeping every raw sample forever.

## Fleet Operations

Run an allowlisted operation across selected Agents: diagnostics, tunnel testing, log collection, managed-service status/restart, Auto-Heal configuration or Agent synchronization. Operations can be immediate or scheduled where safe, expose per-Node progress/output and never accept arbitrary shell commands. Remote provisioning is globally bounded so a large Fleet action cannot exhaust the Hub.

## SSH File Manager

The SSH workspace now combines xterm.js sessions, browser upload, Hub-relayed server-to-server transfer and a full SFTP file manager. Browse directories, download files, verify SHA-256, edit UTF-8 text with atomic replacement, create directories, rename, change permissions and delete empty directories/files. Critical operating-system paths plus DARK NOC runtime and credential paths are protected from mutation.

## Reliability and release safety

`/readyz`, an authenticated runtime-status endpoint and a renewable SQLite controller lease provide a multi-process coordination foundation. The GitHub Release workflow is now a resumable, commit-owned transaction: annotated release tags and hidden draft markers identify ownership, partial drafts can resume, unexpected assets stop publication and every uploaded asset is downloaded by API asset ID and verified before the draft becomes public.

Existing installations can run the normal Hub command. The verified upgrader preserves the database, encryption key, credentials, Nodes, tunnel definitions, TLS material and new tuning values, and rolls back if health verification fails:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

After the Hub upgrade, use **Fleet Ops → Synchronize Agent** or the per-Node **SYNC AGENT** action for SSH-enabled remote Nodes. Pair-Code-only foreign endpoints remain display-only and do not require Agent access.
