#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-999} -ne 0 ]]; then
  echo "Run as root: sudo bash install-hub.sh"
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${DARK_NOC_ADMIN_USER:-}"
ADMIN_PASSWORD="${DARK_NOC_ADMIN_PASSWORD:-}"
PUBLIC_HOST="${DARK_NOC_PUBLIC_HOST:-}"
PUBLIC_PORT="${DARK_NOC_PUBLIC_PORT:-}"
HUB_PORT="${DARK_NOC_HUB_PORT:-}"
PANEL_CERT_MODE="${DARK_NOC_PANEL_CERT_MODE:-}"
TELEGRAM_BOT_TOKEN="${DARK_NOC_TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${DARK_NOC_TELEGRAM_CHAT_ID:-}"
SSH_UPLOAD_LIMIT_MB="${DARK_NOC_SSH_UPLOAD_LIMIT_MB:-}"
SSH_RELAY_LIMIT_MB="${DARK_NOC_SSH_RELAY_LIMIT_MB:-}"
SSH_TRANSFER_CONCURRENCY="${DARK_NOC_SSH_TRANSFER_CONCURRENCY:-}"
SSH_TRANSFER_TIMEOUT_SECONDS="${DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS:-}"
SSH_IDLE_TIMEOUT_SECONDS="${DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS:-}"
SSH_KEEPALIVE_INTERVAL_SECONDS="${DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS:-}"
SSH_KEEPALIVE_COUNT_MAX="${DARK_NOC_SSH_KEEPALIVE_COUNT_MAX:-}"
SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS="${DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS:-}"
PROVISION_TIMEOUT_SECONDS="${DARK_NOC_PROVISION_TIMEOUT_SECONDS:-}"
PROVISION_CONCURRENCY="${DARK_NOC_PROVISION_CONCURRENCY:-}"
SSH_EDITOR_LIMIT_KB="${DARK_NOC_SSH_EDITOR_LIMIT_KB:-}"
MONITOR_RETENTION_DAYS="${DARK_NOC_MONITOR_RETENTION_DAYS:-}"
METRIC_RETENTION_DAYS="${DARK_NOC_METRIC_RETENTION_DAYS:-}"
ROLLUP_RETENTION_DAYS="${DARK_NOC_ROLLUP_RETENTION_DAYS:-}"
TUNNEL_RETENTION_DAYS="${DARK_NOC_TUNNEL_RETENTION_DAYS:-}"
HUB_LEASE_SECONDS="${DARK_NOC_HUB_LEASE_SECONDS:-}"
DATA_DIR="${DARK_NOC_DATA:-}"
LOCAL_ENROLL_SECRET=""

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

