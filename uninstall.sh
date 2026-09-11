#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${EUID:-999} -ne 0 ]]; then echo "Run as root."; exit 1; fi
echo "Select component to remove:"
echo "1) Hub application (keep data)"
echo "2) Agent (keep config)"
read -r -p "Choice: " choice
case "$choice" in
  1)
    systemctl disable --now dark-noc-hub.service 2>/dev/null || true
    systemctl disable --now dark-noc-agent.service 2>/dev/null || true
    rm -f /etc/systemd/system/dark-noc-hub.service
    rm -f /etc/systemd/system/dark-noc-agent.service
    rm -rf /opt/dark-noc
    rm -rf /opt/dark-noc-agent
    rm -f /etc/nginx/sites-enabled/dark-noc /etc/nginx/sites-available/dark-noc
    if command -v nginx >/dev/null 2>&1; then nginx -t >/dev/null 2>&1 && systemctl reload nginx || true; fi
    systemctl daemon-reload
    echo "Hub, its local Agent and DARK NOC Nginx gateway removed. Data, certificates and shared firewall rules were kept."
    ;;
  2)
    systemctl disable --now dark-noc-agent.service 2>/dev/null || true
    rm -f /etc/systemd/system/dark-noc-agent.service
    rm -rf /opt/dark-noc-agent
    systemctl daemon-reload
    echo "Agent removed. Config kept at /etc/dark-noc-agent."
    ;;
  *) echo "Cancelled." ;;
esac
