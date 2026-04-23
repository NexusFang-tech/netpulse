"""SQLite storage for NetPulse metrics and events."""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS ssid_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    ssid_name TEXT NOT NULL,
    client_count INTEGER NOT NULL,
    usage_mb REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ssid_metrics_ts ON ssid_metrics(ts);
CREATE INDEX IF NOT EXISTS idx_ssid_metrics_ssid_ts ON ssid_metrics(ssid_name, ts);

CREATE TABLE IF NOT EXISTS probe_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    probe_type TEXT NOT NULL,
    target TEXT NOT NULL,
    success INTEGER NOT NULL,
    latency_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe_results_ts ON probe_results(ts);
CREATE INDEX IF NOT EXISTS idx_probe_results_type_ts ON probe_results(probe_type, ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    external_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    pattern TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    auto_detected INTEGER DEFAULT 1,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_incidents_started ON incidents(started_at);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
        finally:
            c.close()

    # -- Writers -----------------------------------------------------------
    def insert_ssid_metric(self, ssid_name: str, client_count: int, usage_mb: float = 0) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO ssid_metrics (ts, ssid_name, client_count, usage_mb) VALUES (?, ?, ?, ?)",
                (int(time.time()), ssid_name, client_count, usage_mb),
            )

    def insert_probe(self, probe_type: str, target: str, success: bool,
                     latency_ms: float | None = None, error: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO probe_results (ts, probe_type, target, success, latency_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(time.time()), probe_type, target, 1 if success else 0, latency_ms, error),
            )

    def insert_event(self, source: str, severity: str, category: str,
                     title: str, detail: str | None = None,
                     external_id: str | None = None) -> bool:
        """Returns True if inserted, False if duplicate external_id."""
        try:
            with self.conn() as c:
                c.execute(
                    "INSERT INTO events (ts, source, severity, category, title, detail, external_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(time.time()), source, severity, category, title, detail, external_id),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def open_incident(self, pattern: str, severity: str, summary: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO incidents (started_at, pattern, severity, summary) VALUES (?, ?, ?, ?)",
                (int(time.time()), pattern, severity, summary),
            )
            return cur.lastrowid

    def close_incident(self, incident_id: int) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE incidents SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (int(time.time()), incident_id),
            )

    def acknowledge_incident(self, incident_id: int) -> None:
        with self.conn() as c:
            c.execute("UPDATE incidents SET acknowledged = 1 WHERE id = ?", (incident_id,))

    def get_open_incident(self, pattern: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM incidents WHERE pattern = ? AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
                (pattern,),
            ).fetchone()
            return dict(row) if row else None

    # -- Readers -----------------------------------------------------------
    def get_ssid_history(self, ssid_name: str, since_ts: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT ts, client_count, usage_mb FROM ssid_metrics "
                "WHERE ssid_name = ? AND ts >= ? ORDER BY ts ASC",
                (ssid_name, since_ts),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_ssid_metric(self, ssid_name: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT ts, client_count, usage_mb FROM ssid_metrics "
                "WHERE ssid_name = ? ORDER BY ts DESC LIMIT 1",
                (ssid_name,),
            ).fetchone()
            return dict(row) if row else None

    def get_probe_history(self, probe_type: str, since_ts: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT ts, target, success, latency_ms, error FROM probe_results "
                "WHERE probe_type = ? AND ts >= ? ORDER BY ts ASC",
                (probe_type, since_ts),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_probe(self, probe_type: str) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT ts, target, success, latency_ms, error FROM probe_results "
                "WHERE probe_type = ? ORDER BY ts DESC LIMIT 1",
                (probe_type,),
            ).fetchone()
            return dict(row) if row else None

    def get_recent_events(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_incidents(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Maintenance -------------------------------------------------------
    def prune(self, retention_days: int) -> dict:
        cutoff = int(time.time()) - (retention_days * 86400)
        with self.conn() as c:
            r1 = c.execute("DELETE FROM ssid_metrics WHERE ts < ?", (cutoff,))
            r2 = c.execute("DELETE FROM probe_results WHERE ts < ?", (cutoff,))
            r3 = c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return {
                "ssid_metrics": r1.rowcount,
                "probe_results": r2.rowcount,
                "events": r3.rowcount,
            }
