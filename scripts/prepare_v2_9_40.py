from pathlib import Path

ROOT = Path('.')
old_version = '2.9.39'
new_version = '2.9.40'

requirements_path = ROOT / 'hub/requirements.txt'
requirements = requirements_path.read_text()
old_pin = 'asyncssh==2.19.0'
new_pin = 'asyncssh==2.24.0'
if requirements.count(old_pin) != 1:
    raise RuntimeError('expected exactly one AsyncSSH 2.19.0 pin')
if new_pin in requirements:
    raise RuntimeError('AsyncSSH 2.24.0 pin already present')
requirements_path.write_text(requirements.replace(old_pin, new_pin, 1))

contract = ROOT / 'tests/dependency_asyncssh_contract.py'
contract.write_text('''from importlib.metadata import version\nfrom pathlib import Path\n\nimport asyncssh\n\nroot = Path(__file__).resolve().parents[1]\nrequirements = (root / "hub/requirements.txt").read_text().splitlines()\napp = (root / "hub/app.py").read_text()\nfile_router = (root / "hub/ssh_file_router.py").read_text()\nterminal_router = (root / "hub/ssh_terminal_router.py").read_text()\n\nassert "asyncssh==2.24.0" in requirements\nassert "asyncssh==2.19.0" not in requirements\nassert version("asyncssh") == "2.24.0"\n\n# Lock the AsyncSSH API surface DARK NOC depends on for provisioning,\n# interactive terminals, host-key pinning and SFTP file operations.\nfor name in [\n    "connect", "import_private_key", "generate_private_key", "SSHClient",\n    "SFTPAttrs", "SFTPNoSuchFile", "SFTPOpUnsupported",\n]:\n    assert hasattr(asyncssh, name), name\n\nattrs = asyncssh.SFTPAttrs(permissions=0o600)\nassert attrs.permissions == 0o600\nkey = asyncssh.generate_private_key("ssh-ed25519")\nencoded = key.export_private_key("openssh")\nloaded = asyncssh.import_private_key(encoded)\nassert loaded.get_algorithm() == key.get_algorithm()\n\nfor marker in [\n    "class PinnedSSHClient(asyncssh.SSHClient)",\n    "asyncssh.import_private_key(key)",\n    "async with asyncssh.connect(",\n    "asyncssh.SFTPAttrs(permissions=0o600)",\n    "except asyncssh.SFTPNoSuchFile",\n    "except asyncssh.SFTPOpUnsupported",\n]:\n    assert marker in app or marker in file_router or marker in terminal_router, marker\n\nprint("AsyncSSH dependency hardening contract passed")\n''')

# Permanent CI coverage for the new dependency contract.
ci_path = ROOT / '.github/workflows/ci.yml'
ci = ci_path.read_text()
ci_anchor = '          python tests/dependency_cryptography_contract.py\n'
ci_line = '          python tests/dependency_asyncssh_contract.py\n'
if ci_line not in ci:
    if ci_anchor not in ci:
        raise RuntimeError('CI dependency contract anchor not found')
    ci = ci.replace(ci_anchor, ci_anchor + ci_line, 1)
ci_path.write_text(ci)

# Keep all distributed version markers synchronized with the release.
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
release_notes.write_text('''# DARK NOC v2.9.40 — AsyncSSH Dependency Hardening\n\n- Upgrade the Hub SSH runtime from `asyncssh==2.19.0` to `asyncssh==2.24.0`.\n- Keep the dependency exactly pinned so CI and production installs use the same SSH/SFTP implementation.\n- Validate the AsyncSSH API surface used by DARK NOC: SSHClient host-key pinning, private-key import, SSH connect, SFTP attributes and SFTP exception handling.\n- Exercise the existing SSH Terminal, SFTP File Manager, upload/relay, provisioning and full Hub/Agent regression suites.\n- Run the reproducible release build with no other dependency changes.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nپکیج `AsyncSSH` هاب از نسخه 2.19.0 به 2.24.0 ارتقا داده شد؛ مسیرهای SSH Terminal، SFTP، آپلود/Relay، Host-Key Pinning و نصب Agent با تست‌های کامل بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.40\n- Dependency hardening: pin `asyncssh==2.24.0` (from 2.19.0).\n- Add permanent AsyncSSH API/key/SFTP compatibility coverage.\n- Preserve SSH terminal, file transfer, provisioning, API and frontend behavior.\n\n''' + changelog.read_text())

print('prepared v2.9.40 AsyncSSH dependency hardening')
