# DARK NOC v2.9.45 — Plugin Contract v1

- Replace the hardcoded Hub plugin catalog with validated JSON manifests under `hub/plugins/`.
- Make plugin icon, method label, inventory key, form profile, TLS transports, endpoint behavior, Pair-Code behavior and certificate ownership declarative.
- Refactor plugin deployment transactions to consume manifest runtime profiles instead of branching on concrete plugin IDs.
- Add `DNP1.` generic Pair Code support for future standard plugins while retaining every existing Pair Code format for compatibility.
- Return plugin identity with Pair-Code recovery so the UI no longer guesses plugin type from secret prefixes.
- Centralize privileged Agent plugin job dispatch in `plugin_adapter()` and remove the old unknown-plugin fallback to Backhaul.
- Let new adapters participate in generic monitoring automatically when their service is explicitly recorded in `managed_services`.
- Add fail-closed manifest validation, duplicate protection, CI regression coverage and `docs/plugin-authoring.md`.

## فارسی

بخش پلاگین به Plugin Contract v1 منتقل شد. از این نسخه مشخصات هر پلاگین داخل Manifest مستقل قرار می‌گیرد و Hub/UI برای نام، آیکون، ترنسپورت، پروفایل، TLS، Pair Code و سمت Endpoint به شرط‌های پراکنده وابسته نیستند. برای پلاگین استاندارد جدید، بخش Hub با اضافه‌کردن Manifest قابل توسعه است و در Agent فقط Adapter همان پلاگین در Registry مرکزی اضافه می‌شود؛ دیگر نیازی به دستکاری Routeها و فرم‌های اصلی پنل نیست.

---

# DARK NOC v2.9.44 — Dependency Lock & Supply-Chain Hardening

- Add separate hash-locked dependency graphs for Hub and Agent (`requirements.lock`).
- Generate the locks from reviewed direct requirements with pinned `pip-tools==7.6.1`.
- Enforce `pip --require-hashes` on Hub installs, local Agent installs, zero-touch remote Agent provisioning and normal Agent upgrades.
- Keep rollback compatibility with pre-v2.9.44 installations which only have legacy `requirements.txt`.
- Add a permanent drift guard covering transitive pins, SHA256 hashes and runtime install paths.
- Keep the direct dependency versions unchanged in this release; this step captures and freezes the dependency graph already validated by DARK NOC.
- No API, Agent protocol, tunnel, frontend or Live Matrix behavior changes.

## فارسی

برای Hub و Agent فایل Lock مستقل با Pin کامل dependencyهای ترانزیتی و SHA256 اضافه شد. نصب‌های جدید و Provision/Upgrade از `--require-hashes` استفاده می‌کنند تا تغییر ناخواسته dependency یا جایگزینی فایل پکیج باعث تغییر رفتار پنل نشود؛ Rollback به نسخه‌های قدیمی هم همچنان پشتیبانی می‌شود.

---

# DARK NOC v2.9.43 — FastAPI Dependency Hardening

- Upgrade the Hub API framework from `fastapi==0.115.6` to `fastapi==0.141.1`.
- Keep the dependency exactly pinned so production and CI use the same API framework release.
- Validate APIRouter registration, Pydantic request bodies, path parameters, Header/Cookie dependencies, HTTPException responses and WebSocket routing under FastAPI 0.141.1.
- Exercise the complete Hub/Auth/API/WebSocket/Agent regression suite and reproducible release build with no other direct dependency changes.
- No API contract, Agent, tunnel, frontend or Live Matrix behavior changes.

## فارسی

پکیج `FastAPI` هاب از نسخه 0.115.6 به 0.141.1 ارتقا داده شد؛ Routerها، مدل‌های درخواست، Auth dependencyها، خطاهای HTTP و WebSocket با تست کامل بررسی می‌شوند و هیچ dependency مستقیم دیگری تغییر نمی‌کند.

---

# DARK NOC v2.9.42 — Uvicorn Dependency Hardening

