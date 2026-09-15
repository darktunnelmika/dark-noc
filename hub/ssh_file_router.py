import asyncio
import hashlib
import posixpath
import secrets
import sqlite3
import stat as statmod

import asyncssh
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse


def register_ssh_file_router(app, **deps):
    router = APIRouter()
    current_user = deps["current_user"]
    ssh_file_node = deps["ssh_file_node"]
    clean_remote_path = deps["clean_remote_path"]
    destructive_remote_path_allowed = deps["destructive_remote_path_allowed"]
    disconnect_aware_semaphore = deps["disconnect_aware_semaphore"]
    ssh_connection_options = deps["ssh_connection_options"]
    sftp_path_exists = deps["sftp_path_exists"]
    ssh_destination_lock = deps["ssh_destination_lock"]
    finalize_sftp_file = deps["finalize_sftp_file"]
    cleanup_sftp_path = deps["cleanup_sftp_path"]
    ssh_file_error = deps["ssh_file_error"]
    audit = deps["audit"]
    LOGGER = deps["LOGGER"]
    SSHRelayBody = deps["SSHRelayBody"]
    clean_remote_directory = deps["clean_remote_directory"]
    sftp_entry = deps["sftp_entry"]
    SSHFileWriteBody = deps["SSHFileWriteBody"]
    SSHFileActionBody = deps["SSHFileActionBody"]
    get_ssh_upload_limit = deps["get_ssh_upload_limit"]
    get_ssh_relay_limit = deps["get_ssh_relay_limit"]
    get_ssh_transfer_timeout = deps["get_ssh_transfer_timeout"]
    get_ssh_transfer_idle_timeout = deps["get_ssh_transfer_idle_timeout"]
    get_ssh_transfer_semaphore = deps["get_ssh_transfer_semaphore"]
    get_ssh_file_chunk = deps["get_ssh_file_chunk"]
    get_ssh_editor_limit = deps["get_ssh_editor_limit"]

    @router.post("/api/ssh/upload/{node_id}")
    async def ssh_upload_file(
        node_id: int,
        request: Request,
        remote_path: str = Form(...),
        overwrite: bool = Form(False),
        file: UploadFile = File(...),
        user: sqlite3.Row = Depends(current_user),
    ):
        node, password, key = ssh_file_node(node_id)
        if file.size is not None and file.size > get_ssh_upload_limit():
            await file.close()
            raise HTTPException(413, f"Upload exceeds the {get_ssh_upload_limit() // 1024 // 1024} MB limit")
        try:
            destination = clean_remote_path(remote_path, filename=file.filename)
        except ValueError as exc:
            await file.close()
            raise HTTPException(400, str(exc)) from exc
        if not destructive_remote_path_allowed(destination):
            await file.close()
            raise HTTPException(400, "This protected system path cannot be replaced through file upload")
        temporary = f"{destination}.darknoc-{secrets.token_hex(8)}.part"
        transferred = 0
        try:
            async with asyncio.timeout(get_ssh_transfer_timeout()):
                async with disconnect_aware_semaphore(request, get_ssh_transfer_semaphore()):
                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            try:
                                parent = posixpath.dirname(destination)
                                parent_attrs = await sftp.stat(parent)
                                if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                if await sftp_path_exists(sftp, destination) and not overwrite:
                                    raise HTTPException(409, "Destination already exists; enable overwrite to replace it")
                                async with sftp.open(
                                    temporary,
                                    "xb",
                                    attrs=asyncssh.SFTPAttrs(permissions=0o600),
                                ) as remote:
                                    while True:
                                        if await request.is_disconnected():
                                            raise HTTPException(499, "Client disconnected")
                                        chunk = await asyncio.wait_for(file.read(get_ssh_file_chunk()), get_ssh_transfer_idle_timeout())
                                        if not chunk:
                                            break
                                        transferred += len(chunk)
                                        if transferred > get_ssh_upload_limit():
                                            raise HTTPException(413, f"Upload exceeds the {get_ssh_upload_limit() // 1024 // 1024} MB limit")
                                        await asyncio.wait_for(remote.write(chunk), get_ssh_transfer_idle_timeout())
                                async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], destination)):
                                    if await request.is_disconnected():
                                        raise HTTPException(499, "Client disconnected")
                                    await finalize_sftp_file(sftp, temporary, destination, overwrite)
                            except BaseException:
                                await asyncio.shield(cleanup_sftp_path(sftp, temporary))
                                raise
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc
        finally:
            try:
                await asyncio.shield(file.close())
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning("Could not close uploaded file", exc_info=True)
        try:
            audit(user["id"], "ssh_file_upload", node["name"], f"Uploaded {transferred} bytes to {destination}", request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record successful SSH upload audit event")
        return {"ok": True, "node": node["name"], "path": destination, "bytes": transferred}

    @router.post("/api/ssh/relay")
    async def ssh_relay_file(body: SSHRelayBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        source, source_password, source_key = ssh_file_node(body.source_node_id)
        destination, destination_password, destination_key = ssh_file_node(body.destination_node_id)
        if source["id"] == destination["id"] and body.source_path == body.destination_path:
            raise HTTPException(400, "Source and destination are the same file")
        if not destructive_remote_path_allowed(body.destination_path):
            raise HTTPException(400, "This protected system path cannot be replaced through file relay")
        temporary = f"{body.destination_path}.darknoc-{secrets.token_hex(8)}.part"
        transferred = 0
        try:
            async with asyncio.timeout(get_ssh_transfer_timeout()):
                async with disconnect_aware_semaphore(request, get_ssh_transfer_semaphore()):
                    async with asyncssh.connect(**ssh_connection_options(source, source_password, source_key)) as source_ssh:
                        async with asyncssh.connect(**ssh_connection_options(destination, destination_password, destination_key)) as destination_ssh:
                            async with source_ssh.start_sftp_client() as source_sftp, destination_ssh.start_sftp_client() as destination_sftp:
                                try:
                                    source_attrs = await source_sftp.stat(body.source_path)
                                    if source_attrs.permissions is not None and not statmod.S_ISREG(source_attrs.permissions):
                                        raise HTTPException(400, "Source path must be a regular file")
                                    source_size = int(source_attrs.size or 0)
                                    if source_size > get_ssh_relay_limit():
                                        raise HTTPException(413, f"File exceeds the {get_ssh_relay_limit() // 1024 // 1024} MB relay limit")
                                    parent_attrs = await destination_sftp.stat(posixpath.dirname(body.destination_path))
                                    if parent_attrs.permissions is not None and not statmod.S_ISDIR(parent_attrs.permissions):
                                        raise HTTPException(400, "Destination parent is not a directory")
                                    if await sftp_path_exists(destination_sftp, body.destination_path) and not body.overwrite:
                                        raise HTTPException(409, "Destination already exists; enable overwrite to replace it")
                                    source_mode = statmod.S_IMODE(source_attrs.permissions) if source_attrs.permissions is not None else 0o600
                                    async with source_sftp.open(body.source_path, "rb") as reader, destination_sftp.open(
                                        temporary,
                                        "xb",
                                        attrs=asyncssh.SFTPAttrs(permissions=source_mode),
                                    ) as writer:
                                        while True:
                                            if await request.is_disconnected():
                                                raise HTTPException(499, "Client disconnected")
                                            chunk = await asyncio.wait_for(reader.read(get_ssh_file_chunk()), get_ssh_transfer_idle_timeout())
                                            if not chunk:
                                                break
                                            transferred += len(chunk)
                                            if transferred > get_ssh_relay_limit():
                                                raise HTTPException(413, f"File exceeds the {get_ssh_relay_limit() // 1024 // 1024} MB relay limit")
                                            await asyncio.wait_for(writer.write(chunk), get_ssh_transfer_idle_timeout())
                                    async with disconnect_aware_semaphore(
                                        request, ssh_destination_lock(destination["id"], body.destination_path)
                                    ):
                                        if await request.is_disconnected():
                                            raise HTTPException(499, "Client disconnected")
                                        await finalize_sftp_file(destination_sftp, temporary, body.destination_path, body.overwrite)
                                except BaseException:
                                    await asyncio.shield(cleanup_sftp_path(destination_sftp, temporary))
                                    raise
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc
        try:
            audit(user["id"], "ssh_file_relay", f"{source['name']} → {destination['name']}", f"Relayed {transferred} bytes: {body.source_path} → {body.destination_path}", request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record successful SSH relay audit event")
        return {"ok": True, "source_node": source["name"], "destination_node": destination["name"], "source_path": body.source_path, "destination_path": body.destination_path, "bytes": transferred}

    @router.get("/api/ssh/files/{node_id}")
    async def list_ssh_files(node_id: int, path: str = "/root", _: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        try:
            directory = clean_remote_directory(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            async with asyncio.timeout(min(get_ssh_transfer_timeout(), 120)):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        attrs = await sftp.stat(directory)
                        if attrs.permissions is not None and not statmod.S_ISDIR(attrs.permissions):
                            raise HTTPException(400, "Remote path is not a directory")
                        entries = []
                        async for entry in sftp.scandir(directory):
                            name = str(entry.filename)
                            if name in {".", ".."}:
                                continue
                            entries.append(sftp_entry(name, directory, entry.attrs))
            entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
            parent = None if directory == "/" else posixpath.dirname(directory) or "/"
            return {"node": node["name"], "path": directory, "parent": parent, "entries": entries[:5000]}
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc

    @router.get("/api/ssh/files/{node_id}/read")
    async def read_ssh_file(node_id: int, path: str, _: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        try:
            remote_path = clean_remote_path(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            async with asyncio.timeout(min(get_ssh_transfer_timeout(), 120)):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        attrs = await sftp.stat(remote_path)
                        if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                            raise HTTPException(400, "Only regular files can be opened in the editor")
                        if int(attrs.size or 0) > get_ssh_editor_limit():
                            raise HTTPException(413, f"Editor limit is {get_ssh_editor_limit() // 1024} KB")
                        async with sftp.open(remote_path, "rb") as remote:
                            payload = await asyncio.wait_for(remote.read(get_ssh_editor_limit() + 1), get_ssh_transfer_idle_timeout())
            if len(payload) > get_ssh_editor_limit():
                raise HTTPException(413, f"Editor limit is {get_ssh_editor_limit() // 1024} KB")
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(415, "File is not valid UTF-8 text") from exc
            return {"node": node["name"], "path": remote_path, "content": content, "bytes": len(payload)}
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc

    @router.put("/api/ssh/files/{node_id}/write")
    async def write_ssh_file(node_id: int, body: SSHFileWriteBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        if not destructive_remote_path_allowed(body.path):
            raise HTTPException(400, "This protected system path cannot be edited from File Manager")
        payload = body.content.encode("utf-8")
        if len(payload) > get_ssh_editor_limit():
            raise HTTPException(413, f"Editor limit is {get_ssh_editor_limit() // 1024} KB")
        temporary = f"{body.path}.darknoc-{secrets.token_hex(8)}.part"
        try:
            async with asyncio.timeout(min(get_ssh_transfer_timeout(), 300)):
                async with disconnect_aware_semaphore(request, get_ssh_transfer_semaphore()):
                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            try:
                                parent = await sftp.stat(posixpath.dirname(body.path))
                                if parent.permissions is not None and not statmod.S_ISDIR(parent.permissions):
                                    raise HTTPException(400, "Destination parent is not a directory")
                                if await sftp_path_exists(sftp, body.path) and not body.overwrite:
                                    raise HTTPException(409, "File already exists; enable overwrite to replace it")
                                async with sftp.open(temporary, "xb", attrs=asyncssh.SFTPAttrs(permissions=0o600)) as remote:
                                    await asyncio.wait_for(remote.write(payload), get_ssh_transfer_idle_timeout())
                                async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.path)):
                                    await finalize_sftp_file(sftp, temporary, body.path, body.overwrite)
                            except BaseException:
                                await asyncio.shield(cleanup_sftp_path(sftp, temporary))
                                raise
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc
        try:
            audit(user["id"], "ssh_file_write", node["name"], f"Saved {len(payload)} bytes to {body.path}", request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record SSH editor audit event")
        return {"ok": True, "path": body.path, "bytes": len(payload)}

    @router.post("/api/ssh/files/{node_id}/action")
    async def ssh_file_action(node_id: int, body: SSHFileActionBody, request: Request, user: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        if not destructive_remote_path_allowed(body.path):
            raise HTTPException(400, "This protected system path cannot be changed from File Manager")
        if body.action == "rename":
            if not body.destination:
                raise HTTPException(422, "Rename requires a destination path")
            if not destructive_remote_path_allowed(body.destination):
                raise HTTPException(400, "This protected destination cannot be changed from File Manager")
        if body.action == "chmod" and not body.mode:
            raise HTTPException(422, "CHMOD requires an octal mode")
        try:
            async with asyncio.timeout(min(get_ssh_transfer_timeout(), 120)):
                async with disconnect_aware_semaphore(request, get_ssh_transfer_semaphore()):
                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            async with disconnect_aware_semaphore(request, ssh_destination_lock(node["id"], body.destination or body.path)):
                                if body.action == "mkdir":
                                    await sftp.mkdir(body.path, attrs=asyncssh.SFTPAttrs(permissions=0o750))
                                elif body.action == "rename":
                                    if await sftp_path_exists(sftp, body.destination or ""):
                                        raise HTTPException(409, "Destination already exists")
                                    await sftp.rename(body.path, body.destination)
                                elif body.action == "delete":
                                    attrs = await sftp.lstat(body.path)
                                    if attrs.permissions is not None and statmod.S_ISDIR(attrs.permissions):
                                        await sftp.rmdir(body.path)
                                    else:
                                        await sftp.remove(body.path)
                                else:
                                    await sftp.chmod(body.path, int(body.mode or "600", 8))
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc
        target = f"{body.path} -> {body.destination}" if body.destination else body.path
        try:
            audit(user["id"], f"ssh_file_{body.action}", node["name"], target, request.client.host if request.client else None)
        except Exception:
            LOGGER.exception("Could not record SSH File Manager action")
        return {"ok": True, "action": body.action, "path": body.path, "destination": body.destination}

    @router.get("/api/ssh/files/{node_id}/checksum")
    async def checksum_ssh_file(node_id: int, path: str, _: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        try:
            remote_path = clean_remote_path(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        digest = hashlib.sha256()
        size = 0
        try:
            async with asyncio.timeout(get_ssh_transfer_timeout()):
                async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                    async with ssh.start_sftp_client() as sftp:
                        attrs = await sftp.stat(remote_path)
                        if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                            raise HTTPException(400, "Checksum requires a regular file")
                        async with sftp.open(remote_path, "rb") as remote:
                            while True:
                                chunk = await asyncio.wait_for(remote.read(get_ssh_file_chunk()), get_ssh_transfer_idle_timeout())
                                if not chunk:
                                    break
                                size += len(chunk)
                                digest.update(chunk)
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc
        return {"node": node["name"], "path": remote_path, "bytes": size, "sha256": digest.hexdigest()}

    @router.get("/api/ssh/files/{node_id}/download")
    async def download_ssh_file(node_id: int, path: str, request: Request, user: sqlite3.Row = Depends(current_user)):
        node, password, key = ssh_file_node(node_id)
        try:
            remote_path = clean_remote_path(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                async with ssh.start_sftp_client() as sftp:
                    attrs = await sftp.stat(remote_path)
                    if attrs.permissions is not None and not statmod.S_ISREG(attrs.permissions):
                        raise HTTPException(400, "Only regular files can be downloaded")
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise ssh_file_error(exc) from exc

        async def stream_remote_file():
            async with asyncio.timeout(get_ssh_transfer_timeout()):
                async with disconnect_aware_semaphore(request, get_ssh_transfer_semaphore()):
                    async with asyncssh.connect(**ssh_connection_options(node, password, key)) as ssh:
                        async with ssh.start_sftp_client() as sftp:
                            async with sftp.open(remote_path, "rb") as remote:
                                while True:
                                    if await request.is_disconnected():
                                        break
                                    chunk = await asyncio.wait_for(remote.read(get_ssh_file_chunk()), get_ssh_transfer_idle_timeout())
                                    if not chunk:
                                        break
                                    yield chunk

        safe_name = posixpath.basename(remote_path).replace('"', "_").replace("\\", "_") or "download.bin"
        try:
            audit(user["id"], "ssh_file_download", node["name"], remote_path)
        except Exception:
            LOGGER.exception("Could not record SSH download audit event")
        return StreamingResponse(
            stream_remote_file(), media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "Cache-Control": "no-store"},
        )

    handlers = {
        "ssh_upload_file": ssh_upload_file,
        "ssh_relay_file": ssh_relay_file,
        "list_ssh_files": list_ssh_files,
        "read_ssh_file": read_ssh_file,
        "write_ssh_file": write_ssh_file,
        "ssh_file_action": ssh_file_action,
        "checksum_ssh_file": checksum_ssh_file,
        "download_ssh_file": download_ssh_file
    }
    app.include_router(router)
    return router, handlers
