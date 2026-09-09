# Security policy

## Supported versions

Only the latest tagged release receives security fixes.

## Reporting a vulnerability

Do not open a public issue for authentication bypasses, command execution,
credential exposure, unsafe upgrade behavior or supply-chain vulnerabilities.

Use GitHub's private **Security advisories → Report a vulnerability** feature.
Include the affected version, reproduction steps, impact and any suggested fix.
Please allow reasonable time for a patched release before public disclosure.

## Operational guidance

- Expose only Nginx ports 80/443; the internal Uvicorn port must stay on loopback.
- Use a trusted domain certificate whenever possible.
- Protect one-time Agent tokens and rotate a token after suspected exposure.
- Verify the SSH host-key fingerprint after a legitimate server rebuild.
- Back up `/var/lib/dark-noc` and `/etc/dark-noc` before major upgrades.
- Install Node packages only on servers you control.