- Upgrade the Hub ASGI runtime from `uvicorn[standard]==0.34.0` to `uvicorn[standard]==0.52.4`.
- Keep the dependency exactly pinned so production and CI use the same HTTP/WebSocket server implementation.
- Validate real local ASGI HTTP and WebSocket round trips under Uvicorn 0.52.4 in addition to the existing DARK NOC regression suite.
- Exercise Hub startup, API, WebSocket, SSH terminal/file-transfer and reproducible release coverage with no other direct dependency changes.
- No API, Agent, tunnel, frontend or Live Matrix behavior changes.

## فارسی

پکیج `Uvicorn` هاب از نسخه 0.34.0 به 0.52.4 ارتقا داده شد؛ HTTP و WebSocket واقعی روی ASGI، شروع Hub و کل تست‌های پنل بررسی می‌شوند و هیچ dependency مستقیم دیگری تغییر نمی‌کند.

---

# DARK NOC v2.9.41 — psutil Dependency Hardening

- Upgrade `psutil` from `6.1.1` to `7.2.2` in both Hub and Agent requirement sets.
- Keep the dependency exactly pinned so production and CI use the same telemetry runtime.
- Validate the Agent APIs used for CPU, RAM, swap, disk, boot time, network/disk I/O, socket inventory, process trees and temperature telemetry.
- Exercise the complete Agent heartbeat/inventory regression suite and reproducible release build with no other dependency changes.
- No API, tunnel, frontend or Live Matrix behavior changes.

## فارسی

پکیج `psutil` در Hub و Agent از نسخه 6.1.1 به 7.2.2 ارتقا داده شد؛ مسیرهای Telemetry، Socket/Process inventory و Agent heartbeat با تست کامل بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.

---

# DARK NOC v2.9.40 — AsyncSSH Dependency Hardening

- Upgrade the Hub SSH runtime from `asyncssh==2.19.0` to `asyncssh==2.24.0`.
- Keep the dependency exactly pinned so CI and production installs use the same SSH/SFTP implementation.
- Validate the AsyncSSH API surface used by DARK NOC: SSHClient host-key pinning, private-key import, SSH connect, SFTP attributes and SFTP exception handling.
- Exercise the existing SSH Terminal, SFTP File Manager, upload/relay, provisioning and full Hub/Agent regression suites.
- Run the reproducible release build with no other dependency changes.
- No API, Agent, tunnel, frontend or Live Matrix behavior changes.

## فارسی

پکیج `AsyncSSH` هاب از نسخه 2.19.0 به 2.24.0 ارتقا داده شد؛ مسیرهای SSH Terminal، SFTP، آپلود/Relay، Host-Key Pinning و نصب Agent با تست‌های کامل بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.

---

# DARK NOC v2.9.39 — cryptography Dependency Hardening

- Upgrade the Hub cryptography runtime from `cryptography==44.0.0` to `cryptography==50.0.1`.
- Keep the dependency exactly pinned so CI and production installs resolve the same cryptographic implementation.
- Validate the DARK NOC Fernet encrypt/decrypt path and tamper rejection under the new runtime.
- Run the complete Hub/Agent regression suite and reproducible release build with no other dependency changes.
- No API, Agent, tunnel, frontend or Live Matrix behavior changes.

## فارسی

پکیج `cryptography` هاب از نسخه 44.0.0 به 50.0.1 ارتقا داده شد؛ مسیر Fernet، تشخیص داده دستکاری‌شده، کل تست‌ها و build تکرارپذیر بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.

---

# DARK NOC v2.9.38 — python-multipart Dependency Hardening

- Upgrade the Hub multipart parser from `python-multipart==0.0.20` to `python-multipart==0.0.32`.
- Keep the dependency pinned exactly so production installs and CI resolve the same parser version.
- Verify existing authenticated SSH multipart upload handling, request size enforcement, file operations, API integration and reproducible packaging with the full regression suite.
- Add a permanent dependency contract preventing accidental rollback of the multipart parser pin.
- No API, Agent, tunnel, frontend or Live Matrix behavior changes.

