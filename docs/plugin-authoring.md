# DARK NOC Plugin Contract v1

DARK NOC plugins are manifest-driven. The goal is that a new tunnel plugin does not require edits to Hub routes, plugin cards, transport/profile selectors, TLS selector logic or Pair-Code orchestration.

## 1. Add one manifest

Create `hub/plugins/<plugin-id>.json` and follow schema version `1`. The Hub loads every manifest at startup, validates it fail-closed, rejects duplicate IDs/method labels/inventory keys and publishes the normalized catalog through `/api/plugins`.

Required manifest groups:

- identity: `id`, `name`, `version`, `publisher`, `repository`, `description`
- topology: `roles.iran`, `roles.kharej`
- choices: `transports`, `profiles`
- UI: icon, tunnel method label, Agent inventory key, form profile, TLS transports and endpoint defaults
- runtime: deployment profile, Pair-Code codec, endpoint side, certificate side/role and collision rules

The deployment form reads transports, performance profiles and Pair-Code availability directly from the manifest. A plugin which declares `pair_code=false` is exposed as managed-only without a custom UI branch.

## 2. Reuse an existing Hub runtime profile

Most plugins should use `runtime.settings_profile=standard`. Standard plugins can define different transports, roles, TLS behavior and Pair-Code behavior entirely in the manifest.

Built-in specialized profiles are:

- `standard` — Backhaul/GOST-style coordinated server/client tunnel
- `packet` — PAQET-style KHAREJ public IPv4 endpoint semantics
- `realm` — Realm edge/gateway mappings and Gateway-owned TLS
- `generic` — reserved for plugins using the generic Pair-Code contract

A new standard plugin using `pair_codec=generic-v1` does not require a new Hub Pair-Code branch. Its standalone script only needs to decode the `DNP1.` URL-safe Base64 JSON contract.

## 3. Add the Agent adapter in one place

The Agent owns privileged install/deploy/remove operations. Native plugin capabilities are centralized in `plugin_adapters()` in `agent/agent.py`; job dispatch and inventory collection no longer contain separate per-plugin branches.

For a new native plugin:

1. implement its install/deploy/remove helpers,
2. add one `plugin_adapters()` registry entry with `name`, `inventory_key`, `service_prefix`, `inventory`, `install`, `deploy` and `remove`,
3. make the deploy helper add its service to `managed_services` and its tunnel description to `tunnels`.

That single registry entry drives Core inventory, install, deploy, remove and tunnel-control dispatch. Any tunnel whose service is in `managed_services` is accepted by the generic monitor path, so a new plugin does not need to be added to the explicit tunnel allow-list. Add an auto-discovery parser only when the plugin must discover instances created outside DARK NOC.

Legacy Backhaul/Ghost/Packet/Realm tunnel records remain recognized even when an older Agent configuration has no `managed_services` entry, so upgrading the plugin architecture does not hide existing tunnels.

## 4. Inventory contract

The manifest `ui.inventory_key` must exactly match the Agent adapter `inventory_key`. The adapter `inventory` callback returns at least `installed`, `version` and `instances`. Binary-based plugins can reuse `_binary_plugin_inventory()`; specialized plugins such as Realm may return richer inventory.

Keeping inventory in the same adapter registry means a newly installed Core immediately appears installed in the Plugin Store instead of repeatedly offering `INSTALL CORE`.

## 5. Compatibility rules

- Existing API URLs and deployment database rows keep their current format.
- Existing DB rows without `pair_codec` or `certificate_role` continue through legacy-safe defaults.
- Existing built-in tunnel records without `managed_services` remain monitored through the legacy compatibility path.
- Pair codes remain secret-bearing credentials; never log or expose them outside the authenticated reveal flow.
- Plugin IDs are lowercase `[a-z0-9-]`; inventory keys are lowercase `[a-z0-9_]`.
- Plugin manifests are trusted code configuration and must be reviewed like source changes.

## Minimal standard manifest

```json
{
  "schema_version": 1,
  "order": 100,
  "id": "dark-example",
  "name": "DARK Example",
  "version": "1.0.0",
  "publisher": "@mikakhadm",
  "repository": "https://github.com/darktunnelmika/dark-example",
  "description": "Example DARK NOC tunnel plugin.",
  "roles": {"iran": "server", "kharej": "client"},
  "transports": ["tcp"],
  "profiles": ["stable", "balanced", "lowping", "turbo"],
  "automated": true,
  "ui": {
    "icon": "DX",
    "method": "DARK Example",
    "inventory_key": "dark_example",
    "form_profile": "standard",
    "tls_transports": [],
    "endpoint_default_side": "iran",
    "iran_role_label": "Server · accepts tunnel"
  },
  "runtime": {
    "settings_profile": "standard",
    "pair_codec": "generic-v1",
    "pair_code": true,
    "pair_endpoint": "iran",
    "pair_endpoint_ipv4": false,
    "managed_endpoint": "iran",
    "certificate_side": "none",
    "certificate_role": "",
    "allow_tunnel_port_overlap": false,
    "iran_endpoint_must_match_node": true,
    "pair_hides_certificate": false
  }
}
```

## Minimal Agent adapter entry

```python
"dark-example": {
    "name": "DARK Example",
    "inventory_key": "dark_example",
    "service_prefix": "dark-example@",
    "inventory": lambda tunnels: _binary_plugin_inventory(
        Path("/usr/local/bin/dark-example"), ["--version"], "DARK Example", tunnels
    ),
    "install": _install_dark_example,
    "deploy": lambda payload, config: _deploy_dark_example(payload, config),
    "remove": lambda payload, config: _remove_dark_example(payload, config),
},
```
