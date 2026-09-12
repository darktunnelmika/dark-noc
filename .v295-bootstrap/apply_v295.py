from __future__ import annotations

import re
from pathlib import Path

ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


# ---------------------------------------------------------------------------
# 1. Never restore a successfully repaired Hub Agent to its old stopped state.
# ---------------------------------------------------------------------------
upgrade = read("upgrade.sh")
old_agent_restore = '''    if [[ "$agent_was_present" -eq 1 ]]; then
      restore_service_state dark-noc-agent.service "$agent_was_active" "$agent_was_enabled" "$agent_was_present"
    fi
'''
new_agent_restore = '''    # The local Agent is a required Hub component. A common failure mode was:
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
    row and row[0] == "online" and row[1] == "2.9.5"
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
      echo "Local Hub Agent did not deliver a fresh v2.9.5 post-upgrade pulse." >&2
      systemctl status dark-noc-agent.service --no-pager -l || true
      journalctl -u dark-noc-agent.service -n 160 --no-pager || true
      exit 1
    fi
'''
upgrade = replace_once(
    upgrade, old_agent_restore, new_agent_restore,
    "Hub success path Agent-state repair",
)
write("upgrade.sh", upgrade)

# Make a Hub start pull its required local monitoring Agent back up as well.
hub_unit = read("deploy/dark-noc-hub.service")
hub_unit = replace_once(
    hub_unit,
    "Wants=network-online.target\n",
    "Wants=network-online.target dark-noc-agent.service\n",
    "Hub systemd Wants",
)
write("deploy/dark-noc-hub.service", hub_unit)

# ---------------------------------------------------------------------------
# 2. Expand authoritative local tunnel discovery for disabled and legacy dirs.
# ---------------------------------------------------------------------------
agent = read("agent/agent.py")
agent = replace_once(agent, 'VERSION = "2.9.4"', 'VERSION = "2.9.5"', "Agent version")

old_systemd_scan = '''    code, output = run(
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"],
        timeout=15,
    )
    if code == 0:
        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue
            service = parts[0]
            if (
                DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(service)
                or GHOSTPRO_SERVICE_PATTERN.fullmatch(service)
                or PACKETPRO_SERVICE_PATTERN.fullmatch(service)
                or REALM_SERVICE_PATTERN.fullmatch(service)
            ):
                candidates.add(service)
    else:
        # Managed filesystem roots remain authoritative even if systemd's unit
        # listing is temporarily unavailable.
        LOGGER.warning("systemd unit listing failed during tunnel discovery: %s", output[-500:])

    filesystem_specs = (
        (Path("/etc/dark-backhaul/tunnels"), "config.toml", "backhaul@"),
        (Path("/etc/dark-ghostpro/tunnels"), "config.yaml", "ghostpro@"),
        (Path("/etc/dark-packetpro/tunnels"), "config.yaml", "paqetpro@"),
        (Path("/etc/dark-realm/tunnels"), "config.toml", "dark-realm@"),
    )
    for base, config_name, prefix in filesystem_specs:
'''
new_systemd_scan = '''    service_commands = (
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"],
        ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--plain"],
    )
    systemd_scan_succeeded = False
    for command in service_commands:
        code, output = run(command, timeout=15)
        if code != 0:
            LOGGER.warning(
                "systemd tunnel discovery command failed (%s): %s",
                " ".join(command), output[-500:],
            )
            continue
        systemd_scan_succeeded = True
        for line in output.splitlines():
            parts = line.split()
            if not parts:
                continue
            service = parts[0]
            if (
                DARK_BACKHAUL_SERVICE_PATTERN.fullmatch(service)
                or GHOSTPRO_SERVICE_PATTERN.fullmatch(service)
                or PACKETPRO_SERVICE_PATTERN.fullmatch(service)
                or REALM_SERVICE_PATTERN.fullmatch(service)
            ):
                candidates.add(service)

    filesystem_specs = (
        (Path("/etc/dark-backhaul/tunnels"), ("config.toml", "config.conf", "config.ini"), "backhaul@"),
        (Path("/etc/dark-ghostpro/tunnels"), ("config.yaml", "config.yml", "config.json"), "ghostpro@"),
        (Path("/etc/dark-packetpro/tunnels"), ("config.yaml", "config.yml", "config.json"), "paqetpro@"),
        (Path("/etc/dark-realm/tunnels"), ("config.toml", "config.conf"), "dark-realm@"),
    )
    scanned_root = False
    for base, config_names, prefix in filesystem_specs:
'''
agent = replace_once(
    agent, old_systemd_scan, new_systemd_scan,
    "Agent discovery systemd/filesystem scan",
)

