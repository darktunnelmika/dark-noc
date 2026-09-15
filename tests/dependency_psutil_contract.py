from importlib.metadata import version
from pathlib import Path
import os

import psutil

root = Path(__file__).resolve().parents[1]
hub_requirements = (root / "hub/requirements.txt").read_text().splitlines()
agent_requirements = (root / "agent/requirements.txt").read_text().splitlines()

for requirements in (hub_requirements, agent_requirements):
    assert "psutil==7.2.2" in requirements
    assert "psutil==6.1.1" not in requirements
assert version("psutil") == "7.2.2"

# API surface used by the DARK NOC Agent telemetry/inventory runtime.
for name in [
    "cpu_percent", "virtual_memory", "swap_memory", "disk_usage", "boot_time",
    "net_io_counters", "disk_io_counters", "net_connections", "sensors_temperatures",
]:
    assert callable(getattr(psutil, name, None)), name

assert isinstance(psutil.cpu_percent(interval=None), float)
assert hasattr(psutil.virtual_memory(), "percent")
assert hasattr(psutil.swap_memory(), "percent")
assert hasattr(psutil.disk_usage("/"), "percent")
assert isinstance(psutil.boot_time(), float)
net = psutil.net_io_counters()
assert all(hasattr(net, field) for field in ["bytes_recv", "bytes_sent", "errin", "errout", "dropin", "dropout"])
disk = psutil.disk_io_counters()
assert disk is None or all(hasattr(disk, field) for field in ["read_bytes", "write_bytes"])
process = psutil.Process(os.getpid())
assert isinstance(process.children(recursive=True), list)
assert isinstance(psutil.CONN_ESTABLISHED, str) and isinstance(psutil.CONN_LISTEN, str)
try:
    connections = psutil.net_connections(kind="inet")
except psutil.AccessDenied:
    connections = []
assert isinstance(connections, list)
assert issubclass(psutil.AccessDenied, psutil.Error)

print("psutil dependency contract passed")
