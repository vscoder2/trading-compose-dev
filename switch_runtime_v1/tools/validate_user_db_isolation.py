#!/usr/bin/env python3
"""Validate per-user runtime DB isolation for switch_runtime_v1.

This tool is intentionally independent from strategy logic. It inspects user-scoped
state/event DB files and verifies that marker events written for each user remain
inside that user's DB only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class UserDbSnapshot:
    user: str
    db_path: Path
    exists: bool
    event_count: int
    state_count: int


def _user_slug(username: str) -> str:
    raw = str(username or "").strip().lower()
    if not raw:
        return "anonymous"
    out = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in raw)
    out = out.strip("._-")
    return out or "anonymous"


def _user_db_path(root: Path, username: str) -> Path:
    return root / "switch_runtime_v1" / "runtime_data" / "users" / _user_slug(username) / "switch_runtime_v1_runtime.db"


def _ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state_kv (
                key TEXT PRIMARY KEY,
                value_json TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                event_type TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)")
        conn.commit()
    finally:
        conn.close()


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _snapshot(user: str, db_path: Path) -> UserDbSnapshot:
    if not db_path.exists():
        return UserDbSnapshot(user=user, db_path=db_path, exists=False, event_count=0, state_count=0)
    conn = sqlite3.connect(str(db_path))
    try:
        return UserDbSnapshot(
            user=user,
            db_path=db_path,
            exists=True,
            event_count=_count_table(conn, "events"),
            state_count=_count_table(conn, "state_kv"),
        )
    finally:
        conn.close()


def _insert_marker(db_path: Path, owner_user: str, marker_id: str) -> None:
    payload = {
        "owner_user": owner_user,
        "marker_id": marker_id,
        "note": "isolation_check_marker",
    }
    now = datetime.now(tz=timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO events (ts, event_type, payload_json) VALUES (?, ?, ?)",
            (now, "isolation_check_marker", json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()


def _count_marker_hits(db_path: Path, marker_id: str) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM events
            WHERE event_type = 'isolation_check_marker'
              AND payload_json LIKE ?
            """,
            (f'%"marker_id": "{marker_id}"%',),
        ).fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _format_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    headers = ("User", "DB Exists", "Events", "State Keys", "DB Path")
    all_rows = [headers, *rows]
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(headers))]

    def _fmt(r: Iterable[str]) -> str:
        parts = [str(v).ljust(widths[i]) for i, v in enumerate(r)]
        return " | ".join(parts)

    sep = "-+-".join("-" * w for w in widths)
    out = [_fmt(headers), sep]
    out.extend(_fmt(r) for r in rows)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate strict per-user DB isolation for switch_runtime_v1")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]), help="Project root")
    parser.add_argument("--users", nargs="+", required=True, help="Usernames to validate")
    parser.add_argument("--write-markers", action="store_true", help="Insert marker events and verify cross-DB isolation")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    users = [str(u).strip() for u in args.users if str(u).strip()]
    if len(users) < 2:
        print("Need at least two users for meaningful isolation validation.")
        return 2

    paths = {u: _user_db_path(root, u) for u in users}
    for p in paths.values():
        _ensure_schema(p)

    snaps_before = [_snapshot(u, paths[u]) for u in users]
    rows_before = [
        (s.user, "yes" if s.exists else "no", str(s.event_count), str(s.state_count), str(s.db_path))
        for s in snaps_before
    ]
    print("\n[Before]")
    print(_format_table(rows_before))

    if not args.write_markers:
        print("\nMarker write check skipped (--write-markers not set).")
        return 0

    marker_ids: dict[str, str] = {}
    for u in users:
        marker_id = f"iso_{_user_slug(u)}_{int(datetime.now(tz=timezone.utc).timestamp())}"
        marker_ids[u] = marker_id
        _insert_marker(paths[u], owner_user=u, marker_id=marker_id)

    contamination = 0
    print("\n[Marker Cross-Check]")
    for owner in users:
        marker_id = marker_ids[owner]
        for target in users:
            hits = _count_marker_hits(paths[target], marker_id)
            expected = 1 if owner == target else 0
            status = "OK" if hits == expected else "FAIL"
            if status == "FAIL":
                contamination += 1
            print(f"marker_owner={owner:>12} target_db={target:>12} hits={hits} expected={expected} status={status}")

    snaps_after = [_snapshot(u, paths[u]) for u in users]
    rows_after = [
        (s.user, "yes" if s.exists else "no", str(s.event_count), str(s.state_count), str(s.db_path))
        for s in snaps_after
    ]
    print("\n[After]")
    print(_format_table(rows_after))

    if contamination:
        print(f"\nIsolation validation FAILED: {contamination} contamination checks mismatched.")
        return 1

    print("\nIsolation validation PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
