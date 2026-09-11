# DARK NOC v2.5.0 — Secure File Operations

Maintainer: **@mikakhadm**

The SSH workspace now includes browser-to-server uploads and cross-server transfers. Both operations use the existing encrypted Node credentials and pinned SSH host keys. Cross-server copies are streamed through bounded Hub memory, so the source and destination do not need direct SSH connectivity. Temporary partial files are cleaned on failure and existing destinations require an explicit overwrite choice.

This release adds DARK Packet Pro as a native third tunnel plugin. DARK NOC can deploy both managed sides with the correct reversed role map (IRAN client, KHAREJ server), or configure only IRAN and issue the script-compatible `DPP-N1` Pair Code for an unreachable foreign server. PAQET is pinned to the exact wire-compatible core tag carried by the Pair Code.

This release introduces **TLS Vault** for DARK Backhaul and DARK Ghost Pro. Domains are checked against the selected Node, Let's Encrypt certificates are issued by its Agent, expiry is monitored, renewals are queued automatically and TLS transports cannot deploy without a valid matching certificate. Private keys stay on the server and never enter the browser or Pair Code.

DARK Backhaul `wss` and `wssmux` are now first-class transports. The selected Vault certificate is written only to the IRAN/server configuration, while managed KHAREJ nodes and native B2 Pair Codes connect through the certificate domain. B2 transport indexes 5 and 6 match the official DARK Backhaul v1.9.2 script.

The DARK Ghost Pro integration is synchronized with the current `dark-ghostpro.sh` transport contract and GPC1 Pair Code. The panel exposes the compatible safe set and applies TCP/UDP firewall rules according to the chosen transport.

DARK NOC can now configure the Iran side of DARK Backhaul and generate a native Pair Code for a foreign server that cannot be reached through SSH. On KHAREJ, use the regular script flow and paste the code into **Connect with DARK NOC Pair Code**.

Pair Code deployments include independent Iran-side status, retry, code recovery, and removal controls. Pair secrets are encrypted at rest.

The original floating-node topology returns as a fully dynamic cyber operations map.
Remote IPs are normalized and correlated with registered Nodes and live TCP peers.

## Highlights

- Animated curved SVG routes between floating Iran/Hub and Global Exit nodes.
- Search, health filters, fullscreen and click-through Node intelligence.
- Pairing through managed deployments, matching Backhaul names and target IPs.
- Existing one-sided tunnels remain visible when the remote Node is unavailable.
- Independent Agent ON/OFF and SSH READY/NO ACCESS indicators for both endpoints.
- Green active, amber degraded/stale and red disconnected tunnel lines.
- Hover/focus details on every line: route, service, ports, sessions, traffic and loss.
- SSH badges are status-only and cannot accidentally open a session.

Read `README.md`, `README.fa.md` and `SECURITY.md` before installation.
