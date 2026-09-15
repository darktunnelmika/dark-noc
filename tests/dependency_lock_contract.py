from pathlib import Path
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
        assert f'{package}==' in normalized, (name, package)
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
