import asyncio
import importlib.util
import io
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def load_hub_app() -> Any:
    data_dir = tempfile.TemporaryDirectory(prefix="dark-noc-file-ops-")
    os.environ["DARK_NOC_DATA"] = data_dir.name
    os.environ["DARK_NOC_ADMIN_PASSWORD"] = "FileOps-TestPassword-1234"
    module_name = "dark_noc_file_ops_app"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "hub" / "app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module._file_ops_test_data_dir = data_dir
    return module


HUB = load_hub_app()


class FakeSFTP:
    def __init__(
        self,
        files: dict[str, tuple[bytes, int]],
        fail_rename: tuple[str, str] | None = None,
        posix_error: Exception | None = None,
    ):
        self.files = {
            path: {"data": data, "permissions": permissions}
            for path, (data, permissions) in files.items()
        }
        self.fail_rename = fail_rename
        self.posix_error = posix_error
        self.renames: list[tuple[str, str]] = []
        self.posix_renames: list[tuple[str, str]] = []
        self.opened_write_paths: list[str] = []
        self.removed_paths: list[str] = []
        self.write_chunks: list[bytes] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def stat(self, path: str) -> SimpleNamespace:
        if path not in self.files:
            raise HUB.asyncssh.SFTPNoSuchFile("No such file")
        return SimpleNamespace(
            permissions=self.files[path]["permissions"],
            size=len(self.files[path]["data"]),
            mtime=1_700_000_000,
        )

    async def lstat(self, path: str) -> SimpleNamespace:
        return await self.stat(path)

    async def scandir(self, directory: str):
        prefix = directory.rstrip("/") + "/"
        for path in sorted(self.files):
            if not path.startswith(prefix):
                continue
            name = path[len(prefix):]
            if not name or "/" in name:
                continue
            yield SimpleNamespace(filename=name, attrs=await self.stat(path))

    def open(self, path: str, mode: str, *, attrs: Any | None = None):
        if mode == "rb":
            if path not in self.files:
                raise HUB.asyncssh.SFTPNoSuchFile("No such file")
            return FakeSFTPFile(self, path, mode)
        if mode in {"wb", "xb"}:
            if mode == "xb" and path in self.files:
                raise FileExistsError(path)
            permissions = getattr(attrs, "permissions", None)
            permissions = 0o600 if permissions is None else stat.S_IMODE(permissions)
            self.files[path] = {"data": b"", "permissions": stat.S_IFREG | permissions}
            self.opened_write_paths.append(path)
            return FakeSFTPFile(self, path, mode)
        raise AssertionError(f"unexpected SFTP open mode: {mode}")

    async def chmod(self, path: str, permissions: int) -> None:
        if path not in self.files:
            raise HUB.asyncssh.SFTPNoSuchFile("No such file")
        file_type = stat.S_IFMT(self.files[path]["permissions"])
        self.files[path]["permissions"] = file_type | permissions

    async def mkdir(self, path: str, *, attrs: Any | None = None) -> None:
        if path in self.files:
            raise FileExistsError(path)
        permissions = getattr(attrs, "permissions", 0o750)
        self.files[path] = {"data": b"", "permissions": stat.S_IFDIR | stat.S_IMODE(permissions)}

    async def rmdir(self, path: str) -> None:
        if any(item.startswith(path.rstrip("/") + "/") for item in self.files):
            raise HUB.asyncssh.SFTPFailure("Directory is not empty")
        await self.remove(path)

    async def rename(self, source: str, destination: str) -> None:
        self.renames.append((source, destination))
        if self.fail_rename == (source, destination):
            self.fail_rename = None
            raise RuntimeError("simulated rename failure")
        if source not in self.files:
            raise HUB.asyncssh.SFTPNoSuchFile("No such file")
        if destination in self.files:
            raise RuntimeError("destination already exists")
        self.files[destination] = self.files.pop(source)

    async def posix_rename(self, source: str, destination: str) -> None:
        self.posix_renames.append((source, destination))
        if self.posix_error:
            raise self.posix_error
        if source not in self.files:
            raise HUB.asyncssh.SFTPNoSuchFile("No such file")
        self.files[destination] = self.files.pop(source)

    async def remove(self, path: str) -> None:
        if path not in self.files:
            raise HUB.asyncssh.SFTPNoSuchFile("No such file")
        self.removed_paths.append(path)
        del self.files[path]


