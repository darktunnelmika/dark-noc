from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# Load the mobile layer after the existing desktop + Live Matrix CSS and JS.
index = read("hub/static/index.html")
index = replace_once(
    index,
    '  <link rel="stylesheet" href="/static/live-matrix.css?v=1.0.0" />\n',
    '  <link rel="stylesheet" href="/static/live-matrix.css?v=1.0.0" />\n  <link rel="stylesheet" href="/static/mobile.css?v=2.9.47" />\n',
    "mobile stylesheet hook",
)
index = replace_once(
    index,
    '  <script src="/static/live-matrix.js?v=1.0.0"></script>\n',
    '  <script src="/static/live-matrix.js?v=1.0.0"></script>\n  <script src="/static/mobile-runtime.js?v=2.9.47"></script>\n',
    "mobile runtime hook",
)
index = index.replace('/static/app.js?v=2.9.46', '/static/app.js?v=2.9.47')
write("hub/static/index.html", index)

# Runtime/release version markers.
for path in ["hub/app.py", "agent/agent.py", "darknoc", "install-hub.sh", "install-node.sh", "upgrade.sh"]:
    text = read(path)
    if "2.9.46" not in text:
        raise SystemExit(f"{path}: v2.9.46 marker missing")
    write(path, text.replace("2.9.46", "2.9.47"))

# Current-version tests track the active release marker.
for path in sorted((ROOT / "tests").glob("*.py")):
    text = path.read_text(encoding="utf-8")
    if "2.9.46" in text:
        path.write_text(text.replace("2.9.46", "2.9.47"), encoding="utf-8")

changelog = read("CHANGELOG.md")
if not changelog.startswith("## v2.9.47"):
    changelog = '''## v2.9.47
- Rebuild the phone layout as a production responsive surface instead of a reduced desktop layout.
- Add a safe mobile navigation drawer with backdrop, Escape/outside-tap close behavior and iPhone safe-area support.
- Make Plugins, Fleet Operations, Servers, Incidents, SSH Terminal and File Manager adapt cleanly to narrow screens.
- Convert the tunnel table into readable mobile cards while keeping the desktop table unchanged.
- Make modals/forms mobile bottom sheets with viewport-aware height and safe bottom spacing.
- Compact the Live Tunnel Matrix for phones without changing its desktop visual style or route logic.
- Add a permanent mobile responsive regression contract.

''' + changelog
write("CHANGELOG.md", changelog)

notes = read("RELEASE_NOTES.md")
if not notes.startswith("# DARK NOC v2.9.47"):
    notes = '''# DARK NOC v2.9.47 — Mobile Command UI

- Mobile now has a dedicated responsive layer rather than inheriting desktop-only layouts.
- The sidebar becomes a safe navigation drawer with backdrop, body scroll lock, Escape/outside-tap close and iOS safe-area handling.
- Plugin cards, deployments, Fleet Operations, server cards, incidents, SSH Terminal and File Manager are usable at phone widths.
- Tunnel rows become compact mobile cards with labels for method, latency, loss, throughput, sessions and status.
- Forms and dialogs become viewport-aware bottom sheets so controls remain reachable above the iPhone home indicator and mobile browser UI.
- The Live Tunnel Matrix keeps the existing DARK NOC visual language while using smaller nodes and mobile-friendly controls.

## فارسی

حالت موبایل پنل بازطراحی شد. منوی کناری روی گوشی به Drawer واقعی تبدیل شده، کارت‌ها و فرم‌ها تک‌ستونه و قابل لمس شده‌اند، جدول تونل به کارت موبایلی تبدیل می‌شود و Terminal/File Manager/Plugins/Fleet/Modalها روی نمایشگر کوچک بدون به‌هم‌ریختگی قابل استفاده هستند. طراحی دسکتاپ دست‌نخورده باقی می‌ماند.

---

''' + notes
write("RELEASE_NOTES.md", notes)

print("Prepared DARK NOC v2.9.47 Mobile Command UI")
