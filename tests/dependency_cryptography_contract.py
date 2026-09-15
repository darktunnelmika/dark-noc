from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

root = Path(__file__).resolve().parents[1]
requirements = (root / "hub/requirements.txt").read_text().splitlines()
app = (root / "hub/app.py").read_text()

assert "cryptography==50.0.1" in requirements
assert "cryptography==44.0.0" not in requirements
assert "from cryptography.fernet import Fernet, InvalidToken" in app

key = Fernet.generate_key()
fernet = Fernet(key)
payload = b"dark-noc-cryptography-contract"
token = fernet.encrypt(payload)
assert fernet.decrypt(token) == payload

tampered = token[:-1] + bytes([token[-1] ^ 1])
try:
    fernet.decrypt(tampered)
except InvalidToken:
    pass
else:
    raise AssertionError("tampered Fernet token must be rejected")

print("cryptography dependency contract passed")