old_directory_scan = '''        try:
            directories = list(base.iterdir()) if base.is_dir() else []
        except OSError:
            complete = False
            directories = []
        for directory in directories:
            if (
                directory.is_dir()
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", directory.name)
                and (directory / config_name).is_file()
            ):
                candidates.add(f"{prefix}{directory.name}.service")

    if not candidates and not complete and previous_by_service:
'''
new_directory_scan = '''        try:
            if base.is_dir():
                scanned_root = True
                directories = list(base.iterdir())
            else:
                directories = []
        except OSError:
            complete = False
            directories = []
        for directory in directories:
            if not (
                directory.is_dir()
                and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", directory.name)
            ):
                continue
            has_owned_config = any((directory / name).is_file() for name in config_names)
            if has_owned_config or (directory / "meta.conf").is_file():
                candidates.add(f"{prefix}{directory.name}.service")

    if not systemd_scan_succeeded and not scanned_root:
        complete = False

    if not candidates and not complete and previous_by_service:
'''
agent = replace_once(
    agent, old_directory_scan, new_directory_scan,
    "Agent discovery directory ownership",
)

old_tunnel_config = '''        expected_config = tunnel_dir / ("config.yaml" if ghost or packet else "config.toml")
        filesystem_owned = expected_config.is_file()
'''
new_tunnel_config = '''        config_candidates = (
            ("config.yaml", "config.yml", "config.json")
            if ghost or packet
            else ("config.toml", "config.conf", "config.ini")
        )
        expected_config = next(
            (tunnel_dir / name for name in config_candidates if (tunnel_dir / name).is_file()),
            tunnel_dir / config_candidates[0],
        )
        filesystem_owned = expected_config.is_file() or (tunnel_dir / "meta.conf").is_file()
'''
agent = replace_once(
    agent, old_tunnel_config, new_tunnel_config,
    "Agent discovery legacy config selection",
)
write("agent/agent.py", agent)

# ---------------------------------------------------------------------------
# 3. Restore a visibly solid live route. Moving packets carry the motion;
#    the route itself must never look disconnected/dotted.
# ---------------------------------------------------------------------------
styles = read("hub/static/styles.css")
backbone_pattern = re.compile(r"\.cyber-route-backbone\{[^}]+\}")
flow_pattern = re.compile(r"\.cyber-route-flow\{[^}]+\}")
if len(backbone_pattern.findall(styles)) != 1:
    raise SystemExit("Topology backbone CSS anchor mismatch")
if len(flow_pattern.findall(styles)) != 1:
    raise SystemExit("Topology flow CSS anchor mismatch")
styles = backbone_pattern.sub(
    ".cyber-route-backbone{fill:none;stroke:currentColor;stroke-width:calc(var(--route-width,1.8) + .55);"
    "stroke-linecap:round;opacity:.92;filter:url(#cyber-glow);pointer-events:none;"
    "vector-effect:non-scaling-stroke;transition:opacity .18s,stroke-width .18s}",
    styles,
    count=1,
)
styles = flow_pattern.sub(
    ".cyber-route-flow{fill:none;stroke:currentColor;stroke-width:calc(var(--route-width,1.8) + 2.1);"
    "stroke-linecap:round;opacity:.34;filter:url(#cyber-glow);pointer-events:none;"
    "vector-effect:non-scaling-stroke;animation:route-energy-pulse var(--flow-duration,3s) ease-in-out infinite}",
    styles,
    count=1,
)
styles = replace_once(
    styles,
    "@keyframes route-data-flow{to{stroke-dashoffset:0}}",
    "@keyframes route-energy-pulse{0%,100%{opacity:.16}50%{opacity:.72}}",
    "Topology flow animation",
)
styles = styles.replace(
    ".route-group.degraded .cyber-route-flow{opacity:.72;animation-duration:calc(var(--flow-duration,3s) * 1.35)}",
    ".route-group.degraded .cyber-route-flow{opacity:.5;animation-duration:calc(var(--flow-duration,3s) * 1.35)}",
)
write("hub/static/styles.css", styles)

