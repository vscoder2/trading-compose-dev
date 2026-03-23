#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Tuple


@dataclass
class Lot:
    qty: float
    price: float


def _f(x: str | None, default: float = 0.0) -> float:
    if x is None or x == "":
        return default
    return float(x)


def _load_fills(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r.get("transaction_time", ""))
    return rows


def _replay(rows: List[Dict[str, str]], initial_equity: float) -> Tuple[List[Dict], Dict]:
    cash = float(initial_equity)
    lots: Dict[str, Deque[Lot]] = defaultdict(deque)
    last_px: Dict[str, float] = {}
    realized_pnl = 0.0

    events: List[Dict] = []
    for r in rows:
        ts = r.get("transaction_time", "")
        symbol = (r.get("symbol") or "").upper()
        side = (r.get("side") or "").lower()
        qty = abs(_f(r.get("qty")))
        px = _f(r.get("price"))
        net_amount = _f(r.get("net_amount"), default=qty * px)
        fees = abs(net_amount) - (qty * px)
        fees = max(fees, 0.0)

        if qty <= 0 or px <= 0 or side not in {"buy", "sell"}:
            continue

        if side == "buy":
            cash -= qty * px + fees
            lots[symbol].append(Lot(qty=qty, price=px))
            trade_realized = 0.0
        else:
            cash += qty * px - fees
            remaining = qty
            trade_realized = 0.0
            while remaining > 1e-12 and lots[symbol]:
                lot = lots[symbol][0]
                take = min(remaining, lot.qty)
                trade_realized += (px - lot.price) * take
                lot.qty -= take
                remaining -= take
                if lot.qty <= 1e-12:
                    lots[symbol].popleft()
            # If short-sell without lots, treat as zero-cost remainder to avoid crash.
            if remaining > 1e-12:
                trade_realized += px * remaining
            trade_realized -= fees
            realized_pnl += trade_realized

        last_px[symbol] = px
        market_value = 0.0
        positions = {}
        for sym, qlots in lots.items():
            q = sum(x.qty for x in qlots)
            if q <= 1e-12:
                continue
            lp = last_px.get(sym, 0.0)
            mv = q * lp
            market_value += mv
            positions[sym] = {"qty": q, "last_price": lp, "market_value": mv}

        equity = cash + market_value
        events.append(
            {
                "timestamp": ts,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": px,
                "fees": fees,
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "trade_realized_pnl": trade_realized,
                "cum_realized_pnl": realized_pnl,
                "positions_json": json.dumps(positions, separators=(",", ":")),
            }
        )

    final_equity = events[-1]["equity"] if events else initial_equity
    total_return_pct = ((final_equity / initial_equity) - 1.0) * 100.0 if initial_equity else 0.0
    high = initial_equity
    max_dd_pct = 0.0
    for e in events:
        eq = e["equity"]
        high = max(high, eq)
        if high > 0:
            dd = ((high - eq) / high) * 100.0
            max_dd_pct = max(max_dd_pct, dd)

    summary = {
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "realized_pnl": realized_pnl,
        "max_drawdown_pct": max_dd_pct,
        "events": len(events),
    }
    return events, summary


def _write_events(path: Path, events: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "timestamp",
        "symbol",
        "side",
        "qty",
        "price",
        "fees",
        "cash",
        "market_value",
        "equity",
        "trade_realized_pnl",
        "cum_realized_pnl",
        "positions_json",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for e in events:
            w.writerow(e)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay broker fills and compute realized parity metrics.")
    p.add_argument("--fills-csv", required=True)
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--out-prefix", required=True, help="prefix path without suffix")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    fills_path = Path(args.fills_csv)
    rows = _load_fills(fills_path)
    events, summary = _replay(rows, initial_equity=args.initial_equity)

    out_prefix = Path(args.out_prefix)
    events_csv = out_prefix.with_name(out_prefix.name + "_events.csv")
    summary_json = out_prefix.with_name(out_prefix.name + "_summary.json")
    _write_events(events_csv, events)
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "fills_csv": str(fills_path),
                "events_csv": str(events_csv),
                "summary_json": str(summary_json),
                "summary": summary,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

