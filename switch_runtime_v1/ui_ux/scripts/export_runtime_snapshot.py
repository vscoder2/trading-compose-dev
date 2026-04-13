#!/usr/bin/env python3
"""Export runtime SQLite events/state into a frontend-friendly JSON snapshot.

This script is intentionally standalone so the React UI can consume runtime data
without coupling to Streamlit internals.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path(
    os.getenv(
        "RUNTIME_DB_PATH",
        "/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_data/users/demo_trader/switch_runtime_v1_runtime.db",
    )
)
DEFAULT_OUT = Path(
    "/home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui_ux/src/app/data/runtime_snapshot.json"
)

PROFILE_LABELS: dict[str, str] = {
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m": "Semiconductor Momentum Pro",
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1": "Semiconductor Momentum Pro",
}


@dataclass
class PositionState:
    qty: float = 0.0
    cost_total: float = 0.0


def _safe_json_load(text: str | None) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _pick_price(payload: dict[str, Any], fallback: float = 0.0) -> float:
    keys = [
        "price",
        "trigger_price",
        "trail_stop_price",
        "stop_loss_price",
        "take_profit_price",
        "close",
    ]
    for key in keys:
        val = payload.get(key)
        try:
            v = float(val)
            if v > 0:
                return v
        except Exception:
            continue
    return fallback


def _symbol_name(symbol: str) -> str:
    names = {
        "SOXL": "Direxion Daily Semiconductor Bull 3X Shares",
        "SOXS": "Direxion Daily Semiconductor Bear 3X Shares",
        "TQQQ": "ProShares UltraPro QQQ",
        "SQQQ": "ProShares UltraPro Short QQQ",
        "SPXL": "Direxion Daily S&P 500 Bull 3X Shares",
        "SPXS": "Direxion Daily S&P 500 Bear 3X Shares",
        "TMV": "Direxion Daily 20+ Year Treasury Bear 3X Shares",
        "TMF": "Direxion Daily 20+ Year Treasury Bull 3X Shares",
    }
    return names.get(symbol, symbol)


def _friendly_profile(profile: str) -> str:
    raw = str(profile or "").strip()
    if not raw:
        return "Runtime Strategy"
    if raw in PROFILE_LABELS:
        return PROFILE_LABELS[raw]
    out = raw
    for pid, label in PROFILE_LABELS.items():
        if pid in out:
            out = out.replace(pid, label)
    return out


def _friendly_strategy(raw_strategy: str) -> str:
    raw = str(raw_strategy or "").strip()
    if not raw:
        return "Runtime Strategy"
    if "| variant=" in raw:
        base, variant = raw.split("| variant=", 1)
        variant_title = str(variant or "").strip().replace("_", " ").replace("-", " ").title() or "Baseline"
        return f"{_friendly_profile(base.strip())} ({variant_title})"
    return _friendly_profile(raw)


def export_snapshot(db_path: Path, output_path: Path, trade_limit: int = 200) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"Runtime DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        events = conn.execute(
            "SELECT id, ts, event_type, payload_json FROM events ORDER BY id ASC"
        ).fetchall()
        state_rows = conn.execute(
            "SELECT key, value_json, updated_at FROM state_kv ORDER BY key"
        ).fetchall()
    finally:
        conn.close()

    state = {str(r["key"]): _safe_json_load(r["value_json"]) for r in state_rows}

    # Trade reconstruction from runtime order/exit events.
    positions: dict[str, PositionState] = defaultdict(PositionState)
    symbol_prices: dict[str, list[float]] = defaultdict(list)
    symbol_volumes: dict[str, float] = defaultdict(float)
    symbol_last_ts: dict[str, str] = {}
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []

    realized_pnl = 0.0

    for row in events:
        payload = _safe_json_load(row["payload_json"]) if row["payload_json"] else {}
        event_type = str(row["event_type"])
        ts = str(row["ts"])

        if event_type == "switch_cycle_complete":
            val = None
            if isinstance(payload, dict):
                metrics = payload.get("regime_metrics") or {}
                close = metrics.get("close") if isinstance(metrics, dict) else None
                if close is not None:
                    try:
                        val = float(close)
                    except Exception:
                        val = None
            if val is not None:
                curve.append({"date": ts[:10], "value": round(val, 4)})

        if event_type not in {
            "switch_rebalance_order",
            "switch_profit_lock_intraday_close",
            "switch_profit_lock_close",
        }:
            continue

        if not isinstance(payload, dict):
            continue

        symbol = str(payload.get("symbol") or "").upper().strip()
        side = str(payload.get("side") or "").lower().strip()
        if not symbol or side not in {"buy", "sell"}:
            continue

        qty = float(payload.get("qty") or 0.0)
        if qty <= 0:
            continue

        current_fallback = symbol_prices[symbol][-1] if symbol_prices[symbol] else 0.0
        px = _pick_price(payload, fallback=current_fallback)
        symbol_last_ts[symbol] = ts
        symbol_volumes[symbol] += qty
        if px > 0:
            symbol_prices[symbol].append(px)

        ps = positions[symbol]
        avg = (ps.cost_total / ps.qty) if ps.qty > 0 else 0.0

        if side == "buy":
            # Maintain weighted average cost.
            ps.qty += qty
            if px > 0:
                ps.cost_total += qty * px
            else:
                ps.cost_total += qty * avg
            t_type = "buy"
            trade_pnl = None
        else:
            matched_qty = min(ps.qty, qty)
            if matched_qty > 0 and px > 0:
                realized_pnl += (px - avg) * matched_qty
            if ps.qty > 0 and matched_qty > 0:
                ps.cost_total -= avg * matched_qty
            ps.qty = max(0.0, ps.qty - qty)
            if ps.qty <= 1e-9:
                ps.qty = 0.0
                ps.cost_total = 0.0
            t_type = "sell"
            trade_pnl = round((px - avg) * matched_qty, 4) if (matched_qty > 0 and px > 0) else None

        trades.append(
            {
                "id": str(row["id"]),
                "symbol": symbol,
                "type": t_type,
                "shares": round(qty, 6),
                "price": round(px, 6) if px > 0 else 0.0,
                "timestamp": ts,
                "strategy": _friendly_strategy(str(payload.get("profile") or state.get("switch_last_profile") or "runtime")),
                "profit": trade_pnl,
            }
        )

    trades = trades[-trade_limit:]

    stocks: list[dict[str, Any]] = []
    all_symbols = sorted(set(symbol_prices.keys()) | set(positions.keys()) | set(symbol_volumes.keys()))
    for sym in all_symbols:
        prices = symbol_prices[sym]
        last_px = prices[-1] if prices else 0.0
        first_px = prices[0] if prices else last_px
        chg = last_px - first_px
        chg_pct = (chg / first_px * 100.0) if first_px > 0 else 0.0
        stocks.append(
            {
                "symbol": sym,
                "name": _symbol_name(sym),
                "price": round(last_px, 6),
                "change": round(chg, 6),
                "changePercent": round(chg_pct, 4),
                "volume": int(round(symbol_volumes[sym])),
                "marketCap": "N/A",
            }
        )

    positions_out: list[dict[str, Any]] = []
    portfolio_value = 0.0
    for sym, ps in sorted(positions.items()):
        if ps.qty <= 1e-6:
            continue
        avg_price = (ps.cost_total / ps.qty) if ps.qty > 0 else 0.0
        prices = symbol_prices.get(sym) or []
        cur = prices[-1] if prices else avg_price
        total = ps.qty * cur
        gl = total - ps.cost_total
        gl_pct = (gl / ps.cost_total * 100.0) if ps.cost_total > 0 else 0.0
        portfolio_value += total
        positions_out.append(
            {
                "symbol": sym,
                "name": _symbol_name(sym),
                "shares": round(ps.qty, 6),
                "avgPrice": round(avg_price, 6),
                "currentPrice": round(cur, 6),
                "totalValue": round(total, 6),
                "gainLoss": round(gl, 6),
                "gainLossPercent": round(gl_pct, 4),
            }
        )

    demo_eq = state.get("switch_demo_equity") if isinstance(state.get("switch_demo_equity"), dict) else {}
    end_equity = float(demo_eq.get("end_equity") or portfolio_value or 0.0)
    start_equity = float(demo_eq.get("start_equity") or end_equity or 0.0)
    pnl = float(demo_eq.get("pnl") or (end_equity - start_equity))
    return_pct = float(demo_eq.get("return_pct") or ((pnl / start_equity * 100.0) if start_equity > 0 else 0.0))
    drawdown_pct = float(demo_eq.get("drawdown_pct") or 0.0)

    # Single-bot projection from profile/variant state.
    profile_id = str(state.get("switch_last_profile") or "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    profile = _friendly_profile(profile_id)
    variant = str(state.get("switch_last_variant") or "baseline")

    bots = [
        {
            "id": "runtime-1",
            "name": "Runtime Switch Engine",
            "strategy": _friendly_strategy(f"{profile_id} | variant={variant}"),
            "status": "active",
            "totalTrades": len(trades),
            "winRate": 0.0,
            "profit": round(realized_pnl, 6),
            "riskLevel": "medium",
        }
    ]

    latest_day = max((t["timestamp"][:10] for t in trades), default="")
    todays_trades = [t for t in trades if t["timestamp"].startswith(latest_day)] if latest_day else []

    summary = {
        "portfolioValue": round(end_equity, 6),
        "todayPnL": round(pnl, 6),
        "todayPnLPct": round(return_pct, 4),
        "activeBots": 1,
        "totalBotProfit": round(realized_pnl, 6),
        "todaysTrades": len(todays_trades),
        "openAlerts": 0,
        "maxDrawdownPct": round(drawdown_pct, 4),
        "profile": profile,
        "profileId": profile_id,
        "variant": variant,
    }

    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDb": str(db_path),
        "summary": summary,
        "stocks": stocks,
        "positions": positions_out,
        "trades": trades,
        "bots": bots,
        "portfolioChart": curve[-180:] if curve else [],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export runtime DB snapshot for ui_ux")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Path to runtime sqlite db")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path")
    p.add_argument("--trade-limit", type=int, default=200, help="Max trades to include")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = Path(args.db).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    snapshot = export_snapshot(db, out, trade_limit=max(20, int(args.trade_limit)))
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "db": str(db),
                "stocks": len(snapshot.get("stocks", [])),
                "positions": len(snapshot.get("positions", [])),
                "trades": len(snapshot.get("trades", [])),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
