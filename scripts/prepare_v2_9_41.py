from pathlib import Path

ROOT = Path('.')
old_version = '2.9.40'
new_version = '2.9.41'
old_pin = 'psutil==6.1.1'
new_pin = 'psutil==7.2.2'

for rel in ['hub/requirements.txt', 'agent/requirements.txt']:
    path = ROOT / rel
    data = path.read_text()
    if old_pin not in data:
        raise RuntimeError(f'{old_pin} missing from {rel}')
    path.write_text(data.replace(old_pin, new_pin, 1))

contract = ROOT / 'tests/dependency_psutil_contract.py'
contract.write_text('''from importlib.metadata import version\nfrom pathlib import Path\nimport os\n\nimport psutil\n\nroot = Path(__file__).resolve().parents[1]\nhub_requirements = (root / "hub/requirements.txt").read_text().splitlines()\nagent_requirements = (root / "agent/requirements.txt").read_text().splitlines()\n\nfor requirements in (hub_requirements, agent_requirements):\n    assert "psutil==7.2.2" in requirements\n    assert "psutil==6.1.1" not in requirements\nassert version("psutil") == "7.2.2"\n\n# API surface used by the DARK NOC Agent telemetry/inventory runtime.\nfor name in [\n    "cpu_percent", "virtual_memory", "swap_memory", "disk_usage", "boot_time",\n    "net_io_counters", "disk_io_counters", "net_connections", "sensors_temperatures",\n]:\n    assert callable(getattr(psutil, name, None)), name\n\nassert isinstance(psutil.cpu_percent(interval=None), float)\nassert hasattr(psutil.virtual_memory(), "percent")\nassert hasattr(psutil.swap_memory(), "percent")\nassert hasattr(psutil.disk_usage("/"), "percent")\nassert isinstance(psutil.boot_time(), float)\nnet = psutil.net_io_counters()\nassert all(hasattr(net, field) for field in ["bytes_recv", "bytes_sent", "errin", "errout", "dropin", "dropout"])\ndisk = psutil.disk_io_counters()\nassert disk is None or all(hasattr(disk, field) for field in ["read_bytes", "write_bytes"])\nprocess = psutil.Process(os.getpid())\nassert isinstance(process.children(recursive=True), list)\nassert isinstance(psutil.CONN_ESTABLISHED, str) and isinstance(psutil.CONN_LISTEN, str)\ntry:\n    connections = psutil.net_connections(kind="inet")\nexcept psutil.AccessDenied:\n    connections = []\nassert isinstance(connections, list)\nassert issubclass(psutil.AccessDenied, psutil.Error)\n\nprint("psutil dependency contract passed")\n''')

version_paths = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc',
    ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh',
    ROOT / 'hub/static/index.html',
]
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if old_version in data:
        path.write_text(data.replace(old_version, new_version))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.41 — psutil Dependency Hardening\n\n- Upgrade `psutil` from `6.1.1` to `7.2.2` in both Hub and Agent requirement sets.\n- Keep the dependency exactly pinned so production and CI use the same telemetry runtime.\n- Validate the Agent APIs used for CPU, RAM, swap, disk, boot time, network/disk I/O, socket inventory, process trees and temperature telemetry.\n- Exercise the complete Agent heartbeat/inventory regression suite and reproducible release build with no other dependency changes.\n- No API, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nپکیج `psutil` در Hub و Agent از نسخه 6.1.1 به 7.2.2 ارتقا داده شد؛ مسیرهای Telemetry، Socket/Process inventory و Agent heartbeat با تست کامل بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.41\n- Dependency hardening: pin `psutil==7.2.2` in Hub and Agent (from 6.1.1).\n- Add permanent Agent telemetry/socket/process compatibility coverage.\n- No API, tunnel, frontend or Live Matrix behavior changes.\n\n''' + changelog.read_text())

print('prepared v2.9.41 psutil hardening')
