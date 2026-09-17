from pathlib import Path

root = Path(__file__).resolve().parents[1]

schemas_path = root / "hub/schemas.py"
schemas = schemas_path.read_text(encoding="utf-8")
old_transport = 'pattern=r"^(tcp|tcpmux|ws|wsmux|wss|wssmux|udp|kcp|relay|tls|h2|h2c|grpc|quic|dtls|icmp|relay\\+(?:tls|wss|h2|grpc|quic|ws))$"'
old_profile = 'pattern=r"^(stable|balanced|lowping|turbo)$"'
new_token = 'max_length=64, pattern=r"^[A-Za-z0-9+._-]+$"'
if schemas.count(old_transport) != 3:
    raise SystemExit(f"transport schema boundary changed: {schemas.count(old_transport)}")
if schemas.count(old_profile) != 3:
    raise SystemExit(f"profile schema boundary changed: {schemas.count(old_profile)}")
schemas = schemas.replace(old_transport, new_token)
schemas = schemas.replace(old_profile, new_token)
schemas_path.write_text(schemas, encoding="utf-8")

service_path = root / "hub/node_tunnel_service.py"
service = service_path.read_text(encoding="utf-8")
old = '''        if not plugin or body.transport not in plugin["transports"]:
            raise NodeTunnelServiceError(422, "Transport is not supported by this plugin")
        if any(
'''
new = '''        if not plugin or body.transport not in plugin["transports"]:
            raise NodeTunnelServiceError(422, "Transport is not supported by this plugin")
        if body.profile not in plugin["profiles"]:
            raise NodeTunnelServiceError(422, "Performance profile is not supported by this plugin")
        if any(
'''
if service.count(old) != 1:
    raise SystemExit("tunnel reconfigure manifest validation boundary changed")
service_path.write_text(service.replace(old, new, 1), encoding="utf-8")

print("Prepared v2.9.46 manifest-driven plugin request schema")
