"""SQLite state adapter for Phase 2 control-plane tables.

Design goals:
1. Additive schema migrations only.
2. Transactional writes for deterministic state updates.
3. Minimal external dependencies (stdlib only).
4. Read/write helpers focused on lock lifecycle and decision/audit writes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import DriftRecord, LockState, OpenOrder


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ControlPlaneStore:
    """State adapter for additive control-plane persistence."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_migration(
        self,
        migration_id: str | Path = "001_control_plane",
        sql_file: str | Path | None = None,
    ) -> bool:
        """Apply migration if not yet applied.

        Returns True if migration was newly applied, False if already present.
        """

        # Allow convenience call style: apply_migration(Path(".../001_x.sql")).
        mig_id: str
        if isinstance(migration_id, Path):
            if sql_file is not None:
                raise ValueError("when migration_id is a Path, sql_file must be None")
            sql_path = Path(migration_id)
            mig_id = sql_path.stem
        else:
            mig_id = str(migration_id)
            sql_path = (
                Path(sql_file)
                if sql_file is not None
                else Path(__file__).resolve().parents[1] / "migrations" / "001_control_plane.sql"
            )

        if not sql_path.exists():
            raise FileNotFoundError(f"migration file not found: {sql_path}")
        script = sql_path.read_text(encoding="utf-8")
        sha = _file_sha256(sql_path)

        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    sha256 TEXT NOT NULL
                )
                """
            )
            row = conn.execute(
                "SELECT migration_id, sha256 FROM schema_migrations WHERE migration_id = ?",
                (mig_id,),
            ).fetchone()
            if row is not None:
                return False
            conn.executescript(script)
            conn.execute(
                "INSERT INTO schema_migrations(migration_id, applied_at, sha256) VALUES (?, ?, ?)",
                (mig_id, _utc_now_iso(), sha),
            )
        return True

    def list_tables(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return [str(r["name"]) for r in rows]

    # ------------------------------
    # Lock lifecycle
    # ------------------------------
    def put_lock(
        self,
        *,
        lock_type: str,
        scope: str,
        subject: str | None,
        reason: str,
        metadata: dict[str, Any] | None = None,
        expiry_ts: str | None = None,
    ) -> int:
        """Upsert one active lock keyed by (lock_type, scope, subject)."""

        metadata_json = _json_dumps(metadata or {})
        subject_norm = subject.upper() if subject else None
        now = _utc_now_iso()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT lock_id
                FROM locks
                WHERE lock_type = ? AND scope = ? AND IFNULL(subject, '') = IFNULL(?, '') AND state = 'active'
                ORDER BY lock_id DESC
                LIMIT 1
                """,
                (lock_type, scope, subject_norm),
            ).fetchone()
            if row is not None:
                lock_id = int(row["lock_id"])
                conn.execute(
                    """
                    UPDATE locks
                    SET reason = ?, metadata_json = ?, expiry_ts = ?, updated_at = ?
                    WHERE lock_id = ?
                    """,
                    (reason, metadata_json, expiry_ts, now, lock_id),
                )
                return lock_id

            cur = conn.execute(
                """
                INSERT INTO locks(lock_type, scope, subject, state, reason, metadata_json, start_ts, expiry_ts, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (lock_type, scope, subject_norm, reason, metadata_json, now, expiry_ts, now),
            )
            return int(cur.lastrowid)

    def get_active_locks(self, *, lock_type: str | None = None, subject: str | None = None) -> list[LockState]:
        sql = "SELECT lock_type, scope, subject, state, reason, metadata_json FROM locks WHERE state = 'active'"
        params: list[Any] = []
        if lock_type:
            sql += " AND lock_type = ?"
            params.append(lock_type)
        if subject:
            sql += " AND subject = ?"
            params.append(subject.upper())
        sql += " ORDER BY lock_id ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[LockState] = []
        for r in rows:
            out.append(
                LockState(
                    lock_type=str(r["lock_type"]),
                    scope=str(r["scope"]),
                    subject=str(r["subject"]) if r["subject"] is not None else None,
                    active=True,
                    reason=str(r["reason"]),
                    metadata=_json_loads(str(r["metadata_json"]), {}),
                )
            )
        return out

    def clear_lock(self, lock_id: int, *, cleared_state: str = "cleared") -> bool:
        if cleared_state not in {"cleared", "expired"}:
            raise ValueError("cleared_state must be 'cleared' or 'expired'")
        now = _utc_now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE locks
                SET state = ?, cleared_ts = ?, updated_at = ?
                WHERE lock_id = ? AND state = 'active'
                """,
                (cleared_state, now, now, int(lock_id)),
            )
            return int(cur.rowcount) > 0

    def expire_due_locks(self, now_ts: str | None = None) -> int:
        now = now_ts or _utc_now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE locks
                SET state = 'expired', cleared_ts = ?, updated_at = ?
                WHERE state = 'active' AND expiry_ts IS NOT NULL AND expiry_ts <= ?
                """,
                (now, now, now),
            )
            return int(cur.rowcount)

    # ------------------------------
    # Decision + reason ledger
    # ------------------------------
    def put_decision_cycle(
        self,
        *,
        cycle_id: str,
        cycle_type: str,
        profile: str,
        target: dict[str, Any],
        effective_target: dict[str, Any],
        decision_hash: str,
        severity: str,
        details: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        stamp = ts or _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO decision_cycles(
                    cycle_id, ts, cycle_type, profile, target_json, effective_target_json, decision_hash, severity, details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    ts=excluded.ts,
                    cycle_type=excluded.cycle_type,
                    profile=excluded.profile,
                    target_json=excluded.target_json,
                    effective_target_json=excluded.effective_target_json,
                    decision_hash=excluded.decision_hash,
                    severity=excluded.severity,
                    details_json=excluded.details_json
                """,
                (
                    cycle_id,
                    stamp,
                    cycle_type,
                    profile,
                    _json_dumps(target),
                    _json_dumps(effective_target),
                    decision_hash,
                    severity,
                    _json_dumps(details or {}),
                ),
            )

    def append_decision_reason(
        self,
        *,
        cycle_id: str,
        reason_code: str,
        priority_class: str,
        symbol: str | None = None,
        detail: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> int:
        stamp = ts or _utc_now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO decision_reasons(cycle_id, symbol, reason_code, priority_class, detail_json, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    symbol.upper() if symbol else None,
                    reason_code,
                    priority_class,
                    _json_dumps(detail or {}),
                    stamp,
                ),
            )
            return int(cur.lastrowid)

    # ------------------------------
    # Open-order and drift tables
    # ------------------------------
    def upsert_open_order(self, order: OpenOrder, *, state: str, metadata: dict[str, Any] | None = None) -> None:
        now = _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO open_order_state(order_id, symbol, side, qty, state, created_ts, updated_ts, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    side=excluded.side,
                    qty=excluded.qty,
                    state=excluded.state,
                    updated_ts=excluded.updated_ts,
                    metadata_json=excluded.metadata_json
                """,
                (
                    str(order.order_id),
                    str(order.symbol).upper(),
                    str(order.side).lower(),
                    float(order.qty),
                    state,
                    order.created_ts.isoformat() if order.created_ts else None,
                    now,
                    _json_dumps(metadata or {}),
                ),
            )

    def list_open_orders(self, *, symbol: str | None = None) -> list[OpenOrder]:
        sql = "SELECT order_id, symbol, side, qty, state, created_ts FROM open_order_state"
        params: list[Any] = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY order_id ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[OpenOrder] = []
        for r in rows:
            created = None
            if r["created_ts"]:
                created = datetime.fromisoformat(str(r["created_ts"]))
            out.append(
                OpenOrder(
                    order_id=str(r["order_id"]),
                    symbol=str(r["symbol"]),
                    side=str(r["side"]),
                    qty=float(r["qty"]),
                    status=str(r["state"]),
                    created_ts=created,
                )
            )
        return out

    def remove_open_order(self, order_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM open_order_state WHERE order_id = ?", (str(order_id),))
            return int(cur.rowcount) > 0

    def append_drift_records(self, *, cycle_id: str | None, rows: list[DriftRecord], detail: dict[str, Any] | None = None) -> int:
        if not rows:
            return 0
        count = 0
        stamp = _utc_now_iso()
        with self._conn() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO drift_snapshots(
                        ts, cycle_id, symbol, expected_qty, broker_qty, qty_drift, unexpected_open_orders, severity, resolved, detail_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        stamp,
                        cycle_id,
                        row.symbol.upper(),
                        float(row.expected_qty),
                        float(row.broker_qty),
                        float(row.qty_drift),
                        int(row.unexpected_open_orders),
                        str(row.severity),
                        _json_dumps(detail or {}),
                    ),
                )
                count += 1
        return count

    # ------------------------------
    # Risk + session + shadow
    # ------------------------------
    def put_risk_state(
        self,
        *,
        equity: float,
        peak_equity: float,
        drawdown_pct: float,
        exposure_scalar: float,
        dd_brake_state: str,
        recovery_phase: str | None = None,
        detail: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> str:
        stamp = ts or _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO risk_state(
                    ts, equity, peak_equity, drawdown_pct, exposure_scalar, dd_brake_state, recovery_phase, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    float(equity),
                    float(peak_equity),
                    float(drawdown_pct),
                    float(exposure_scalar),
                    dd_brake_state,
                    recovery_phase,
                    _json_dumps(detail or {}),
                ),
            )
        return stamp

    def put_session_state(
        self,
        *,
        session_date: str,
        session_pnl: float,
        breaker_state: str,
        reentry_blocked_symbols: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now_iso()
        normalized = sorted({s.upper() for s in (reentry_blocked_symbols or [])})
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO session_state(session_date, session_pnl, breaker_state, reentry_blocked_json, detail_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    session_pnl=excluded.session_pnl,
                    breaker_state=excluded.breaker_state,
                    reentry_blocked_json=excluded.reentry_blocked_json,
                    detail_json=excluded.detail_json,
                    updated_at=excluded.updated_at
                """,
                (
                    session_date,
                    float(session_pnl),
                    breaker_state,
                    _json_dumps(normalized),
                    _json_dumps(detail or {}),
                    now,
                ),
            )

    def append_shadow_cycle(
        self,
        *,
        cycle_id: str,
        variant_name: str,
        effective_target: dict[str, Any],
        hypothetical_actions: list[dict[str, Any]],
        diff: dict[str, Any],
        ts: str | None = None,
    ) -> int:
        stamp = ts or _utc_now_iso()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO shadow_cycles(
                    cycle_id, variant_name, effective_target_json, hypothetical_actions_json, diff_json, ts
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    variant_name,
                    _json_dumps(effective_target),
                    _json_dumps(hypothetical_actions),
                    _json_dumps(diff),
                    stamp,
                ),
            )
            return int(cur.lastrowid)

    # ------------------------------
    # Diagnostics helpers
    # ------------------------------
    def count_rows(self, table_name: str) -> int:
        with self._conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()
            return int(row["c"]) if row else 0

    def dump_active_locks(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT lock_id, lock_type, scope, subject, state, reason, metadata_json, start_ts, expiry_ts, cleared_ts, updated_at
                FROM locks
                WHERE state = 'active'
                ORDER BY lock_id ASC
                """
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "lock_id": int(r["lock_id"]),
                    "lock_type": str(r["lock_type"]),
                    "scope": str(r["scope"]),
                    "subject": str(r["subject"]) if r["subject"] is not None else None,
                    "state": str(r["state"]),
                    "reason": str(r["reason"]),
                    "metadata": _json_loads(str(r["metadata_json"]), {}),
                    "start_ts": str(r["start_ts"]),
                    "expiry_ts": str(r["expiry_ts"]) if r["expiry_ts"] is not None else None,
                    "cleared_ts": str(r["cleared_ts"]) if r["cleared_ts"] is not None else None,
                    "updated_at": str(r["updated_at"]),
                }
            )
        return out

    def dump_decision_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM decision_cycles WHERE cycle_id = ?", (cycle_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["target_json"] = _json_loads(str(payload.get("target_json")), {})
        payload["effective_target_json"] = _json_loads(str(payload.get("effective_target_json")), {})
        payload["details_json"] = _json_loads(str(payload.get("details_json")), {})
        return payload
