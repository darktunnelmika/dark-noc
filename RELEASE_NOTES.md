# DARK NOC v2.9.19 — Backend Runtime Configuration Module

- Start backend modularization by extracting environment parsing and runtime limits from hub/app.py into hub/runtime_config.py.
- Preserve VERSION and process-local runtime state in app.py.
- Keep all API routes, database behavior, Agent behavior and frontend visuals unchanged.
- Add a permanent backend module-boundary regression contract.

## فارسی

اولین مرحله ماژولار کردن بک‌اند انجام شد: تنظیمات محیطی و محدودیت‌های Runtime از hub/app.py به hub/runtime_config.py منتقل شدند، بدون تغییر API یا رفتار پنل.

---

# DARK NOC v2.9.18 — Frontend Runtime Core Split

- Extract shared frontend state into state.js.
- Extract API and remote job helpers into api-client.js.
- Extract cached/live refresh orchestration into refresh-runtime.js.
- Extract authentication termination, boot and live WebSocket lifecycle into session-runtime.js.
- Keep app.js focused on UI rendering/bindings while preserving behavior and the approved Live Matrix visuals.
- Update regression contracts for the new ownership boundaries.

## فارسی

State، API، Refresh و Boot/WebSocket از app.js جدا شدند تا هسته فرانت سبک‌تر و قابل نگهداری‌تر شود؛ ظاهر و رفتار فعلی پنل تغییر نکرده است.

---

# DARK NOC v2.9.17 — Delegated Action Router Split

- Architecture review found the monolithic document click dispatcher to be the largest remaining frontend regression hotspot.
- Split delegated Node, Plugin/Deployment, Tunnel and cross-feature actions into dedicated handler modules.
- Keep the central dispatcher intentionally tiny and preserve action ordering/behavior.
- No visual changes to Live Matrix, Incident, Monitoring, Fleet, SSH, File Manager or Tunnel cockpit.
- Add permanent module-boundary regression coverage.

## فارسی

روتر بزرگ کلیک پنل به هندلرهای جدا برای Node، Plugin، Tunnel و عملیات عمومی تقسیم شد؛ ظاهر و رفتار فعلی پنل تغییر نکرده است.

---

# DARK NOC v2.9.16 — Tunnel Operations Module Extraction

- Architecture review identified tunnel cockpit/runtime as the highest-risk remaining feature block in app.js.
- Extract 24h tunnel history rendering, operations cockpit loading, tunnel edit/redeploy and delete bindings into tunnel-operations.js.
- Preserve delegated Start/Stop/Restart/Status/Logs/Test/SSH actions in the central event router unchanged.
- Keep Live Matrix, Incident, Monitoring, Fleet, SSH, File Manager, APIs and Agent behavior unchanged.
- Add permanent module-boundary regression coverage.

## فارسی

پس از بازبینی معماری، بخش پرریسک مدیریت تونل از app.js جدا و به tunnel-operations.js منتقل شد؛ ظاهر و رفتار پنل تغییر نکرده است.

---

# DARK NOC v2.9.15 — Incident Command Module Extraction

- Extract Incident Command rendering, detail loading, timeline notes and report export from app.js into incident-management.js.
- Preserve acknowledge, resolve, reopen, SSH takeover and delegated incident actions.
- Keep Synthetic Monitoring, Fleet, SSH, File Manager, Matrix, APIs and Agent behavior unchanged.
- Add permanent module-boundary regression coverage.

## فارسی

مرحله هفتم ماژولار کردن فرانت‌اند انجام شد: منطق Incident Command از app.js به incident-management.js منتقل شد، بدون تغییر ظاهر یا رفتار مدیریت رخدادها.

---

# DARK NOC v2.9.14 — Synthetic Monitoring Module Extraction

- Extract monitor rendering, editor and form runtime from app.js into monitoring.js.
- Preserve ICMP/TCP/HTTP/HTTPS/DNS/TLS/SNMP checks, run-all behavior and monitor configuration.
- Keep delegated monitor actions, incidents, Fleet, SSH, File Manager, Matrix, APIs and Agent behavior unchanged.
- Add permanent module-boundary regression coverage.

