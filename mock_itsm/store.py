"""SQLite storage for the mock incident system."""

import sqlite3
import threading
from datetime import datetime, timezone

_COLUMNS = (
    "number", "external_id", "short_description", "description", "priority", "category",
    "application", "caller_name", "caller_email", "state", "opened_at", "created_at",
)


class IncidentStore:
    def __init__(self, path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT UNIQUE,
                    external_id TEXT NOT NULL UNIQUE,
                    short_description TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    category TEXT,
                    application TEXT,
                    caller_name TEXT,
                    caller_email TEXT,
                    state TEXT NOT NULL,
                    opened_at TEXT,
                    created_at TEXT NOT NULL
                )"""
            )

    def create(self, data):
        """Insert an incident. Returns (record, created); created is False for a repeated external_id."""
        with self._lock:
            existing = self._one("SELECT * FROM incidents WHERE external_id = ?", (data["external_id"],))
            if existing:
                return existing, False
            row = {
                **{k: data.get(k) for k in _COLUMNS},
                "state": "New",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            with self._conn:  # one transaction: insert + assign number
                cursor = self._conn.execute(
                    f"INSERT INTO incidents ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                    [row[k] for k in _COLUMNS],
                )
                number = f"INC{cursor.lastrowid:07d}"
                self._conn.execute("UPDATE incidents SET number = ? WHERE id = ?", (number, cursor.lastrowid))
            return self.get(number), True

    def get(self, number):
        with self._lock:
            return self._one("SELECT * FROM incidents WHERE number = ?", (number,))

    def list(self, limit=50):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._record(r) for r in rows]

    def _one(self, sql, params):
        row = self._conn.execute(sql, params).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row):
        record = dict(row)
        record.pop("id")
        return record
