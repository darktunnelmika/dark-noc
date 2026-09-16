from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PLUGIN_SCHEMA_VERSION = 1
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
INVENTORY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,63}$")
ALLOWED_ROLES = {"server", "client", "edge", "gateway"}
ALLOWED_FORM_PROFILES = {"standard", "packet", "realm", "generic"}
ALLOWED_SETTINGS_PROFILES = {"standard", "packet", "realm", "generic"}
ALLOWED_PAIR_CODECS = {"backhaul", "ghostpro", "packetpro", "realm", "generic-v1"}
ALLOWED_ENDPOINT_SIDES = {"iran", "kharej"}
ALLOWED_MANAGED_ENDPOINTS = {"iran", "kharej", "kharej_public_ipv4"}
ALLOWED_CERTIFICATE_SIDES = {"iran", "kharej", "none"}


class PluginRegistryError(RuntimeError):
    pass


def _text(value: Any, field: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise PluginRegistryError(f"{field} must be a string")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise PluginRegistryError(f"{field} is invalid")
    return value


def _string_list(value: Any, field: str, *, maximum: int = 64) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise PluginRegistryError(f"{field} must be a non-empty list")
    result = [_text(item, field, maximum=96) for item in value]
    if len(result) != len(set(result)):
        raise PluginRegistryError(f"{field} cannot contain duplicates")
    return result


def validate_plugin_manifest(raw: Any, *, source: str = "plugin manifest") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PluginRegistryError(f"{source}: manifest must be an object")
    if raw.get("schema_version") != PLUGIN_SCHEMA_VERSION:
        raise PluginRegistryError(f"{source}: unsupported schema_version")

    plugin_id = _text(raw.get("id"), f"{source}.id", maximum=64)
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise PluginRegistryError(f"{source}: id must use lowercase letters, digits and hyphens")

    name = _text(raw.get("name"), f"{source}.name", maximum=96)
    version = _text(raw.get("version"), f"{source}.version", maximum=48)
    publisher = _text(raw.get("publisher"), f"{source}.publisher", maximum=96)
    repository = _text(raw.get("repository"), f"{source}.repository", maximum=256)
    if not repository.startswith("https://github.com/"):
        raise PluginRegistryError(f"{source}: repository must be an HTTPS GitHub URL")
    description = _text(raw.get("description"), f"{source}.description", maximum=512)

    roles = raw.get("roles")
    if not isinstance(roles, dict) or set(roles) != {"iran", "kharej"}:
        raise PluginRegistryError(f"{source}: roles must define exactly iran and kharej")
    normalized_roles = {side: _text(roles[side], f"{source}.roles.{side}", maximum=24) for side in ("iran", "kharej")}
    if any(role not in ALLOWED_ROLES for role in normalized_roles.values()):
        raise PluginRegistryError(f"{source}: unsupported runtime role")

    transports = _string_list(raw.get("transports"), f"{source}.transports")
    profiles = _string_list(raw.get("profiles"), f"{source}.profiles")
    automated = raw.get("automated")
    if not isinstance(automated, bool):
        raise PluginRegistryError(f"{source}: automated must be boolean")

    ui = raw.get("ui")
    if not isinstance(ui, dict):
        raise PluginRegistryError(f"{source}: ui must be an object")
    icon = _text(ui.get("icon"), f"{source}.ui.icon", maximum=4)
    method = _text(ui.get("method"), f"{source}.ui.method", maximum=96)
    inventory_key = _text(ui.get("inventory_key"), f"{source}.ui.inventory_key", maximum=64)
    if not INVENTORY_KEY_RE.fullmatch(inventory_key):
        raise PluginRegistryError(f"{source}: inventory_key is invalid")
    form_profile = _text(ui.get("form_profile"), f"{source}.ui.form_profile", maximum=24)
    if form_profile not in ALLOWED_FORM_PROFILES:
        raise PluginRegistryError(f"{source}: unsupported form_profile")
    tls_transports = ui.get("tls_transports", [])
    if not isinstance(tls_transports, list):
        raise PluginRegistryError(f"{source}: ui.tls_transports must be a list")
    normalized_tls = [_text(item, f"{source}.ui.tls_transports", maximum=96) for item in tls_transports]
    if len(normalized_tls) != len(set(normalized_tls)) or any(item not in transports for item in normalized_tls):
        raise PluginRegistryError(f"{source}: ui.tls_transports must be unique supported transports")
    endpoint_default_side = _text(ui.get("endpoint_default_side", "iran"), f"{source}.ui.endpoint_default_side", maximum=16)
    if endpoint_default_side not in ALLOWED_ENDPOINT_SIDES:
        raise PluginRegistryError(f"{source}: unsupported endpoint_default_side")
    iran_role_label = _text(ui.get("iran_role_label", "IRAN side"), f"{source}.ui.iran_role_label", maximum=128)

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict):
        raise PluginRegistryError(f"{source}: runtime must be an object")
    settings_profile = _text(runtime.get("settings_profile"), f"{source}.runtime.settings_profile", maximum=24)
    if settings_profile not in ALLOWED_SETTINGS_PROFILES:
        raise PluginRegistryError(f"{source}: unsupported settings_profile")
    pair_codec = _text(runtime.get("pair_codec"), f"{source}.runtime.pair_codec", maximum=24)
    if pair_codec not in ALLOWED_PAIR_CODECS:
        raise PluginRegistryError(f"{source}: unsupported pair_codec")
    pair_endpoint = _text(runtime.get("pair_endpoint", "iran"), f"{source}.runtime.pair_endpoint", maximum=16)
    if pair_endpoint not in ALLOWED_ENDPOINT_SIDES:
        raise PluginRegistryError(f"{source}: unsupported pair_endpoint")
    managed_endpoint = _text(runtime.get("managed_endpoint", "iran"), f"{source}.runtime.managed_endpoint", maximum=32)
    if managed_endpoint not in ALLOWED_MANAGED_ENDPOINTS:
        raise PluginRegistryError(f"{source}: unsupported managed_endpoint")
    certificate_side = _text(runtime.get("certificate_side", "none"), f"{source}.runtime.certificate_side", maximum=16)
    if certificate_side not in ALLOWED_CERTIFICATE_SIDES:
        raise PluginRegistryError(f"{source}: unsupported certificate_side")
    certificate_role = str(runtime.get("certificate_role") or "").strip()
    if certificate_role and certificate_role not in ALLOWED_ROLES:
        raise PluginRegistryError(f"{source}: unsupported certificate_role")
    for boolean_name in (
        "pair_code", "pair_endpoint_ipv4", "allow_tunnel_port_overlap",
        "iran_endpoint_must_match_node", "pair_hides_certificate",
    ):
        if not isinstance(runtime.get(boolean_name), bool):
            raise PluginRegistryError(f"{source}: runtime.{boolean_name} must be boolean")

    normalized = dict(raw)
    normalized.update({
        "schema_version": PLUGIN_SCHEMA_VERSION,
        "id": plugin_id,
        "name": name,
        "version": version,
        "publisher": publisher,
        "repository": repository,
        "description": description,
        "roles": normalized_roles,
        "transports": transports,
        "profiles": profiles,
        "automated": automated,
        "ui": {
            **ui,
            "icon": icon,
            "method": method,
            "inventory_key": inventory_key,
            "form_profile": form_profile,
            "tls_transports": normalized_tls,
            "endpoint_default_side": endpoint_default_side,
            "iran_role_label": iran_role_label,
        },
        "runtime": {
            **runtime,
            "settings_profile": settings_profile,
            "pair_codec": pair_codec,
            "pair_endpoint": pair_endpoint,
            "managed_endpoint": managed_endpoint,
            "certificate_side": certificate_side,
            "certificate_role": certificate_role,
        },
    })
    return normalized