## فارسی

مرحله ششم ماژولار کردن فرانت‌اند انجام شد: منطق Synthetic Monitoring از app.js به monitoring.js منتقل شد، بدون تغییر ظاهر یا رفتار پنل.

---

# DARK NOC v2.9.13 — Fleet Operations Module Extraction

- Extract Fleet Operations rendering, version compliance, fleet editor and fleet form runtime from app.js into fleet-operations.js.
- Preserve controlled Agent rollouts, canary selection, Auto-Heal configuration, scheduled jobs and service playbooks.
- Keep delegated fleet output/cancel actions, SSH, File Manager, Matrix, APIs and Agent behavior unchanged.
- Add permanent module-boundary regression coverage.

## فارسی

مرحله پنجم ماژولار کردن فرانت‌اند انجام شد: منطق Fleet Operations از app.js به fleet-operations.js منتقل شد، بدون تغییر ظاهر یا رفتار پنل.

---

# DARK NOC v2.9.12 — Secure File Transfer Module Extraction

- Extract SFTP file-manager runtime plus browser upload and Hub relay bindings from app.js into file-transfer.js.
- Preserve upload progress, cancellation, size limits, authentication handling, relay behavior and remote file actions.
- Keep SSH terminal, Live tunnel matrix, API, Agent and tunnel behavior unchanged.
- Add permanent module-boundary regression coverage.

## فارسی

مرحله چهارم ماژولار کردن فرانت‌اند انجام شد: File Manager، Upload و Relay از app.js به file-transfer.js منتقل شدند، بدون تغییر ظاهر یا رفتار پنل.

---

# DARK NOC v2.9.11 — SSH Terminal Module Extraction

- Extract the browser SSH terminal runtime from app.js into ssh-terminal.js.
- Preserve terminal panes, reconnect logic, xterm addons, clipboard handling, snippets and replay behavior.
- Keep upload/relay/file-manager handlers and all UI behavior unchanged.
- Add permanent CI syntax and module-boundary regression coverage.
- No API, Agent, tunnel configuration or database behavior changes beyond version synchronization.

## فارسی

مرحله سوم ماژولار کردن فرانت‌اند انجام شد: موتور ترمینال SSH از app.js به ssh-terminal.js منتقل شد، بدون تغییر ظاهر یا رفتار SSH و پنل.

---

# DARK NOC v2.9.10 — Topology Module Extraction

- Extract topology grouping, side inference, endpoint resolution and health aggregation from app.js into topology.js.
- Keep the current Live tunnel matrix rendering and visual behavior unchanged.
- Add a permanent topology module-boundary regression test.
- No API, Agent, tunnel configuration or database behavior changes beyond version synchronization.

## فارسی

مرحله دوم ماژولار کردن فرانت‌اند انجام شد: منطق مدل‌سازی توپولوژی از app.js به topology.js منتقل شد، بدون تغییر ظاهر یا رفتار Live tunnel matrix.

---

# DARK NOC v2.9.9 — Frontend Core Modularization

- First safe modularization step: extract stable, dependency-free frontend helpers from app.js into core.js.
- Keep all UI behavior and Live tunnel matrix visuals unchanged.
- Load core.js before app.js and add regression coverage for the module boundary.
- No API, tunnel, Agent, installer or database behavior changes beyond version synchronization.

## فارسی

اولین مرحله ماژولار کردن فرانت‌اند انجام شد: توابع پایه و مستقل از app.js به core.js منتقل شدند، بدون تغییر ظاهر یا رفتار پنل.

---

# DARK NOC v2.9.8 — Runtime Hardening & View-Aware Rendering

- Port the verified Realm IPv6 listener and UFW ownership fixes onto current main.
- Preserve prior Realm-owned UFW rules across redeploys and roll back only newly-created rules on failure.
- Keep the v2.9.7 Live tunnel matrix visuals unchanged.
- Stop full refreshes from rebuilding every hidden page; only the active view is rendered from refreshed state.
- Add syntax coverage for live-matrix.js and permanent Realm, topology-refresh and view-render regression tests to CI.

