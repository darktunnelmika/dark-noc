from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "2.9.48"
NEW = "2.9.49"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    if old not in content:
        raise SystemExit(f"marker not found in {path}: {old[:120]!r}")
    write(path, content.replace(old, new, 1))


# 1) Preserve the last real remote peer independently from current live sockets.
path = "hub/agent_control_router.py"
content = read(path)
if "import ipaddress\n" not in content:
    content = content.replace("import hmac\nimport json\n", "import hmac\nimport ipaddress\nimport json\n", 1)

helper_marker = "\n\ndef register_agent_control_router(app, **deps):\n"
helper = '''

def _usable_peer_ips(values) -> list[str]:
    """Keep only routable/non-local peer identities reported by tunnel telemetry."""
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text.casefold() == "localhost":
            continue
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            normalized = text
        else:
            if address.is_loopback or address.is_unspecified:
                continue
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            normalized = str(address)
        if normalized not in result:
            result.append(normalized)
        if len(result) >= 32:
            break
    return result
'''
if "_usable_peer_ips(values)" not in content:
    if helper_marker not in content:
        raise SystemExit("agent helper insertion marker missing")
    content = content.replace(helper_marker, helper + helper_marker, 1)

old_block = '''                old = conn.execute("SELECT * FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
                conn.execute("INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,last_check,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET method=excluded.method,target=excluded.target,service=excluded.service,listen_port=excluded.listen_port,status=excluded.status,latency_ms=excluded.latency_ms,packet_loss=excluded.packet_loss,sessions=excluded.sessions,rx_bps=excluded.rx_bps,tx_bps=excluded.tx_bps,last_check=excluded.last_check,details=excluded.details", (node["id"], name, tunnel.get("method"), tunnel.get("target"), tunnel.get("service"), tunnel.get("listen_port"), tunnel.get("status"), tunnel.get("latency_ms"), tunnel.get("packet_loss"), tunnel.get("sessions"), tunnel.get("rx_bps"), tunnel.get("tx_bps"), now, json.dumps(tunnel)))
'''
new_block = '''                old = conn.execute("SELECT * FROM tunnels WHERE node_id=? AND name=?", (node["id"], name)).fetchone()
                stored_tunnel = dict(tunnel)
                live_peer_ips = _usable_peer_ips(stored_tunnel.get("peer_ips"))
                previous_details = {}
                if old:
                    try:
                        previous_details = json.loads(old["details"] or "{}")
                    except (TypeError, ValueError):
                        previous_details = {}
                remembered_peer_ips = (
                    _usable_peer_ips(previous_details.get("last_known_peer_ips"))
                    or _usable_peer_ips(previous_details.get("peer_ips"))
                )
                stored_tunnel["peer_ips"] = live_peer_ips
                stored_tunnel["last_known_peer_ips"] = live_peer_ips or remembered_peer_ips
                conn.execute("INSERT INTO tunnels(node_id,name,method,target,service,listen_port,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,last_check,details) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(node_id,name) DO UPDATE SET method=excluded.method,target=excluded.target,service=excluded.service,listen_port=excluded.listen_port,status=excluded.status,latency_ms=excluded.latency_ms,packet_loss=excluded.packet_loss,sessions=excluded.sessions,rx_bps=excluded.rx_bps,tx_bps=excluded.tx_bps,last_check=excluded.last_check,details=excluded.details", (node["id"], name, tunnel.get("method"), tunnel.get("target"), tunnel.get("service"), tunnel.get("listen_port"), tunnel.get("status"), tunnel.get("latency_ms"), tunnel.get("packet_loss"), tunnel.get("sessions"), tunnel.get("rx_bps"), tunnel.get("tx_bps"), now, json.dumps(stored_tunnel)))
'''
if old_block not in content:
    raise SystemExit("agent tunnel persistence block missing")
content = content.replace(old_block, new_block, 1)
write(path, content)


# 2) API prefers live peer, then last-known peer, and never exposes loopback as remote.
path = "hub/tunnels_router.py"
content = read(path)
marker = '''        result: list[dict[str, Any]] = []
        for row in rows:
'''
replacement = '''        local_endpoint_values = {"", "127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}

        def remote_values(values: Any) -> list[str]:
            if not isinstance(values, list):
                return []
            result_values: list[str] = []
            for value in values:
                normalized = normalize_ip(value)
                if not normalized or normalized.casefold() in local_endpoint_values:
                    continue
                if normalized not in result_values:
                    result_values.append(normalized)
                if len(result_values) >= 32:
                    break
            return result_values

        result: list[dict[str, Any]] = []
        for row in rows:
'''
if marker not in content:
    raise SystemExit("tunnel API helper marker missing")
