# DARK NOC Hub v2.5.0 — Nightfall Command

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
SHA-256 checksum before starting the interactive installer.

## Included

- Central FastAPI Hub with SQLite/WAL storage
- Lightweight Python Agent for Ubuntu/Debian nodes
- CPU, RAM, swap, disk, load, bandwidth, uptime and connection telemetry
- Tunnel process and TCP path checks
- Incident creation and automatic resolution
- Optional Telegram incident and recovery alerts
- Allowlisted remote diagnostics and service restarts
- Auto-Heal with cooldown and hourly restart limits
- Interactive SSH over authenticated WebSocket
- Full xterm.js SSH console with ANSI/VT support, two concurrent sessions, PTY resize, search, clipboard, fullscreen and log export
- Browser-to-server file upload over pinned SFTP with real upload progress, size limits and atomic destination writes
- Cross-server SFTP relay through the Hub, requiring no direct SSH path between the two Nodes
- Encrypted SSH credentials at rest
- Scrypt passwords, HTTP-only sessions and audit logs
- Responsive NIGHTFALL command interface
- Automatic HTTPS reverse proxy with a trusted domain certificate or encrypted IP certificate
- Random strong first-login credentials and in-panel username/password change
- Hub bound privately to a chosen or randomly generated localhost port behind Nginx
- Controlled iperf3 speed tests through a fixed configured endpoint
- Extensible Tunnel Plugin Store with coordinated two-node deployment
- Hybrid DARK Backhaul Pair Code mode for KHAREJ servers without SSH or an Agent
- Native DARK Packet Pro plugin with managed IRAN-client/KHAREJ-server deployment and DPP-N1 Pair Code mode
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
- Strict plugin discovery for DARK Backhaul (`backhaul@NAME.service`), DARK Ghost Pro (`ghostpro@NAME.service`) and DARK Packet Pro (`paqetpro@NAME.service`); unrelated tunnel families remain excluded
- Node IP shown beside every server name in the Live Tunnel Matrix
- Tree topology pairs Iran/Hub with remote endpoints even when the remote side has no manageable SSH Node
- Separate read-only Agent and SSH readiness indicators on both sides of every Backhaul path
- Real remote IP discovery from established Backhaul TCP peers and hover diagnostics on every colored path

## Hub installation

Upload and extract the archive on the server selected as the central Hub, then run:

```bash
chmod +x install-hub.sh upgrade.sh uninstall.sh
sudo bash install-hub.sh
```

Use the dedicated `DARK-NOC-HUB` archive only on the central management server.

The installer always asks for the panel domain or public IP, including during a Hub upgrade. With a domain, its DNS A/AAAA record must already point to the Hub so Let's Encrypt can validate it; the installer obtains and configures the certificate automatically. With an IP, the installer creates a self-signed certificate containing that IP; traffic is encrypted but the browser shows a trust warning until the certificate is trusted locally.

The installer also asks for the internal Hub port. Enter a specific port such as `9090`, or press Enter to generate an unused random port. This backend port binds only to `127.0.0.1`; users always open the domain normally over HTTPS port 443. At the end, the installer prints a randomly generated operator username and password once. Open `https://DOMAIN_OR_IP`, sign in, then use the operator menu to change both.

The Hub server is registered as a node automatically and a local Agent is installed for it. Use the separate Node archive and `install-node.sh` for every other server. Agents discover managed DARK Backhaul, DARK Ghost Pro and DARK Packet Pro instances; unrelated tunnel projects are intentionally excluded.

## Agent installation

Upload the dedicated `DARK-NOC-NODE` archive to every monitored server and run:

```bash
sudo bash install-node.sh
```

Use the separate `DARK-NOC-NODE` archive on monitored servers. It installs only operating-system prerequisites and ensures SSH is available. Existing tunnels are not changed.

The Node installer asks for no Hub URL and no enrollment token. After it finishes, open the Hub panel, select **ADD NODE**, choose Iran Edge or Global Exit, and enter the Node IP, SSH port plus root password or private key. The Hub installs and configures the Agent remotely, creates its private enrollment token, applies the correct TLS trust and waits for the first heartbeat. Progress and actionable errors appear on the server card under **INSTALL LOG**; use **RETRY INSTALL** after fixing the reported issue.

Running `install-node.sh` again only refreshes prerequisites. Agent updates and configuration are controlled by the Hub while preserving tunnel, service and Auto-Heal rules. Remote Hub addresses always use HTTPS.

For a self-signed IP certificate, the Hub securely copies its exact certificate to the Node and configures certificate pinning instead of disabling TLS verification.

## Configure tunnels and Auto-Heal

### Deploy DARK Backhaul from the panel

Enroll one Iran node and one foreign node and wait until both Agents are online. Open **Tunnels**, choose **INSTALL & CREATE** on the DARK Backhaul plugin, select both servers, set the Iran public address, tunnel port and user ports, then deploy. The Hub creates matching configuration and the Agents install the official manager/core and start `backhaul@TUNNEL_NAME.service` on both sides.

### Deploy DARK Ghost Pro

Choose **DARK Ghost Pro** in Tunnels to install the official GOST core and create a managed `ghostpro@TUNNEL_NAME.service`. For an unreachable foreign server, choose Pair Code and paste the generated `DGP-` code into the normal KHAREJ flow of the DARK Ghost Pro script.

### TLS Vault and domain transports

Open **TLS Vault**, select the Iran Node and enter a domain whose DNS already points to that Node. The Agent performs ACME validation and keeps the private key locally. When a TLS/WSS/H2/gRPC transport is selected, tunnel creation requires a valid Vault certificate and changes the endpoint to its matching domain. DARK NOC queues renewal automatically 30 days before expiry.

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
```

## Upgrade an existing installation

Extract the new archive, enter `dark-noc-pro`, then run:

```bash
sudo bash upgrade.sh hub
sudo bash upgrade.sh agent
```

Agent configuration, Hub database, credentials and telemetry are preserved. A Hub upgrade asks for the desired domain/IP again, rebuilds the Nginx HTTPS configuration, obtains the domain certificate when required, and disables obsolete uvicorn systemd overrides after keeping a backup.

If an upgraded Agent still points to a legacy HTTP `:9090` URL, edit that Node in the panel, make sure its root SSH credentials are present, then select **RETRY INSTALL**. The Hub migrates it to HTTPS automatically.
