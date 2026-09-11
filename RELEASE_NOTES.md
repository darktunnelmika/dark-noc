# DARK NOC v2.6.0 — NOC Operations Suite

Maintainer: **@mikakhadm**

This release turns DARK NOC into a broader day-to-day Network Operations Center while keeping tunnel automation and browser SSH intact.

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
