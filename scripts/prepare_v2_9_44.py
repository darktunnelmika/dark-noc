from pathlib import Path

ROOT = Path('.')
OLD_VERSION = '2.9.43'
NEW_VERSION = '2.9.44'


def replace_once(rel: str, old: str, new: str) -> None:
    path = ROOT / rel
    data = path.read_text()
    count = data.count(old)
    if count != 1:
        raise RuntimeError(f'{rel}: expected one match, found {count}: {old[:80]!r}')
    path.write_text(data.replace(old, new, 1))


# Hub install: install both Hub and local-Agent runtimes from hash-locked files.
replace_once(
    'install-hub.sh',
    'install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc/agent-payload/requirements.txt\ninstall -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /opt/dark-noc/agent-payload/dark-noc-agent.service',
    'install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc/agent-payload/requirements.txt\ninstall -m 0644 "$SCRIPT_DIR/agent/requirements.lock" /opt/dark-noc/agent-payload/requirements.lock\ninstall -m 0644 "$SCRIPT_DIR/deploy/dark-noc-agent.service" /opt/dark-noc/agent-payload/dark-noc-agent.service',
)
replace_once(
    'install-hub.sh',
    '/opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt',
    '/opt/dark-noc/venv/bin/pip install --disable-pip-version-check --require-hashes -r /opt/dark-noc/hub/requirements.lock',
)
replace_once(
    'install-hub.sh',
    'install -m 0644 /opt/dark-noc/agent-payload/requirements.txt /opt/dark-noc-agent/requirements.txt\npython3 -m venv /opt/dark-noc-agent/venv\n/opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt',
    'install -m 0644 /opt/dark-noc/agent-payload/requirements.txt /opt/dark-noc-agent/requirements.txt\ninstall -m 0644 /opt/dark-noc/agent-payload/requirements.lock /opt/dark-noc-agent/requirements.lock\npython3 -m venv /opt/dark-noc-agent/venv\n/opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check --require-hashes -r /opt/dark-noc-agent/requirements.lock',
)

# Remote zero-touch provisioning: ship the Agent lock and enforce hashes remotely.
replace_once(
    'hub/app.py',
    'requirements_file = AGENT_PAYLOAD_DIR / "requirements.txt"\n            service_file = AGENT_PAYLOAD_DIR / "dark-noc-agent.service"\n            for required in (agent_file, realm_adapter_file, requirements_file, service_file):',
    'requirements_file = AGENT_PAYLOAD_DIR / "requirements.txt"\n            requirements_lock_file = AGENT_PAYLOAD_DIR / "requirements.lock"\n            service_file = AGENT_PAYLOAD_DIR / "dark-noc-agent.service"\n            for required in (agent_file, realm_adapter_file, requirements_file, requirements_lock_file, service_file):',
)
replace_once(
    'hub/app.py',
    '("/opt/dark-noc-agent/requirements.txt", requirements_file.read_bytes(), 0o644),\n                    ("/etc/systemd/system/dark-noc-agent.service", service_file.read_bytes(), 0o644),',
    '("/opt/dark-noc-agent/requirements.txt", requirements_file.read_bytes(), 0o644),\n                    ("/opt/dark-noc-agent/requirements.lock", requirements_lock_file.read_bytes(), 0o644),\n                    ("/etc/systemd/system/dark-noc-agent.service", service_file.read_bytes(), 0o644),',
)
replace_once(
    'hub/app.py',
    'requirements_stage = next(entry["temporary"] for entry in staged_files if entry["target"].endswith("requirements.txt"))\n                    venv_result = await ssh.run(\n                        f"test ! -e {shlex.quote(venv_stage)} && "\n                        f"python3 -m venv {shlex.quote(venv_stage)} && "\n                        f"{shlex.quote(venv_stage + \'/bin/pip\')} install --disable-pip-version-check -r {shlex.quote(requirements_stage)}",',
    'requirements_stage = next(entry["temporary"] for entry in staged_files if entry["target"].endswith("requirements.txt"))\n                    requirements_lock_stage = next(entry["temporary"] for entry in staged_files if entry["target"].endswith("requirements.lock"))\n                    venv_result = await ssh.run(\n                        f"test ! -e {shlex.quote(venv_stage)} && "\n                        f"python3 -m venv {shlex.quote(venv_stage)} && "\n                        f"{shlex.quote(venv_stage + \'/bin/pip\')} install --disable-pip-version-check --require-hashes -r {shlex.quote(requirements_lock_stage)}",',
)

