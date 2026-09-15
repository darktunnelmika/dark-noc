from importlib.metadata import version
from pathlib import Path

import asyncssh

root = Path(__file__).resolve().parents[1]
requirements = (root / "hub/requirements.txt").read_text().splitlines()
app = (root / "hub/app.py").read_text()
file_router = (root / "hub/ssh_file_router.py").read_text()
terminal_router = (root / "hub/ssh_terminal_router.py").read_text()

assert "asyncssh==2.24.0" in requirements
assert "asyncssh==2.19.0" not in requirements
assert version("asyncssh") == "2.24.0"

# Lock the AsyncSSH API surface DARK NOC depends on for provisioning,
# interactive terminals, host-key pinning and SFTP file operations.
for name in [
    "connect", "import_private_key", "generate_private_key", "SSHClient",
    "SFTPAttrs", "SFTPNoSuchFile", "SFTPOpUnsupported",
]:
    assert hasattr(asyncssh, name), name

attrs = asyncssh.SFTPAttrs(permissions=0o600)
assert attrs.permissions == 0o600
key = asyncssh.generate_private_key("ssh-ed25519")
encoded = key.export_private_key("openssh")
loaded = asyncssh.import_private_key(encoded)
assert loaded.get_algorithm() == key.get_algorithm()

for marker in [
    "class PinnedSSHClient(asyncssh.SSHClient)",
    "asyncssh.import_private_key(key)",
    "async with asyncssh.connect(",
    "asyncssh.SFTPAttrs(permissions=0o600)",
    "except asyncssh.SFTPNoSuchFile",
    "except asyncssh.SFTPOpUnsupported",
]:
    assert marker in app or marker in file_router or marker in terminal_router, marker

print("AsyncSSH dependency hardening contract passed")
