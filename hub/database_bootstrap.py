from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


def bootstrap_database(
    *,
    DATA_DIR: Path,
    KEY_PATH: Path,
    db: Any,
    SCHEMA: str,
    utc_ts: Any,
    hash_password: Any,
    finalize_fleet_operation: Any,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        os.chmod(KEY_PATH, 0o600)
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        deployment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(plugin_deployments)").fetchall()}
        if "pair_token_enc" not in deployment_columns:
            conn.execute("ALTER TABLE plugin_deployments ADD COLUMN pair_token_enc TEXT")
        if "lifecycle" not in deployment_columns:
            conn.execute("ALTER TABLE plugin_deployments ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'active'")
        tunnel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tunnels)").fetchall()}
        if "failure_streak" not in tunnel_columns:
            conn.execute("ALTER TABLE tunnels ADD COLUMN failure_streak INTEGER NOT NULL DEFAULT 0")
        node_columns = {row["name"] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "ssh_host_fingerprint" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN ssh_host_fingerprint TEXT")
        if "observed_ip" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN observed_ip TEXT")
        if "plugin_inventory" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN plugin_inventory TEXT NOT NULL DEFAULT '{}'")
        if "provision_status" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN provision_status TEXT")
        if "provision_output" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN provision_output TEXT")
        if "autoheal_enabled" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_enabled INTEGER NOT NULL DEFAULT 0")
        if "autoheal_cooldown" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_cooldown INTEGER NOT NULL DEFAULT 300")
        if "autoheal_max_restarts" not in node_columns:
            conn.execute("ALTER TABLE nodes ADD COLUMN autoheal_max_restarts INTEGER NOT NULL DEFAULT 3")
        incident_columns = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)").fetchall()}
        for name, definition in {
            "acknowledged_at": "INTEGER",
            "last_changed_at": "INTEGER",
            "root_cause": "TEXT",
            "resolution": "TEXT",
        }.items():
            if name not in incident_columns:
                conn.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
        conn.execute("UPDATE incidents SET last_changed_at=COALESCE(last_changed_at,resolved_at,opened_at)")
        conn.execute(
            """INSERT INTO incident_events(incident_id,event_type,message,data,created_at)
               SELECT incidents.id,'detected','Incident detected',COALESCE(incidents.detail,'{}'),incidents.opened_at
               FROM incidents
               WHERE NOT EXISTS (SELECT 1 FROM incident_events WHERE incident_events.incident_id=incidents.id)"""
        )
        interrupted_message = "Provisioning was interrupted by a Hub restart before completion. Retry INSTALL/SYNC AGENT."
        conn.execute(
            """UPDATE nodes
               SET provision_status='failed',
                   provision_output=CASE
                     WHEN TRIM(COALESCE(provision_output,''))=''
                       THEN ?
                     ELSE SUBSTR(provision_output || CHAR(10) || ?, -12000)
                   END,
                   updated_at=?
               WHERE provision_status='provisioning'""",
            (interrupted_message, interrupted_message, utc_ts()),
        )
        conn.execute(
            """UPDATE fleet_operation_items SET status='failed'
               WHERE status='running' AND job_id IS NULL"""
        )
        interrupted_operations = conn.execute(
            "SELECT id FROM fleet_operations WHERE status='running'"
        ).fetchall()
        for operation in interrupted_operations:
            finalize_fleet_operation(conn, operation["id"])
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password = os.getenv("DARK_NOC_ADMIN_PASSWORD")
            if not password or len(password) < 10:
                raise RuntimeError("DARK_NOC_ADMIN_PASSWORD must contain at least 10 characters")
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                (os.getenv("DARK_NOC_ADMIN_USER", "admin"), hash_password(password), "owner", utc_ts()),
            )
