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
HUB_PORT="${DARK_NOC_HUB_PORT:-}"
TELEGRAM_BOT_TOKEN="${DARK_NOC_TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${DARK_NOC_TELEGRAM_CHAT_ID:-}"
LOCAL_ENROLL_SECRET=""

echo ""
echo "  DARK NOC // HUB INSTALLER"
echo "  Nightfall Command v1.6.2"
echo ""

SERVER_IP="$(hostname -I | awk '{print $1}')"
DEFAULT_PUBLIC_HOST="$SERVER_IP"
if [[ -f /etc/dark-noc/hub.env ]]; then
  saved_public_host="$(bash -c 'source "$1"; printf %s "${DARK_NOC_PUBLIC_HOST:-}"' _ /etc/dark-noc/hub.env)"
  [[ -z "$saved_public_host" ]] || DEFAULT_PUBLIC_HOST="$saved_public_host"
fi
if [[ -z "$PUBLIC_HOST" ]]; then
  read -r -p "Panel domain or public IP [$DEFAULT_PUBLIC_HOST]: " input_host
  PUBLIC_HOST="${input_host:-$DEFAULT_PUBLIC_HOST}"
fi
PUBLIC_HOST="${PUBLIC_HOST#http://}"; PUBLIC_HOST="${PUBLIC_HOST#https://}"; PUBLIC_HOST="${PUBLIC_HOST%%/*}"
if [[ ! "$PUBLIC_HOST" =~ ^([A-Za-z0-9.-]+|[0-9A-Fa-f:]+)$ ]]; then
  echo "Invalid domain or IP: $PUBLIC_HOST"; exit 1
fi
OLD_HUB_PORT="9090"
if [[ -f /etc/dark-noc/hub.env ]]; then
  saved_hub_port="$(bash -c 'source "$1"; printf %s "${DARK_NOC_HUB_PORT:-}"' _ /etc/dark-noc/hub.env)"
  [[ -z "$saved_hub_port" ]] || OLD_HUB_PORT="$saved_hub_port"
fi
if [[ -z "$HUB_PORT" ]]; then
  read -r -p "Internal Hub port [current: $OLD_HUB_PORT, Enter = random]: " input_port
  HUB_PORT="$input_port"
fi
if [[ -z "$HUB_PORT" ]]; then
  HUB_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    print(sock.getsockname()[1])
PY
)"
  echo "Generated internal Hub port: $HUB_PORT"
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
apt-get install -y python3 python3-venv python3-pip ca-certificates curl nginx openssl
for public_port in 80 443; do
  holder="$(ss -H -ltnp "sport = :$public_port" 2>/dev/null || true)"
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
if [[ -s /var/lib/dark-noc/dark-noc.db ]] && python3 - <<'PY' >/dev/null 2>&1
import sqlite3
c=sqlite3.connect('/var/lib/dark-noc/dark-noc.db')
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
install -d -m 0750 -o darknoc -g darknoc /opt/dark-noc/hub /var/lib/dark-noc
install -d -m 0750 -o root -g darknoc /etc/dark-noc
cp -a "$SCRIPT_DIR/hub/." /opt/dark-noc/hub/
python3 -m venv /opt/dark-noc/venv
/opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt

umask 0077
{
  printf 'DARK_NOC_ADMIN_USER=%q\n' "$ADMIN_USER"
  printf 'DARK_NOC_ADMIN_PASSWORD=%q\n' "$ADMIN_PASSWORD"
  printf 'DARK_NOC_DATA=%q\n' "/var/lib/dark-noc"
  printf 'DARK_NOC_COOKIE_SECURE=%q\n' "1"
  printf 'DARK_NOC_PUBLIC_HOST=%q\n' "$PUBLIC_HOST"
  printf 'DARK_NOC_HUB_PORT=%q\n' "$HUB_PORT"
  printf 'DARK_NOC_TELEGRAM_BOT_TOKEN=%q\n' "$TELEGRAM_BOT_TOKEN"
  printf 'DARK_NOC_TELEGRAM_CHAT_ID=%q\n' "$TELEGRAM_CHAT_ID"
  printf 'DARK_NOC_LOCAL_ENROLL_SECRET=%q\n' "$LOCAL_ENROLL_SECRET"
} > /etc/dark-noc/hub.env

install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-hub.service" /etc/systemd/system/dark-noc-hub.service
if [[ -f /etc/systemd/system/dark-noc-hub.service.d/override.conf ]] && grep -q '^ExecStart=.*uvicorn' /etc/systemd/system/dark-noc-hub.service.d/override.conf; then
  install -m 0600 /etc/systemd/system/dark-noc-hub.service.d/override.conf /etc/dark-noc/legacy-systemd-override.conf
  rm -f /etc/systemd/system/dark-noc-hub.service.d/override.conf
  echo "Legacy uvicorn override disabled; backup: /etc/dark-noc/legacy-systemd-override.conf"
fi

CERT_DIR="/etc/dark-noc/tls"
install -d -m 0700 "$CERT_DIR"
if [[ -f /etc/nginx/sites-available/dark-noc ]]; then
  if ! grep -qE 'proxy_pass http://127\.0\.0\.1:[0-9]+' /etc/nginx/sites-available/dark-noc; then
    echo "Existing /etc/nginx/sites-available/dark-noc is not managed by DARK NOC; refusing to overwrite it."
    exit 1
  fi
  install -m 0600 /etc/nginx/sites-available/dark-noc "/etc/dark-noc/nginx-site.backup.$(date +%s)"
