import asyncio
import importlib.util
import json
import socket
from pathlib import Path
from unittest.mock import patch


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

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
monitor_port = listener.getsockname()[1]
tcp_monitor = agent.execute_monitor({
    "monitor_id": 7, "kind": "tcp", "target": "127.0.0.1",
    "port": monitor_port, "timeout_seconds": 2,
})
assert tcp_monitor["status"] == "up" and tcp_monitor["detail"]["port"] == monitor_port
listener.close()

dns_monitor = agent.execute_monitor({
    "monitor_id": 8, "kind": "dns", "target": "localhost", "timeout_seconds": 2,
})
assert dns_monitor["status"] == "up" and dns_monitor["detail"]["records"]

with patch.object(agent.shutil, "which", return_value="/usr/bin/snmpget"), patch.object(
    agent, "run", return_value=(0, "SNMPv2-MIB::sysUpTime.0 = Timeticks: (123) 0:00:01.23")
) as snmp_run:
    snmp_monitor = agent.execute_monitor({
        "monitor_id": 9, "kind": "snmp", "target": "127.0.0.1", "port": 161,
        "timeout_seconds": 2, "snmp_community": "unit-secret",
        "snmp_oid": ".1.3.6.1.2.1.1.3.0",
    })
assert snmp_monitor["status"] == "up"
assert "unit-secret" in snmp_run.call_args.args[0]

failed_status, failed_output = agent.execute_job({
    "kind": "monitor_run", "payload": json.dumps({
        "monitor_id": 10, "kind": "tcp", "target": "127.0.0.1",
        "port": monitor_port, "timeout_seconds": 1,
    })
}, {})
assert failed_status == "failed"
failed_monitor = json.loads(failed_output)
assert failed_monitor["status"] == "down" and failed_monitor["detail"]["error"]

print("DARK NOC Agent logic test passed")
