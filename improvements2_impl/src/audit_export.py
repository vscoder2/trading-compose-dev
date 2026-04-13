"""Phase 4 observability export helpers.

Provides deterministic end-of-day (EOD) reporting with:
1. Exactly one row per (report_date, profile) via SQLite UPSERT.
2. Built-in turnover fields for execution-quality monitoring.
3. Optional free-form metadata payload for audit context.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EODReportRow:
    report_date: str
    profile: str
    start_equity: float
    end_equity: float
    max_drawdown_pct: float
    trade_count: int
    turnover_buy_notional: float
    turnover_sell_notional: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return float(self.end_equity) - float(self.start_equity)

    @property
    def return_pct(self) -> float:
        start = float(self.start_equity)
        if abs(start) < 1e-12:
            return 0.0
        return (self.pnl / start) * 100.0

    @property
    def turnover_total_notional(self) -> float:
        return float(self.turnover_buy_notional) + float(self.turnover_sell_notional)

    @property
    def turnover_ratio(self) -> float:
        start = max(1e-12, abs(float(self.start_equity)))
        return self.turnover_total_notional / start


def _conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_eod_tables(db_path: str | Path) -> None:
    db = Path(db_path)
    conn = _conn(db)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS eod_reports (
                report_date TEXT NOT NULL,
                profile TEXT NOT NULL,
                start_equity REAL NOT NULL,
                end_equity REAL NOT NULL,
                pnl REAL NOT NULL,
                return_pct REAL NOT NULL,
                max_drawdown_pct REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                turnover_buy_notional REAL NOT NULL,
                turnover_sell_notional REAL NOT NULL,
                turnover_total_notional REAL NOT NULL,
                turnover_ratio REAL NOT NULL,
                metadata_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                PRIMARY KEY (report_date, profile)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_eod_report(db_path: str | Path, row: EODReportRow) -> None:
    db = Path(db_path)
    ensure_eod_tables(db)
    conn = _conn(db)
    try:
        conn.execute(
            """
            INSERT INTO eod_reports(
                report_date, profile, start_equity, end_equity, pnl, return_pct,
                max_drawdown_pct, trade_count, turnover_buy_notional, turnover_sell_notional,
                turnover_total_notional, turnover_ratio, metadata_json, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, profile) DO UPDATE SET
                start_equity = excluded.start_equity,
                end_equity = excluded.end_equity,
                pnl = excluded.pnl,
                return_pct = excluded.return_pct,
                max_drawdown_pct = excluded.max_drawdown_pct,
                trade_count = excluded.trade_count,
                turnover_buy_notional = excluded.turnover_buy_notional,
                turnover_sell_notional = excluded.turnover_sell_notional,
                turnover_total_notional = excluded.turnover_total_notional,
                turnover_ratio = excluded.turnover_ratio,
                metadata_json = excluded.metadata_json,
                generated_at = excluded.generated_at
            """,
            (
                row.report_date,
                row.profile,
                float(row.start_equity),
                float(row.end_equity),
                float(row.pnl),
                float(row.return_pct),
                float(row.max_drawdown_pct),
                int(row.trade_count),
                float(row.turnover_buy_notional),
                float(row.turnover_sell_notional),
                float(row.turnover_total_notional),
                float(row.turnover_ratio),
                json.dumps(row.metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
                _utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_eod_reports(db_path: str | Path, profile: str | None = None) -> list[dict[str, Any]]:
    db = Path(db_path)
    ensure_eod_tables(db)
    conn = _conn(db)
    try:
        if profile:
            rows = conn.execute(
                """
                SELECT *
                FROM eod_reports
                WHERE profile = ?
                ORDER BY report_date ASC
                """,
                (profile,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM eod_reports
                ORDER BY profile ASC, report_date ASC
                """
            ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        payload = dict(r)
        payload["metadata"] = json.loads(str(payload.pop("metadata_json")))
        out.append(payload)
    return out


def build_eod_row(
    *,
    report_date: str,
    profile: str,
    start_equity: float,
    end_equity: float,
    max_drawdown_pct: float,
    trade_count: int,
    turnover_buy_notional: float,
    turnover_sell_notional: float,
    metadata: dict[str, Any] | None = None,
) -> EODReportRow:
    """Convenience builder to keep callsites explicit."""

    return EODReportRow(
        report_date=report_date,
        profile=profile,
        start_equity=float(start_equity),
        end_equity=float(end_equity),
        max_drawdown_pct=float(max_drawdown_pct),
        trade_count=int(trade_count),
        turnover_buy_notional=float(turnover_buy_notional),
        turnover_sell_notional=float(turnover_sell_notional),
        metadata=dict(metadata or {}),
    )


def row_to_dict(row: EODReportRow) -> dict[str, Any]:
    data = asdict(row)
    data["pnl"] = row.pnl
    data["return_pct"] = row.return_pct
    data["turnover_total_notional"] = row.turnover_total_notional
    data["turnover_ratio"] = row.turnover_ratio
    return data