validate_integer_setting() {
  local name="$1" value="$2" minimum="$3" maximum="$4"
  if [[ ! "$value" =~ ^[1-9][0-9]{0,5}$ ]] || (( 10#$value < minimum || 10#$value > maximum )); then
    echo "$name must be a whole number from $minimum to $maximum." >&2
    return 1
  fi
}

echo ""
echo "  DARK NOC // HUB INSTALLER"
echo "  Nightfall Command v2.9.3"
echo ""

SERVER_IP="$(hostname -I | awk '{print $1}')"
DEFAULT_PUBLIC_HOST="$SERVER_IP"
DEFAULT_PUBLIC_PORT="9090"
HAD_HUB_ENV=0
if [[ -f /etc/dark-noc/hub.env ]]; then
  HAD_HUB_ENV=1
  saved_public_host="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PUBLIC_HOST:-}"' _ /etc/dark-noc/hub.env)"
  saved_public_port="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PUBLIC_PORT:-}"' _ /etc/dark-noc/hub.env)"
  saved_cert_mode="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PANEL_CERT_MODE:-}"' _ /etc/dark-noc/hub.env)"
  saved_admin_user="$(bash -c 'source "$1"; printf %s "${DARK_NOC_ADMIN_USER:-}"' _ /etc/dark-noc/hub.env)"
  saved_admin_password="$(bash -c 'source "$1"; printf %s "${DARK_NOC_ADMIN_PASSWORD:-}"' _ /etc/dark-noc/hub.env)"
  saved_upload_limit="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_UPLOAD_LIMIT_MB:-}"' _ /etc/dark-noc/hub.env)"
  saved_relay_limit="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_RELAY_LIMIT_MB:-}"' _ /etc/dark-noc/hub.env)"
  saved_transfer_concurrency="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_TRANSFER_CONCURRENCY:-}"' _ /etc/dark-noc/hub.env)"
  saved_transfer_timeout="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_idle_timeout="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_keepalive_interval="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_keepalive_count="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_KEEPALIVE_COUNT_MAX:-}"' _ /etc/dark-noc/hub.env)"
  saved_upload_queue_timeout="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_provision_timeout="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PROVISION_TIMEOUT_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_provision_concurrency="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PROVISION_CONCURRENCY:-}"' _ /etc/dark-noc/hub.env)"
  saved_editor_limit="$(bash -c 'source "$1"; printf %s "${DARK_NOC_SSH_EDITOR_LIMIT_KB:-}"' _ /etc/dark-noc/hub.env)"
  saved_monitor_retention="$(bash -c 'source "$1"; printf %s "${DARK_NOC_MONITOR_RETENTION_DAYS:-}"' _ /etc/dark-noc/hub.env)"
  saved_metric_retention="$(bash -c 'source "$1"; printf %s "${DARK_NOC_METRIC_RETENTION_DAYS:-}"' _ /etc/dark-noc/hub.env)"
  saved_rollup_retention="$(bash -c 'source "$1"; printf %s "${DARK_NOC_ROLLUP_RETENTION_DAYS:-}"' _ /etc/dark-noc/hub.env)"
  saved_tunnel_retention="$(bash -c 'source "$1"; printf %s "${DARK_NOC_TUNNEL_RETENTION_DAYS:-}"' _ /etc/dark-noc/hub.env)"
  saved_hub_lease="$(bash -c 'source "$1"; printf %s "${DARK_NOC_HUB_LEASE_SECONDS:-}"' _ /etc/dark-noc/hub.env)"
  saved_data_dir="$(bash -c 'source "$1"; printf %s "${DARK_NOC_DATA:-}"' _ /etc/dark-noc/hub.env)"
  [[ -z "$saved_public_host" ]] || DEFAULT_PUBLIC_HOST="$saved_public_host"
  [[ -n "$PUBLIC_HOST" ]] || PUBLIC_HOST="$saved_public_host"
  # Releases before v2.7.0 exposed the panel on 443 and did not store a public-port key.
  DEFAULT_PUBLIC_PORT="${saved_public_port:-443}"
  [[ -n "$PUBLIC_PORT" ]] || PUBLIC_PORT="$saved_public_port"
  [[ -n "$PANEL_CERT_MODE" ]] || PANEL_CERT_MODE="$saved_cert_mode"
  if [[ -z "$PANEL_CERT_MODE" && -n "$saved_public_host" && -L /etc/dark-noc/tls/panel.crt ]]; then
    saved_certificate_target="$(readlink -f /etc/dark-noc/tls/panel.crt 2>/dev/null || true)"
    [[ "$saved_certificate_target" != /etc/letsencrypt/live/*/fullchain.pem ]] || PANEL_CERT_MODE="letsencrypt"
  fi
  [[ -n "$ADMIN_USER" ]] || ADMIN_USER="$saved_admin_user"
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$saved_admin_password"
  [[ -n "$SSH_UPLOAD_LIMIT_MB" ]] || SSH_UPLOAD_LIMIT_MB="$saved_upload_limit"
  [[ -n "$SSH_RELAY_LIMIT_MB" ]] || SSH_RELAY_LIMIT_MB="$saved_relay_limit"
  [[ -n "$SSH_TRANSFER_CONCURRENCY" ]] || SSH_TRANSFER_CONCURRENCY="$saved_transfer_concurrency"
  [[ -n "$SSH_TRANSFER_TIMEOUT_SECONDS" ]] || SSH_TRANSFER_TIMEOUT_SECONDS="$saved_transfer_timeout"
  [[ -n "$SSH_IDLE_TIMEOUT_SECONDS" ]] || SSH_IDLE_TIMEOUT_SECONDS="$saved_idle_timeout"
  [[ -n "$SSH_KEEPALIVE_INTERVAL_SECONDS" ]] || SSH_KEEPALIVE_INTERVAL_SECONDS="$saved_keepalive_interval"
  [[ -n "$SSH_KEEPALIVE_COUNT_MAX" ]] || SSH_KEEPALIVE_COUNT_MAX="$saved_keepalive_count"
  [[ -n "$SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS" ]] || SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS="$saved_upload_queue_timeout"
  [[ -n "$PROVISION_TIMEOUT_SECONDS" ]] || PROVISION_TIMEOUT_SECONDS="$saved_provision_timeout"
  [[ -n "$PROVISION_CONCURRENCY" ]] || PROVISION_CONCURRENCY="$saved_provision_concurrency"
  [[ -n "$SSH_EDITOR_LIMIT_KB" ]] || SSH_EDITOR_LIMIT_KB="$saved_editor_limit"
  [[ -n "$MONITOR_RETENTION_DAYS" ]] || MONITOR_RETENTION_DAYS="$saved_monitor_retention"
  [[ -n "$METRIC_RETENTION_DAYS" ]] || METRIC_RETENTION_DAYS="$saved_metric_retention"
  [[ -n "$ROLLUP_RETENTION_DAYS" ]] || ROLLUP_RETENTION_DAYS="$saved_rollup_retention"
  [[ -n "$TUNNEL_RETENTION_DAYS" ]] || TUNNEL_RETENTION_DAYS="$saved_tunnel_retention"
  [[ -n "$HUB_LEASE_SECONDS" ]] || HUB_LEASE_SECONDS="$saved_hub_lease"
  # Existing installations retain their data location. Moving the database and
  # master key requires a separate explicit migration, never a routine upgrade.
  [[ -z "$saved_data_dir" ]] || DATA_DIR="$saved_data_dir"
fi
SSH_UPLOAD_LIMIT_MB="${SSH_UPLOAD_LIMIT_MB:-1024}"
SSH_RELAY_LIMIT_MB="${SSH_RELAY_LIMIT_MB:-20480}"
SSH_TRANSFER_CONCURRENCY="${SSH_TRANSFER_CONCURRENCY:-4}"
SSH_TRANSFER_TIMEOUT_SECONDS="${SSH_TRANSFER_TIMEOUT_SECONDS:-86400}"
SSH_IDLE_TIMEOUT_SECONDS="${SSH_IDLE_TIMEOUT_SECONDS:-60}"
SSH_KEEPALIVE_INTERVAL_SECONDS="${SSH_KEEPALIVE_INTERVAL_SECONDS:-15}"
SSH_KEEPALIVE_COUNT_MAX="${SSH_KEEPALIVE_COUNT_MAX:-3}"
SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS="${SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS:-30}"
PROVISION_TIMEOUT_SECONDS="${PROVISION_TIMEOUT_SECONDS:-1800}"
PROVISION_CONCURRENCY="${PROVISION_CONCURRENCY:-4}"
SSH_EDITOR_LIMIT_KB="${SSH_EDITOR_LIMIT_KB:-1024}"
MONITOR_RETENTION_DAYS="${MONITOR_RETENTION_DAYS:-90}"
METRIC_RETENTION_DAYS="${METRIC_RETENTION_DAYS:-31}"
ROLLUP_RETENTION_DAYS="${ROLLUP_RETENTION_DAYS:-730}"
TUNNEL_RETENTION_DAYS="${TUNNEL_RETENTION_DAYS:-31}"
HUB_LEASE_SECONDS="${HUB_LEASE_SECONDS:-75}"
DATA_DIR="$(validate_data_dir "${DATA_DIR:-/var/lib/dark-noc}")"
validate_integer_setting DARK_NOC_SSH_UPLOAD_LIMIT_MB "$SSH_UPLOAD_LIMIT_MB" 1 102400
validate_integer_setting DARK_NOC_SSH_RELAY_LIMIT_MB "$SSH_RELAY_LIMIT_MB" 1 102400
validate_integer_setting DARK_NOC_SSH_TRANSFER_CONCURRENCY "$SSH_TRANSFER_CONCURRENCY" 1 32
validate_integer_setting DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS "$SSH_TRANSFER_TIMEOUT_SECONDS" 30 86400
validate_integer_setting DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS "$SSH_IDLE_TIMEOUT_SECONDS" 5 3600
validate_integer_setting DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS "$SSH_KEEPALIVE_INTERVAL_SECONDS" 1 300
validate_integer_setting DARK_NOC_SSH_KEEPALIVE_COUNT_MAX "$SSH_KEEPALIVE_COUNT_MAX" 1 20
validate_integer_setting DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS "$SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS" 1 300
validate_integer_setting DARK_NOC_PROVISION_TIMEOUT_SECONDS "$PROVISION_TIMEOUT_SECONDS" 60 7200
validate_integer_setting DARK_NOC_PROVISION_CONCURRENCY "$PROVISION_CONCURRENCY" 1 32
validate_integer_setting DARK_NOC_SSH_EDITOR_LIMIT_KB "$SSH_EDITOR_LIMIT_KB" 16 16384
validate_integer_setting DARK_NOC_MONITOR_RETENTION_DAYS "$MONITOR_RETENTION_DAYS" 7 730
validate_integer_setting DARK_NOC_METRIC_RETENTION_DAYS "$METRIC_RETENTION_DAYS" 1 365
validate_integer_setting DARK_NOC_ROLLUP_RETENTION_DAYS "$ROLLUP_RETENTION_DAYS" 30 3650
validate_integer_setting DARK_NOC_TUNNEL_RETENTION_DAYS "$TUNNEL_RETENTION_DAYS" 1 365
validate_integer_setting DARK_NOC_HUB_LEASE_SECONDS "$HUB_LEASE_SECONDS" 30 300
if [[ -z "$PUBLIC_HOST" ]]; then
  read -r -p "Panel domain or public IP [$DEFAULT_PUBLIC_HOST]: " input_host
  PUBLIC_HOST="${input_host:-$DEFAULT_PUBLIC_HOST}"
fi
PUBLIC_HOST="${PUBLIC_HOST#http://}"; PUBLIC_HOST="${PUBLIC_HOST#https://}"; PUBLIC_HOST="${PUBLIC_HOST%%/*}"
if [[ ! "$PUBLIC_HOST" =~ ^([A-Za-z0-9.-]+|[0-9A-Fa-f:]+)$ ]]; then
  echo "Invalid domain or IP: $PUBLIC_HOST"; exit 1
fi
if [[ "$PUBLIC_HOST" == *:* ]]; then
  if ! python3 - "$PUBLIC_HOST" <<'PY'
import ipaddress, sys
raise SystemExit(0 if isinstance(ipaddress.ip_address(sys.argv[1]), ipaddress.IPv6Address) else 1)
PY
  then
    echo "Invalid IPv6 address: $PUBLIC_HOST"
    exit 1
  fi
  PUBLIC_URL_HOST="[$PUBLIC_HOST]"
else
  PUBLIC_URL_HOST="$PUBLIC_HOST"
fi
AUTO_ISSUE_PANEL_SSL=0
if [[ -n "${saved_public_host:-}" && "$PUBLIC_HOST" != "$saved_public_host" ]]; then
  PANEL_CERT_MODE="selfsigned"
fi
if [[ ! "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$PUBLIC_HOST" != *:* && "$PANEL_CERT_MODE" != "letsencrypt" ]]; then
  AUTO_ISSUE_PANEL_SSL=1
fi
if [[ -z "$PUBLIC_PORT" ]]; then
  if [[ "$HAD_HUB_ENV" -eq 1 ]]; then
    PUBLIC_PORT="$DEFAULT_PUBLIC_PORT"
  else
    read -r -p "Public panel HTTPS port [$DEFAULT_PUBLIC_PORT]: " input_public_port
    PUBLIC_PORT="${input_public_port:-$DEFAULT_PUBLIC_PORT}"
  fi
fi
if [[ ! "$PUBLIC_PORT" =~ ^[0-9]{1,5}$ ]] || ! (( 10#$PUBLIC_PORT == 443 || (10#$PUBLIC_PORT >= 1024 && 10#$PUBLIC_PORT <= 65535) )) || (( 10#$PUBLIC_PORT == 80 )); then
  echo "Invalid public panel port: $PUBLIC_PORT (use 443 or 1024-65535; port 80 is reserved for SSL)"
  exit 1
fi
PUBLIC_PORT_SUFFIX=""
[[ "$PUBLIC_PORT" == "443" ]] || PUBLIC_PORT_SUFFIX=":$PUBLIC_PORT"
PUBLIC_URL="https://$PUBLIC_URL_HOST$PUBLIC_PORT_SUFFIX"
OLD_HUB_PORT="9090"
if [[ -f /etc/dark-noc/hub.env ]]; then
  saved_hub_port="$(bash -c 'source "$1"; printf %s "${DARK_NOC_HUB_PORT:-}"' _ /etc/dark-noc/hub.env)"
  [[ -z "$saved_hub_port" ]] || OLD_HUB_PORT="$saved_hub_port"
fi
if [[ -z "$HUB_PORT" ]]; then
  if [[ -f /etc/dark-noc/hub.env ]]; then
    HUB_PORT="$OLD_HUB_PORT"
  else
    HUB_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
  fi
fi
if [[ "$HUB_PORT" == "$PUBLIC_PORT" ]]; then
  HUB_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
  echo "Public port needed the old backend port; internal Hub moved automatically to $HUB_PORT."
fi
if [[ ! "$HUB_PORT" =~ ^[0-9]+$ ]] || (( HUB_PORT < 1024 || HUB_PORT > 65535 )); then
  echo "Invalid internal port: $HUB_PORT (use 1024-65535)"
  exit 1
fi
if [[ "$HUB_PORT" != "$OLD_HUB_PORT" ]] && ! python3 - "$HUB_PORT" <<'PY'
import socket, sys
with socket.socket() as sock:
    sock.bind(('127.0.0.1', int(sys.argv[1])))
PY
then
  echo "Internal port $HUB_PORT is already in use. Run the installer again and choose another port."
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip ca-certificates curl tar nginx openssl certbot python3-certbot-nginx iproute2 iputils-ping iptables iperf3 snmp tmux
for public_port in 80 "$PUBLIC_PORT"; do
  holder="$(ss -H -ltnp "sport = :$public_port" 2>/dev/null || true)"
  if [[ "$public_port" == "$OLD_HUB_PORT" && "$holder" == *uvicorn* ]]; then
    # The old private backend is moved before Nginx claims this public port.
    continue
  fi
  if [[ -n "$holder" && "$holder" != *nginx* ]]; then
    echo "Public port $public_port is already used by a non-Nginx service:"
    echo "$holder"
    echo "Installation stopped without replacing the existing gateway."
    exit 1
  fi
done
LOCAL_ENROLL_SECRET="$(openssl rand -hex 32)"
EXISTING_ACCOUNT=0
OLD_PUBLIC_HOST=""
DB_PATH="$DATA_DIR/dark-noc.db"
if [[ -s "$DB_PATH" ]] && python3 - "$DB_PATH" <<'PY' >/dev/null 2>&1
import sqlite3
import sys
c=sqlite3.connect(sys.argv[1])
raise SystemExit(0 if c.execute("SELECT 1 FROM users LIMIT 1").fetchone() else 1)
PY
then EXISTING_ACCOUNT=1; fi
if [[ "$EXISTING_ACCOUNT" -eq 1 && -f /etc/dark-noc/hub.env ]]; then
  OLD_PUBLIC_HOST="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PUBLIC_HOST:-}"' _ /etc/dark-noc/hub.env)"
  [[ -n "$TELEGRAM_BOT_TOKEN" ]] || TELEGRAM_BOT_TOKEN="$(bash -c 'source "$1"; printf %s "${DARK_NOC_TELEGRAM_BOT_TOKEN:-}"' _ /etc/dark-noc/hub.env)"
  [[ -n "$TELEGRAM_CHAT_ID" ]] || TELEGRAM_CHAT_ID="$(bash -c 'source "$1"; printf %s "${DARK_NOC_TELEGRAM_CHAT_ID:-}"' _ /etc/dark-noc/hub.env)"
fi
if [[ -z "$ADMIN_USER" ]]; then ADMIN_USER="noc-$(openssl rand -hex 3)"; fi
if [[ -z "$ADMIN_PASSWORD" ]]; then ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '\n=/+' | cut -c1-24)"; fi
if [[ ${#ADMIN_PASSWORD} -lt 10 ]]; then
  echo "DARK_NOC_ADMIN_PASSWORD must contain at least 10 characters."
  exit 1
fi

id darknoc >/dev/null 2>&1 || useradd --system --home /var/lib/dark-noc --shell /usr/sbin/nologin darknoc
install -d -m 0750 -o darknoc -g darknoc /opt/dark-noc/hub "$DATA_DIR"
install -d -m 0750 -o root -g darknoc /etc/dark-noc
install -d -m 0750 -o root -g darknoc /opt/dark-noc/agent-payload
cp -a "$SCRIPT_DIR/hub/." /opt/dark-noc/hub/
install -m 0755 "$SCRIPT_DIR/agent/agent.py" /opt/dark-noc/agent-payload/agent.py
install -m 0644 "$SCRIPT_DIR/agent/realm_plugin.py" /opt/dark-noc/agent-payload/realm_plugin.py
install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc/agent-payload/requirements.txt
install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /opt/dark-noc/agent-payload/dark-noc-agent.service
python3 -m py_compile /opt/dark-noc/agent-payload/agent.py /opt/dark-noc/agent-payload/realm_plugin.py
python3 -m venv /opt/dark-noc/venv
/opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt

umask 0077
{
  printf 'DARK_NOC_ADMIN_USER=%q\n' "$ADMIN_USER"
  printf 'DARK_NOC_ADMIN_PASSWORD=%q\n' "$ADMIN_PASSWORD"
  printf 'DARK_NOC_DATA=%q\n' "$DATA_DIR"
  printf 'DARK_NOC_COOKIE_SECURE=%q\n' "1"
  printf 'DARK_NOC_PUBLIC_HOST=%q\n' "$PUBLIC_HOST"
  printf 'DARK_NOC_PUBLIC_PORT=%q\n' "$PUBLIC_PORT"
  printf 'DARK_NOC_HUB_PORT=%q\n' "$HUB_PORT"
  printf 'DARK_NOC_PANEL_CERT_MODE=%q\n' "${PANEL_CERT_MODE:-selfsigned}"
  printf 'DARK_NOC_TELEGRAM_BOT_TOKEN=%q\n' "$TELEGRAM_BOT_TOKEN"
  printf 'DARK_NOC_TELEGRAM_CHAT_ID=%q\n' "$TELEGRAM_CHAT_ID"
  printf 'DARK_NOC_LOCAL_ENROLL_SECRET=%q\n' "$LOCAL_ENROLL_SECRET"
  printf 'DARK_NOC_SSH_UPLOAD_LIMIT_MB=%q\n' "$SSH_UPLOAD_LIMIT_MB"
  printf 'DARK_NOC_SSH_RELAY_LIMIT_MB=%q\n' "$SSH_RELAY_LIMIT_MB"
  printf 'DARK_NOC_SSH_TRANSFER_CONCURRENCY=%q\n' "$SSH_TRANSFER_CONCURRENCY"
  printf 'DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS=%q\n' "$SSH_TRANSFER_TIMEOUT_SECONDS"
  printf 'DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS=%q\n' "$SSH_IDLE_TIMEOUT_SECONDS"
  printf 'DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS=%q\n' "$SSH_KEEPALIVE_INTERVAL_SECONDS"
  printf 'DARK_NOC_SSH_KEEPALIVE_COUNT_MAX=%q\n' "$SSH_KEEPALIVE_COUNT_MAX"
  printf 'DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS=%q\n' "$SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS"
  printf 'DARK_NOC_PROVISION_TIMEOUT_SECONDS=%q\n' "$PROVISION_TIMEOUT_SECONDS"
  printf 'DARK_NOC_PROVISION_CONCURRENCY=%q\n' "$PROVISION_CONCURRENCY"
  printf 'DARK_NOC_SSH_EDITOR_LIMIT_KB=%q\n' "$SSH_EDITOR_LIMIT_KB"
  printf 'DARK_NOC_MONITOR_RETENTION_DAYS=%q\n' "$MONITOR_RETENTION_DAYS"
  printf 'DARK_NOC_METRIC_RETENTION_DAYS=%q\n' "$METRIC_RETENTION_DAYS"
  printf 'DARK_NOC_ROLLUP_RETENTION_DAYS=%q\n' "$ROLLUP_RETENTION_DAYS"
  printf 'DARK_NOC_TUNNEL_RETENTION_DAYS=%q\n' "$TUNNEL_RETENTION_DAYS"
  printf 'DARK_NOC_HUB_LEASE_SECONDS=%q\n' "$HUB_LEASE_SECONDS"
} > /etc/dark-noc/hub.env

install -m 0755 "$SCRIPT_DIR/darknoc" /usr/local/bin/darknoc

install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-hub.service" /etc/systemd/system/dark-noc-hub.service
install -d -m 0755 /etc/systemd/system/dark-noc-hub.service.d
{
  printf '[Service]\n'
  printf 'ReadWritePaths=%s\n' "$DATA_DIR"
} > /etc/systemd/system/dark-noc-hub.service.d/data-path.conf
chmod 0644 /etc/systemd/system/dark-noc-hub.service.d/data-path.conf
if [[ -f /etc/systemd/system/dark-noc-hub.service.d/override.conf ]] && grep -q '^ExecStart=.*uvicorn' /etc/systemd/system/dark-noc-hub.service.d/override.conf; then
  install -m 0600 /etc/systemd/system/dark-noc-hub.service.d/override.conf /etc/dark-noc/legacy-systemd-override.conf
  rm -f /etc/systemd/system/dark-noc-hub.service.d/override.conf
  echo "Legacy uvicorn override disabled; backup: /etc/dark-noc/legacy-systemd-override.conf"
fi

systemctl daemon-reload
systemctl enable --now dark-noc-hub.service
systemctl restart dark-noc-hub.service
for attempt in {1..20}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$HUB_PORT/healthz" >/dev/null; then break; fi
  if [[ "$attempt" -eq 20 ]]; then
    echo "Hub failed its local health check."
    systemctl status dark-noc-hub.service --no-pager -l || true
    exit 1
  fi
  sleep 1
done

# The server-only `darknoc` CLI owns public port, Nginx and panel certificate settings.
darknoc --apply-gateway
if [[ "$AUTO_ISSUE_PANEL_SSL" -eq 1 ]]; then
  if darknoc issue-ssl; then
    PANEL_CERT_MODE="letsencrypt"
  else
    PANEL_CERT_MODE="selfsigned"
    echo "WARNING: Let's Encrypt was not available yet; the panel remains encrypted with a local certificate."
    echo "Fix DNS/port 80 and run: sudo darknoc -> Get / renew panel SSL"
  fi
fi
PANEL_CERT_MODE="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PANEL_CERT_MODE:-selfsigned}"' _ /etc/dark-noc/hub.env)"
if [[ "$PANEL_CERT_MODE" == "letsencrypt" ]]; then CERT_KIND="Let's Encrypt certificate"; else CERT_KIND="local self-signed certificate"; fi

# Enroll and run a local Agent so the Hub server appears as a monitored node.
EXISTING_LOCAL_AGENT_TOKEN="$(python3 - <<'PY'
import json
from pathlib import Path
path = Path('/etc/dark-noc-agent/config.json')
try:
    value = json.loads(path.read_text()).get('agent_token', '')
except (OSError, ValueError, TypeError):
    value = ''
if isinstance(value, str) and 8 <= len(value) <= 512 and not any(ord(char) < 33 for char in value):
    print(value, end='')
PY
)"
LOCAL_ENROLL_HEADERS=(-H "X-Dark-Noc-Bootstrap: $LOCAL_ENROLL_SECRET")
if [[ -n "$EXISTING_LOCAL_AGENT_TOKEN" ]]; then
  LOCAL_ENROLL_HEADERS+=(-H "X-Dark-Noc-Existing-Agent: $EXISTING_LOCAL_AGENT_TOKEN")
fi
LOCAL_AGENT_ENROLLMENT="$(curl -fsS -X POST "${LOCAL_ENROLL_HEADERS[@]}" "http://127.0.0.1:$HUB_PORT/api/agent/local-enroll")"
readarray -t LOCAL_AGENT_VALUES < <(python3 - "$LOCAL_AGENT_ENROLLMENT" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
print(payload['id'])
print(payload['agent_token'])
print('1' if payload.get('reused') else '0')
PY
)
LOCAL_AGENT_NODE_ID="${LOCAL_AGENT_VALUES[0]:-}"
LOCAL_AGENT_TOKEN="${LOCAL_AGENT_VALUES[1]:-}"
LOCAL_AGENT_REUSED="${LOCAL_AGENT_VALUES[2]:-0}"
if [[ ! "$LOCAL_AGENT_NODE_ID" =~ ^[0-9]+$ || -z "$LOCAL_AGENT_TOKEN" ]]; then
  echo "Local Agent enrollment returned an invalid response."
  exit 1
fi
LOCAL_AGENT_BASELINE="$(python3 - "$DB_PATH" "$LOCAL_AGENT_NODE_ID" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute('SELECT last_seen FROM nodes WHERE id=?', (int(sys.argv[2]),)).fetchone()
print(int(row[0] or 0) if row else 0)
PY
)"

install -d -m 0755 /opt/dark-noc-agent
install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul /etc/dark-ghostpro /etc/dark-packetpro /etc/dark-realm
install -d -m 0755 /var/lib/dark-noc-acme /var/lib/dark-noc-acme/.well-known /var/lib/dark-noc-acme/.well-known/acme-challenge /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /etc/nginx/conf.d
install -m 0755 /opt/dark-noc/agent-payload/agent.py /opt/dark-noc-agent/agent.py
install -m 0644 /opt/dark-noc/agent-payload/realm_plugin.py /opt/dark-noc-agent/realm_plugin.py
install -m 0644 /opt/dark-noc/agent-payload/requirements.txt /opt/dark-noc-agent/requirements.txt
python3 -m venv /opt/dark-noc-agent/venv
/opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt
/opt/dark-noc-agent/venv/bin/python -m py_compile /opt/dark-noc-agent/agent.py /opt/dark-noc-agent/realm_plugin.py
python3 - "$HUB_PORT" "$LOCAL_AGENT_TOKEN" <<'PY'
import json, sys
from pathlib import Path
path = Path('/etc/dark-noc-agent/config.json')
old = {}
try:
    old = json.loads(path.read_text())
except FileNotFoundError:
    pass
except (OSError, ValueError, TypeError) as exc:
    raise SystemExit(f'Existing local Agent configuration is invalid: {exc}')
if not isinstance(old, dict):
    raise SystemExit('Existing local Agent configuration must be a JSON object')
old.update({
    'hub_url': f'http://127.0.0.1:{sys.argv[1]}', 'agent_token': sys.argv[2], 'verify_tls': True,
    'interval_seconds': old.get('interval_seconds', 15), 'services': old.get('services', []),
    'managed_services': old.get('managed_services', []), 'tunnels': old.get('tunnels', []),
    'speedtest': old.get('speedtest', {'host': '', 'port': 5201}),
    'auto_discovery': True,
    'autoheal': old.get('autoheal', {'enabled': False, 'cooldown_seconds': 300, 'max_restarts_per_hour': 3}),
})
tmp = path.with_name(path.name + '.installing')
tmp.write_text(json.dumps(old, indent=2) + '\n')
tmp.chmod(0o600)
tmp.replace(path)
PY
install -m 0644 /opt/dark-noc/agent-payload/dark-noc-agent.service /etc/systemd/system/dark-noc-agent.service
systemctl daemon-reload
systemctl enable dark-noc-agent.service
systemctl restart dark-noc-agent.service
if ! systemctl is-active --quiet dark-noc-agent.service; then
  echo "Local Agent failed to start."
  systemctl status dark-noc-agent.service --no-pager -l || true
  journalctl -u dark-noc-agent.service -n 100 --no-pager || true
  exit 1
fi
LOCAL_AGENT_READY=0
for attempt in {1..30}; do
  if python3 - "$DB_PATH" "$LOCAL_AGENT_NODE_ID" "$LOCAL_AGENT_BASELINE" <<'PY'
import sqlite3, sys, time
with sqlite3.connect(sys.argv[1]) as connection:
    row = connection.execute('SELECT status,agent_version,last_seen FROM nodes WHERE id=?', (int(sys.argv[2]),)).fetchone()
baseline = int(sys.argv[3])
ready = bool(
    row and row[0] == 'online' and row[1] == '2.9.3'
    and int(row[2] or 0) > baseline and int(row[2] or 0) >= int(time.time()) - 120
)
raise SystemExit(0 if ready else 1)
PY
  then
    LOCAL_AGENT_READY=1
    break
  fi
  sleep 2
done
if [[ "$LOCAL_AGENT_READY" -ne 1 ]]; then
  echo "Local Agent started but DARK NOC did not receive a fresh v2.9.3 heartbeat."
  echo "Enrollment token reused: $LOCAL_AGENT_REUSED"
  systemctl status dark-noc-agent.service --no-pager -l || true
  journalctl -u dark-noc-agent.service -n 120 --no-pager || true
  exit 1
fi
curl -kfsS -H "Host: $PUBLIC_HOST" --max-time 8 "https://127.0.0.1:$PUBLIC_PORT/healthz" >/dev/null
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw delete allow "$OLD_HUB_PORT/tcp" >/dev/null 2>&1 || true
  ufw delete allow "$HUB_PORT/tcp" >/dev/null 2>&1 || true
  ufw allow 80/tcp
  ufw allow "$PUBLIC_PORT/tcp"
fi

echo ""
echo "DARK NOC Hub is active."
echo "Open: $PUBLIC_URL"
echo "Public panel port: $PUBLIC_PORT/tcp"
echo "Internal Hub: 127.0.0.1:$HUB_PORT (not exposed publicly)"
echo "Local monitoring Agent: enabled (Hub node is registered automatically)"
echo "TLS: $CERT_KIND"
echo "Health check: PASSED"
if [[ "$EXISTING_ACCOUNT" -eq 1 ]]; then
  echo "Existing operator account and password were preserved."
else
  echo "User: $ADMIN_USER"
  echo "Password: $ADMIN_PASSWORD"
  echo "Save these credentials now. They are shown only here. Change them later with: sudo darknoc"
fi
echo "Check: systemctl status dark-noc-hub --no-pager"
echo "Server control: sudo darknoc"
