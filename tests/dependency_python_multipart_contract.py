from importlib.metadata import version
from pathlib import Path

root = Path(__file__).resolve().parents[1]
requirements = (root / "hub/requirements.txt").read_text()
assert "python-multipart==0.0.32" in requirements
assert "python-multipart==0.0.20" not in requirements
assert version("python-multipart") == "0.0.32"

# FastAPI multipart/Form parsing remains exercised by smoke.py/file_ops.py; this
# contract additionally prevents an accidental pin rollback.
print("python-multipart dependency hardening contract passed")
