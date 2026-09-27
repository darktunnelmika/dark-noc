from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

agent_path = ROOT / "agent" / "agent.py"
spec = importlib.util.spec_from_file_location("dark_noc_scan_contract_agent", agent_path)
assert spec is not None and spec.loader is not None
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

assert "tunnel_scan" in agent.ALLOWED_JOB_KINDS

with tempfile.TemporaryDirectory(prefix="dark-noc-packet-ports-") as tmp:
    root = Path(tmp)
    standalone = root / "standalone.list"
    standalone.write_text(
        "tcp\t443\t127.0.0.1:443\n"
        "udp\t8443\t127.0.0.1:8443\n",
        encoding="utf-8",
    )
    assert agent._discovered_user_ports(standalone, packet=True) == [443, 8443]

    native = root / "native.list"
    native.write_text(
        "443\t127.0.0.1:443\n"
        "8443\t127.0.0.1:8443\n",
        encoding="utf-8",
    )
    assert agent._discovered_user_ports(native, packet=True) == [443, 8443]

agent_source = agent_path.read_text(encoding="utf-8")
schemas = (ROOT / "hub" / "schemas.py").read_text(encoding="utf-8")
index = (ROOT / "hub" / "static" / "index.html").read_text(encoding="utf-8")
app = (ROOT / "hub" / "static" / "app.js").read_text(encoding="utf-8")
scan = (ROOT / "hub" / "static" / "tunnel-scan.js").read_text(encoding="utf-8")
state = (ROOT / "hub" / "static" / "state.js").read_text(encoding="utf-8")
refresh = (ROOT / "hub" / "static" / "refresh-runtime.js").read_text(encoding="utf-8")
session = (ROOT / "hub" / "static" / "session-runtime.js").read_text(encoding="utf-8")

assert "def discover_tunnels_snapshot(force: bool = False)" in agent_source
assert "discover_tunnels_snapshot(force=True)" in agent_source
assert '"transport": meta.get("TRANSPORT") or ("kcp" if packet else "unknown")' in agent_source
assert '"normal": "stable", "fast": "balanced", "fast2": "lowping", "fast3": "turbo"' in agent_source
assert "tunnel_scan" in schemas

assert index.count("data-scan-tunnels") >= 2
assert "tunnel-scan.js?v=1.0.0" in index
assert "async function scanTunnelInventory" in scan
assert "kind: 'tunnel_scan'" in scan
assert "setInterval(()=>refreshActiveView(),30000)" in app

assert "summary: null" in state
assert "COMMON_REFRESH_TTL_MS = 8000" in refresh
assert "LIVE_REFRESH_MIN_INTERVAL_MS = 4000" in refresh
assert "lastCommonRefreshAt" in refresh
assert "setTimeout(refreshLive,650)" in session
assert ">=60000" in session

print("Tunnel scan, Packet Pro discovery and refresh performance contract passed")