class FakeSFTPFile:
    def __init__(self, sftp: FakeSFTP, path: str, mode: str):
        self.sftp = sftp
        self.path = path
        self.mode = mode
        self.offset = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def read(self, size: int) -> bytes:
        assert self.mode == "rb"
        data = self.sftp.files[self.path]["data"]
        chunk = data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    async def write(self, chunk: bytes) -> None:
        assert self.mode in {"wb", "xb"}
        copied = bytes(chunk)
        self.sftp.write_chunks.append(copied)
        self.sftp.files[self.path]["data"] += copied


class FakeSSH:
    def __init__(self, sftp: FakeSFTP):
        self.sftp = sftp

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def start_sftp_client(self) -> FakeSFTP:
        return self.sftp


class FakeConnectRouter:
    def __init__(self, hosts: dict[str, FakeSFTP]):
        self.hosts = hosts
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **options: Any) -> FakeSSH:
        self.calls.append(options)
        host = options["host"]
        assert host in self.hosts, f"unexpected mocked SSH host: {host}"
        return FakeSSH(self.hosts[host])


class FakeRequest:
    client = SimpleNamespace(host="testclient")

    async def is_disconnected(self) -> bool:
        return False


class FakeUpload:
    def __init__(self, filename: str, payload: bytes, *, size: int | None = None):
        self.filename = filename
        self.size = size
        self._stream = io.BytesIO(payload)
        self.closed = False

    async def read(self, size: int) -> bytes:
        return self._stream.read(size)

    async def close(self) -> None:
        self.closed = True


class AsyncGate:
    def __init__(self):
        self.acquired = False

    async def acquire(self) -> bool:
        assert not self.acquired
        self.acquired = True
        return True

    def release(self) -> None:
        assert self.acquired
        self.acquired = False

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.release()
        return None


def assert_rejected_path(value: str, *, filename: str | None = None) -> None:
    try:
        HUB.clean_remote_path(value, filename=filename)
    except ValueError:
        return
    raise AssertionError(f"unsafe remote path was accepted: {value!r}")


def test_clean_remote_path() -> None:
    assert HUB.clean_remote_path("/tmp/../root/archive.tar") == "/root/archive.tar"
    assert HUB.clean_remote_path("/var/tmp/", filename="report.txt") == "/var/tmp/report.txt"

    for path in (
        "relative/file",
        "/",
        " /root/file",
        "/root/file ",
        "/root/new\nline",
        "/root/tab\tname",
        "/root/delete\x7fname",
        "/root/control\x80name",
        "/" + ("a" * 4096),
    ):
        assert_rejected_path(path)

    assert_rejected_path("/var/tmp/", filename="bad\nname")
    assert_rejected_path("/var/tmp/", filename="x" * 4092)