def load_plugin_catalog(manifest_dir: Path | str | None = None) -> list[dict[str, Any]]:
    directory = Path(manifest_dir) if manifest_dir is not None else Path(__file__).resolve().with_name("plugins")
    if not directory.is_dir():
        raise PluginRegistryError(f"Plugin manifest directory is missing: {directory}")
    manifests: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_methods: set[str] = set()
    seen_inventory_keys: set[str] = set()
    files = sorted(directory.glob("*.json"))
    if not files:
        raise PluginRegistryError("Plugin manifest directory is empty")
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginRegistryError(f"{path.name}: cannot read manifest: {exc}") from exc
        manifest = validate_plugin_manifest(raw, source=path.name)
        plugin_id = manifest["id"]
        method = manifest["ui"]["method"]
        inventory_key = manifest["ui"]["inventory_key"]
        if plugin_id in seen_ids:
            raise PluginRegistryError(f"Duplicate plugin id: {plugin_id}")
        if method in seen_methods:
            raise PluginRegistryError(f"Duplicate plugin method label: {method}")
        if inventory_key in seen_inventory_keys:
            raise PluginRegistryError(f"Duplicate plugin inventory key: {inventory_key}")
        seen_ids.add(plugin_id)
        seen_methods.add(method)
        seen_inventory_keys.add(inventory_key)
        manifests.append(manifest)
    manifests.sort(key=lambda item: (int(item.get("order", 1000)), item["id"]))
    return manifests
