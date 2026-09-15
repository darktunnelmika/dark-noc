from pathlib import Path

ROOT = Path('.')
OLD_VERSION = '2.9.37'
NEW_VERSION = '2.9.38'
OLD_MULTIPART = 'python-multipart==0.0.20'
NEW_MULTIPART = 'python-multipart==0.0.32'

requirements = ROOT / 'hub/requirements.txt'
req = requirements.read_text()
if OLD_MULTIPART not in req:
    raise RuntimeError(f'Expected dependency pin not found: {OLD_MULTIPART}')
if req.count(OLD_MULTIPART) != 1:
    raise RuntimeError('Unexpected python-multipart pin count')
requirements.write_text(req.replace(OLD_MULTIPART, NEW_MULTIPART, 1))

# Keep the product release version aligned across Hub, Agent, CLI/installers and contracts.
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

contract = ROOT / 'tests/dependency_python_multipart_contract.py'
contract.write_text('''from importlib.metadata import version\nfrom pathlib import Path\n\nroot = Path(__file__).resolve().parents[1]\nrequirements = (root / "hub/requirements.txt").read_text()\nassert "python-multipart==0.0.32" in requirements\nassert "python-multipart==0.0.20" not in requirements\nassert version("python-multipart") == "0.0.32"\n\n# FastAPI multipart/Form parsing remains exercised by smoke.py/file_ops.py; this\n# contract additionally prevents an accidental pin rollback.\nprint("python-multipart dependency hardening contract passed")\n''')

notes = ROOT / 'RELEASE_NOTES.md'
notes.write_text('''# DARK NOC v2.9.38 — python-multipart Dependency Hardening\n\n- Upgrade the Hub multipart parser from `python-multipart==0.0.20` to `python-multipart==0.0.32`.\n- Keep the dependency pinned exactly so production installs and CI resolve the same parser version.\n- Verify existing authenticated SSH multipart upload handling, request size enforcement, file operations, API integration and reproducible packaging with the full regression suite.\n- Add a permanent dependency contract preventing accidental rollback of the multipart parser pin.\n- No API, Agent, tunnel, frontend or Live Matrix behavior changes.\n\n## فارسی\n\n`python-multipart` هاب از نسخه 0.0.20 به 0.0.32 ارتقا داده شد. تمام تست‌های Upload/SSH، محدودیت حجم، API و build تکرارپذیر بدون تغییر رفتاری باید پاس شوند.\n\n---\n\n''' + notes.read_text())

changelog = ROOT / 'CHANGELOG.md'
changelog.write_text('''## v2.9.38\n- Dependency hardening: pin `python-multipart` at 0.0.32 instead of 0.0.20.\n- Add permanent dependency pin regression coverage.\n- Preserve multipart upload/auth/size-limit behavior and all existing UI/API contracts.\n\n''' + changelog.read_text())

(ROOT / 'docs/dependency-hardening-v2.9.38.md').write_text('''# DARK NOC Dependency Hardening — v2.9.38\n\nThis release starts the one-dependency-at-a-time hardening wave.\n\n## Changed\n- `python-multipart`: `0.0.20` → `0.0.32`\n\n## Validation boundary\n- Hub requirements remain exact pins.\n- Existing multipart upload authentication and body-size protection are unchanged.\n- Existing SSH upload/file regression tests remain authoritative for behavior.\n- Full Hub/Agent regression suite and reproducible release build are required before merge.\n\nNo other dependency is changed in this release.\n''')

print('prepared v2.9.38 python-multipart hardening')