content = content.replace(marker, replacement, 1)

old_peer_line = '''            item["peer_ips"] = [normalize_ip(value) for value in details.get("peer_ips", []) if value] if isinstance(details.get("peer_ips"), list) else []
'''
new_peer_line = '''            item["peer_ips"] = remote_values(details.get("peer_ips"))
            item["last_known_peer_ips"] = remote_values(details.get("last_known_peer_ips"))
'''
if old_peer_line not in content:
    raise SystemExit("peer_ips parse marker missing")
content = content.replace(old_peer_line, new_peer_line, 1)

old_remote = '''            remote_addresses = {normalize_ip(value) for value in item["peer_ips"] if normalize_ip(value)}
            if item["target_host"] not in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}:
                remote_addresses.add(item["target_host"])
'''
new_remote = '''            remote_addresses = set(item["peer_ips"]) | set(item["last_known_peer_ips"])
            if item["target_host"] and item["target_host"].casefold() not in local_endpoint_values:
                remote_addresses.add(item["target_host"])
'''
if old_remote not in content:
    raise SystemExit("remote address marker missing")
content = content.replace(old_remote, new_remote, 1)

old_peer_host = '''            item["peer_node_id"] = peer_id
            item["peer_name"] = peer["name"] if peer else None
            item["peer_host"] = (
                (peer.get("observed_ip") or peer["host"])
                if peer else (
                    item["peer_ips"][0]
                    if item["peer_ips"] else (
                        item["target_host"]
                        if item["target_host"] not in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}
                        else None
                    )
                )
            )
            item["peer_role"] = peer["role"] if peer else None
'''
new_peer_host = '''            item["peer_node_id"] = peer_id
            item["peer_name"] = peer["name"] if peer else None
            if peer:
                item["peer_host"] = peer.get("observed_ip") or peer["host"]
                item["peer_host_source"] = "node"
            elif item["peer_ips"]:
                item["peer_host"] = item["peer_ips"][0]
                item["peer_host_source"] = "live"
            elif item["last_known_peer_ips"]:
                item["peer_host"] = item["last_known_peer_ips"][0]
                item["peer_host_source"] = "last_known"
            elif item["target_host"] and item["target_host"].casefold() not in local_endpoint_values:
                item["peer_host"] = item["target_host"]
                item["peer_host_source"] = "target"
            else:
                item["peer_host"] = None
                item["peer_host_source"] = None
            item["peer_host_last_known"] = item["peer_host_source"] == "last_known"
            item["peer_role"] = peer["role"] if peer else None
'''
if old_peer_host not in content:
    raise SystemExit("peer_host resolution block missing")
content = content.replace(old_peer_host, new_peer_host, 1)
write(path, content)


# 3) Frontend never falls back to a loopback target for a remote endpoint.
path = "hub/static/topology.js"
content = read(path)
old_endpoint = '''  function endpoint(node,name,host){return {node,name:node?.name||name||'UNRESOLVED ENDPOINT',host:displayHost(node?.observed_ip||node?.host||host||'IP UNAVAILABLE')};}
'''
new_endpoint = '''  const localRemoteHosts=new Set(['','127.0.0.1','localhost','::1','0.0.0.0','::','::ffff:127.0.0.1']);
  function remoteHost(...candidates){
    for(const value of candidates.flat(Infinity)){
      const text=String(value||'').trim();
      if(!text||localRemoteHosts.has(text.toLowerCase()))continue;
      return text;
    }
    return null;
  }
  function endpoint(node,name,host,lastKnown=false){return {node,name:node?.name||name||'UNRESOLVED ENDPOINT',host:displayHost(node?.observed_ip||node?.host||host||'IP UNAVAILABLE'),lastKnown:Boolean(lastKnown&&!node)};}
'''
if old_endpoint not in content:
    raise SystemExit("topology endpoint marker missing")
content = content.replace(old_endpoint, new_endpoint, 1)

