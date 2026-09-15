#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-999} -ne 0 ]]; then
  echo "Run as root: sudo bash install-node.sh"
  exit 1
fi

echo ""
echo "  DARK NOC // NODE PREREQUISITES"
echo "  Zero-touch Node v2.9.14"
echo ""
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip ca-certificates curl tar openssl certbot iproute2 iputils-ping iptables iperf3 snmp openssh-server tmux
systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true
install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul /etc/dark-ghostpro /etc/dark-packetpro /etc/dark-realm
install -d -m 0755 /var/lib/dark-noc-acme /var/lib/dark-noc-acme/.well-known /var/lib/dark-noc-acme/.well-known/acme-challenge /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /etc/nginx/conf.d
touch /var/lib/dark-noc-agent/prerequisites-ready
chmod 0600 /var/lib/dark-noc-agent/prerequisites-ready
echo ""
echo "Node prerequisites are ready."
echo "No Hub URL or enrollment token is needed here."
echo "Next: open DARK NOC Hub -> ADD NODE, then enter this server IP and root SSH credentials."
echo "The Hub will install, configure and enroll the Agent automatically."