## فارسی

این نسخه ظاهر پنل را تغییر نمی‌دهد. باگ‌های Realm در تشخیص پورت IPv6 و مالکیت UFW رفع شده و رفرش کامل دیگر صفحه‌های مخفی را بی‌دلیل دوباره رندر نمی‌کند. تست‌های دائمی برای Matrix و Realm نیز به CI اضافه شده‌اند.

---

# DARK NOC v2.9.7 — Smooth Live Matrix Refresh

This maintenance release keeps the current Live tunnel matrix design intact while removing full SVG rebuilds on normal telemetry updates.

- Stable topology structure is rendered once and updated in place.
- Traffic, status, width, speed and Agent/SSH badges update without replacing the matrix DOM.
- Route hover/click handlers read the latest live link state instead of stale closures.
- WebSocket telemetry uses a lightweight three-endpoint refresh; the heavier full dashboard refresh is bounded to at most once every 30 seconds.
- No visual redesign of the matrix, cards, header or other pages.

## فارسی

ظاهر فعلی Live tunnel matrix بدون تغییر حفظ شده است. رفرش تله‌متری دیگر کل SVG را از نو نمی‌سازد؛ وضعیت و ترافیک مسیرها به‌صورت درجا به‌روزرسانی می‌شوند تا تیک زدن، پرش و بار اضافه کمتر شود.

---

# DARK NOC v2.9.6 — Live Tunnel Matrix Cyber Refresh

This release delivers the already-committed, isolated Live tunnel matrix
enhancement through the normal verified installer and updater.

- A continuous, unfiltered base path stays visible at zero or low traffic.
- Separate neon cores, flow segments and packet effects decorate active paths.
- Flat horizontal routes have an explicit padded glow filter region.
- Idle, stale and offline routes do not animate fake traffic.
- Route/card hover, keyboard focus and status colors are clearer.
- Motion pauses off-screen and respects reduced-motion preferences;
  expensive effects are bounded for large route counts.

Scope: only the overview's Live tunnel matrix visuals and interactions.
The main app.js, shared styles.css, other views, APIs, tunnel configuration,
pairing, deployment and Agent logic are unchanged. Release identifiers,
installer exact-version handshakes, cache keys and test version expectations
are synchronized to 2.9.6 for packaging; no runtime logic or tests are removed.

## فارسی

این انتشار فقط ظاهر بخش Live tunnel matrix را به‌روز می‌کند: خط اتصال
در ترافیک کم یا صفر هم دیده می‌شود و افکت انتقال داده از خط پایه جداست.
هدر، منوها، صفحات دیگر و منطق تونل‌ها تغییری نکرده‌اند. شماره‌های نسخه
در Hub، Agent، نصب‌کننده و تست‌ها فقط برای هماهنگی انتشار تغییر کرده‌اند.

---

# DARK NOC v2.9.5 — Hub Agent Runtime & Solid Live Routes

The Hub upgrade transaction no longer restores a successfully repaired local
Agent to its old stopped state. A fresh v2.9.5 pulse is required after the final
Agent restart, and starting the Hub also pulls in its monitoring Agent.

Local tunnel discovery now combines loaded systemd units, installed unit files
and DARK-owned configuration roots, including stopped instances and legacy
config extensions. The topology uses a solid luminous backbone with pulsing
energy and moving packets rather than a dotted primary route.

---

# DARK NOC v2.9.4 — Hub Pulse & Live Route Flow

This maintenance release makes Hub liveness independent from full inventory
validation. Every Agent sends a small authenticated pulse first; malformed,
oversized or schema-incompatible telemetry can no longer mark a running Hub
offline. Rejected cached reports are quarantined and rebuilt automatically.

Tunnel discovery now trusts valid DARK configuration directories even when a
stopped template instance cannot be queried through systemd. The cyber topology
keeps the corrected role/pairing model from v2.9.3 while restoring a solid
glowing route, animated throughput flow and moving data comets.

---

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
