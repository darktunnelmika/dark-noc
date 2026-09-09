#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${EUID:-999} -ne 0 ]]; then echo "Run as root: sudo bash upgrade.sh hub|agent"; exit 1; fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  hub)
    if [[ ! -d /opt/dark-noc/hub ]]; then echo "Hub is not installed."; exit 1; fi
    backup_dir="$(mktemp -d /tmp/dark-noc-hub-upgrade.XXXXXX)"
    had_hub_env=0; had_nginx_site=0; had_systemd_override=0; had_database=0
    cp -a /opt/dark-noc/hub "$backup_dir/hub"
    [[ -f /etc/systemd/system/dark-noc-hub.service ]] && cp -a /etc/systemd/system/dark-noc-hub.service "$backup_dir/service"
    if [[ -f /etc/dark-noc/hub.env ]]; then had_hub_env=1; cp -a /etc/dark-noc/hub.env "$backup_dir/hub.env"; fi
    if [[ -f /var/lib/dark-noc/dark-noc.db ]]; then
      had_database=1
      python3 - "$backup_dir/dark-noc.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect('/var/lib/dark-noc/dark-noc.db')
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
source.close(); target.close()
PY
    fi
    if [[ -f /etc/nginx/sites-available/dark-noc ]]; then had_nginx_site=1; cp -a /etc/nginx/sites-available/dark-noc "$backup_dir/nginx-site"; fi
    if [[ -f /etc/systemd/system/dark-noc-hub.service.d/override.conf ]]; then
      had_systemd_override=1
      cp -a /etc/systemd/system/dark-noc-hub.service.d/override.conf "$backup_dir/override.conf"
    fi
    rollback_hub() {
      echo "Upgrade failed; restoring previous Hub files."
      cp -a "$backup_dir/hub/." /opt/dark-noc/hub/
      [[ -f "$backup_dir/service" ]] && cp -a "$backup_dir/service" /etc/systemd/system/dark-noc-hub.service
      [[ -f "$backup_dir/hub.env" ]] && cp -a "$backup_dir/hub.env" /etc/dark-noc/hub.env
      if [[ "$had_database" -eq 1 && -f "$backup_dir/dark-noc.db" ]]; then
        systemctl stop dark-noc-hub.service 2>/dev/null || true
        rm -f /var/lib/dark-noc/dark-noc.db-wal /var/lib/dark-noc/dark-noc.db-shm
        cp -a "$backup_dir/dark-noc.db" /var/lib/dark-noc/dark-noc.db
        chown darknoc:darknoc /var/lib/dark-noc/dark-noc.db
      fi
      [[ -f "$backup_dir/nginx-site" ]] && cp -a "$backup_dir/nginx-site" /etc/nginx/sites-available/dark-noc
      if [[ "$had_systemd_override" -eq 1 ]]; then
        install -d -m 0755 /etc/systemd/system/dark-noc-hub.service.d
        cp -a "$backup_dir/override.conf" /etc/systemd/system/dark-noc-hub.service.d/override.conf
      fi
      [[ "$had_hub_env" -eq 0 ]] && rm -f /etc/dark-noc/hub.env
      if [[ "$had_nginx_site" -eq 0 ]]; then rm -f /etc/nginx/sites-enabled/dark-noc /etc/nginx/sites-available/dark-noc; fi
      /opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt >/dev/null 2>&1 || true
      systemctl daemon-reload
      command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
      systemctl start dark-noc-hub.service || true
      rm -rf "$backup_dir"
    }
    trap rollback_hub ERR
    echo "Configuring the public domain/IP and HTTPS gateway."
    bash "$SCRIPT_DIR/install-hub.sh"
    systemctl daemon-reload
    systemctl start dark-noc-hub.service
    systemctl status dark-noc-hub.service --no-pager -l
    trap - ERR
    rm -rf "$backup_dir"
    ;;
  agent)
    if [[ ! -d /opt/dark-noc-agent ]]; then echo "Agent is not installed."; exit 1; fi
    backup_dir="$(mktemp -d /tmp/dark-noc-agent-upgrade.XXXXXX)"
    cp -a /opt/dark-noc-agent/agent.py /opt/dark-noc-agent/requirements.txt "$backup_dir/"
    [[ -f /etc/systemd/system/dark-noc-agent.service ]] && cp -a /etc/systemd/system/dark-noc-agent.service "$backup_dir/service"
    rollback_agent() {
      echo "Upgrade failed; restoring previous Agent files."
      cp -a "$backup_dir/agent.py" "$backup_dir/requirements.txt" /opt/dark-noc-agent/
      [[ -f "$backup_dir/service" ]] && cp -a "$backup_dir/service" /etc/systemd/system/dark-noc-agent.service
      systemctl daemon-reload
      systemctl start dark-noc-agent.service || true
      rm -rf "$backup_dir"
    }
    trap rollback_agent ERR
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y ca-certificates curl tar openssl iproute2 iperf3
    install -d -m 0700 /etc/dark-backhaul
    systemctl stop dark-noc-agent.service
    install -m 0755 "$SCRIPT_DIR/agent/agent.py" /opt/dark-noc-agent/agent.py
    install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt
    /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt
    install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /etc/systemd/system/dark-noc-agent.service
    if grep -q '"hub_url"[[:space:]]*:[[:space:]]*"http://.*:9090' /etc/dark-noc-agent/config.json 2>/dev/null; then
      echo "WARNING: Agent still uses the legacy HTTP :9090 Hub URL."
      echo "Run install-agent.sh and enter the new HTTPS URL plus this node's current/new token."
    fi
    systemctl daemon-reload
    systemctl start dark-noc-agent.service
    systemctl status dark-noc-agent.service --no-pager -l
    trap - ERR
    rm -rf "$backup_dir"
    ;;
  *) echo "Usage: sudo bash upgrade.sh hub|agent"; exit 1 ;;
esac
