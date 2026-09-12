# DARK NOC Hub v2.9.3 — Nightfall Command

**English** · [فارسی](README.fa.md) · Maintainer: **@mikakhadm**

DARK NOC is a self-hosted Network Operations Center for Linux servers and tunnel infrastructure. It combines live node telemetry, tunnel health, incidents, controlled remediation jobs and browser-based SSH in one interface.

## Quick install

Run as root on the central Hub server:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

Run as root on every monitored Iran/Kharej Node:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) node
```

The bootstrapper downloads the latest stable GitHub Release and verifies its
SHA-256 checksum before starting. On an existing Hub it automatically uses the
package's rollback-protected upgrade path; on a new server it starts the fresh
interactive installer.

## Included

- Central FastAPI Hub with SQLite/WAL storage
- Lightweight Python Agent for Ubuntu/Debian nodes
- CPU, RAM, swap, disk, load, bandwidth, uptime and connection telemetry
- Inode, temperature, disk-I/O, interface error/drop, OS update/reboot and Docker-health telemetry
- Agent-based ICMP, TCP, HTTP, HTTPS, DNS, TLS-expiry and encrypted SNMP monitoring
- Tunnel process/path checks with retained per-tunnel traffic, session, uptime, latency, loss and health-score history
- Incident Center with timeline, notes, acknowledgement, root cause, resolution, reopen and automatic recovery
- Optional Telegram incident and recovery alerts
- Allowlisted remote diagnostics and service restarts
- Auto-Heal with cooldown and hourly restart limits
- Interactive SSH over authenticated WebSocket
- Full xterm.js SSH Workspace with unlimited panes, grid/column/focus layouts, persistent tmux sessions, automatic reconnect, snippets, recording/replay, PTY resize, search, clipboard, fullscreen and log export
- Browser-to-server file upload over pinned SFTP with real progress, size limits, atomic POSIX replacement on OpenSSH and a recoverable fallback on older SFTP servers
- Cross-server SFTP relay through the Hub, requiring no direct SSH path between the two Nodes
- Remote SFTP File Manager with browsing, atomic UTF-8 editing, download, SHA-256, mkdir, rename, chmod and guarded deletion
- Encrypted SSH credentials at rest
- Scrypt passwords, HTTP-only sessions and audit logs
- Responsive NIGHTFALL command interface
- Automatic HTTPS reverse proxy with a trusted domain certificate or encrypted IP certificate
- Random strong first-login credentials with server-only username/password management through `darknoc`
- Configurable public HTTPS panel port (default `9090`) with an automatically isolated private Hub backend
- Server-only panel domain/IP switching, Let's Encrypt issuance/renewal and transactional Nginx reconfiguration
- Controlled iperf3 speed tests through a fixed configured endpoint
- Fleet Operations for safe multi-Node diagnostics, tunnel tests, logs, managed-service actions, Auto-Heal, bounded Agent sync and canary-first version-verified upgrades
- Extensible Tunnel Plugin Store with coordinated two-node deployment
- Hybrid DARK Backhaul Pair Code mode for KHAREJ servers without SSH or an Agent
- Native DARK Packet Pro plugin with managed IRAN-client/KHAREJ-server deployment and DPP-N1 Pair Code mode
- Native DARK Realm Pro direct relay with managed Iran Edge/Kharej Gateway deployment, DR1 Pair Code, TCP/TLS/WS/WSS and multi-port mappings
- TLS Vault with DNS validation, Agent-side Let's Encrypt issuance and automatic renewal
- Automatic Backhaul core inventory/install and per-tunnel Start, Stop, Restart, Logs, Status and Test controls
- Safe port, transport, profile and restart-schedule editing from Tunnel Manager
- One-click DARK Backhaul installation and zero-copy IRAN/KHAREJ pairing
- DARK Backhaul transport, performance profile, port and restart scheduling controls
- WebSocket-pushed live overview with real Agent counts, fresh throughput, socket totals and traffic history; polling remains as fallback
- Concurrent tunnel probing with sampled loss/latency and real established-session counts
- Background remote-job execution so long installations never pause Agent heartbeats
- Automatic stale tunnel cleanup and offline-node exclusion from live aggregates
- Safe Agent systemd write paths for real tunnel/plugin configuration
- Recoverable plugin retry and coordinated two-node removal
- Hourly metric rollups, configurable retention, readiness/runtime health endpoints and a renewable single-controller lease for multi-process safety
- Job leases with crash recovery and atomic Agent job claiming
- Debounced incidents to prevent flapping alerts
- Live systemd service inventory, security headers and login rate limiting
- UFW-aware DARK Backhaul deployment with tracked rule cleanup
- Fail-safe Hub/Agent upgrades with automatic file rollback
- SSH host-key TOFU pinning with explicit reset after verified key rotation
- Backhaul port/disk/DNS preflight, local cleanup and coordinated partial-deployment rollback
- Node offline and CPU/RAM/disk/load incidents with acknowledge and manual resolve actions
- Editable node credentials, encrypted SSH key input and live service controls
- Zero-touch Node provisioning from ADD NODE using root SSH credentials, with automatic prerequisites, Agent, TLS and private token configuration
- Automatic local Hub enrollment with its monitoring Agent enabled during Hub installation
- Strict plugin discovery for DARK Backhaul (`backhaul@NAME.service`), DARK Ghost Pro (`ghostpro@NAME.service`), DARK Packet Pro (`paqetpro@NAME.service`) and DARK Realm Pro (`dark-realm@NAME.service`); unrelated tunnel families remain excluded
- Node IP shown beside every server name in the Live Tunnel Matrix
- Animated tree topology pairs Iran/Hub with remote endpoints even when the remote side has no manageable SSH Node; route color, flow speed, labels and hover intelligence expose live tunnel state
- Separate read-only Agent and SSH readiness indicators on both sides of every Backhaul path
- Real remote IP discovery from established Backhaul TCP peers and hover diagnostics on every colored path

## Hub installation

Upload and extract the archive on the server selected as the central Hub, then run:

```bash
chmod +x install-hub.sh upgrade.sh uninstall.sh darknoc
sudo bash install-hub.sh
```

Use the dedicated `DARK-NOC-HUB` archive only on the central management server.

On a fresh server, the installer asks for the panel domain or public IP and its public HTTPS port. A fresh installation defaults to port `9090`; upgrades preserve the active address and port without asking, and they can be changed afterward with `sudo darknoc`. With a domain, its DNS A/AAAA record must already point to the Hub so Let's Encrypt can validate it. With an IP, the installer creates a self-signed certificate containing that IP; traffic is encrypted but the browser shows a trust warning until the certificate is trusted locally.

The internal FastAPI port is selected automatically, binds only to `127.0.0.1` and is never opened by the firewall. If an old backend already uses the selected public port, the installer moves it safely before Nginx claims that port. At the end, the installer prints a randomly generated operator username and password once. Open `https://DOMAIN_OR_IP:PORT` (the port is omitted only for `443`) and sign in.

