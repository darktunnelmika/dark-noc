from pathlib import Path

app_path = Path("hub/app.py")
text = app_path.read_text()

repository = '''from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

ConnectionFactory = Callable[[], sqlite3.Connection]


def find_node_endpoint_conflict(
    conn: sqlite3.Connection,
    host: str,
    ssh_port: int,
    canonicalize_host: Callable[[Any], str],
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    canonical_host = canonicalize_host(host)
    rows = conn.execute("SELECT id,name,host FROM nodes WHERE ssh_port=?", (ssh_port,)).fetchall()
    return next(
        (
            row for row in rows
            if row["id"] != exclude_id and canonicalize_host(row["host"]) == canonical_host
        ),
        None,
    )


def fetch_node_inventory(
    db: ConnectionFactory,
    cutoff: int,
) -> list[tuple[sqlite3.Row, sqlite3.Row | None, list[sqlite3.Row]]]:
    records: list[tuple[sqlite3.Row, sqlite3.Row | None, list[sqlite3.Row]]] = []
    with db() as conn:
        rows = conn.execute("""SELECT * FROM nodes
            ORDER BY CASE role WHEN 'hub' THEN 0 WHEN 'edge' THEN 1 WHEN 'exit' THEN 2 ELSE 3 END,
                     CASE WHEN last_seen>=? THEN 0 WHEN last_seen IS NULL THEN 2 ELSE 1 END,
                     name COLLATE NOCASE""", (cutoff,)).fetchall()
        for row in rows:
            metric = conn.execute(
                "SELECT * FROM metrics WHERE node_id=? ORDER BY ts DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            services = conn.execute(
                "SELECT name,status,last_check FROM node_services WHERE node_id=? ORDER BY name",
                (row["id"],),
            ).fetchall()
            records.append((row, metric, services))
    return records


def fetch_tunnel_inventory(
    db: ConnectionFactory,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
    with db() as conn:
        rows = conn.execute("""SELECT tunnels.*,nodes.name node_name,nodes.host node_host,
            nodes.observed_ip node_observed_ip,nodes.region,nodes.role node_role,
            nodes.last_seen node_last_seen,nodes.status node_agent_status,
            CASE WHEN nodes.ssh_password_enc IS NOT NULL OR nodes.ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END node_ssh_configured
            FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id ORDER BY tunnels.status DESC,tunnels.name""").fetchall()
        nodes = conn.execute("""SELECT id,name,host,observed_ip,region,role,status,last_seen,
            CASE WHEN ssh_password_enc IS NOT NULL OR ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END ssh_configured FROM nodes""").fetchall()
        deployments = conn.execute(
            """SELECT id,name,iran_node_id,kharej_node_id FROM plugin_deployments
               WHERE lifecycle NOT IN ('removed','rolled_back') ORDER BY id DESC"""
        ).fetchall()
    return rows, nodes, deployments


def fetch_tunnel_operation_rows(
    db: ConnectionFactory,
    *,
    tunnel_id: int,
    cutoff: int,
    node_ids: list[int],
    tunnel_name: str,
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], sqlite3.Row | None, sqlite3.Row | None]:
    with db() as conn:
        samples = conn.execute(
            """SELECT ts,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,
                      service_uptime,process_ok,path_ok
               FROM tunnel_samples WHERE tunnel_id=? AND ts>=? ORDER BY ts LIMIT 10000""",
            (tunnel_id, cutoff),
        ).fetchall()
        placeholders = ",".join("?" for _ in node_ids)
        job_rows = conn.execute(
            f"SELECT jobs.*,nodes.name node_name FROM jobs JOIN nodes ON nodes.id=jobs.node_id "
            f"WHERE jobs.node_id IN ({placeholders}) ORDER BY jobs.id DESC LIMIT 100",
            node_ids,
        ).fetchall()
        managed = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings FROM plugin_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel_name,),
        ).fetchone()
        hybrid = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings,remote_label FROM hybrid_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel_name,),
        ).fetchone()
    return samples, job_rows, managed, hybrid
'''
Path("hub/node_tunnel_repository.py").write_text(repository)

old_conflict = '''def node_endpoint_conflict(conn: sqlite3.Connection, host: str, ssh_port: int, exclude_id: int | None = None) -> sqlite3.Row | None:
    canonical_host = canonical_node_host(host)
    rows = conn.execute("SELECT id,name,host FROM nodes WHERE ssh_port=?", (ssh_port,)).fetchall()
    return next(
        (
            row for row in rows
            if row["id"] != exclude_id and canonical_node_host(row["host"]) == canonical_host
        ),
        None,
    )
'''
new_conflict = '''def node_endpoint_conflict(conn: sqlite3.Connection, host: str, ssh_port: int, exclude_id: int | None = None) -> sqlite3.Row | None:
    return find_node_endpoint_conflict(conn, host, ssh_port, canonical_node_host, exclude_id)
'''
assert old_conflict in text
text = text.replace(old_conflict, new_conflict, 1)