# ---------------------------------------------------------------------------
# 4. Version alignment and permanent regression contracts.
# ---------------------------------------------------------------------------
for relative in (
    "hub/app.py",
    "install-hub.sh",
    "install-node.sh",
    "darknoc",
    "hub/static/index.html",
    "README.md",
    "README.fa.md",
    "NODE-README.md",
    "tests/smoke.py",
    "tests/realm_integration.py",
    "tests/local_hub_recovery.py",
    "tests/installer_contracts.py",
    "tests/agent_heartbeat_resilience.py",
    "tests/topology_inventory_regression.py",
):
    source = read(relative)
    if "2.9.4" not in source:
        raise SystemExit(f"{relative}: current-version anchor missing")
    write(relative, source.replace("2.9.4", "2.9.5"))

contracts = read("tests/installer_contracts.py")
contract_anchor = '''assert "python tests/topology_inventory_regression.py" in ci

with tempfile.TemporaryDirectory'''
contract_replacement = '''assert "python tests/topology_inventory_regression.py" in ci

hub_upgrade_success = upgrader.split('bash "$SCRIPT_DIR/install-hub.sh"', 1)[1].split(
    ";;\\n  agent)", 1
)[0]
assert "POST_UPGRADE_AGENT_BASELINE" in hub_upgrade_success
assert "systemctl enable dark-noc-agent.service" in hub_upgrade_success
assert "systemctl restart dark-noc-agent.service" in hub_upgrade_success
assert "fresh v2.9.5 post-upgrade pulse" in hub_upgrade_success
assert 'restore_service_state dark-noc-agent.service "$agent_was_active"' not in hub_upgrade_success
assert "Wants=network-online.target dark-noc-agent.service" in text("deploy/dark-noc-hub.service")

with tempfile.TemporaryDirectory'''
contracts = replace_once(
    contracts, contract_anchor, contract_replacement,
    "Hub upgrade runtime contract",
)
write("tests/installer_contracts.py", contracts)

topology_test = read("tests/topology_inventory_regression.py")
topology_anchor = '''assert "--topology-canvas-height" in app_js and "--topology-canvas-height" in styles
assert "No matching tunnel path" in app_js

print("Topology and authoritative inventory regression tests passed")
'''
topology_replacement = '''assert "--topology-canvas-height" in app_js and "--topology-canvas-height" in styles
assert "No matching tunnel path" in app_js
flow_css = styles.split(".cyber-route-flow{", 1)[1].split("}", 1)[0]
backbone_css = styles.split(".cyber-route-backbone{", 1)[1].split("}", 1)[0]
assert "stroke-dasharray" not in flow_css
assert "stroke-dasharray" not in backbone_css
assert "route-energy-pulse" in styles
assert "route-packet-core" in app_js and "route-packet-halo" in app_js
assert "list-unit-files" in agent_source
assert "config.ini" in agent_source and "config.yml" in agent_source

print("Topology and authoritative inventory regression tests passed")
'''
topology_test = replace_once(
    topology_test, topology_anchor, topology_replacement,
    "Solid topology regression contract",
)
write("tests/topology_inventory_regression.py", topology_test)

changelog = read("CHANGELOG.md")
changelog_entry = '''# DARK NOC v2.9.5

- Keeps the repaired local Hub Agent enabled and running after an upgrade, even when the pre-upgrade Agent was crashed or stopped.
- Requires a fresh post-upgrade v2.9.5 pulse before the upgrade can succeed.
- Makes the Hub service pull in its local Agent on service start and boot.
- Expands Hub-local tunnel discovery across loaded units, unit files, stopped instances, metadata-only directories and legacy config file names.
- Restores a solid glowing Iran-to-Kharej route with pulsing energy and moving traffic particles; healthy routes no longer look dotted.

'''
write("CHANGELOG.md", changelog_entry + changelog)

notes = read("RELEASE_NOTES.md")
notes_entry = '''# DARK NOC v2.9.5 — Hub Agent Runtime & Solid Live Routes

The Hub upgrade transaction no longer restores a successfully repaired local
Agent to its old stopped state. A fresh v2.9.5 pulse is required after the final
Agent restart, and starting the Hub also pulls in its monitoring Agent.

Local tunnel discovery now combines loaded systemd units, installed unit files
and DARK-owned configuration roots, including stopped instances and legacy
config extensions. The topology uses a solid luminous backbone with pulsing
energy and moving packets rather than a dotted primary route.

---

'''
write("RELEASE_NOTES.md", notes_entry + notes)

print("DARK NOC v2.9.5 patch applied")
