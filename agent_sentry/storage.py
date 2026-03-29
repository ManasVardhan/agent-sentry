"""SQLite backend for agent-sentry event storage."""

import sqlite3
import json
import threading
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from contextlib import contextmanager


DEFAULT_DB_PATH = os.path.expanduser("~/.agent-sentry/events.db")

_local = threading.local()


class EventStore:
    """SQLite-backed store for agent events."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(_local, "connections"):
            _local.connections = {}
        if self.db_path not in _local.connections:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            _local.connections[self.db_path] = conn
        return _local.connections[self.db_path]

    @contextmanager
    def _cursor(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self):
        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    function_name TEXT,
                    args_json TEXT,
                    result_json TEXT,
                    error_message TEXT,
                    error_type TEXT,
                    traceback TEXT,
                    duration_ms REAL,
                    success INTEGER NOT NULL DEFAULT 1,
                    token_usage_json TEXT,
                    cost REAL,
                    root_cause TEXT,
                    metadata_json TEXT,
                    tags TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_success ON events(success)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_root_cause ON events(root_cause)
            """)

    def store_event(self, event: Dict[str, Any]) -> None:
        """Store a single event."""
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO events
                (event_id, timestamp, event_type, function_name, args_json,
                 result_json, error_message, error_type, traceback, duration_ms,
                 success, token_usage_json, cost, root_cause, metadata_json, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event["event_id"],
                event.get("timestamp", datetime.now(timezone.utc).isoformat()),
                event["event_type"],
                event.get("function_name"),
                json.dumps(event.get("args")) if event.get("args") else None,
                json.dumps(event.get("result")) if event.get("result") else None,
                event.get("error_message"),
                event.get("error_type"),
                event.get("traceback"),
                event.get("duration_ms"),
                1 if event.get("success", True) else 0,
                json.dumps(event.get("token_usage")) if event.get("token_usage") else None,
                event.get("cost"),
                event.get("root_cause"),
                json.dumps(event.get("metadata")) if event.get("metadata") else None,
                json.dumps(event.get("tags")) if event.get("tags") else None,
            ))

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
        success: Optional[bool] = None,
        since: Optional[str] = None,
        root_cause: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query events with optional filters."""
        query = "SELECT * FROM events WHERE 1=1"
        params: list = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if success is not None:
            query += " AND success = ?"
            params.append(1 if success else 0)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if root_cause:
            query += " AND root_cause = ?"
            params.append(root_cause)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_failure_count(self, since: Optional[str] = None) -> int:
        """Count failed events."""
        query = "SELECT COUNT(*) FROM events WHERE success = 0"
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        with self._cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]

    def get_total_count(self, since: Optional[str] = None) -> int:
        """Count all events."""
        query = "SELECT COUNT(*) FROM events WHERE 1=1"
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        with self._cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()[0]

    def get_reliability_score(self, since: Optional[str] = None) -> float:
        """Calculate reliability score (0-100) based on success rate."""
        total = self.get_total_count(since)
        if total == 0:
            return 100.0
        failures = self.get_failure_count(since)
        return round((1 - failures / total) * 100, 2)

    def get_failure_breakdown(self, since: Optional[str] = None) -> Dict[str, int]:
        """Get failure counts grouped by root cause."""
        query = "SELECT root_cause, COUNT(*) as cnt FROM events WHERE success = 0"
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " GROUP BY root_cause ORDER BY cnt DESC"
        with self._cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return {(row[0] or "unknown"): row[1] for row in rows}

    def get_event_type_breakdown(self, since: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        """Get success/failure counts grouped by event type."""
        query = """
            SELECT event_type, success, COUNT(*) as cnt
            FROM events WHERE 1=1
        """
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        query += " GROUP BY event_type, success"
        with self._cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        result: Dict[str, Dict[str, int]] = {}
        for row in rows:
            et = row[0]
            if et not in result:
                result[et] = {"success": 0, "failure": 0}
            if row[1]:
                result[et]["success"] = row[2]
            else:
                result[et]["failure"] = row[2]
        return result

    def health_check(self) -> Dict[str, Any]:
        """Run a health check on the event store.

        Returns a dict with status, db_path, event_count, db_size_bytes,
        and writable flag. Useful for monitoring and diagnostics.
        """
        result: Dict[str, Any] = {
            "status": "unknown",
            "db_path": self.db_path,
            "event_count": 0,
            "db_size_bytes": 0,
            "writable": False,
        }
        try:
            result["event_count"] = self.get_total_count()
            result["db_size_bytes"] = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

            # Check write capability with a no-op
            with self._cursor() as cur:
                cur.execute("SELECT 1")
            result["writable"] = True
            result["status"] = "healthy"
        except Exception as exc:
            result["status"] = "unhealthy"
            result["error"] = str(exc)
        return result

    def get_summary(self, since: Optional[str] = None) -> Dict[str, Any]:
        """Get a high-level summary of the event store.

        Returns total events, failures, reliability score, top root causes,
        and event type breakdown.
        """
        total = self.get_total_count(since)
        failures = self.get_failure_count(since)
        reliability = self.get_reliability_score(since)
        breakdown = self.get_failure_breakdown(since)
        types = self.get_event_type_breakdown(since)

        # Compute average duration
        query = "SELECT AVG(duration_ms) FROM events WHERE duration_ms IS NOT NULL"
        params: list = []
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        with self._cursor() as cur:
            cur.execute(query, params)
            avg_duration = cur.fetchone()[0]

        return {
            "total_events": total,
            "failures": failures,
            "reliability_score": reliability,
            "avg_duration_ms": round(avg_duration, 2) if avg_duration else None,
            "top_root_causes": dict(list(breakdown.items())[:5]),
            "event_types": types,
        }

    def clear(self) -> None:
        """Delete all events."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM events")

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for key in ("args_json", "result_json", "token_usage_json", "metadata_json", "tags"):
            if d.get(key):
                try:
                    d[key.replace("_json", "")] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key.replace("_json", "")] = d[key]
            else:
                d[key.replace("_json", "")] = None
        d["success"] = bool(d["success"])
        return d


# Global default store
_default_store: Optional[EventStore] = None
_store_lock = threading.Lock()


def get_store(db_path: Optional[str] = None) -> EventStore:
    """Get or create the default event store."""
    global _default_store
    if db_path:
        return EventStore(db_path)
    with _store_lock:
        if _default_store is None:
            _default_store = EventStore()
        return _default_store


def reset_default_store() -> None:
    """Reset the default store (useful for testing)."""
    global _default_store
    with _store_lock:
        _default_store = None