old_nodes = '''    with db() as conn:
        rows = conn.execute("""SELECT * FROM nodes
            ORDER BY CASE role WHEN 'hub' THEN 0 WHEN 'edge' THEN 1 WHEN 'exit' THEN 2 ELSE 3 END,
                     CASE WHEN last_seen>=? THEN 0 WHEN last_seen IS NULL THEN 2 ELSE 1 END,
                     name COLLATE NOCASE""", (utc_ts() - NODE_STALE_AFTER,)).fetchall()
        result = []
        for row in rows:
            metric = conn.execute("SELECT * FROM metrics WHERE node_id=? ORDER BY ts DESC LIMIT 1", (row["id"],)).fetchone()
            item = public_node(row, metric)
            try:
                item["plugins"] = json.loads(row["plugin_inventory"] or "{}")
            except (TypeError, ValueError):
                item["plugins"] = {}
            item["services"] = [dict(service) for service in conn.execute("SELECT name,status,last_check FROM node_services WHERE node_id=? ORDER BY name", (row["id"],)).fetchall()]
            if not row["last_seen"] or row["last_seen"] < utc_ts() - NODE_STALE_AFTER:
                item["status"] = "pending" if not row["last_seen"] else "offline"
            result.append(item)
'''
new_nodes = '''    records = fetch_node_inventory(db, utc_ts() - NODE_STALE_AFTER)
    result = []
    for row, metric, services in records:
        item = public_node(row, metric)
        try:
            item["plugins"] = json.loads(row["plugin_inventory"] or "{}")
        except (TypeError, ValueError):
            item["plugins"] = {}
        item["services"] = [dict(service) for service in services]
        if not row["last_seen"] or row["last_seen"] < utc_ts() - NODE_STALE_AFTER:
            item["status"] = "pending" if not row["last_seen"] else "offline"
        result.append(item)
'''
assert old_nodes in text
text = text.replace(old_nodes, new_nodes, 1)

old_tunnels = '''    with db() as conn:
        rows = conn.execute("""SELECT tunnels.*,nodes.name node_name,nodes.host node_host,
            nodes.observed_ip node_observed_ip,nodes.region,nodes.role node_role,
            nodes.last_seen node_last_seen,nodes.status node_agent_status,
            CASE WHEN nodes.ssh_password_enc IS NOT NULL OR nodes.ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END node_ssh_configured
            FROM tunnels JOIN nodes ON nodes.id=tunnels.node_id ORDER BY tunnels.status DESC,tunnels.name""").fetchall()
        nodes = conn.execute("""SELECT id,name,host,observed_ip,region,role,status,last_seen,
            CASE WHEN ssh_password_enc IS NOT NULL OR ssh_key_enc IS NOT NULL THEN 1 ELSE 0 END ssh_configured FROM nodes""").fetchall()
        deployments = conn.execute(
            """SELECT id,name,iran_node_id,kharej_node_id FROM plugin_deployments
               WHERE lifecycle NOT IN ('removed','rolled_back') ORDER BY id DESC"""
        ).fetchall()
'''
new_tunnels = '''    rows, nodes, deployments = fetch_tunnel_inventory(db)
'''
assert old_tunnels in text
text = text.replace(old_tunnels, new_tunnels, 1)

old_operations = '''    with db() as conn:
        samples = conn.execute(
            """SELECT ts,status,latency_ms,packet_loss,sessions,rx_bps,tx_bps,
                      service_uptime,process_ok,path_ok
               FROM tunnel_samples WHERE tunnel_id=? AND ts>=? ORDER BY ts LIMIT 10000""",
            (tunnel_id, cutoff),
        ).fetchall()
        placeholders = ",".join("?" for _ in node_ids)
        job_rows = conn.execute(
            f"SELECT jobs.*,nodes.name node_name FROM jobs JOIN nodes ON nodes.id=jobs.node_id "
            f"WHERE jobs.node_id IN ({placeholders}) ORDER BY jobs.id DESC LIMIT 100",
            node_ids,
        ).fetchall()
        managed = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings FROM plugin_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel["name"],),
        ).fetchone()
        hybrid = conn.execute(
            "SELECT id,plugin_id,lifecycle,created_at,settings,remote_label FROM hybrid_deployments WHERE name=? AND lifecycle!='removed' ORDER BY id DESC LIMIT 1",
            (tunnel["name"],),
        ).fetchone()
'''
new_operations = '''    samples, job_rows, managed, hybrid = fetch_tunnel_operation_rows(
        db,
        tunnel_id=tunnel_id,
        cutoff=cutoff,
        node_ids=node_ids,
        tunnel_name=tunnel["name"],
    )
'''
assert old_operations in text
text = text.replace(old_operations, new_operations, 1)

