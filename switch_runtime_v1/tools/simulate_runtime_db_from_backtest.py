#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
DEFAULT_PROFILE = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"


@dataclass(frozen=True)
class DailyRow:
    day: str
    start_equity: float
    end_equity: float
    pnl: float
    ret_pct: float
    drawdown_pct: float
    holdings: dict[str, float]
    notes: str
    variant: str
    variant_reason: str


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def _parse_notes(notes: str) -> tuple[str, str]:
    variant = "baseline"
    reason = "derived_from_notes"
    for token in str(notes or "").split(";"):
        token = token.strip()
        if token.startswith("variant="):
            variant = token.split("=", 1)[1].strip() or variant
        elif token.startswith("variant_reason="):
            reason = token.split("=", 1)[1].strip() or reason
    return variant, reason


def _parse_holdings(raw: str) -> dict[str, float]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in obj.items():
        qty = _to_float(v, 0.0)
        if abs(qty) > 1e-12:
            out[str(k).upper()] = qty
    return out


def _load_daily_rows(path: Path) -> list[DailyRow]:
    rows: list[DailyRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            day = str(r.get("Date", "")).strip()
            if not day:
                continue
            notes = str(r.get("Notes", "") or "")
            variant, variant_reason = _parse_notes(notes)
            rows.append(
                DailyRow(
                    day=day,
                    start_equity=_to_float(r.get("Start Equity"), 0.0),
                    end_equity=_to_float(r.get("End Equity"), 0.0),
                    pnl=_to_float(r.get("PnL"), 0.0),
                    ret_pct=_to_float(r.get("Return %"), 0.0),
                    drawdown_pct=_to_float(r.get("Drawdown %"), 0.0),
                    holdings=_parse_holdings(str(r.get("Holdings", "") or "")),
                    notes=notes,
                    variant=variant,
                    variant_reason=variant_reason,
                )
            )
    rows.sort(key=lambda x: x.day)
    return rows


def _ny_to_utc_iso(day: str, hh: int, mm: int) -> str:
    dt_ny = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=NY)
    return dt_ny.astimezone(timezone.utc).isoformat()


def _threshold_from_drawdown(drawdown_pct: float) -> float:
    # Stable synthetic threshold shaping so analytics tabs have a realistic curve.
    raw = 10.0 + max(0.0, drawdown_pct) / 8.0
    return max(8.0, min(30.0, raw))


def _target_weights(holdings: dict[str, float]) -> dict[str, float]:
    if not holdings:
        return {}
    total = sum(abs(q) for q in holdings.values())
    if total <= 0:
        return {}
    return {s: abs(q) / total for s, q in holdings.items()}


def _init_schema(conn: sqlite3.Connection) -> None:
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