# Upgrade/rollback paths: new releases enforce locks, while rollback remains compatible
# with pre-v2.9.44 backups which do not contain lock files.
replace_once(
    'upgrade.sh',
    'if [[ -x /opt/dark-noc/venv/bin/pip && -f /opt/dark-noc/hub/requirements.txt ]]; then\n        rollback_step "could not restore Hub Python dependencies" /opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt || rollback_failed=1\n      else\n        echo "ROLLBACK ERROR: Hub virtualenv or restored requirements are missing" >&2\n        rollback_failed=1\n      fi',
    'if [[ -x /opt/dark-noc/venv/bin/pip && -f /opt/dark-noc/hub/requirements.lock ]]; then\n        rollback_step "could not restore Hub Python dependencies" /opt/dark-noc/venv/bin/pip install --disable-pip-version-check --require-hashes -r /opt/dark-noc/hub/requirements.lock || rollback_failed=1\n      elif [[ -x /opt/dark-noc/venv/bin/pip && -f /opt/dark-noc/hub/requirements.txt ]]; then\n        rollback_step "could not restore legacy Hub Python dependencies" /opt/dark-noc/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc/hub/requirements.txt || rollback_failed=1\n      else\n        echo "ROLLBACK ERROR: Hub virtualenv or restored requirements are missing" >&2\n        rollback_failed=1\n      fi',
)
replace_once(
    'upgrade.sh',
    'if [[ -x /opt/dark-noc-agent/venv/bin/pip && -f /opt/dark-noc-agent/requirements.txt ]]; then\n        rollback_step "could not restore Agent Python dependencies" /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt || rollback_failed=1\n      else\n        echo "ROLLBACK ERROR: Agent virtualenv or restored requirements are missing" >&2\n        rollback_failed=1\n      fi',
    'if [[ -x /opt/dark-noc-agent/venv/bin/pip && -f /opt/dark-noc-agent/requirements.lock ]]; then\n        rollback_step "could not restore Agent Python dependencies" /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check --require-hashes -r /opt/dark-noc-agent/requirements.lock || rollback_failed=1\n      elif [[ -x /opt/dark-noc-agent/venv/bin/pip && -f /opt/dark-noc-agent/requirements.txt ]]; then\n        rollback_step "could not restore legacy Agent Python dependencies" /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt || rollback_failed=1\n      else\n        echo "ROLLBACK ERROR: Agent virtualenv or restored requirements are missing" >&2\n        rollback_failed=1\n      fi',
)
replace_once(
    'upgrade.sh',
    'install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt\n    /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check -r /opt/dark-noc-agent/requirements.txt',
    'install -m 0644 "$SCRIPT_DIR/agent/requirements.txt" /opt/dark-noc-agent/requirements.txt\n    install -m 0644 "$SCRIPT_DIR/agent/requirements.lock" /opt/dark-noc-agent/requirements.lock\n    /opt/dark-noc-agent/venv/bin/pip install --disable-pip-version-check --require-hashes -r /opt/dark-noc-agent/requirements.lock',
)