async def test_finalize_sftp_file() -> None:
    regular_0600 = stat.S_IFREG | 0o600
    regular_0640 = stat.S_IFREG | 0o640

    no_overwrite = FakeSFTP({
        "/upload.part": (b"new", regular_0600),
        "/target.bin": (b"old", regular_0640),
    })
    try:
        await HUB.finalize_sftp_file(no_overwrite, "/upload.part", "/target.bin", False)
    except HUB.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("an existing destination was overwritten without permission")
    assert no_overwrite.files["/target.bin"]["data"] == b"old"
    assert no_overwrite.files["/upload.part"]["data"] == b"new"
    assert no_overwrite.renames == []

    overwrite = FakeSFTP({
        "/upload.part": (b"new", regular_0600),
        "/target.bin": (b"old", regular_0640),
    })
    await HUB.finalize_sftp_file(overwrite, "/upload.part", "/target.bin", True)
    assert overwrite.files["/target.bin"]["data"] == b"new"
    assert stat.S_IMODE(overwrite.files["/target.bin"]["permissions"]) == 0o640
    assert "/upload.part" not in overwrite.files
    assert not any(path.endswith(".bak") for path in overwrite.files)
    assert overwrite.posix_renames == [("/upload.part", "/target.bin")]

    rollback = FakeSFTP(
        {
            "/upload.part": (b"new", regular_0600),
            "/target.bin": (b"old", regular_0640),
        },
        fail_rename=("/upload.part", "/target.bin"),
        posix_error=HUB.asyncssh.SFTPOpUnsupported("unsupported in fallback test"),
    )
    try:
        await HUB.finalize_sftp_file(rollback, "/upload.part", "/target.bin", True)
    except RuntimeError as exc:
        assert str(exc) == "simulated rename failure"
    else:
        raise AssertionError("the simulated final rename failure was hidden")
    assert rollback.files["/target.bin"]["data"] == b"old"
    assert rollback.files["/upload.part"]["data"] == b"new"
    assert not any(path.endswith(".bak") for path in rollback.files)

    atomic_failure = FakeSFTP(
        {
            "/upload.part": (b"new", regular_0600),
            "/target.bin": (b"old", regular_0640),
        },
        posix_error=HUB.asyncssh.SFTPFailure("simulated atomic rename failure"),
    )
    try:
        await HUB.finalize_sftp_file(atomic_failure, "/upload.part", "/target.bin", True)
    except HUB.asyncssh.SFTPFailure:
        pass
    else:
        raise AssertionError("a real POSIX rename failure incorrectly entered the fallback path")
    assert atomic_failure.files["/target.bin"]["data"] == b"old"
    assert atomic_failure.files["/upload.part"]["data"] == b"new"
    assert atomic_failure.renames == []


