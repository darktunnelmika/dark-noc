# DARK NOC Node v1.6.3

Maintainer: **@mikakhadm**

This package is only for monitored Node servers. It installs the lightweight Agent and its runtime prerequisites, enrolls the server with the Hub, and leaves tunnel deployment and management to the Hub panel.

```bash
chmod +x install-node.sh
sudo bash install-node.sh
```

Enter the HTTPS Hub domain and the one-time enrollment token created by **ADD NODE** in the Hub. Re-running the same command upgrades the Node Agent while preserving its identity and Hub-managed configuration.

The Node bootstrap does not create, remove, restart, or reconfigure existing tunnels. After enrollment, manage supported tunnel plugins exclusively from the Hub.