old_lr = '''      const left=iran?endpoint(iran.node,iran.tunnel.node_name,iran.tunnel.node_observed_ip||iran.tunnel.node_host):endpoint(peerNode,kharej?.tunnel.peer_name||'IRAN / HUB',kharej?.tunnel.peer_host);
      const right=kharej?endpoint(kharej.node,kharej.tunnel.node_name,kharej.tunnel.node_observed_ip||kharej.tunnel.node_host):endpoint(peerNode,iran?.tunnel.peer_name||'REMOTE ENDPOINT',iran?.tunnel.peer_host||iran?.tunnel.target_host);
'''
new_lr = '''      const left=iran?endpoint(iran.node,iran.tunnel.node_name,iran.tunnel.node_observed_ip||iran.tunnel.node_host):endpoint(peerNode,kharej?.tunnel.peer_name||'IRAN / HUB',kharej?.tunnel.peer_host);
      const remoteIdentity=remoteHost(iran?.tunnel.peer_host,iran?.tunnel.last_known_peer_ips,iran?.tunnel.target_host);
      const right=kharej?endpoint(kharej.node,kharej.tunnel.node_name,kharej.tunnel.node_observed_ip||kharej.tunnel.node_host):endpoint(peerNode,iran?.tunnel.peer_name||'REMOTE ENDPOINT',remoteIdentity,Boolean(iran?.tunnel.peer_host_last_known));
'''
if old_lr not in content:
    raise SystemExit("topology left/right marker missing")
content = content.replace(old_lr, new_lr, 1)
write(path, content)


# 4) Make remembered identity explicit in the Matrix card and hover detail.
path = "hub/static/app.js"
content = read(path)
old_card = '''const nodeCard=(entry,y,side)=>{const node=entry.node,agent=node?node.status==='online':null,ssh=node?Boolean(node.ssh_configured):null,badge=side==='root'?(node?.role==='hub'?'HB':'IR'):'EX';return `<button class="cyber-node ${side} ${node?'':'unresolved'}" style="top:${y.toFixed(1)}px" data-node-id="${node?.id||''}" data-endpoint-name="${esc(entry.name)}"><i>${badge}</i><span><strong>${esc(entry.name)}</strong><small>${esc(entry.host)}</small><em><b class="${agent===true?'ready':agent===false?'missing':'neutral'}">AG ${agent===true?'ON':agent===false?'OFF':'N/A'}</b><b class="${ssh===true?'ready':ssh===false?'missing':'neutral'}">SSH ${ssh===true?'READY':ssh===false?'NO':'N/A'}</b></em></span></button>`;};
'''
new_card = '''const nodeCard=(entry,y,side)=>{const node=entry.node,agent=node?node.status==='online':null,ssh=node?Boolean(node.ssh_configured):null,badge=side==='root'?(node?.role==='hub'?'HB':'IR'):'EX';return `<button class="cyber-node ${side} ${node?'':'unresolved'}" style="top:${y.toFixed(1)}px" data-node-id="${node?.id||''}" data-endpoint-name="${esc(entry.name)}"><i>${badge}</i><span><strong>${esc(entry.name)}</strong><small>${esc(entry.host)}${entry.lastKnown?' · LAST KNOWN':''}</small><em><b class="${agent===true?'ready':agent===false?'missing':'neutral'}">AG ${agent===true?'ON':agent===false?'OFF':'N/A'}</b><b class="${ssh===true?'ready':ssh===false?'missing':'neutral'}">SSH ${ssh===true?'READY':ssh===false?'NO':'N/A'}</b></em></span></button>`;};
'''
if old_card not in content:
    raise SystemExit("matrix node card marker missing")
content = content.replace(old_card, new_card, 1)

old_tip = '''<span>${esc(link.left.host)} → ${esc(link.right.host)}</span>'''
new_tip = '''<span>${esc(link.left.host)} → ${esc(link.right.host)}${link.right.lastKnown?' · LAST KNOWN':''}</span>'''
if old_tip not in content:
    raise SystemExit("matrix tooltip marker missing")
content = content.replace(old_tip, new_tip, 1)
write(path, content)


# 5) Permanent regression: a healthy peer is remembered after a fresh DOWN report.
path = "tests/topology_inventory_regression.py"
content = read(path)
visible_marker = '''        assert visible[0]["peer_host"] == "198.51.100.20"

        # A fresh authoritative empty snapshot is the only report allowed to prune.
'''
visible_replacement = '''        assert visible[0]["peer_host"] == "198.51.100.20"
        assert visible[0]["peer_host_source"] == "live"
        assert visible[0]["peer_host_last_known"] is False

        # When the TCP peer disappears, keep the last real remote identity.
        # target_host=127.0.0.1 is a local listener detail, never a remote server.
        down_report = json.loads(json.dumps(full_report))
        down_report["metrics"]["inventory_snapshot_at"] = app.utc_ts()
        down_report["tunnels"][0].update({
            "status": "down", "packet_loss": 100, "sessions": 0, "peer_ips": [],
            "checks": {"process": True, "path": False},
        })
        assert client.post("/api/agent/heartbeat", headers=auth, json=down_report).status_code == 200
        down_visible = client.get("/api/tunnels").json()
        assert len(down_visible) == 1
        assert down_visible[0]["target_host"] == "127.0.0.1"
        assert down_visible[0]["peer_ips"] == []
        assert down_visible[0]["last_known_peer_ips"] == ["198.51.100.20"]
        assert down_visible[0]["peer_host"] == "198.51.100.20"
        assert down_visible[0]["peer_host_source"] == "last_known"
        assert down_visible[0]["peer_host_last_known"] is True

        # A fresh authoritative empty snapshot is the only report allowed to prune.
'''
if visible_marker not in content:
    raise SystemExit("topology regression insertion marker missing")
