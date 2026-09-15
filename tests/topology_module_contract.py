from pathlib import Path
app=Path("hub/static/app.js").read_text(); topology=Path("hub/static/topology.js").read_text(); index=Path("hub/static/index.html").read_text()
assert "window.DarkNocTopology.buildLinks" in app
assert "function inferSide" not in app
assert "function buildLinks" in topology
assert "window.DarkNocTopology = Object.freeze" in topology
assert index.index("core.js") < index.index("topology.js") < index.index("app.js")
print("Topology module boundary passed")