def insert_ssh_node(name: str, host: str, role: str) -> int:
    now = HUB.utc_ts()
    with HUB.db() as conn:
        cursor = conn.execute(
            """INSERT INTO nodes(
                   name,region,role,host,ssh_port,ssh_user,ssh_password_enc,
                   status,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                "File operation tests",
                role,
                host,
                22,
                "root",
                HUB.encrypt("mock-password"),
                "online",
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def test_ssh_file_endpoints() -> None:
    directory = stat.S_IFDIR | 0o755
    regular_0644 = stat.S_IFREG | 0o644
    upload_sftp = FakeSFTP({
        "/srv": (b"", directory),
        "/srv/uploads": (b"", directory),
        "/srv/uploads/protected.bin": (b"must stay", regular_0644),
    })
    relay_payload = b"server-to-server relay payload with multiple chunks"
    source_sftp = FakeSFTP({
        "/data": (b"", directory),
        "/data/source.bin": (relay_payload, regular_0644),
    })
    destination_sftp = FakeSFTP({
        "/archive": (b"", directory),
    })
    connector = FakeConnectRouter({
        "upload.test": upload_sftp,
        "source.test": source_sftp,
        "destination.test": destination_sftp,
    })

    with (
        patch.object(HUB.asyncssh, "connect", connector),
        patch.object(HUB, "SSH_FILE_CHUNK", 7),
        patch.object(HUB, "SSH_TRANSFER_SEMAPHORE", AsyncGate()),
        TestClient(HUB.app, base_url="https://testserver") as client,
    ):
        upload_node_id = insert_ssh_node("FILE-UPLOAD", "upload.test", "edge")
        source_node_id = insert_ssh_node("FILE-SOURCE", "source.test", "edge")
        destination_node_id = insert_ssh_node("FILE-DESTINATION", "destination.test", "exit")

        # Upload authentication is checked by middleware before multipart
        # parsing reaches the endpoint and therefore before any SSH attempt.
        unauthenticated = client.post(
            f"/api/ssh/upload/{upload_node_id}",
            data={"remote_path": "/srv/uploads/"},
            files={"file": ("unauthenticated.bin", b"must-not-connect")},
        )
        assert unauthenticated.status_code == 401
        assert connector.calls == [], "unauthenticated upload attempted an SSH connection"

        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "FileOps-TestPassword-1234"},
        )
        assert login.status_code == 200

        upload_payload = b"exact browser upload bytes across chunks"
        uploaded = client.post(
            f"/api/ssh/upload/{upload_node_id}",
            data={"remote_path": "/srv/uploads/", "overwrite": "false"},
            files={"file": ("payload.bin", upload_payload, "application/octet-stream")},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json() == {
            "ok": True,
            "node": "FILE-UPLOAD",
            "path": "/srv/uploads/payload.bin",
            "bytes": len(upload_payload),
        }
        assert upload_sftp.files["/srv/uploads/payload.bin"]["data"] == upload_payload
        assert all(0 < len(chunk) <= HUB.SSH_FILE_CHUNK for chunk in upload_sftp.write_chunks)
        assert not any(".darknoc-" in path for path in upload_sftp.files)

        listed = client.get(f"/api/ssh/files/{upload_node_id}", params={"path": "/srv/uploads"})
        assert listed.status_code == 200, listed.text
        assert listed.json()["entries"][0]["name"] == "payload.bin"
        assert listed.json()["entries"][0]["mode"] == "0600"

        opened = client.get(
            f"/api/ssh/files/{upload_node_id}/read", params={"path": "/srv/uploads/payload.bin"}
        )
        assert opened.status_code == 200 and opened.json()["content"] == upload_payload.decode()
        saved = client.put(f"/api/ssh/files/{upload_node_id}/write", json={
            "path": "/srv/uploads/payload.bin", "content": "edited remotely", "overwrite": True,
        })
        assert saved.status_code == 200, saved.text
        assert upload_sftp.files["/srv/uploads/payload.bin"]["data"] == b"edited remotely"
        checksum = client.get(
            f"/api/ssh/files/{upload_node_id}/checksum", params={"path": "/srv/uploads/payload.bin"}
        )
        assert checksum.status_code == 200
        assert checksum.json()["sha256"] == "4f87b3ee0bda1c04714f752e9394c24bf9a30cc60e44deefa535b0198b5ae789"
        downloaded = client.get(
            f"/api/ssh/files/{upload_node_id}/download", params={"path": "/srv/uploads/payload.bin"}
        )
        assert downloaded.status_code == 200 and downloaded.content == b"edited remotely"
        assert 'filename="payload.bin"' in downloaded.headers["content-disposition"]

        made = client.post(f"/api/ssh/files/{upload_node_id}/action", json={
            "action": "mkdir", "path": "/srv/uploads/newdir",
        })
        assert made.status_code == 200
        renamed = client.post(f"/api/ssh/files/{upload_node_id}/action", json={
            "action": "rename", "path": "/srv/uploads/payload.bin",
            "destination": "/srv/uploads/renamed.bin",
        })
        assert renamed.status_code == 200
        changed_mode = client.post(f"/api/ssh/files/{upload_node_id}/action", json={
            "action": "chmod", "path": "/srv/uploads/renamed.bin", "mode": "0600",
        })
        assert changed_mode.status_code == 200
        assert stat.S_IMODE(upload_sftp.files["/srv/uploads/renamed.bin"]["permissions"]) == 0o600
        deleted = client.post(f"/api/ssh/files/{upload_node_id}/action", json={
            "action": "delete", "path": "/srv/uploads/renamed.bin",
        })
        assert deleted.status_code == 200 and "/srv/uploads/renamed.bin" not in upload_sftp.files
        assert client.post(f"/api/ssh/files/{upload_node_id}/action", json={
            "action": "delete", "path": "/etc",
        }).status_code == 400

        protected = client.post(
            f"/api/ssh/upload/{upload_node_id}",
            data={"remote_path": "/srv/uploads/protected.bin", "overwrite": "false"},
            files={"file": ("replacement.bin", b"must not replace")},
        )
        assert protected.status_code == 409, protected.text
        assert upload_sftp.files["/srv/uploads/protected.bin"]["data"] == b"must stay"
        assert not any(".darknoc-" in path for path in upload_sftp.files)

        relayed = client.post(
            "/api/ssh/relay",
            json={
                "source_node_id": source_node_id,
                "destination_node_id": destination_node_id,
                "source_path": "/data/source.bin",
                "destination_path": "/archive/copied.bin",
                "overwrite": False,
            },
        )
        assert relayed.status_code == 200, relayed.text
        assert relayed.json() == {
            "ok": True,
            "source_node": "FILE-SOURCE",
            "destination_node": "FILE-DESTINATION",
            "source_path": "/data/source.bin",
            "destination_path": "/archive/copied.bin",
            "bytes": len(relay_payload),
        }
        assert source_sftp.files["/data/source.bin"]["data"] == relay_payload
        assert destination_sftp.files["/archive/copied.bin"]["data"] == relay_payload
        assert stat.S_IMODE(destination_sftp.files["/archive/copied.bin"]["permissions"]) == 0o644
        assert destination_sftp.write_chunks
        assert all(0 < len(chunk) <= HUB.SSH_FILE_CHUNK for chunk in destination_sftp.write_chunks)
        assert not any(".darknoc-" in path for path in destination_sftp.files)


async def test_oversize_upload_cleanup() -> None:
    directory = stat.S_IFDIR | 0o755
    upload_sftp = FakeSFTP({
        "/srv": (b"", directory),
        "/srv/uploads": (b"", directory),
    })
    connector = FakeConnectRouter({"upload.test": upload_sftp})
    upload = FakeUpload("oversize.bin", b"123456789", size=None)
    with HUB.db() as conn:
        node_id = int(conn.execute("SELECT id FROM nodes WHERE name='FILE-UPLOAD'").fetchone()["id"])
        user = conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    assert user is not None

    with (
        patch.object(HUB.asyncssh, "connect", connector),
        patch.object(HUB, "SSH_UPLOAD_LIMIT", 6),
        patch.object(HUB, "SSH_FILE_CHUNK", 4),
        patch.object(HUB, "SSH_TRANSFER_SEMAPHORE", AsyncGate()),
    ):
        try:
            await HUB.ssh_upload_file(
                node_id,
                FakeRequest(),
                remote_path="/srv/uploads/oversize.bin",
                overwrite=False,
                file=upload,
                user=user,
            )
        except HUB.HTTPException as exc:
            assert exc.status_code == 413
        else:
            raise AssertionError("an upload larger than the streaming limit succeeded")

    assert upload.closed
    assert connector.calls, "oversize streaming test did not reach mocked SSH"
    assert upload_sftp.opened_write_paths, "oversize streaming test did not create a staged file"
    temporary = upload_sftp.opened_write_paths[0]
    assert temporary in upload_sftp.removed_paths
    assert temporary not in upload_sftp.files
    assert "/srv/uploads/oversize.bin" not in upload_sftp.files
    assert not any(".darknoc-" in path for path in upload_sftp.files)


def nginx_location(source: str, route: str) -> str:
    match = re.search(
        rf"location\s+\^~\s+{re.escape(route)}\s*\{{(?P<body>.*?)^\s*\}}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"Nginx location {route} is missing"
    return match.group("body")


def test_installer_and_release_guards() -> None:
    installer = (ROOT / "install-hub.sh").read_text(encoding="utf-8")
    server_cli = (ROOT / "darknoc").read_text(encoding="utf-8")
    upload_location = nginx_location(server_cli, "/api/ssh/upload/")
    upload_location_match = re.search(r"location\s+\^~\s+/api/ssh/upload/\s*\{", server_cli)
    assert upload_location_match
    upload_location_start = upload_location_match.start()
    assert "client_max_body_size 1m;" in server_cli[:upload_location_start]
    assert "client_max_body_size __UPLOAD_LIMIT__m;" in upload_location
    assert "proxy_request_buffering off;" in upload_location
    for directive in (
        "proxy_connect_timeout 86400;",
        "proxy_send_timeout 86400;",
        "proxy_read_timeout 86400;",
        "send_timeout 86400;",
    ):
        assert directive in upload_location
    compact_cli = re.sub(r"\s+", "", server_cli)
    assert 'upload_limit=$((10#$upload_limit+1))' in compact_cli
    assert re.search(
        r"validate_integer_setting\s+DARK_NOC_SSH_UPLOAD_LIMIT_MB\s+[^\n]+\s+1\s+102400",
        installer,
    )
    assert re.search(
        r"validate_integer_setting\s+DARK_NOC_SSH_RELAY_LIMIT_MB\s+[^\n]+\s+1\s+102400",
        installer,
    )
    assert re.search(r'install\s+-d\s+-m\s+0710\s+-o\s+root\s+-g\s+"\$CERT_GROUP"\s+"\$CERT_DIR"', server_cli)
    assert re.search(r'chown\s+"root:\$CERT_GROUP"\s+"\$cert_tmp"', server_cli)
    assert re.search(r'chown\s+root:root\s+"\$key_tmp"', server_cli)
    assert re.search(r'chmod\s+0640\s+"\$cert_tmp"', server_cli)
    assert re.search(r'chmod\s+0600\s+"\$key_tmp"', server_cli)
    assert re.search(r"DARK_NOC_SSH_UPLOAD_LIMIT_MB=%q", installer)
    assert re.search(r"DARK_NOC_SSH_RELAY_LIMIT_MB=%q", installer)
    ssh_tuning = {
        "DARK_NOC_SSH_TRANSFER_CONCURRENCY": (1, 32),
        "DARK_NOC_SSH_TRANSFER_TIMEOUT_SECONDS": (30, 86400),
        "DARK_NOC_SSH_IDLE_TIMEOUT_SECONDS": (5, 3600),
        "DARK_NOC_SSH_KEEPALIVE_INTERVAL_SECONDS": (1, 300),
        "DARK_NOC_SSH_KEEPALIVE_COUNT_MAX": (1, 20),
        "DARK_NOC_SSH_UPLOAD_QUEUE_TIMEOUT_SECONDS": (1, 300),
        "DARK_NOC_PROVISION_TIMEOUT_SECONDS": (60, 7200),
        "DARK_NOC_PROVISION_CONCURRENCY": (1, 32),
        "DARK_NOC_SSH_EDITOR_LIMIT_KB": (16, 16384),
        "DARK_NOC_MONITOR_RETENTION_DAYS": (7, 730),
        "DARK_NOC_METRIC_RETENTION_DAYS": (1, 365),
        "DARK_NOC_ROLLUP_RETENTION_DAYS": (30, 3650),
        "DARK_NOC_HUB_LEASE_SECONDS": (30, 300),
    }
    for setting, (minimum, maximum) in ssh_tuning.items():
        assert f"{setting}=%q" in installer
        assert re.search(
            rf"validate_integer_setting\s+{setting}\s+[^\n]+\s+{minimum}\s+{maximum}",
            installer,
        ), f"{setting} installer validation does not match the application bounds"
        assert f'${{{setting}:-}}' in installer, f"{setting} is not preserved from hub.env"

    assert 'saved_data_dir="$(bash -c' in installer
    assert 'DATA_DIR="$(validate_data_dir "${DATA_DIR:-/var/lib/dark-noc}")"' in installer
    assert 'DB_PATH="$DATA_DIR/dark-noc.db"' in installer
    assert "sqlite3.connect(sys.argv[1])" in installer
    assert 'printf \'DARK_NOC_DATA=%q\\n\' "$DATA_DIR"' in installer
    assert 'printf \'ReadWritePaths=%s\\n\' "$DATA_DIR"' in installer
    assert 'PUBLIC_URL_HOST="[$PUBLIC_HOST]"' in installer
    assert 'printf \'DARK_NOC_PUBLIC_PORT=%q\\n\' "$PUBLIC_PORT"' in installer
    assert 'printf \'DARK_NOC_PANEL_CERT_MODE=%q\\n\'' in installer
    assert 'install -m 0755 "$SCRIPT_DIR/darknoc" /usr/local/bin/darknoc' in installer
    assert 'darknoc --apply-gateway' in installer
    assert '"https://127.0.0.1:$PUBLIC_PORT/healthz"' in installer
    assert 'echo "Open: $PUBLIC_URL"' in installer
    assert 'DARK_NOC_PUBLIC_PORT="${DARK_NOC_PUBLIC_PORT:-443}"' in server_cli
    assert 'Get / renew panel SSL' in server_cli
    assert 'certbot certonly --webroot' in server_cli
    assert 'Account changes are server-only' in (ROOT / "hub" / "app.py").read_text(encoding="utf-8")

    upgrader = (ROOT / "upgrade.sh").read_text(encoding="utf-8")
    assert 'hub_data_dir="$(validate_data_dir "$existing_data_dir")"' in upgrader
    assert 'hub_db_path="$hub_data_dir/dark-noc.db"' in upgrader
    assert 'hub_key_path="$hub_data_dir/master.key"' in upgrader
    quiesce_agent = upgrader.index("systemctl stop dark-noc-agent.service", upgrader.index("Quiescing the local Agent"))
    quiesce_hub = upgrader.index("systemctl stop dark-noc-hub.service", quiesce_agent)
    final_snapshot = upgrader.index('python3 - "$hub_db_path" "$backup_dir/dark-noc.db"', quiesce_hub)
    mutation_start = upgrader.index("mutation_started=1", final_snapshot)
    installer_start = upgrader.index('bash "$SCRIPT_DIR/install-hub.sh"', mutation_start)
    assert quiesce_agent < quiesce_hub < final_snapshot < mutation_start < installer_start
    hub_success = upgrader[installer_start : upgrader.index(";;", installer_start)]
    assert 'restore_service_state dark-noc-hub.service' in hub_success
    assert 'restore_service_state dark-noc-agent.service' in hub_success
    assert 'restore_service_state nginx.service' in hub_success
    assert 'cp -a "$hub_key_path" "$backup_dir/master.key"' in upgrader
    assert 'cp -a "$backup_dir/master.key" "$hub_key_path"' in upgrader
    assert 'could not restore the darknoc server CLI' in upgrader
    assert 'rm -f "$hub_db_path" "$hub_db_path-wal" "$hub_db_path-shm"' in upgrader
    assert "/var/lib/dark-noc/dark-noc.db" not in upgrader
    agent_upgrade = upgrader[upgrader.index("  agent)") :]
    agent_success_start = agent_upgrade.rindex("systemctl daemon-reload")
    agent_success = agent_upgrade[agent_success_start : agent_upgrade.index(";;", agent_success_start)]
    assert 'restore_service_state dark-noc-agent.service' in agent_success
    assert "systemctl start dark-noc-agent.service" not in agent_success
    local_config_load = re.search(
        r"old = \{\}\ntry:\n\s+old = json\.loads\(path\.read_text\(\)\)\n"
        r"except (?P<exception>[^:]+):",
        installer,
    )
    assert local_config_load, "local Agent configuration preservation block is missing"
    assert local_config_load.group("exception") == "FileNotFoundError", (
        "an unreadable or malformed existing local Agent configuration must abort the upgrade"
    )
    assert "tmp.replace(path)" in installer, "local Agent configuration must be replaced atomically"

    quick_installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert 'if [[ -d /opt/dark-noc/hub ]]; then' in quick_installer
    existing_hub_branch = quick_installer.split(
        'if [[ -d /opt/dark-noc/hub ]]; then', 1
    )[1].split("else", 1)[0]
    assert 'INSTALLER="upgrade.sh"' in existing_hub_branch
    assert "INSTALL_ARGS=(hub)" in existing_hub_branch
    assert "verified Hub upgrade with rollback" in existing_hub_branch
    assert quick_installer.index('ACTUAL="$(sha256sum') < quick_installer.index('tar -xzf')

    release_builder = (ROOT / "scripts" / "build-release.sh").read_text(encoding="utf-8")
    assert '"$ROOT_DIR/darknoc"' in release_builder

    agent_service = (ROOT / "deploy" / "dark-noc-agent.service").read_text(encoding="utf-8")
    assert re.search(r"^UMask=0077$", agent_service, flags=re.MULTILINE)
    write_paths_match = re.search(r"^ReadWritePaths=(.+)$", agent_service, flags=re.MULTILINE)
    assert write_paths_match, "Agent systemd write allow-list is missing"
    write_paths = set(write_paths_match.group(1).split())
    required_write_paths = {
        "/var/lib/dark-noc-agent",
        "/etc/dark-noc-agent",
        "/etc/dark-backhaul",
        "/etc/dark-ghostpro",
        "/etc/dark-packetpro",
        "/var/lib/dark-noc-acme",
        "/etc/letsencrypt",
        "/var/lib/letsencrypt",
        "/var/log/letsencrypt",
        "/etc/nginx/conf.d",
        "/etc/systemd/system",
        "/usr/local/bin",
    }
    assert required_write_paths <= write_paths

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    test_step = workflow.find("- name: Test release source")
    publish_step = workflow.find("- name: Publish verified draft")
    assert 0 <= test_step < publish_step, "release publishing must run after release tests"
    assert "python tests/file_ops.py" in workflow[test_step:publish_step]
    assert "python tests/noc_features.py" in workflow[test_step:publish_step]
    assert "--clobber" not in workflow
    assert "gh api --paginate" in workflow
    assert "| jq -s 'add'" in workflow
    assert "--slurp" not in workflow
    assert 'releases?per_page=100' in workflow
    assert "releases/tags/" not in workflow
    assert "gh release download" not in workflow
    assert "dark-noc-release:$release_tag:$target_sha" in workflow
    assert "git tag -a" in workflow
    assert "Moved unpublished owned tag" in workflow
    assert "Retargeted owned draft release" in workflow
    assert "Removed outdated owned draft release" in workflow
    resolve = workflow[:publish_step]
    assert 'gh api -X POST "repos/$GITHUB_REPOSITORY/releases" --input -' in resolve
    assert "tag_name: $tag" in resolve and "draft: true" in resolve
    publish = workflow[publish_step:]
    upload_step = publish.find("gh release upload")
    asset_download_step = publish.find("-H 'Accept: application/octet-stream'")
    final_publish_step = publish.find('-F draft=false -f make_latest=true')
    assert 0 <= upload_step < asset_download_step < final_publish_step
    assert "must contain exactly three expected assets" in publish
    assert "(cd dist && sha256sum -c SHA256SUMS)" in publish
    assert '(cd "$verify_dir" && sha256sum -c SHA256SUMS)' in publish
    assert 'releases/assets/$asset_id' in publish
    assert "Owned draft contains unexpected asset" in publish
    assert "Removed owned orphan draft release" in publish
    assert "refusing to modify it" in workflow

    builder = (ROOT / "scripts" / "build-release.sh").read_text(encoding="utf-8")
    node_stage_match = re.search(r'mkdir\s+-p\s+"\$STAGE_DIR/dark-noc-node/agent"', builder)
    assert node_stage_match, "Node staging section is missing"
    node_stage_start = node_stage_match.start()
    node_stage_end = builder.index("build_archive()", node_stage_start)
    node_stage = builder[node_stage_start:node_stage_end]
    assert '"$ROOT_DIR/upgrade.sh"' in node_stage


def main() -> None:
    test_clean_remote_path()
    asyncio.run(test_finalize_sftp_file())
    test_ssh_file_endpoints()
    asyncio.run(test_oversize_upload_cleanup())
    test_installer_and_release_guards()
    print("DARK NOC file operation regression tests passed")


if __name__ == "__main__":
    main()