## فارسی

`python-multipart` هاب از نسخه 0.0.20 به 0.0.32 ارتقا داده شد. تمام تست‌های Upload/SSH، محدودیت حجم، API و build تکرارپذیر بدون تغییر رفتاری باید پاس شوند.

---

# DARK NOC v2.9.37 — View-Aware Fetch Scheduling

- Replace the broad 10-endpoint 15-second frontend poll with active-view fetch plans.
- Keep Dashboard, Nodes and Tunnels as the lightweight shared live base; fetch Incidents/Traffic, Plugins/Deployments/Certificates, Monitors or Fleet data only when the owning view is active.
- Keep full refresh for boot/login and explicit post-mutation synchronization.
- Make WebSocket catch-up use the active-view plan instead of triggering a broad full refresh every 30 seconds.
- Skip periodic view polling while the document is hidden and refresh the active view immediately when it becomes visible again.
- Coalesce a requested full refresh behind an in-flight view refresh so post-mutation state is not lost.
- Stop rebuilding hidden Live Matrix DOM on telemetry updates; the approved topology appearance and always-visible connected path invariant are unchanged.
- Add permanent view-aware fetch regression coverage.

## فارسی

Polling پنل سبک‌تر شد: به‌جای گرفتن همه endpointها هر ۱۵ ثانیه، فقط داده‌های مشترک و داده‌های صفحه فعال دریافت می‌شوند. هنگام برگشت به تب مرورگر صفحه فعال فوراً sync می‌شود و ظاهر Live Matrix هیچ تغییری نکرده است.

---

# DARK NOC v2.9.36 — Backend Final Audit & Maintenance Runtime Cleanup

- Extract maintenance scheduling, retention/rollup work, scheduled Monitor/Fleet queueing, certificate renewal scheduling and FastAPI lifespan cleanup into `hub/maintenance_runtime.py`.
- Keep thin compatibility wrappers in `hub/app.py` so existing tests/internal callers and runtime overrides remain stable.
- Remove route-only FastAPI imports and obsolete lifespan imports from `hub/app.py`.
- Enforce the final composition boundary: no API/WebSocket route decorators remain in `hub/app.py`; only global middleware and static mounting stay there.
- Add a permanent maintenance/runtime composition regression contract and final backend ownership audit.
- Preserve transaction scopes, lease semantics, retention windows, Agent behavior, API contracts and all frontend/Live Matrix visuals.

## فارسی

Audit نهایی بک‌اند انجام شد؛ maintenance/lifecycle و retention scheduling به `maintenance_runtime.py` منتقل شدند، importهای اضافی پاک شدند و `app.py` حالا فقط composition، middleware و helperهای مشترک را نگه می‌دارد؛ رفتار پنل و Agent تغییر نکرده است.

---

# DARK NOC v2.9.35 — Agent Control Plane & Live Router Split

- Extract local Agent enrollment, liveness pulse, full heartbeat and Agent job poll/result/lease routes into `hub/agent_control_router.py`.
- Extract authenticated `/ws/live` telemetry WebSocket into `hub/live_router.py`.
- Preserve Agent authentication, inventory freshness semantics, Incident/Monitor/Fleet/Plugin result transitions, heartbeat broadcasts, local enrollment recovery and job idempotency.
- Preserve runtime callback/live-client lookup so shared state and test/runtime overrides remain compatible.
- Keep shared Agent/WebSocket state-machine primitives and Hub lifecycle loops centralized in `hub/app.py`.
- Add permanent Agent Control Plane/Live router-boundary regression coverage.
- No frontend or Live Matrix visual changes.

## فارسی

Routeهای Agent Control Plane شامل enrollment، pulse، heartbeat، job poll/result/lease و WebSocket زنده `/ws/live` به Routerهای مستقل منتقل شدند؛ منطق state machine، Incident/Fleet/Plugin، Session و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.

---

# DARK NOC v2.9.34 — Backend Router Split: Generic Jobs & System Status