Panel address, port, certificate and owner credentials are intentionally not editable in the web UI. Run `sudo darknoc` over SSH on the Hub to view the active URL/credentials, change or generate a password, change the public port, set a domain/IP, issue or renew Let's Encrypt SSL, restart services, inspect logs, create a local backup or launch a verified update. Configuration changes are validated before Nginx reload and roll back when activation fails.

The Hub server is registered as a node automatically and a local Agent is installed for it. Use the separate Node archive and `install-node.sh` for every other server. Agents discover managed DARK Backhaul, DARK Ghost Pro, DARK Packet Pro and DARK Realm Pro instances; unrelated tunnel projects are intentionally excluded.

## Agent installation

Upload the dedicated `DARK-NOC-NODE` archive to every monitored server and run:

```bash
sudo bash install-node.sh
```

Use the separate `DARK-NOC-NODE` archive on monitored servers. It installs only operating-system prerequisites and ensures SSH is available. Existing tunnels are not changed.

The Node installer asks for no Hub URL and no enrollment token. After it finishes, open the Hub panel, select **ADD NODE**, choose Iran Edge or Global Exit, and enter the Node IP, SSH port plus root password or private key. The Hub installs and configures the Agent remotely, creates its private enrollment token, applies the correct TLS trust and waits for the first heartbeat. Progress and actionable errors appear on the server card under **INSTALL LOG**; use **RETRY INSTALL** after fixing the reported issue.

