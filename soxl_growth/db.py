from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import sqlite3
from typing import Iterator

from soxl_growth.logging_setup import get_logger

logger = get_logger(__name__)


class StateStore:
    """SQLite-backed runtime state/event storage.

    The schema is deliberately compact and append-friendly so we can recover
    trading context after process restarts.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state_kv (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        logger.debug("Initialized state store schema at %s", self.path)

    def put(self, key: str, value: object) -> None:
        now = datetime.utcnow().isoformat()
        payload = json.dumps(value, sort_keys=True)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO state_kv(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (key, payload, now),
            )
            conn.commit()
        logger.debug("Stored state key=%s", key)

    def get(self, key: str, default: object | None = None) -> object | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value_json FROM state_kv WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row[0])

    def append_event(self, event_type: str, payload: object) -> None:
        ts = datetime.utcnow().isoformat()
        payload_json = json.dumps(payload, sort_keys=True)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO events(ts, event_type, payload_json) VALUES (?, ?, ?)",
                (ts, event_type, payload_json),
            )
            conn.commit()
        logger.info("Recorded event type=%s", event_type)

    def list_events(self, limit: int = 100) -> list[dict[str, object]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, event_type, payload_json FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[dict[str, object]] = []
        for ts, event_type, payload_json in rows:
            out.append(
                {
                    "ts": ts,
                    "event_type": event_type,
                    "payload": json.loads(payload_json),
                }
            )
        return out