- Extract authenticated Generic Job read routes into `hub/jobs_router.py`.
- Move `/api/system/status` into `hub/system_router.py`.
- Preserve job payload redaction, response limits, retention/status counters and API contracts.
- Keep Agent job queue/result/lease, heartbeat state machine and Live WebSocket in `hub/app.py` for the final control-plane phase.
- Add permanent Generic Jobs/System Status router-boundary regression coverage.

## فارسی

Routeهای عمومی Job و `/api/system/status` از `hub/app.py` جدا شدند؛ Agent job state machine، heartbeat و Live WebSocket بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.33 — Backend Router Split: SSH & File Transfer

- Extract all SSH File Transfer and File Manager HTTP routes into `hub/ssh_file_router.py`.
- Extract the interactive `/ws/ssh/{node_id}` terminal into `hub/ssh_terminal_router.py`.
- Preserve multipart upload authentication, streaming download behavior, disconnect-aware semaphores, timeouts, SFTP atomic overwrite/rollback, checksum/editor limits, host-key pinning and persistent tmux sessions.
- Preserve runtime/test overrides for SSH limits, chunking, transfer semaphore and keepalive settings through dynamic lookup.
- Keep SSH/SFTP helper primitives, upload middleware, Agent control plane and Live telemetry WebSocket in `hub/app.py`.
- Add permanent SSH router-boundary regression coverage.

## فارسی

Routeهای SSH File Manager، Upload/Relay/Download و WebSocket ترمینال SSH به Routerهای مستقل منتقل شدند؛ محدودیت‌ها، rollback اتمیک، قطع Client، Session، host-key pinning و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.

---

# DARK NOC v2.9.32 — Backend Router Split: Auth, Dashboard & System

- Continue backend route modularization by extracting Authentication routes into `hub/auth_router.py`.
- Extract Dashboard routes into `hub/dashboard_router.py` and public health/readiness/index routes into `hub/system_router.py`.
- Preserve login throttling, secure session cookie behavior, dashboard metrics/limits, health/readiness checks, static mounting, API paths and response contracts.
- Keep SSH/File Transfer, Agent control plane, generic Job reads, Live WebSocket and all frontend/Live Matrix behavior unchanged.
- Add permanent Auth/Dashboard/System router-boundary regression coverage.

## فارسی

Routeهای Auth، Dashboard و System health/index از `hub/app.py` به Routerهای مستقل منتقل شدند؛ Session، Dashboard، health check، SSH، Agent و ظاهر پنل بدون تغییر رفتاری باقی مانده‌اند.

---

# DARK NOC v2.9.31 — Backend Router Split: Deployments, Certificates & Fleet

- Continue backend route modularization by extracting Plugin Deployment routes into `hub/plugin_deployments_router.py`.
- Extract Certificate routes into `hub/certificates_router.py` and Fleet Operations routes into `hub/fleet_router.py`.
- Preserve Plugin Deployment and Certificate/Fleet service ownership, Pair Code behavior, TLS validation, Fleet scheduling/canary semantics and audit responses.
- Preserve Fleet BackgroundTasks through runtime callback lookup so test/runtime overrides remain compatible.
- Keep Dashboard/System/Auth, SSH/File Transfer, Agent control plane, Live WebSocket and all frontend/Live Matrix behavior unchanged.
- Add permanent Plugin/Certificate/Fleet router-boundary regression coverage.

## فارسی

Routeهای Plugin Deployment، Certificate و Fleet Operations از `hub/app.py` به Routerهای مستقل منتقل شدند؛ Pair Code، TLS، Fleet rollout، Agent و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.30 — Backend Router Split: Nodes & Tunnels

- Continue backend route modularization by extracting all `/api/nodes...` routes into `hub/nodes_router.py`.
- Extract all `/api/tunnels...` routes into `hub/tunnels_router.py`.
- Preserve Node provisioning monkeypatch/runtime indirection, BackgroundTasks, Repository + Service ownership, API paths, audit behavior, tunnel topology semantics, Agent control plane, SSH/File Transfer and frontend visuals.
- Add permanent Node/Tunnel router-boundary regression coverage.

