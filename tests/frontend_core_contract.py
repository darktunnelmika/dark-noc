from pathlib import Path
core=Path("hub/static/core.js").read_text()
app=Path("hub/static/app.js").read_text()
index=Path("hub/static/index.html").read_text()
assert "window.DarkNocCore=Object.freeze" in core
assert "const { $, $$, esc, bytesPerSecond, fileSize, displayHost, relativeTime, duration, elapsedDuration } = window.DarkNocCore;" in app
assert "function esc(value)" not in app
assert "function bytesPerSecond(value)" not in app
assert index.index("/static/core.js?v=1.0.0") < index.index("/static/app.js?v=2.9.9")
print("Frontend core modularization contract passed")