Running `install-node.sh` again only refreshes prerequisites. Agent updates and configuration are controlled by the Hub while preserving tunnel, service and Auto-Heal rules. After a Hub upgrade, use **SYNC AGENT** on each remote server card to deploy the matching Agent and hardened service unit. Remote Hub addresses always use HTTPS.

For any self-signed panel certificate, the Hub securely copies its exact certificate to the Node and configures certificate pinning instead of disabling TLS verification. Remote Agent URLs include the configured public panel port.

## Configure tunnels and Auto-Heal

### Deploy DARK Backhaul from the panel

Enroll one Iran node and one foreign node and wait until both Agents are online. Open **Tunnels**, choose **INSTALL & CREATE** on the DARK Backhaul plugin, select both servers, set the Iran public address, tunnel port and user ports, then deploy. The Hub creates matching configuration and the Agents install the official manager/core and start `backhaul@TUNNEL_NAME.service` on both sides.

### Deploy DARK Ghost Pro

Choose **DARK Ghost Pro** in Tunnels to install the official GOST core and create a managed `ghostpro@TUNNEL_NAME.service`. For an unreachable foreign server, choose Pair Code and paste the generated `DGP-` code into the normal KHAREJ flow of the DARK Ghost Pro script.

### TLS Vault and domain transports

Open **TLS Vault**, select the Node that terminates TLS and enter a domain whose DNS already points to that Node. Backhaul/Ghost certificates normally belong to the Iran listener; Realm TLS/WSS certificates belong to the Kharej Gateway listener. The Agent performs ACME validation and keeps the private key locally. DARK NOC queues renewal automatically 30 days before expiry.

The server nodes require outbound HTTPS access to GitHub during the initial plugin installation. The official release asset must include a matching SHA-256 digest. An already installed working Backhaul core is preserved and never replaced while creating another tunnel. TCPMUX is the recommended starting transport.

Edit `/etc/dark-noc-agent/config.json`. Only services explicitly present in `managed_services` can be restarted remotely or by Auto-Heal. Start with Auto-Heal disabled, validate monitoring, then enable it.

Example configuration is available at `agent/config.example.json`.

For Telegram alerts, set `DARK_NOC_TELEGRAM_BOT_TOKEN` and `DARK_NOC_TELEGRAM_CHAT_ID` in `/etc/dark-noc/hub.env`, then restart the Hub.

```bash
sudo systemctl restart dark-noc-agent
sudo journalctl -u dark-noc-agent -f
```

## Security notes

- Use a dedicated management Hub and HTTPS domain.
- Restrict the Hub with a firewall or trusted management network when possible.
- Prefer a dedicated SSH key instead of a root password.
- The browser never receives stored SSH credentials.
- Terminal libraries are bundled locally; the SSH workspace does not execute third-party CDN code.
- The first SSH connection pins the server host-key fingerprint. A changed key is rejected until an operator explicitly resets the pin on that node.
- The Agent executes only defined job types and allowlisted service names.
- Back up `/var/lib/dark-noc` and `/etc/dark-noc`.

## Service commands

```bash
systemctl status dark-noc-hub --no-pager
systemctl status dark-noc-agent --no-pager
journalctl -u dark-noc-hub -f
journalctl -u dark-noc-agent -f
darknoc
```

## Upgrade an existing installation

The recommended command detects the existing Hub, downloads and verifies the latest release, then enters the rollback-protected upgrade path automatically:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-noc/main/install.sh) hub
```

For an archive already downloaded and verified, run `sudo bash upgrade.sh hub`. Hub database, credentials, Nodes, telemetry and active public address/port are preserved. A Hub upgrade rebuilds the Nginx HTTPS configuration, installs the `darknoc` server command and disables obsolete uvicorn systemd overrides after keeping a backup.

Afterward, select **SYNC AGENT** on every remote Node with SSH credentials. This deploys the matching Agent version and service sandbox while preserving its tunnel, service and Auto-Heal rules. If a Node still points to a legacy HTTP `:9090` URL, the same action migrates it to HTTPS automatically.