## فارسی

تمام Routeهای Node و Tunnel از `hub/app.py` به `nodes_router.py` و `tunnels_router.py` منتقل شدند؛ API، Agent، SSH و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.29 — Backend Router Split: Monitoring & Incidents

- Complete a backend architecture audit after the Repository + Service modularization wave.
- Start route-registration modularization by extracting Monitoring routes into `hub/monitoring_router.py` and Incident routes into `hub/incidents_router.py`.
- Use FastAPI `APIRouter` modules with explicit dependency injection while preserving API paths, request schemas, HTTP status/details and audit behavior.
- Keep telemetry-driven Incident automation, Agent heartbeat/job-result state machines, SSH/File Transfer, Live WebSocket and frontend visuals unchanged.
- Add a permanent backend router-boundary regression contract and architecture audit document.

## فارسی

Audit نهایی بک‌اند انجام شد و مرحله Routerها شروع شد: Routeهای Monitoring و Incident از `hub/app.py` به Routerهای مستقل منتقل شدند، بدون تغییر API، Agent یا ظاهر پنل.

---

# DARK NOC v2.9.28 — Certificate & Fleet Operations Service Layer

- Continue backend write/service modularization by extracting Certificate issue/renew transactions and Fleet Operation create/cancel transactions from `hub/app.py` into `hub/certificate_fleet_service.py`.
- Keep FastAPI route declarations, audit logging, BackgroundTasks orchestration, Agent upgrade/provision execution and API response shaping in `hub/app.py`.
- Preserve DNS/certificate validation, Fleet scheduling/canary rollout semantics, transaction boundaries, Agent behavior and frontend visuals.
- Add permanent Certificate/Fleet service-boundary regression coverage.

## فارسی

عملیات نوشتنی Certificate و Fleet Operations از `hub/app.py` به `hub/certificate_fleet_service.py` منتقل شدند؛ Routeها، Audit، Background Task، رفتار Agent و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.27 — Monitor & Incident Mutation Service Layer

- Continue the backend write/service layer by extracting Monitor create/update/delete/run transactions and Incident note/action mutations from `hub/app.py` into `hub/monitor_incident_service.py`.
- Keep FastAPI route declarations, audit logging, API response shaping and telemetry-driven Incident automation in `hub/app.py`.
- Preserve transaction boundaries, HTTP status/details, Synthetic Monitoring behavior, Incident lifecycle behavior, Agent behavior and frontend visuals.
- Add permanent Monitor/Incident mutation-service regression coverage.

## فارسی

عملیات نوشتنی Monitoring و Incident از `hub/app.py` به `hub/monitor_incident_service.py` منتقل شدند؛ Routeها، Audit، رفتار Agent و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.26 — Plugin Deployment Service Layer

- Continue the backend write/service layer by extracting Managed and Pair-Code plugin deployment creation plus deployment recovery/retry/remove transactions from `hub/app.py` into `hub/plugin_deployment_service.py`.
- Keep FastAPI routes, deployment list rendering, audit logging and response shaping in `hub/app.py`.
- Preserve transaction boundaries, Pair Code compatibility, Realm/Packet/Backhaul/Ghost behavior, Agent behavior and frontend visuals.
- Add permanent Plugin Deployment service-boundary regression coverage.

## فارسی

عملیات ساخت، Retry و Remove دیپلوی‌های Managed و Pair-Code از `hub/app.py` به `hub/plugin_deployment_service.py` منتقل شدند؛ Routeها، Audit و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.25 — Node & Tunnel Mutation Service Layer

- Start the backend write/service layer by extracting Node create/provision/update/delete/fingerprint mutations and Tunnel control/install/reconfigure/remove transactions from `hub/app.py` into `hub/node_tunnel_service.py`.
- Keep FastAPI route declarations, BackgroundTasks, audit logging and response shaping in `hub/app.py` while moving database write ownership into the service layer.
- Preserve transaction boundaries, HTTP status/details, Agent behavior and frontend visuals.
- Add permanent Node/Tunnel mutation-service regression coverage.

