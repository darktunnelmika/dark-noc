# DARK NOC Node v2.9.4

Maintainer: **@mikakhadm**

This package is only for monitored Node servers. It prepares the operating system and SSH service. It does not ask for a Hub URL or enrollment token and does not touch existing tunnels.

```bash
chmod +x install-node.sh
sudo bash install-node.sh
```

When it finishes, open the Hub panel and select **ADD NODE**. Enter the Node IP, SSH port and root password or private key. The Hub connects over SSH, installs the Agent, configures its private enrollment token and TLS trust, starts the service and waits for its first heartbeat.

The Node bootstrap and Agent installation do not create, remove, restart or reconfigure existing tunnels. After enrollment, manage supported tunnel plugins exclusively from the Hub. If installation fails, open **INSTALL LOG** on the server card, correct the reported SSH/network issue, then select **RETRY INSTALL**.
