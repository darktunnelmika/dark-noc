from pathlib import Path

ROOT = Path('.')
OLD_VERSION = '2.9.38'
NEW_VERSION = '2.9.39'
OLD_PIN = 'cryptography==44.0.0'
NEW_PIN = 'cryptography==50.0.1'

requirements = ROOT / 'hub/requirements.txt'
text = requirements.read_text()
if text.count(OLD_PIN) != 1:
    raise RuntimeError(f'Expected exactly one {OLD_PIN} pin')
if 'cryptography==' in text.replace(OLD_PIN, ''):
    raise RuntimeError('Unexpected additional cryptography pin')
requirements.write_text(text.replace(OLD_PIN, NEW_PIN, 1))

contract = ROOT / 'tests/dependency_cryptography_contract.py'
contract.write_text('''from pathlib import Path\n\nfrom cryptography.fernet import Fernet, InvalidToken\n\nroot = Path(__file__).resolve().parents[1]\nrequirements = (root / "hub/requirements.txt").read_text().splitlines()\napp = (root / "hub/app.py").read_text()\n\nassert "cryptography==50.0.1" in requirements\nassert "cryptography==44.0.0" not in requirements\nassert "from cryptography.fernet import Fernet, InvalidToken" in app\n\nkey = Fernet.generate_key()\nfernet = Fernet(key)\npayload = b"dark-noc-cryptography-contract"\ntoken = fernet.encrypt(payload)\nassert fernet.decrypt(token) == payload\n\ntampered = token[:-1] + bytes([token[-1] ^ 1])\ntry:\n    fernet.decrypt(tampered)\nexcept InvalidToken:\n    pass\nelse:\n    raise AssertionError("tampered Fernet token must be rejected")\n\nprint("cryptography dependency contract passed")\n''')

version_paths = [
    ROOT / 'hub/app.py', ROOT / 'agent/agent.py', ROOT / 'darknoc',
    ROOT / 'install-hub.sh', ROOT / 'install-node.sh', ROOT / 'upgrade.sh',
    ROOT / 'hub/static/index.html',
]
version_paths += sorted((ROOT / 'tests').glob('*.py'))
for path in version_paths:
    data = path.read_text()
    if OLD_VERSION in data:
        path.write_text(data.replace(OLD_VERSION, NEW_VERSION))

release_notes = ROOT / 'RELEASE_NOTES.md'
release_notes.write_text('''# DARK NOC v2.9.39 — cryptography Dependency Hardening\n\n- Upgrade the Hub cryptography runtime from `cryptography==44.0.0` to `cryptography==50.0.1`.\n- Keep the dependency exactly pinned so CI and production installs resolve the same cryptographic implementation.\n- Validate the DARK NOC Fernet encrypt/decrypt path and tamper rejection under the new runtime.\n- Run the complete Hub/Agent regression suite and reproducible release build with no other dependency changes.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\nپکیج `cryptography` هاب از نسخه 44.0.0 به 50.0.1 ارتقا داده شد؛ مسیر Fernet، تشخیص داده دستکاری‌شده، کل تست‌ها و build تکرارپذیر بررسی می‌شوند و هیچ dependency دیگری تغییر نمی‌کند.\n\n---\n\n''' + release_notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.39\n- Dependency hardening: pin `cryptography==50.0.1` (from 44.0.0).\n- Add permanent Fernet round-trip/tamper-rejection dependency coverage.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n''' + changelog.read_text())

doc = ROOT / 'docs/dependency-hardening-v2.9.39.md'
doc.write_text('''# DARK NOC Dependency Hardening — v2.9.39\n\nThis release advances exactly one dependency: `cryptography` from 44.0.0 to 50.0.1.\n\nValidation scope:\n- exact production pin\n- Hub import compatibility\n- Fernet key generation, encryption and decryption\n- tampered-token rejection through `InvalidToken`\n- full Hub + Agent regression suite\n- reproducible release packaging\n\nNo other dependency is intentionally changed in this release.\n''')

print('prepared v2.9.39 cryptography hardening')