## فارسی

عملیات نوشتنی اصلی Node و Tunnel از `hub/app.py` به `hub/node_tunnel_service.py` منتقل شدند؛ Routeها، Audit، Background Task و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.24 — Monitor & Incident Repository Query Layer

- Continue the backend repository/query layer by extracting Synthetic Monitor inventory/history reads and Incident list/detail/timeline reads from `hub/app.py` into `hub/monitor_incident_repository.py`.
- Keep Monitor/Incident mutation transactions, API response shaping and HTTP error behavior in `hub/app.py` to preserve behavior.
- Preserve SQLite semantics, Agent behavior and frontend visuals.
- Add permanent Monitor/Incident repository-boundary regression coverage.

## فارسی

Queryهای خواندنی Monitoring و Incident از `hub/app.py` به `hub/monitor_incident_repository.py` منتقل شدند؛ عملیات Create/Edit/Delete/Action و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.23 — Node & Tunnel Repository Query Layer

- Start the backend repository/query layer by extracting Node inventory, SSH endpoint-conflict lookup, Tunnel inventory and Tunnel Operations read queries from `hub/app.py` into `hub/node_tunnel_repository.py`.
- Keep response shaping, topology resolution, API routes and all mutation transactions in `hub/app.py` to preserve behavior and keep this split low-risk.
- Preserve SQLite transaction semantics, Agent behavior and frontend visuals.
- Add permanent Node/Tunnel repository ownership regression coverage.

## فارسی

Queryهای خواندنی Node و Tunnel از `hub/app.py` به `hub/node_tunnel_repository.py` منتقل شدند؛ Routeها، پاسخ API، عملیات تغییردهنده دیتابیس و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

# DARK NOC v2.9.22 — Database Bootstrap & Migrations Module

- Continue database-layer modularization by extracting Hub database bootstrap, migrations and startup recovery into `hub/database_bootstrap.py`.
- Preserve the existing SQLite schema, `ALTER TABLE` migration behavior, interrupted provisioning/fleet recovery and initial owner creation.
- Keep `hub/app.py` with a compatibility `bootstrap()` wrapper and unchanged lifespan behavior.
- Preserve API routes, feature query flows, Agent behavior and frontend visuals.
- Add permanent database-bootstrap ownership regression coverage.

## فارسی

Bootstrap دیتابیس، Migrationها و Recoveryهای زمان شروع از `hub/app.py` به `hub/database_bootstrap.py` منتقل شدند؛ رفتار فعلی دیتابیس، API و پنل بدون تغییر باقی مانده است.

---

# DARK NOC v2.9.21 — Backend Database Core Module

- Start database-layer modularization by extracting SQLite data path, connection policy and schema DDL from hub/app.py into hub/database.py.
- Keep bootstrap migrations and feature-specific query flows in app.py for the next safe database-service split.
- Preserve all API routes, existing migrations, transaction behavior, Agent behavior and frontend visuals.
- Add permanent database-core ownership regression coverage.

## فارسی

هسته دیتابیس شامل مسیر SQLite، تنظیمات اتصال و Schema از hub/app.py به hub/database.py منتقل شد؛ Migrationهای Bootstrap و Queryهای فیچرها فعلاً بدون تغییر باقی ماندند تا مرحله‌ای و کم‌ریسک ادامه بدهیم.

---

# DARK NOC v2.9.20 — Backend Request Schemas Module

- Continue backend modularization by extracting Pydantic request/validation models from hub/app.py into hub/schemas.py.
- Preserve all route paths, payload contracts, validators, database behavior, Agent behavior and frontend visuals.
- Load schemas through the same importlib-safe pattern used by the Hub test harness.
- Add a permanent backend schema ownership regression contract.

## فارسی

مدل‌های Pydantic و اعتبارسنجی درخواست‌ها از hub/app.py به hub/schemas.py منتقل شدند؛ قرارداد API و رفتار پنل تغییر نکرده است.

---

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
