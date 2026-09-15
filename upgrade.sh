#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${EUID:-999} -ne 0 ]]; then echo "Run as root: sudo bash upgrade.sh hub|agent"; exit 1; fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

service_was_active() {
  if systemctl is-active --quiet "$1" 2>/dev/null; then printf '1'; else printf '0'; fi
}

service_was_enabled() {
  if systemctl is-enabled --quiet "$1" 2>/dev/null; then printf '1'; else printf '0'; fi
}

service_was_present() {
  if systemctl cat "$1" >/dev/null 2>&1; then printf '1'; else printf '0'; fi
}

validate_data_dir() {
  local candidate="$1" normalized
  if [[ ! "$candidate" =~ ^(/[A-Za-z0-9._+@:-]+)+/?$ ]]; then
    echo "DARK_NOC_DATA must be a dedicated absolute directory without spaces." >&2
    return 1
  fi
  normalized="$(realpath -m -- "$candidate")"
  if [[ ! "$normalized" =~ ^(/[A-Za-z0-9._+@:-]+)+$ ]]; then
    echo "DARK_NOC_DATA resolves to an unsupported path: $normalized" >&2
    return 1
  fi
  case "$normalized" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib32|/lib64|/proc|/root|/run|/sbin|/sys|/tmp|/usr|/var|/var/lib)
      echo "DARK_NOC_DATA is too broad or protected: $normalized" >&2
      return 1
      ;;
    /bin/*|/boot/*|/dev/*|/etc/*|/home/*|/lib/*|/lib32/*|/lib64/*|/proc/*|/root/*|/run/*|/sbin/*|/sys/*|/tmp/*|/usr/*)
      echo "DARK_NOC_DATA cannot be placed under a protected system/runtime directory: $normalized" >&2
      return 1
      ;;
  esac
  if [[ -e "$normalized" && ! -d "$normalized" ]]; then
    echo "DARK_NOC_DATA is not a directory: $normalized" >&2
    return 1
  fi
  printf '%s' "$normalized"
}

restore_service_state() {
  local service="$1" was_active="$2" was_enabled="$3" was_present="$4" failed=0
  if [[ "$was_present" -ne 1 ]]; then
    # A failed upgrade may have introduced and started this service. Keep the
    # package, but restore the pre-upgrade state by stopping and disabling it.
    if systemctl cat "$service" >/dev/null 2>&1; then
      if ! systemctl disable "$service"; then
        echo "ROLLBACK ERROR: could not disable newly introduced $service" >&2
        failed=1
      fi
      if ! systemctl stop "$service"; then
        echo "ROLLBACK ERROR: could not stop newly introduced $service" >&2
        failed=1
      fi
    fi
    return "$failed"
  fi
  if [[ "$was_enabled" -eq 1 ]]; then
    if ! systemctl enable "$service"; then
      echo "ROLLBACK ERROR: could not re-enable $service" >&2
      failed=1
    fi
  else
    if ! systemctl disable "$service"; then
      echo "ROLLBACK ERROR: could not restore disabled state for $service" >&2
      failed=1
    fi
  fi
  if [[ "$was_active" -eq 1 ]]; then
    if ! systemctl start "$service"; then
      echo "ROLLBACK ERROR: could not restart $service" >&2
      failed=1
    fi
  else
    if ! systemctl stop "$service"; then
      echo "ROLLBACK ERROR: could not restore stopped state for $service" >&2
      failed=1
    fi
  fi
  return "$failed"
}

rollback_step() {
  local description="$1"
  shift
  if "$@"; then
    return 0
  fi
  echo "ROLLBACK ERROR: $description" >&2
  return 1
}

case "${1:-}" in
  hub)
    if [[ ! -d /opt/dark-noc/hub ]]; then echo "Hub is not installed."; exit 1; fi
    existing_data_dir="/var/lib/dark-noc"
    if [[ -f /etc/dark-noc/hub.env ]]; then
      configured_data_dir="$(bash -c 'source "$1"; printf %s "${DARK_NOC_DATA:-}"' _ /etc/dark-noc/hub.env)"
      [[ -z "$configured_data_dir" ]] || existing_data_dir="$configured_data_dir"
    fi
    hub_data_dir="$(validate_data_dir "$existing_data_dir")"
    hub_db_path="$hub_data_dir/dark-noc.db"
    hub_key_path="$hub_data_dir/master.key"
    backup_dir="$(mktemp -d /tmp/dark-noc-hub-upgrade.XXXXXX)"
    had_hub_env=0; had_nginx_site=0; had_nginx_link=0; had_systemd_override=0; had_systemd_data_path=0
    had_database=0; had_master_key=0; mutation_started=0
    had_hub_service=0; had_tls=0; had_local_agent_opt=0; had_local_agent_etc=0; had_local_agent_service=0; had_darknoc_cli=0; had_agent_payload=0
    hub_was_active="$(service_was_active dark-noc-hub.service)"
    hub_was_enabled="$(service_was_enabled dark-noc-hub.service)"
    hub_was_present="$(service_was_present dark-noc-hub.service)"
    agent_was_active="$(service_was_active dark-noc-agent.service)"
    agent_was_enabled="$(service_was_enabled dark-noc-agent.service)"
    agent_was_present="$(service_was_present dark-noc-agent.service)"
    nginx_was_active="$(service_was_active nginx.service)"
    nginx_was_enabled="$(service_was_enabled nginx.service)"
    nginx_was_present="$(service_was_present nginx.service)"
    cp -a /opt/dark-noc/hub "$backup_dir/hub"
    if [[ -d /opt/dark-noc/agent-payload ]]; then had_agent_payload=1; cp -a /opt/dark-noc/agent-payload "$backup_dir/agent-payload"; fi
    if [[ -f /etc/systemd/system/dark-noc-hub.service ]]; then had_hub_service=1; cp -a /etc/systemd/system/dark-noc-hub.service "$backup_dir/hub.service"; fi
    if [[ -f /etc/dark-noc/hub.env ]]; then had_hub_env=1; cp -a /etc/dark-noc/hub.env "$backup_dir/hub.env"; fi
    if [[ -d /etc/dark-noc/tls ]]; then had_tls=1; cp -a /etc/dark-noc/tls "$backup_dir/tls"; fi
    if [[ -d /opt/dark-noc-agent ]]; then had_local_agent_opt=1; cp -a /opt/dark-noc-agent "$backup_dir/local-agent-opt"; fi
    if [[ -d /etc/dark-noc-agent ]]; then had_local_agent_etc=1; cp -a /etc/dark-noc-agent "$backup_dir/local-agent-etc"; fi
    if [[ -f /etc/systemd/system/dark-noc-agent.service ]]; then had_local_agent_service=1; cp -a /etc/systemd/system/dark-noc-agent.service "$backup_dir/local-agent.service"; fi
    if [[ -f /usr/local/bin/darknoc ]]; then had_darknoc_cli=1; cp -a /usr/local/bin/darknoc "$backup_dir/darknoc"; fi
    [[ ! -f "$hub_db_path" ]] || had_database=1
    [[ ! -f "$hub_key_path" ]] || had_master_key=1
    if [[ -f /etc/nginx/sites-available/dark-noc ]]; then had_nginx_site=1; cp -a /etc/nginx/sites-available/dark-noc "$backup_dir/nginx-site"; fi
    if [[ -e /etc/nginx/sites-enabled/dark-noc || -L /etc/nginx/sites-enabled/dark-noc ]]; then had_nginx_link=1; cp -a /etc/nginx/sites-enabled/dark-noc "$backup_dir/nginx-link"; fi
    if [[ -f /etc/systemd/system/dark-noc-hub.service.d/override.conf ]]; then
      had_systemd_override=1
      cp -a /etc/systemd/system/dark-noc-hub.service.d/override.conf "$backup_dir/override.conf"
    fi
    if [[ -f /etc/systemd/system/dark-noc-hub.service.d/data-path.conf ]]; then
      had_systemd_data_path=1
      cp -a /etc/systemd/system/dark-noc-hub.service.d/data-path.conf "$backup_dir/data-path.conf"
    fi
    rollback_hub() {
      local reason="${1:-ERR}" exit_status="${2:-1}" rollback_failed=0
      trap - ERR
      trap '' INT TERM
      set +Ee
      [[ "$exit_status" =~ ^[0-9]+$ ]] || exit_status=1
      [[ "$exit_status" -ne 0 ]] || exit_status=1
      echo "Upgrade interrupted ($reason); restoring previous Hub files."
      if [[ "$mutation_started" -eq 0 ]]; then
        # No package/config/data mutation has occurred yet. Only undo service
        # quiescence, avoiding an older pre-quiesce database restore.
        restore_service_state dark-noc-hub.service "$hub_was_active" "$hub_was_enabled" "$hub_was_present" || rollback_failed=1
        restore_service_state dark-noc-agent.service "$agent_was_active" "$agent_was_enabled" "$agent_was_present" || rollback_failed=1
        if [[ "$rollback_failed" -eq 0 ]]; then
          rm -rf "$backup_dir" || rollback_failed=1
        fi
        if [[ "$rollback_failed" -ne 0 ]]; then
          echo "ROLLBACK PARTIAL FAILURE: service quiescence could not be fully undone." >&2
          echo "Backup retained at: $backup_dir" >&2
        else
          echo "Pre-upgrade service state restored successfully."
        fi
        exit "$exit_status"
      fi
      if [[ "$hub_was_present" -eq 1 || -f /etc/systemd/system/dark-noc-hub.service ]]; then
        rollback_step "could not stop Hub before restore" systemctl stop dark-noc-hub.service || rollback_failed=1
      fi
      if [[ "$agent_was_present" -eq 1 || -f /etc/systemd/system/dark-noc-agent.service ]]; then
        rollback_step "could not stop local Agent before restore" systemctl stop dark-noc-agent.service || rollback_failed=1
      fi
      if rollback_step "could not remove the upgraded Hub directory" rm -rf /opt/dark-noc/hub; then
        rollback_step "could not restore the Hub directory" cp -a "$backup_dir/hub" /opt/dark-noc/hub || rollback_failed=1
      else
        rollback_failed=1
      fi
      if rollback_step "could not remove the upgraded Agent payload" rm -rf /opt/dark-noc/agent-payload; then
        if [[ "$had_agent_payload" -eq 1 ]]; then
          rollback_step "could not restore the Agent payload" cp -a "$backup_dir/agent-payload" /opt/dark-noc/agent-payload || rollback_failed=1
        fi
      else
        rollback_failed=1
      fi
      if [[ "$had_hub_service" -eq 1 ]]; then
        rollback_step "could not restore the Hub systemd unit" cp -a "$backup_dir/hub.service" /etc/systemd/system/dark-noc-hub.service || rollback_failed=1
      else
        rollback_step "could not remove the newly installed Hub systemd unit" rm -f /etc/systemd/system/dark-noc-hub.service || rollback_failed=1
      fi
      if [[ "$had_hub_env" -eq 1 ]]; then
        rollback_step "could not restore hub.env" cp -a "$backup_dir/hub.env" /etc/dark-noc/hub.env || rollback_failed=1
      else
        rollback_step "could not remove the newly installed hub.env" rm -f /etc/dark-noc/hub.env || rollback_failed=1
      fi
      if rollback_step "could not remove upgraded TLS files" rm -rf /etc/dark-noc/tls; then
        if [[ "$had_tls" -eq 1 ]]; then
          rollback_step "could not restore TLS files" cp -a "$backup_dir/tls" /etc/dark-noc/tls || rollback_failed=1
        fi
      else
        rollback_failed=1
      fi
      if rollback_step "could not remove upgraded local Agent files" rm -rf /opt/dark-noc-agent /etc/dark-noc-agent; then
        if [[ "$had_local_agent_opt" -eq 1 ]]; then
          rollback_step "could not restore local Agent program files" cp -a "$backup_dir/local-agent-opt" /opt/dark-noc-agent || rollback_failed=1
        fi
        if [[ "$had_local_agent_etc" -eq 1 ]]; then
          rollback_step "could not restore local Agent configuration" cp -a "$backup_dir/local-agent-etc" /etc/dark-noc-agent || rollback_failed=1
        fi
      else
        rollback_failed=1
      fi
      if [[ "$had_local_agent_service" -eq 1 ]]; then
        rollback_step "could not restore the local Agent systemd unit" cp -a "$backup_dir/local-agent.service" /etc/systemd/system/dark-noc-agent.service || rollback_failed=1
      else
        rollback_step "could not remove the newly installed local Agent systemd unit" rm -f /etc/systemd/system/dark-noc-agent.service || rollback_failed=1
      fi
      if [[ "$had_darknoc_cli" -eq 1 ]]; then
        rollback_step "could not restore the darknoc server CLI" cp -a "$backup_dir/darknoc" /usr/local/bin/darknoc || rollback_failed=1
      else
        rollback_step "could not remove the newly installed darknoc server CLI" rm -f /usr/local/bin/darknoc || rollback_failed=1
      fi
      if [[ "$had_database" -eq 1 && -f "$backup_dir/dark-noc.db" ]]; then
        rollback_step "could not remove database WAL/SHM files" rm -f "$hub_db_path-wal" "$hub_db_path-shm" || rollback_failed=1
        rollback_step "could not restore the Hub database" cp -a "$backup_dir/dark-noc.db" "$hub_db_path" || rollback_failed=1
      elif [[ "$had_database" -eq 0 ]]; then
        rollback_step "could not remove the database created by the failed upgrade" rm -f "$hub_db_path" "$hub_db_path-wal" "$hub_db_path-shm" || rollback_failed=1
      else
        echo "ROLLBACK ERROR: the database backup is missing" >&2
        rollback_failed=1
      fi
      if [[ "$had_master_key" -eq 1 && -f "$backup_dir/master.key" ]]; then
        rollback_step "could not restore the Hub master key" cp -a "$backup_dir/master.key" "$hub_key_path" || rollback_failed=1
      elif [[ "$had_master_key" -eq 0 ]]; then
        rollback_step "could not remove the master key created by the failed upgrade" rm -f "$hub_key_path" || rollback_failed=1
      else
        echo "ROLLBACK ERROR: the master-key backup is missing" >&2
        rollback_failed=1
      fi
      if [[ "$had_nginx_site" -eq 1 ]]; then
        rollback_step "could not restore the Nginx site" cp -a "$backup_dir/nginx-site" /etc/nginx/sites-available/dark-noc || rollback_failed=1
      else
        rollback_step "could not remove the newly installed Nginx site" rm -f /etc/nginx/sites-available/dark-noc || rollback_failed=1
      fi
      if rollback_step "could not remove the upgraded Nginx site link" rm -f /etc/nginx/sites-enabled/dark-noc; then
        if [[ "$had_nginx_link" -eq 1 ]]; then
          rollback_step "could not restore the Nginx site link" cp -a "$backup_dir/nginx-link" /etc/nginx/sites-enabled/dark-noc || rollback_failed=1
        fi
      else
        rollback_failed=1
      fi
      if [[ "$had_systemd_override" -eq 1 ]]; then
        if rollback_step "could not recreate the Hub systemd override directory" install -d -m 0755 /etc/systemd/system/dark-noc-hub.service.d; then
          rollback_step "could not restore the Hub systemd override" cp -a "$backup_dir/override.conf" /etc/systemd/system/dark-noc-hub.service.d/override.conf || rollback_failed=1
        else
          rollback_failed=1
        fi
      else
        rollback_step "could not remove the Hub systemd override created by the failed upgrade" rm -f /etc/systemd/system/dark-noc-hub.service.d/override.conf || rollback_failed=1
      fi
      if [[ "$had_systemd_data_path" -eq 1 ]]; then
        if rollback_step "could not recreate the Hub systemd data-path directory" install -d -m 0755 /etc/systemd/system/dark-noc-hub.service.d; then
          rollback_step "could not restore the Hub systemd data-path policy" cp -a "$backup_dir/data-path.conf" /etc/systemd/system/dark-noc-hub.service.d/data-path.conf || rollback_failed=1
        else
          rollback_failed=1
        fi
      else
        rollback_step "could not remove the Hub data-path policy created by the failed upgrade" rm -f /etc/systemd/system/dark-noc-hub.service.d/data-path.conf || rollback_failed=1
      fi
      if [[ -x /opt/dark-noc/venv/bin/pip && -f /opt/dark-noc/hub/requirements.txt ]]; then
        rollback_step "could not restore Hub Python dependencies" /opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt || rollback_failed=1
      else
        echo "ROLLBACK ERROR: Hub virtualenv or restored requirements are missing" >&2
        rollback_failed=1
      fi
      rollback_step "systemd daemon-reload failed" systemctl daemon-reload || rollback_failed=1
      restore_service_state nginx.service "$nginx_was_active" "$nginx_was_enabled" "$nginx_was_present" || rollback_failed=1
      if [[ "$nginx_was_active" -eq 1 && "$nginx_was_present" -eq 1 ]]; then
        if rollback_step "restored Nginx configuration is invalid" nginx -t; then
          rollback_step "could not reload restored Nginx configuration" systemctl reload nginx.service || rollback_failed=1
        else
          rollback_failed=1
        fi
      fi
      restore_service_state dark-noc-hub.service "$hub_was_active" "$hub_was_enabled" "$hub_was_present" || rollback_failed=1
      restore_service_state dark-noc-agent.service "$agent_was_active" "$agent_was_enabled" "$agent_was_present" || rollback_failed=1
      if [[ "$rollback_failed" -eq 0 ]]; then
        if rm -rf "$backup_dir"; then
          echo "Rollback completed successfully."
        else
          rollback_failed=1
          echo "ROLLBACK ERROR: rollback completed, but the backup could not be removed" >&2
        fi
      fi
      if [[ "$rollback_failed" -ne 0 ]]; then
        echo "ROLLBACK PARTIAL FAILURE: manual recovery may be required." >&2
        echo "Backup retained at: $backup_dir" >&2
      fi
      exit "$exit_status"
    }
    trap 'rollback_hub ERR "$?"' ERR
    trap 'rollback_hub INT 130' INT
    trap 'rollback_hub TERM 143' TERM
    echo "Quiescing the local Agent and Hub for a consistent final snapshot."
    if [[ "$agent_was_present" -eq 1 ]]; then systemctl stop dark-noc-agent.service; fi
    if [[ "$hub_was_present" -eq 1 ]]; then systemctl stop dark-noc-hub.service; fi
    if [[ "$had_database" -eq 1 ]]; then
      python3 - "$hub_db_path" "$backup_dir/dark-noc.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
source.close(); target.close()
PY
      chown --reference="$hub_db_path" "$backup_dir/dark-noc.db"
      chmod --reference="$hub_db_path" "$backup_dir/dark-noc.db"
    fi
    if [[ "$had_master_key" -eq 1 ]]; then cp -a "$hub_key_path" "$backup_dir/master.key"; fi
    mutation_started=1
    echo "Configuring the public domain/IP and HTTPS gateway."
    bash "$SCRIPT_DIR/install-hub.sh"
    systemctl daemon-reload
    if [[ "$hub_was_present" -eq 1 ]]; then
      restore_service_state dark-noc-hub.service "$hub_was_active" "$hub_was_enabled" "$hub_was_present"
    fi
    # The local Agent is a required Hub component. A common failure mode was:
    # old Agent is crashed/stopped -> install-hub repairs it -> upgrade restores
    # the old stopped state. Keep the repaired Agent enabled and prove that a
    # fresh pulse reaches the Hub before declaring the upgrade successful.
    POST_UPGRADE_AGENT_BASELINE="$(python3 - "$hub_db_path" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute(
        "SELECT COALESCE(MAX(last_seen),0) FROM nodes WHERE role='hub'"
    ).fetchone()
print(int(row[0] or 0) if row else 0)
PY
)"
    systemctl enable dark-noc-agent.service
    systemctl restart dark-noc-agent.service
    POST_UPGRADE_AGENT_READY=0
    for attempt in {1..30}; do
      if python3 - "$hub_db_path" "$POST_UPGRADE_AGENT_BASELINE" <<'PY'
import sqlite3, sys, time
with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute(
        "SELECT status,agent_version,last_seen FROM nodes WHERE role='hub' ORDER BY id LIMIT 1"
    ).fetchone()
baseline = int(sys.argv[2])
ready = bool(
    row and row[0] == "online" and row[1] == "2.9.26"
    and int(row[2] or 0) > baseline
    and int(row[2] or 0) >= int(time.time()) - 90
)
raise SystemExit(0 if ready else 1)
PY
      then
        POST_UPGRADE_AGENT_READY=1
        break
      fi
      sleep 2
    done
    if [[ "$POST_UPGRADE_AGENT_READY" -ne 1 ]]; then
      echo "Local Hub Agent did not deliver a fresh v2.9.26 post-upgrade pulse." >&2
      systemctl status dark-noc-agent.service --no-pager -l || true
      journalctl -u dark-noc-agent.service -n 160 --no-pager || true
      exit 1
    fi
    if [[ "$nginx_was_present" -eq 1 ]]; then
      restore_service_state nginx.service "$nginx_was_active" "$nginx_was_enabled" "$nginx_was_present"
    fi
    if systemctl is-active --quiet dark-noc-hub.service; then
      systemctl status dark-noc-hub.service --no-pager -l
    else
      echo "Hub upgrade completed; its pre-upgrade stopped state was preserved."
    fi
    trap - ERR INT TERM
    if ! rm -rf "$backup_dir"; then echo "WARNING: upgrade succeeded, but backup cleanup failed: $backup_dir" >&2; fi
    ;;
  agent)
    if [[ ! -d /opt/dark-noc-agent ]]; then echo "Agent is not installed."; exit 1; fi
    backup_dir="$(mktemp -d /tmp/dark-noc-agent-upgrade.XXXXXX)"
    agent_was_active="$(service_was_active dark-noc-agent.service)"
    agent_was_enabled="$(service_was_enabled dark-noc-agent.service)"
    agent_was_present="$(service_was_present dark-noc-agent.service)"
    had_agent_service=0; had_realm_adapter=0
    cp -a /opt/dark-noc-agent/agent.py /opt/dark-noc-agent/requirements.txt "$backup_dir/"
    if [[ -f /opt/dark-noc-agent/realm_plugin.py ]]; then had_realm_adapter=1; cp -a /opt/dark-noc-agent/realm_plugin.py "$backup_dir/"; fi
    if [[ -f /etc/systemd/system/dark-noc-agent.service ]]; then had_agent_service=1; cp -a /etc/systemd/system/dark-noc-agent.service "$backup_dir/service"; fi
    rollback_agent() {
      local reason="${1:-ERR}" exit_status="${2:-1}" rollback_failed=0
      trap - ERR
      trap '' INT TERM
      set +Ee
      [[ "$exit_status" =~ ^[0-9]+$ ]] || exit_status=1
      [[ "$exit_status" -ne 0 ]] || exit_status=1
      echo "Upgrade interrupted ($reason); restoring previous Agent files."
      if [[ "$agent_was_present" -eq 1 || -f /etc/systemd/system/dark-noc-agent.service ]]; then
        rollback_step "could not stop Agent before restore" systemctl stop dark-noc-agent.service || rollback_failed=1
      fi
      rollback_step "could not restore Agent program files" cp -a "$backup_dir/agent.py" "$backup_dir/requirements.txt" /opt/dark-noc-agent/ || rollback_failed=1
      if [[ "$had_realm_adapter" -eq 1 ]]; then
        rollback_step "could not restore Realm Agent adapter" cp -a "$backup_dir/realm_plugin.py" /opt/dark-noc-agent/realm_plugin.py || rollback_failed=1
      else
        rollback_step "could not remove newly installed Realm Agent adapter" rm -f /opt/dark-noc-agent/realm_plugin.py || rollback_failed=1
      fi
      if [[ "$had_agent_service" -eq 1 ]]; then
        rollback_step "could not restore the Agent systemd unit" cp -a "$backup_dir/service" /etc/systemd/system/dark-noc-agent.service || rollback_failed=1
      else
        rollback_step "could not remove the Agent systemd unit created by the failed upgrade" rm -f /etc/systemd/system/dark-noc-agent.service || rollback_failed=1
      fi
      if [[ -x /opt/dark-noc-agent/venv/bin/pip && -f /opt/dark-noc-agent/requirements.txt ]]; then
        rollback_step "could not restore Agent Python dependencies" /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt || rollback_failed=1
      else
        echo "ROLLBACK ERROR: Agent virtualenv or restored requirements are missing" >&2
        rollback_failed=1
      fi
      rollback_step "systemd daemon-reload failed" systemctl daemon-reload || rollback_failed=1
      restore_service_state dark-noc-agent.service "$agent_was_active" "$agent_was_enabled" "$agent_was_present" || rollback_failed=1
      if [[ "$rollback_failed" -eq 0 ]]; then
        if rm -rf "$backup_dir"; then
          echo "Rollback completed successfully."
        else
          rollback_failed=1
          echo "ROLLBACK ERROR: rollback completed, but the backup could not be removed" >&2
        fi
      fi
      if [[ "$rollback_failed" -ne 0 ]]; then
        echo "ROLLBACK PARTIAL FAILURE: manual recovery may be required." >&2
        echo "Backup retained at: $backup_dir" >&2
      fi
      exit "$exit_status"
    }
    trap 'rollback_agent ERR "$?"' ERR
    trap 'rollback_agent INT 130' INT
    trap 'rollback_agent TERM 143' TERM
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y ca-certificates curl tar openssl certbot iproute2 iputils-ping iptables iperf3 snmp tmux
    install -d -m 0700 /etc/dark-backhaul /etc/dark-ghostpro /etc/dark-packetpro /etc/dark-realm
    install -d -m 0755 /var/lib/dark-noc-acme /var/lib/dark-noc-acme/.well-known /var/lib/dark-noc-acme/.well-known/acme-challenge /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /etc/nginx/conf.d
    systemctl stop dark-noc-agent.service
    install -m 0755 "$SCRIPT_DIR/agent/agent.py" /opt/dark-noc-agent/agent.py
    install -m 0644 "$SCRIPT_DIR/agent/realm_plugin.py" /opt/dark-noc-agent/realm_plugin.py
    install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt
    /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt
    /opt/dark-noc-agent/venv/bin/python -m py_compile /opt/dark-noc-agent/agent.py /opt/dark-noc-agent/realm_plugin.py
    install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /etc/systemd/system/dark-noc-agent.service
    if grep -q '"hub_url"[[:space:]]*:[[:space:]]*"http://.*:9090' /etc/dark-noc-agent/config.json 2>/dev/null; then
      echo "WARNING: Agent still uses the legacy HTTP :9090 Hub URL."
      echo "Run install-agent.sh and enter the new HTTPS URL plus this node's current/new token."
    fi
    systemctl daemon-reload
    if [[ "$agent_was_present" -eq 1 ]]; then
      restore_service_state dark-noc-agent.service "$agent_was_active" "$agent_was_enabled" "$agent_was_present"
    fi
    if systemctl is-active --quiet dark-noc-agent.service; then
      systemctl status dark-noc-agent.service --no-pager -l
    else
      echo "Agent upgrade completed; its pre-upgrade stopped state was preserved."
    fi
    trap - ERR INT TERM
    if ! rm -rf "$backup_dir"; then echo "WARNING: upgrade succeeded, but backup cleanup failed: $backup_dir" >&2; fi
    ;;
  *) echo "Usage: sudo bash upgrade.sh hub|agent"; exit 1 ;;
esac
