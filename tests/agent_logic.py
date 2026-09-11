import asyncio
import importlib.util
from pathlib import Path


agent_path = Path(__file__).resolve().parents[1] / "agent" / "agent.py"
spec = importlib.util.spec_from_file_location("dark_noc_agent", agent_path)
agent = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(agent)


class Address:
    def __init__(self, port):
        self.port = port


class Connection:
    def __init__(self, local_port, remote_port):
        self.status = agent.psutil.CONN_ESTABLISHED
        self.laddr = Address(local_port)
        self.raddr = Address(remote_port)


agent.psutil.net_connections = lambda kind: [Connection(443, 50000), Connection(50001, 443)]
agent.refresh_connection_snapshot()
assert agent.port_sessions(443) == 2
assert agent.port_sessions(0) == 0

agent.systemd_status = lambda service: "active"
agent.socket_state = lambda port, remote_host=None: (True, 3)
agent.port_sessions = lambda port: 2

server = asyncio.run(agent.tunnel_report({
    "name": "dark-link", "method": "DARK Backhaul", "role": "server",
    "service": "backhaul@dark-link.service", "target_port": 3080,
    "listen_port": 3080, "user_ports": [443, 8443], "target_host": "127.0.0.1",
}))
assert server["status"] == "healthy"
assert server["sessions"] == 4
assert server["latency_ms"] is None

client = asyncio.run(agent.tunnel_report({
    "name": "dark-link", "method": "DARK Backhaul", "role": "client",
    "service": "backhaul@dark-link.service", "target_port": 3080,
    "target_host": "203.0.113.10",
}))
assert client["status"] == "healthy"
assert client["sessions"] == 3

packet = asyncio.run(agent.tunnel_report({
    "name": "packet-link", "method": "DARK Packet Pro", "role": "client",
    "service": "paqetpro@packet-link.service", "target_port": 9898,
    "target_host": "198.51.100.20", "user_ports": [443, 8443],
}))
assert packet["status"] == "healthy" and packet["sessions"] == 4

print("DARK NOC Agent logic test passed")