content = content.replace(visible_marker, visible_replacement, 1)

footer_marker = '''assert "No matching tunnel path" in app_js
'''
footer_replacement = '''assert "No matching tunnel path" in app_js
assert "LAST KNOWN" in app_js
assert "remoteHost(" in topology_js and "last_known_peer_ips" in topology_js
'''
if footer_marker not in content:
    raise SystemExit("topology frontend assertion marker missing")
content = content.replace(footer_marker, footer_replacement, 1)
write(path, content)


# 6) Version/cache synchronization.
for path in [
    "hub/app.py",
    "agent/agent.py",
    "darknoc",
    "install-hub.sh",
    "install-node.sh",
    "upgrade.sh",
    "hub/static/index.html",
]:
    current = read(path)
    if OLD not in current:
        raise SystemExit(f"current version marker missing in {path}")
    write(path, current.replace(OLD, NEW))

for test_path in (ROOT / "tests").glob("*.py"):
    current = test_path.read_text(encoding="utf-8")
    if OLD in current:
        test_path.write_text(current.replace(OLD, NEW), encoding="utf-8")

index = read("hub/static/index.html")
index = index.replace("/static/topology.js?v=1.0.0", "/static/topology.js?v=1.0.1")
write("hub/static/index.html", index)
for test_path in (ROOT / "tests").glob("*.py"):
    current = test_path.read_text(encoding="utf-8")
    if "/static/topology.js?v=1.0.0" in current:
        test_path.write_text(current.replace("/static/topology.js?v=1.0.0", "/static/topology.js?v=1.0.1"), encoding="utf-8")


# 7) Release documentation.
changelog = read("CHANGELOG.md")
if not changelog.startswith("# DARK NOC v2.9.49"):
    write("CHANGELOG.md", """# DARK NOC v2.9.49 — Persistent Remote Identity

- Preserve the last real remote peer IP for tunnel paths when live TCP peer discovery becomes empty after a disconnect.
- Keep live peer identity separate from Last Known identity; the API exposes the source explicitly.
- Never render 127.0.0.1, localhost, ::1 or unspecified addresses as a Global Exit.
- Mark remembered remote endpoints as LAST KNOWN in the Live Tunnel Matrix.
- Add a regression that transitions a healthy Pair-Code-style tunnel to DOWN while retaining its previous foreign IP.

""" + changelog)

notes = read("RELEASE_NOTES.md")
if not notes.startswith("# DARK NOC v2.9.49"):
    write("RELEASE_NOTES.md", """# DARK NOC v2.9.49 — Persistent Remote Identity

- Live Tunnel Matrix no longer changes a disconnected foreign endpoint to `127.0.0.1`.
- The Hub remembers the last valid remote peer independently from current socket telemetry.
- DOWN/OFFLINE paths continue to show the correct previous foreign IP with a `LAST KNOWN` marker.
- Loopback/unspecified addresses are rejected as remote identities at Hub API and frontend layers.
- Existing v2.9.48 tunnel records migrate automatically the next time telemetry is received; no database migration is required.

## فارسی

وقتی تونل قطع می‌شود، IP واقعی سرور خارج دیگر با `127.0.0.1` جایگزین نمی‌شود. Hub آخرین Peer واقعی را نگه می‌دارد و در حالت قطع با علامت `LAST KNOWN` نمایش می‌دهد تا مشخص باشد این IP هویت آخرین اتصال معتبر است، نه وضعیت زنده.

---

""" + notes)


# Temporary automation files must not ship in the release.
for temporary in [
    ROOT / "scripts" / "prepare-v2.9.49-remote-identity.py",
    ROOT / ".github" / "workflows" / "automation-v2.9.49-remote-identity.yml",
]:
    if temporary.exists():
        temporary.unlink()

print("Prepared DARK NOC v2.9.49 persistent remote identity hotfix.")