# Permanent lock/supply-chain contract. Lock files themselves are generated in CI
# from the reviewed direct requirements using pinned pip-tools 7.6.1.
(ROOT / 'tests/dependency_lock_contract.py').write_text(r'''from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
hub_lock = (root / 'hub/requirements.lock').read_text()
agent_lock = (root / 'agent/requirements.lock').read_text()
install_hub = (root / 'install-hub.sh').read_text()
upgrade = (root / 'upgrade.sh').read_text()
app = (root / 'hub/app.py').read_text()


def validate_lock(name: str, text: str, required: set[str]) -> None:
    assert '--hash=sha256:' in text, f'{name} has no SHA256 hashes'
    assert text.count('--hash=sha256:') >= len(required), f'{name} hash coverage is unexpectedly small'
    normalized = text.lower().replace('_', '-')
    for package in required:
        assert re.search(rf'(?m)^{re.escape(package)}==[^\\\s]+(?:\\s+\\\\)?$', normalized), (name, package)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith('--hash=') or stripped == '\\':
            continue
        if stripped.startswith(('-i ', '--index-url', '--trusted-host')):
            raise AssertionError(f'{name} must not embed an alternate package index: {stripped}')
        if stripped.startswith(('-', '    ')):
            continue
        if '==' in stripped:
            continue
        if stripped.endswith('\\') and '==' in stripped:
            continue
        raise AssertionError(f'{name} contains an unpinned requirement: {stripped}')


validate_lock('hub', hub_lock, {
    'fastapi', 'starlette', 'pydantic', 'pydantic-core', 'uvicorn', 'asyncssh',
    'cryptography', 'python-multipart', 'psutil', 'httpx', 'httpcore', 'anyio',
})
validate_lock('agent', agent_lock, {'httpx', 'httpcore', 'psutil', 'anyio', 'certifi', 'h11', 'idna'})

assert '--require-hashes -r /opt/dark-noc/hub/requirements.lock' in install_hub
assert '--require-hashes -r /opt/dark-noc-agent/requirements.lock' in install_hub
assert 'agent/requirements.lock' in install_hub
assert 'requirements_lock_file = AGENT_PAYLOAD_DIR / "requirements.lock"' in app
assert '--require-hashes -r {shlex.quote(requirements_lock_stage)}' in app
assert '--require-hashes -r /opt/dark-noc-agent/requirements.lock' in upgrade
assert '--require-hashes -r /opt/dark-noc/hub/requirements.lock' in upgrade

print('dependency lock and hash enforcement contract passed')
''')

# Version propagation.
version_paths = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc', ROOT / 'install-hub.sh',
    ROOT / 'install-node.sh', ROOT / 'upgrade.sh', ROOT / 'hub/static/index.html',
]
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if OLD_VERSION in data:
        path.write_text(data.replace(OLD_VERSION, NEW_VERSION))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.44 — Dependency Lock & Supply-Chain Hardening\n\n- Add separate hash-locked dependency graphs for Hub and Agent (`requirements.lock`).\n- Generate the locks from reviewed direct requirements with pinned `pip-tools==7.6.1`.\n- Enforce `pip --require-hashes` on Hub installs, local Agent installs, zero-touch remote Agent provisioning and normal Agent upgrades.\n- Keep rollback compatibility with pre-v2.9.44 installations which only have legacy `requirements.txt`.\n- Add a permanent drift guard covering transitive pins, SHA256 hashes and runtime install paths.\n- Keep the direct dependency versions unchanged in this release; this step captures and freezes the dependency graph already validated by DARK NOC.\n- No API, Agent protocol, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nبرای Hub و Agent فایل Lock مستقل با Pin کامل dependencyهای ترانزیتی و SHA256 اضافه شد. نصب‌های جدید و Provision/Upgrade از `--require-hashes` استفاده می‌کنند تا تغییر ناخواسته dependency یا جایگزینی فایل پکیج باعث تغییر رفتار پنل نشود؛ Rollback به نسخه‌های قدیمی هم همچنان پشتیبانی می‌شود.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.44\n- Supply-chain hardening: add SHA256 hash-locked Hub and Agent dependency graphs.\n- Enforce `--require-hashes` in install, provisioning and upgrade paths.\n- Preserve legacy requirements fallback only for rollback compatibility with older releases.\n- No direct dependency version, API, Agent protocol, tunnel, frontend or Live Matrix behavior changes.\n\n''' + changelog.read_text())

print('prepared v2.9.44 dependency lock hardening')