anchor = "bootstrap_database = _database_bootstrap_module.bootstrap_database\n"
assert anchor in text
loader = '''

_NODE_TUNNEL_REPOSITORY_PATH = Path(__file__).resolve().with_name('node_tunnel_repository.py')
_node_tunnel_repository_spec = _realm_support_importlib_util.spec_from_file_location('dark_noc_node_tunnel_repository', _NODE_TUNNEL_REPOSITORY_PATH)
if _node_tunnel_repository_spec is None or _node_tunnel_repository_spec.loader is None:
    raise ImportError(f'Could not load Node/Tunnel repository: {_NODE_TUNNEL_REPOSITORY_PATH}')
_node_tunnel_repository_module = _realm_support_importlib_util.module_from_spec(_node_tunnel_repository_spec)
_node_tunnel_repository_spec.loader.exec_module(_node_tunnel_repository_module)
for _repository_name in ['find_node_endpoint_conflict', 'fetch_node_inventory', 'fetch_tunnel_inventory', 'fetch_tunnel_operation_rows']:
    globals()[_repository_name] = getattr(_node_tunnel_repository_module, _repository_name)
del _repository_name
'''
text = text.replace(anchor, anchor + loader, 1)
app_path.write_text(text)

contract = '''from pathlib import Path
root = Path(__file__).resolve().parents[1]
app = (root / 'hub/app.py').read_text()
repo = (root / 'hub/node_tunnel_repository.py').read_text()
assert "with_name('node_tunnel_repository.py')" in app
assert '_node_tunnel_repository_spec.loader.exec_module' in app
for marker in ['def find_node_endpoint_conflict(', 'def fetch_node_inventory(', 'def fetch_tunnel_inventory(', 'def fetch_tunnel_operation_rows(']:
    assert marker in repo
assert 'fetch_node_inventory(db,' in app
assert 'fetch_tunnel_inventory(db)' in app
assert 'fetch_tunnel_operation_rows(' in app
assert "ORDER BY CASE role WHEN 'hub'" in repo
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' in repo
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' in repo
assert "ORDER BY CASE role WHEN 'hub'" not in app
assert 'FROM tunnel_samples WHERE tunnel_id=? AND ts>=?' not in app
assert 'SELECT id,name,host FROM nodes WHERE ssh_port=?' not in app
assert 'VERSION = "2.9.23"' in app
print('Node/Tunnel repository query boundary passed')
'''
Path("tests/backend_node_tunnel_repository_contract.py").write_text(contract)

identity_paths = [
    Path("hub/app.py"), Path("agent/agent.py"), Path("darknoc"),
    Path("install-hub.sh"), Path("install-node.sh"), Path("upgrade.sh"),
    Path("hub/static/index.html"),
]
identity_paths.extend(Path("tests").glob("*.py"))
for p in identity_paths:
    value = p.read_text()
    if "2.9.22" in value:
        p.write_text(value.replace("2.9.22", "2.9.23"))

notes = '''# DARK NOC v2.9.23 — Node & Tunnel Repository Query Layer

- Start the backend repository/query layer by extracting Node inventory, SSH endpoint-conflict lookup, Tunnel inventory and Tunnel Operations read queries from `hub/app.py` into `hub/node_tunnel_repository.py`.
- Keep response shaping, topology resolution, API routes and all mutation transactions in `hub/app.py` to preserve behavior and keep this split low-risk.
- Preserve SQLite transaction semantics, Agent behavior and frontend visuals.
- Add permanent Node/Tunnel repository ownership regression coverage.

## فارسی

Queryهای خواندنی Node و Tunnel از `hub/app.py` به `hub/node_tunnel_repository.py` منتقل شدند؛ Routeها، پاسخ API، عملیات تغییردهنده دیتابیس و ظاهر پنل بدون تغییر باقی مانده‌اند.

---

'''
p = Path("RELEASE_NOTES.md")
p.write_text(notes + p.read_text())
p = Path("CHANGELOG.md")
old = p.read_text()
first, sep, rest = old.partition("\n")
p.write_text(first + sep + "\n## 2.9.23 — Node and Tunnel repository query layer\n\n- Extract Node/Tunnel inventory and operations read queries into hub/node_tunnel_repository.py.\n- Preserve API response shaping and mutation transactions in hub/app.py.\n- Add repository boundary regression coverage.\n\n" + rest)
