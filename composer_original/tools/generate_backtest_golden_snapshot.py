#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSER_ORIGINAL_DIR = ROOT / "composer_original"
FIXTURE = COMPOSER_ORIGINAL_DIR / "fixtures" / "deep_check_prices.csv"
OUT = COMPOSER_ORIGINAL_DIR / "spec" / "backtest_golden_snapshot.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import BacktestConfig


def _load_history(path: Path) -> dict[str, list[tuple[date, float]]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        symbols = [c for c in (r.fieldnames or []) if c != "date"]
        hist: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
        for row in r:
            d = date.fromisoformat(row["date"])
            for s in symbols:
                hist[s].append((d, float(row[s])))
    return hist


def main() -> int:
    history = _load_history(FIXTURE)
    cfg = BacktestConfig(initial_equity=100_000.0, warmup_days=260, slippage_bps=1.0, sell_fee_bps=0.0)
    result = run_backtest(history, cfg)

    alloc = result.allocations
    mid_i = len(alloc) // 2 if alloc else 0
    snapshot = {
        "fixture_csv": "composer_original/fixtures/deep_check_prices.csv",
        "config": {
            "initial_equity": 100_000.0,
            "warmup_days": 260,
            "slippage_bps": 1.0,
            "sell_fee_bps": 0.0,
        },
        "summary": {
            "equity_points": len(result.equity_curve),
            "allocation_days": len(result.allocations),
            "trade_count": len(result.trades),
            "final_equity": result.final_equity,
            "total_return_pct": result.total_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "cagr_pct": result.cagr_pct,
            "avg_daily_return_pct": result.avg_daily_return_pct,
        },
        "allocation_samples": {
            "first": {"date": alloc[0][0].isoformat(), "weights": alloc[0][1]} if alloc else None,
            "middle": {"date": alloc[mid_i][0].isoformat(), "weights": alloc[mid_i][1]} if alloc else None,
            "last": {"date": alloc[-1][0].isoformat(), "weights": alloc[-1][1]} if alloc else None,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "snapshot": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
