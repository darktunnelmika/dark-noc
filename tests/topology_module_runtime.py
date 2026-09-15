from pathlib import Path

# Structural regression: topology model must remain isolated from app.js and load before it.
root = Path(__file__).resolve().parents[1]
app = (root / 'hub' / 'static' / 'app.js').read_text()
topology = (root / 'hub' / 'static' / 'topology.js').read_text()
index = (root / 'hub' / 'static' / 'index.html').read_text()

assert 'window.DarkNocTopology.buildLinks' in app
assert 'function inferSide' not in app
assert 'function buildLinks' in topology
assert 'DARK Realm Pro' in topology and 'DARK Packet Pro' in topology and 'DARK Backhaul' in topology
assert index.index('core.js') < index.index('topology.js') < index.index('app.js')
print('Topology module runtime contract passed')