fi
if [[ "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ || "$PUBLIC_HOST" == *:* ]]; then
  if [[ "$OLD_PUBLIC_HOST" != "$PUBLIC_HOST" || ! -s "$CERT_DIR/panel.key" || ! -s "$CERT_DIR/panel.crt" ]]; then
    rm -f "$CERT_DIR/panel.key" "$CERT_DIR/panel.crt"
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
      -keyout "$CERT_DIR/panel.key" -out "$CERT_DIR/panel.crt" \
      -subj "/CN=$PUBLIC_HOST/O=DARK NOC" -addext "subjectAltName=IP:$PUBLIC_HOST" >/dev/null 2>&1
  fi
  CERT_KIND="self-signed IP certificate"
else
  apt-get install -y certbot python3-certbot-nginx
  systemctl enable --now nginx
  cat > /etc/nginx/sites-available/dark-noc <<EOF
server { listen 80; listen [::]:80; server_name $PUBLIC_HOST; location / { proxy_pass http://127.0.0.1:$HUB_PORT; } }
EOF
  ln -sfn /etc/nginx/sites-available/dark-noc /etc/nginx/sites-enabled/dark-noc
  nginx -t && systemctl reload nginx
  certbot --nginx -d "$PUBLIC_HOST" --non-interactive --agree-tos --register-unsafely-without-email --redirect
  ln -sfn "/etc/letsencrypt/live/$PUBLIC_HOST/fullchain.pem" "$CERT_DIR/panel.crt"
  ln -sfn "/etc/letsencrypt/live/$PUBLIC_HOST/privkey.pem" "$CERT_DIR/panel.key"
  CERT_KIND="Let's Encrypt certificate"
fi

cat > /etc/nginx/sites-available/dark-noc <<EOF
server {
  listen 80; listen [::]:80; server_name $PUBLIC_HOST;
  return 301 https://\$host\$request_uri;
}
server {
  listen 443 ssl http2; listen [::]:443 ssl http2; server_name $PUBLIC_HOST;
  ssl_certificate $CERT_DIR/panel.crt;
  ssl_certificate_key $CERT_DIR/panel.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  add_header Strict-Transport-Security "max-age=31536000" always;
  client_max_body_size 1m;
  location / {
    proxy_pass http://127.0.0.1:$HUB_PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600;
  }
}
EOF
ln -sfn /etc/nginx/sites-available/dark-noc /etc/nginx/sites-enabled/dark-noc
nginx -t
systemctl enable --now nginx
systemctl reload nginx
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

# Enroll and run a local Agent so the Hub server appears as a monitored node.
LOCAL_AGENT_TOKEN="$(curl -fsS -X POST -H "X-Dark-Noc-Bootstrap: $LOCAL_ENROLL_SECRET" "http://127.0.0.1:$HUB_PORT/api/agent/local-enroll" | python3 -c 'import json,sys; print(json.load(sys.stdin)["agent_token"])')"
install -d -m 0755 /opt/dark-noc-agent
install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul
install -m 0755 "$SCRIPT_DIR/agent/agent.py" /opt/dark-noc-agent/agent.py
install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt
python3 -m venv /opt/dark-noc-agent/venv
/opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt
python3 - "$HUB_PORT" "$LOCAL_AGENT_TOKEN" <<'PY'
import json, sys
from pathlib import Path
path = Path('/etc/dark-noc-agent/config.json')
old = {}
try:
    old = json.loads(path.read_text())
except Exception:
    pass
old.update({
    'hub_url': f'http://127.0.0.1:{sys.argv[1]}', 'agent_token': sys.argv[2], 'verify_tls': True,
    'interval_seconds': old.get('interval_seconds', 15), 'services': old.get('services', []),
    'managed_services': old.get('managed_services', []), 'tunnels': old.get('tunnels', []),
    'speedtest': old.get('speedtest', {'host': '', 'port': 5201}),
    'auto_discovery': True,
    'autoheal': old.get('autoheal', {'enabled': False, 'cooldown_seconds': 300, 'max_restarts_per_hour': 3}),
})
path.write_text(json.dumps(old, indent=2)); path.chmod(0o600)
PY
install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /etc/systemd/system/dark-noc-agent.service
systemctl daemon-reload
systemctl enable --now dark-noc-agent.service
systemctl restart dark-noc-agent.service
if [[ "$CERT_KIND" == "self-signed IP certificate" ]]; then
  curl -fsS --resolve "$PUBLIC_HOST:443:127.0.0.1" --cacert "$CERT_DIR/panel.crt" --max-time 8 "https://$PUBLIC_HOST/healthz" >/dev/null
else
  curl -fsS --resolve "$PUBLIC_HOST:443:127.0.0.1" --max-time 8 "https://$PUBLIC_HOST/healthz" >/dev/null
fi
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw delete allow "$OLD_HUB_PORT/tcp" >/dev/null 2>&1 || true
  ufw delete allow "$HUB_PORT/tcp" >/dev/null 2>&1 || true
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

echo ""
echo "DARK NOC Hub is active."
echo "Open: https://$PUBLIC_HOST"
echo "Internal Hub: 127.0.0.1:$HUB_PORT (not exposed publicly)"
echo "Local monitoring Agent: enabled (Hub node is registered automatically)"
echo "TLS: $CERT_KIND"
echo "Health check: PASSED"
if [[ "$EXISTING_ACCOUNT" -eq 1 ]]; then
  echo "Existing operator account and password were preserved."
else
  echo "User: $ADMIN_USER"
  echo "Password: $ADMIN_PASSWORD"
  echo "Save these credentials now. They are shown only here. Change them from the account menu after login."
fi
echo "Check: systemctl status dark-noc-hub --no-pager"
