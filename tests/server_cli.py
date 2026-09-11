import base64
import hashlib
import os
import shlex
import sqlite3
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "darknoc"


def password_hash(password: str, salt: bytes = b"0123456789abcdef") -> str:
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify(password: str, stored: str) -> bool:
    _, salt, expected = stored.split("$", 2)
    digest = hashlib.scrypt(
        password.encode(), salt=base64.urlsafe_b64decode(salt), n=2**14, r=8, p=1, dklen=32
    )
    return base64.urlsafe_b64encode(digest).decode() == expected


with tempfile.TemporaryDirectory(prefix="darknoc-cli-") as temporary:
    root = Path(temporary)
    hub_dir = root / "hub"
    data_dir = root / "data"
    hub_dir.mkdir()
    data_dir.mkdir()
    env_file = root / "hub.env"
    database = data_dir / "dark-noc.db"
    original_password = "Original-Password-1234"
    values = {
        "DARK_NOC_ADMIN_USER": "admin",
        "DARK_NOC_ADMIN_PASSWORD": original_password,
        "DARK_NOC_DATA": str(data_dir),
        "DARK_NOC_PUBLIC_HOST": "noc.example.test",
        "DARK_NOC_PUBLIC_PORT": "9090",
        "DARK_NOC_HUB_PORT": "19090",
        "DARK_NOC_PANEL_CERT_MODE": "selfsigned",
    }
    env_file.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()))
    env_file.chmod(0o600)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
              id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'owner', created_at INTEGER NOT NULL
            );
            CREATE TABLE sessions (
              token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL,
              ip TEXT, created_at INTEGER NOT NULL
            );
            CREATE TABLE audit_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
              target TEXT, detail TEXT, ip TEXT, created_at INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(1,?,?,?,1)",
            ("admin", password_hash(original_password), "owner"),
        )
        connection.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES('session',1,9999999999,1)"
        )

    process_env = os.environ.copy()
    process_env.update({"DARK_NOC_ENV_FILE": str(env_file), "DARK_NOC_HUB_DIR": str(hub_dir)})

    credentials = subprocess.run(
        [str(CLI), "credentials"], env=process_env, text=True, capture_output=True, check=True
    ).stdout
    assert "Username: admin" in credentials
    assert f"Password: {original_password}" in credentials

    subprocess.run(
        [str(CLI), "set-username", "noc-owner"], env=process_env, text=True, capture_output=True, check=True
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT username FROM users WHERE id=1").fetchone()[0] == "noc-owner"
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0

    reset = subprocess.run(
        [str(CLI), "reset-password"],
        env=process_env,
        input="\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    generated_line = next(line for line in reset.splitlines() if line.startswith("New password: "))
    generated_password = generated_line.split(": ", 1)[1]
    assert len(generated_password) >= 20
    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[0]
        assert verify(generated_password, stored)
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 2

    refreshed = subprocess.run(
        [str(CLI), "credentials"], env=process_env, text=True, capture_output=True, check=True
    ).stdout
    assert "Username: noc-owner" in refreshed
    assert f"Password: {generated_password}" in refreshed

    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    for name, body in {
        "nginx": "#!/usr/bin/env bash\nexit 0\n",
        "systemctl": "#!/usr/bin/env bash\nexit 0\n",
        "ss": "#!/usr/bin/env bash\nexit 0\n",
        "curl": "#!/usr/bin/env bash\nexit 0\n",
        "ufw": "#!/usr/bin/env bash\n[[ ${1:-} == status ]] && echo 'Status: inactive'\nexit 0\n",
        "certbot": """#!/usr/bin/env bash
domain=''
while [[ $# -gt 0 ]]; do
  if [[ $1 == -d ]]; then domain=$2; shift 2; else shift; fi
done
if [[ -n $domain ]]; then
  mkdir -p "$DARK_NOC_LETSENCRYPT_LIVE_ROOT/$domain"
  cp -L "$DARK_NOC_PANEL_CERT_DIR/panel.crt" "$DARK_NOC_LETSENCRYPT_LIVE_ROOT/$domain/fullchain.pem"
  cp -L "$DARK_NOC_PANEL_CERT_DIR/panel.key" "$DARK_NOC_LETSENCRYPT_LIVE_ROOT/$domain/privkey.pem"
fi
exit 0
""",
    }.items():
        command = fake_bin / name
        command.write_text(body)
        command.chmod(0o755)
    nginx_site = root / "nginx" / "sites-available" / "dark-noc"
    nginx_link = root / "nginx" / "sites-enabled" / "dark-noc"
    cert_dir = root / "certs"
    acme_root = root / "acme"
    local_agent = root / "agent.json"
    letsencrypt_live = root / "letsencrypt" / "live"
    letsencrypt_hooks = root / "letsencrypt" / "hooks"
    local_agent.write_text('{"hub_url":"http://127.0.0.1:19090"}\n')
    process_env.update(
        {
            "PATH": f"{fake_bin}:{process_env['PATH']}",
            "DARK_NOC_NGINX_SITE": str(nginx_site),
            "DARK_NOC_NGINX_LINK": str(nginx_link),
            "DARK_NOC_PANEL_CERT_DIR": str(cert_dir),
            "DARK_NOC_ACME_ROOT": str(acme_root),
            "DARK_NOC_LOCAL_AGENT_CONFIG": str(local_agent),
            "DARK_NOC_CERT_GROUP": "root",
            "DARK_NOC_LETSENCRYPT_LIVE_ROOT": str(letsencrypt_live),
            "DARK_NOC_LETSENCRYPT_HOOK_DIR": str(letsencrypt_hooks),
        }
    )
    subprocess.run(
        [str(CLI), "set-port", "9443"], env=process_env, text=True, capture_output=True, check=True
    )
    site = nginx_site.read_text()
    assert "listen 9443 ssl http2;" in site
    assert "proxy_pass http://127.0.0.1:19090;" in site
    assert "return 301 https://noc.example.test:9443$request_uri;" in site
    assert "__" not in site

    # A requested public port which equals the old private backend must move
    # the backend first, then update the local Agent loopback URL.
    subprocess.run(
        [str(CLI), "set-port", "19090"], env=process_env, text=True, capture_output=True, check=True
    )
    site = nginx_site.read_text()
    assert "listen 19090 ssl http2;" in site
    assert "proxy_pass http://127.0.0.1:19090;" not in site
    updated_agent = __import__("json").loads(local_agent.read_text())
    assert updated_agent["hub_url"].startswith("http://127.0.0.1:")
    assert updated_agent["hub_url"] != "http://127.0.0.1:19090"

    subprocess.run(
        [str(CLI), "set-domain", "panel.example.test"],
        env=process_env,
        input="n\n",
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [str(CLI), "issue-ssl"], env=process_env, text=True, capture_output=True, check=True
    )
    assert (cert_dir / "panel.crt").is_symlink()
    assert (cert_dir / "panel.crt").resolve() == letsencrypt_live / "panel.example.test" / "fullchain.pem"
    assert (letsencrypt_hooks / "dark-noc-reload").stat().st_mode & 0o111
    assert "server_name panel.example.test;" in nginx_site.read_text()

print("DARK NOC server CLI regression tests passed")
