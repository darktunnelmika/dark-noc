from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DARK_NOC_DATA", "/var/lib/dark-noc"))
DB_PATH = DATA_DIR / "dark-noc.db"

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'owner', created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL, ip TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, region TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'edge', host TEXT NOT NULL, ssh_port INTEGER NOT NULL DEFAULT 22,
  ssh_user TEXT NOT NULL DEFAULT 'root', ssh_password_enc TEXT, ssh_key_enc TEXT,
  agent_token_hash TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'pending', agent_version TEXT,
  observed_ip TEXT, plugin_inventory TEXT NOT NULL DEFAULT '{}',
  provision_status TEXT, provision_output TEXT,
  ssh_host_fingerprint TEXT, autoheal_enabled INTEGER NOT NULL DEFAULT 0,
  autoheal_cooldown INTEGER NOT NULL DEFAULT 300, autoheal_max_restarts INTEGER NOT NULL DEFAULT 3,
  last_seen INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, cpu REAL, ram REAL, swap REAL, disk REAL, load1 REAL,
  rx_bps REAL, tx_bps REAL, uptime INTEGER, connections INTEGER, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_node_ts ON metrics(node_id, ts DESC);
CREATE TABLE IF NOT EXISTS node_services (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, status TEXT NOT NULL, last_check INTEGER NOT NULL, UNIQUE(node_id,name)
);
CREATE TABLE IF NOT EXISTS tunnels (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, method TEXT, target TEXT, service TEXT, listen_port INTEGER,
  status TEXT, latency_ms REAL, packet_loss REAL, sessions INTEGER, rx_bps REAL, tx_bps REAL,
  last_check INTEGER, details TEXT, failure_streak INTEGER NOT NULL DEFAULT 0, UNIQUE(node_id, name)
);
CREATE TABLE IF NOT EXISTS tunnel_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tunnel_id INTEGER NOT NULL REFERENCES tunnels(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, status TEXT NOT NULL, latency_ms REAL, packet_loss REAL,
  sessions INTEGER, rx_bps REAL, tx_bps REAL, service_uptime INTEGER,
  process_ok INTEGER, path_ok INTEGER
);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
  tunnel_id INTEGER REFERENCES tunnels(id) ON DELETE SET NULL, severity TEXT NOT NULL,
  title TEXT NOT NULL, detail TEXT, status TEXT NOT NULL DEFAULT 'open', opened_at INTEGER NOT NULL,
  acknowledged_at INTEGER, resolved_at INTEGER, last_changed_at INTEGER,
  root_cause TEXT, resolution TEXT, autoheal_stage INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incident_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  output TEXT, created_by INTEGER, created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS plugin_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plugin_id TEXT NOT NULL, name TEXT NOT NULL,
  iran_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kharej_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  iran_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  kharej_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  settings TEXT NOT NULL, pair_token_enc TEXT, lifecycle TEXT NOT NULL DEFAULT 'active', created_by INTEGER, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, plugin_id TEXT NOT NULL, name TEXT NOT NULL,
  iran_node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  iran_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  iran_endpoint TEXT NOT NULL, remote_label TEXT NOT NULL DEFAULT 'KHAREJ',
  settings TEXT NOT NULL, pair_token_enc TEXT NOT NULL, pair_code_hash TEXT NOT NULL,
  lifecycle TEXT NOT NULL DEFAULT 'active', created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS certificates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', issuer TEXT NOT NULL DEFAULT 'letsencrypt',
  cert_path TEXT, key_path TEXT, expires_at INTEGER, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  last_error TEXT, created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(node_id,domain)
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL,
  target TEXT, detail TEXT, ip TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS monitors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL, port INTEGER,
  secret_enc TEXT, snmp_oid TEXT,
  interval_seconds INTEGER NOT NULL DEFAULT 60, timeout_seconds INTEGER NOT NULL DEFAULT 5,
  expected_status INTEGER, enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'pending',
  latency_ms REAL, detail TEXT, failure_streak INTEGER NOT NULL DEFAULT 0,
  next_run_at INTEGER NOT NULL, last_run_at INTEGER, job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  created_by INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(node_id,name)
);
CREATE TABLE IF NOT EXISTS monitor_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
  ts INTEGER NOT NULL, status TEXT NOT NULL, latency_ms REAL, detail TEXT
);
CREATE TABLE IF NOT EXISTS fleet_operations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'scheduled',
  scheduled_at INTEGER NOT NULL, created_by INTEGER, created_at INTEGER NOT NULL,
  started_at INTEGER, finished_at INTEGER
);
CREATE TABLE IF NOT EXISTS fleet_operation_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id INTEGER NOT NULL REFERENCES fleet_operations(id) ON DELETE CASCADE,
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL, status TEXT NOT NULL DEFAULT 'scheduled',
  UNIQUE(operation_id,node_id)
);
CREATE TABLE IF NOT EXISTS hub_leases (
  name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_rollups (
  node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, bucket INTEGER NOT NULL,
  samples INTEGER NOT NULL, cpu_avg REAL, ram_avg REAL, disk_max REAL, load_avg REAL,
  rx_avg REAL, tx_avg REAL, connections_max INTEGER,
  PRIMARY KEY(node_id,bucket)
);
CREATE INDEX IF NOT EXISTS idx_jobs_node_status ON jobs(node_id,status,id);
CREATE INDEX IF NOT EXISTS idx_incidents_status_opened ON incidents(status,opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_tunnels_node ON tunnels(node_id);
CREATE INDEX IF NOT EXISTS idx_tunnel_samples_tunnel_ts ON tunnel_samples(tunnel_id,ts DESC);
CREATE INDEX IF NOT EXISTS idx_hybrid_node ON hybrid_deployments(iran_node_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_certificates_node ON certificates(node_id,status);
CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id,created_at,id);
CREATE INDEX IF NOT EXISTS idx_monitors_due ON monitors(enabled,next_run_at);
CREATE INDEX IF NOT EXISTS idx_monitor_results_monitor_ts ON monitor_results(monitor_id,ts DESC);
CREATE INDEX IF NOT EXISTS idx_fleet_operations_due ON fleet_operations(status,scheduled_at);
CREATE INDEX IF NOT EXISTS idx_metric_rollups_bucket ON metric_rollups(bucket);
"""
