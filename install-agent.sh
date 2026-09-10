#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-999} -ne 0 ]]; then
  echo "Run as root: sudo bash install-agent.sh"
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HUB_URL="${DARK_NOC_HUB_URL:-}"
AGENT_TOKEN="${DARK_NOC_AGENT_TOKEN:-}"

echo ""
echo "  DARK NOC // NODE BOOTSTRAP"
echo "  Lightweight Node v1.6.3"
echo ""
OLD_HUB_URL=""; OLD_AGENT_TOKEN=""
if [[ -f /etc/dark-noc-agent/config.json ]]; then
  read -r OLD_HUB_URL OLD_AGENT_TOKEN < <(python3 - <<'PY'
import json
try:
    c=json.load(open('/etc/dark-noc-agent/config.json'))
    print(c.get('hub_url',''), c.get('agent_token',''))
except Exception:
    print('', '')
PY
  )
fi
if [[ -z "$HUB_URL" ]]; then read -r -p "Hub domain or URL${OLD_HUB_URL:+ [$OLD_HUB_URL]}: " input_hub; HUB_URL="${input_hub:-$OLD_HUB_URL}"; fi
if [[ -n "$HUB_URL" && ! "$HUB_URL" =~ ^https?:// ]]; then HUB_URL="https://$HUB_URL"; fi
if [[ -z "$AGENT_TOKEN" ]]; then
  if [[ -n "$OLD_AGENT_TOKEN" ]]; then read -r -s -p "Enrollment token [press Enter to keep current]: " input_token; AGENT_TOKEN="${input_token:-$OLD_AGENT_TOKEN}";
  else read -r -s -p "Enrollment token: " AGENT_TOKEN; fi
fi
echo ""
if [[ -z "$HUB_URL" || -z "$AGENT_TOKEN" ]]; then
  echo "Hub URL and enrollment token are required."
  echo "To get a token: sign in to DARK NOC -> ADD NODE -> create this server -> copy the one-time Agent token."
  echo "The token is unique to one node; it is not the panel username or password."
  exit 1
fi
if [[ ! "$HUB_URL" =~ ^https?://[^[:space:]]+$ ]]; then
  echo "Hub URL must start with https:// and contain no spaces."
  exit 1
fi
if [[ "$HUB_URL" == http://* && ! "$HUB_URL" =~ ^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$ ]]; then
  echo "Remote Hub connections must use HTTPS. Plain HTTP is allowed only for a local Hub Agent."
  exit 1
fi
if [[ ${#AGENT_TOKEN} -lt 20 ]]; then
  echo "Enrollment token is not valid."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip ca-certificates curl tar openssl iproute2 iperf3
install -d -m 0755 /opt/dark-noc-agent
install -d -m 0700 /etc/dark-noc-agent /var/lib/dark-noc-agent /etc/dark-backhaul
TLS_VERIFY="true"
if [[ "$HUB_URL" == https://* ]] && ! curl -fsS --max-time 12 "$HUB_URL/healthz" >/dev/null 2>&1; then
  read -r hub_host hub_port < <(python3 - "$HUB_URL" <<'PY'
import sys
from urllib.parse import urlparse
u=urlparse(sys.argv[1]); print(u.hostname or "", u.port or 443)
PY
  )
  if [[ -z "$hub_host" ]]; then echo "Could not parse Hub host."; exit 1; fi
  if ! python3 - "$hub_host" <<'PY'
import ipaddress, sys
try:
    ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
  then
    echo "The HTTPS health check failed for a domain Hub."
    echo "Check DNS, port 443, Nginx and the Let's Encrypt certificate on the Hub; refusing to pin an untrusted domain certificate."
    exit 1
  fi
  echo "The Hub certificate is not trusted by this server."
  echo | openssl s_client -connect "${hub_host}:${hub_port}" -servername "$hub_host" -showcerts 2>/dev/null | openssl x509 -outform PEM > /etc/dark-noc-agent/hub-ca.crt
  if [[ ! -s /etc/dark-noc-agent/hub-ca.crt ]]; then echo "Could not download the Hub certificate."; exit 1; fi
  openssl x509 -in /etc/dark-noc-agent/hub-ca.crt -noout -subject -issuer -fingerprint -sha256
  trust_answer="${DARK_NOC_TRUST_SELF_SIGNED:-}"
  [[ -n "$trust_answer" ]] || read -r -p "Pin and trust exactly this Hub certificate? [y/N]: " trust_answer
  if [[ ! "$trust_answer" =~ ^[Yy]([Ee][Ss])?$ && "$trust_answer" != "1" ]]; then
    echo "Certificate was not trusted; Agent installation stopped."; exit 1
  fi
  if ! curl -fsS --cacert /etc/dark-noc-agent/hub-ca.crt --max-time 12 "$HUB_URL/healthz" >/dev/null; then
    echo "Pinned certificate could not validate the Hub endpoint."; exit 1
  fi
  TLS_VERIFY="/etc/dark-noc-agent/hub-ca.crt"
fi
install -m 0755 "$SCRIPT_DIR/agent/agent.py" /opt/dark-noc-agent/agent.py
install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt
python3 -m venv /opt/dark-noc-agent/venv
/opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt

if [[ ! -f /etc/dark-noc-agent/config.json ]]; then
python3 - "$HUB_URL" "$AGENT_TOKEN" "$TLS_VERIFY" <<'PY'
import json, sys
from pathlib import Path
verify_tls = True if sys.argv[3] == "true" else sys.argv[3]
config = {
    "hub_url": sys.argv[1].rstrip("/"),
    "agent_token": sys.argv[2],
    "verify_tls": verify_tls,
    "interval_seconds": 15,
    "services": [],
    "managed_services": [],
    "speedtest": {"host": "", "port": 5201},
    "tunnels": [],
    "auto_discovery": True,
    "autoheal": {"enabled": False, "cooldown_seconds": 300, "max_restarts_per_hour": 3}
}
path = Path("/etc/dark-noc-agent/config.json")
path.write_text(json.dumps(config, indent=2))
path.chmod(0o600)
PY
else
  python3 - "$HUB_URL" "$AGENT_TOKEN" "$TLS_VERIFY" <<'PY'
import json, sys
from pathlib import Path
path = Path("/etc/dark-noc-agent/config.json")
config = json.loads(path.read_text())
config["hub_url"] = sys.argv[1].rstrip("/")
config["agent_token"] = sys.argv[2]
config["verify_tls"] = True if sys.argv[3] == "true" else sys.argv[3]
config.setdefault("auto_discovery", True)
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(config, indent=2))
tmp.chmod(0o600)
tmp.replace(path)
PY
  echo "Existing monitoring rules preserved; Hub URL, token and TLS trust were updated."
fi

install -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /etc/systemd/system/dark-noc-agent.service
systemctl daemon-reload
systemctl enable --now dark-noc-agent.service
systemctl restart dark-noc-agent.service
sleep 2
systemctl status dark-noc-agent.service --no-pager -l || true
echo ""
echo "Node prerequisite Agent is ready. Manage plugins, tunnels and operations from the Hub panel."
echo "Agent status: systemctl status dark-noc-agent --no-pager"