def _put_state(conn: sqlite3.Connection, key: str, value: Any, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO state_kv(key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """,
        (key, json.dumps(value, sort_keys=True), updated_at),
    )


def _append_event(conn: sqlite3.Connection, ts: str, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO events(ts, event_type, payload_json) VALUES (?, ?, ?)",
        (ts, event_type, json.dumps(payload, sort_keys=True)),
    )


def _make_regime_metrics(row: DailyRow) -> dict[str, Any]:
    close = row.end_equity
    ret = row.ret_pct
    dd = row.drawdown_pct
    ma20 = close / max(0.2, 1.0 + (ret / 100.0))
    ma60 = ma20 * 0.97
    ma200 = ma20 * 0.90
    return {
        "close": round(close, 6),
        "ma20": round(ma20, 6),
        "ma60": round(ma60, 6),
        "ma200": round(ma200, 6),
        "slope20_pct": round(ret / 20.0, 6),
        "slope60_pct": round(ret / 60.0, 6),
        "rv20_ann": round(min(200.0, 20.0 + dd * 1.7), 6),
        "crossovers20": int(max(0, math.floor(abs(row.pnl) / 500.0))),
        "dd20_pct": round(dd, 6),
    }


def _simulate_into_db(rows: list[DailyRow], out_db: Path, profile: str) -> dict[str, Any]:
    if out_db.exists():
        out_db.unlink()
    out_db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(out_db))
    try:
        _init_schema(conn)
        prev_variant = "baseline"
        prev_holdings: dict[str, float] = {}
        switch_state = {
            "current_variant": "baseline",
            "cond2_streak": 0,
            "cond3_streak": 0,
            "cond2_false_streak": 0,
            "forced_baseline_days": 0,
            "high_vol_lock": False,
        }
        event_count = 0

        for row in rows:
            t_variant = _ny_to_utc_iso(row.day, 15, 54)
            t_intraday = _ny_to_utc_iso(row.day, 13, 8)
            t_rebal = _ny_to_utc_iso(row.day, 15, 55)
            threshold = _threshold_from_drawdown(row.drawdown_pct)
            final_target = _target_weights(row.holdings)
            baseline_target = dict(final_target)

            if row.variant != prev_variant:
                _append_event(
                    conn,
                    t_variant,
                    "switch_variant_changed",
                    {"from": prev_variant, "to": row.variant, "reason": row.variant_reason},
                )
                event_count += 1
                prev_variant = row.variant

            all_symbols = sorted(set(prev_holdings.keys()) | set(row.holdings.keys()))
            intent_count = 0
            submitted = 0

            # Optional intraday close when notes indicate optimistic trail touch.
            if "optimistic_trail_touch" in row.notes and prev_holdings:
                top_sym = max(prev_holdings, key=lambda s: abs(prev_holdings[s]))
                top_qty = abs(prev_holdings.get(top_sym, 0.0))
                if top_qty > 0:
                    _append_event(
                        conn,
                        t_intraday,
                        "switch_profit_lock_intraday_close",
                        {
                            "symbol": top_sym,
                            "side": "sell",
                            "qty": round(top_qty * 0.25, 8),
                            "profile": profile,
                            "variant": row.variant,
                            "order_type": "market_order",
                            "threshold_pct": threshold,
                            "trigger_price": round(max(1.0, row.start_equity * 0.004), 6),
                            "trail_stop_price": round(max(1.0, row.start_equity * 0.00392), 6),
                        },
                    )
                    event_count += 1
                    intent_count += 1
                    submitted += 1

            for sym in all_symbols:
                old_q = float(prev_holdings.get(sym, 0.0))
                new_q = float(row.holdings.get(sym, 0.0))
                delta = new_q - old_q
                if abs(delta) < 1e-10:
                    continue
                side = "buy" if delta > 0 else "sell"
                _append_event(
                    conn,
                    t_rebal,
                    "switch_rebalance_order",
                    {
                        "symbol": sym,
                        "side": side,
                        "qty": round(abs(delta), 8),
                        "target_weight": round(float(final_target.get(sym, 0.0)), 8),
                        "profile": profile,
                        "variant": row.variant,
                        "order_type": "market",
                        "threshold_pct": threshold,
                        "take_profit_price": None,
                        "stop_loss_price": None,
                    },
                )
                event_count += 1
                intent_count += 1
                submitted += 1

            cycle_payload = {
                "ts": t_rebal,
                "day": row.day,
                "profile": profile,
                "variant": row.variant,
                "variant_reason": row.variant_reason,
                "inverse_note": row.notes,
                "threshold_pct": threshold,
                "profit_lock_closed_symbols": [],
                "profit_lock_order_type": "market_order",
                "rebalance_order_type": "market",
                "intent_count": intent_count,
                "orders_submitted": submitted,
                "execute_orders": False,
                "regime_metrics": _make_regime_metrics(row),
            }
            _append_event(conn, t_rebal, "switch_cycle_complete", cycle_payload)
            event_count += 1

            switch_state["current_variant"] = row.variant
            switch_state["cond2_streak"] = int(switch_state["cond2_streak"]) + (1 if row.variant == "inverse_ma20" else 0)
            switch_state["cond3_streak"] = int(switch_state["cond3_streak"]) + (1 if row.variant == "inverse_ma60" else 0)
            switch_state["cond2_false_streak"] = int(switch_state["cond2_false_streak"]) + (1 if row.variant == "baseline" else 0)
            switch_state["high_vol_lock"] = bool(row.drawdown_pct >= 25.0)

            _put_state(conn, "switch_executed_day", row.day, t_rebal)
            _put_state(conn, "switch_last_profile", profile, t_rebal)
            _put_state(conn, "switch_last_variant", row.variant, t_rebal)
            _put_state(conn, "switch_last_baseline_target", baseline_target, t_rebal)
            _put_state(conn, "switch_last_final_target", final_target, t_rebal)
            _put_state(conn, "switch_regime_state", switch_state, t_rebal)
            _put_state(
                conn,
                "switch_demo_equity",
                {
                    "start_equity": row.start_equity,
                    "end_equity": row.end_equity,
                    "pnl": row.pnl,
                    "return_pct": row.ret_pct,
                    "drawdown_pct": row.drawdown_pct,
                },
                t_rebal,
            )

            prev_holdings = dict(row.holdings)

        conn.commit()
        return {"rows": len(rows), "events": event_count, "db": str(out_db)}
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a runtime-style demo SQLite DB from switch backtest daily CSV so UI tabs are fully populated."
    )
    p.add_argument("--daily-csv", required=True, help="Input daily CSV from switch_runtime_v1 reports.")
    p.add_argument(
        "--out-db",
        default="/home/chewy/projects/trading-compose-dev/switch_runtime_v1/switch_runtime_v1_runtime.db",
        help="Output SQLite DB path for UI loading.",
    )
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--limit-days", type=int, default=0, help="Optional: keep only last N days (0 = all rows).")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    rows = _load_daily_rows(Path(args.daily_csv))
    if not rows:
        raise SystemExit("No rows found in input CSV.")
    if args.limit_days and args.limit_days > 0:
        rows = rows[-int(args.limit_days) :]
    summary = _simulate_into_db(rows, Path(args.out_db), profile=str(args.profile))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

