from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
index = (ROOT / "hub/static/index.html").read_text(encoding="utf-8")
css = (ROOT / "hub/static/mobile.css").read_text(encoding="utf-8")
runtime = (ROOT / "hub/static/mobile-runtime.js").read_text(encoding="utf-8")

assert '/static/mobile.css?v=2.9.49' in index
assert '/static/mobile-runtime.js?v=2.9.49' in index
assert index.index('/static/mobile.css?v=2.9.49') > index.index('/static/live-matrix.css')
assert index.index('/static/mobile-runtime.js?v=2.9.49') > index.index('/static/live-matrix.js')

for marker in [
    '@media (max-width: 850px)',
    '@media (max-width: 680px)',
    '@media (max-width: 460px)',
    '.mobile-nav-backdrop',
    '.sidebar.open',
    '.topbar',
    '.plugin-card',
    '#tunnel-rows .table-row',
    '.terminal-connectors',
    '.file-manager-nav',
    '.modal-backdrop',
    'env(safe-area-inset-bottom)',
    '--mobile-vh',
]:
    assert marker in css, marker

assert css.count('{') == css.count('}'), 'mobile.css braces are unbalanced'
assert "matchMedia('(max-width: 850px)')" in runtime
assert 'MutationObserver' in runtime
assert "sidebar.classList.remove('open')" in runtime
assert "document.body.classList.toggle('mobile-nav-open'" in runtime
assert "visualViewport" in runtime
assert "aria-expanded" in runtime
assert 'VERSION = "2.9.49"' in (ROOT / 'hub/app.py').read_text(encoding='utf-8')
print('Mobile responsive contract passed')
