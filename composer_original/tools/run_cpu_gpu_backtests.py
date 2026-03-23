#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import BacktestConfig


@dataclass
class GpuReplayResult:
    equity_curve: list[tuple[str, float]]
    final_equity: float
    total_return_pct: float
    trade_count: int
    allocation_days: int


def _load_history(path: Path) -> dict[str, list[tuple[date, float]]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or r.fieldnames[0].lower() != "date":
            raise ValueError("CSV must have first column named 'date'")
        symbols = [c for c in r.fieldnames if c.lower() != "date"]
        hist: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
        for row in r:
            d = date.fromisoformat(row["date"])
            for s in symbols:
                hist[s].append((d, float(row[s])))
    return hist


def _ensure_cuda_env_for_cupy() -> None:
    # In this repo setup, NVRTC can be installed into the local venv.
    # If CUDA_PATH is missing, point it at that package location.
    if os.getenv("CUDA_PATH"):
        return
    venv_nvrtc = ROOT / "composer_original" / ".venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cuda_nvrtc"
    lib_dir = venv_nvrtc / "lib"
    if venv_nvrtc.exists() and lib_dir.exists():
        os.environ["CUDA_PATH"] = str(venv_nvrtc)
        current = os.getenv("LD_LIBRARY_PATH", "")
        prefix = str(lib_dir)
        os.environ["LD_LIBRARY_PATH"] = f"{prefix}:{current}" if current else prefix


def _gpu_replay_from_cpu_allocations(
    price_history: dict[str, list[tuple[date, float]]],
    cpu_allocations: list[tuple[date, dict[str, float]]],
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
) -> GpuReplayResult:
    _ensure_cuda_env_for_cupy()
    import cupy as cp

    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    dates = [price_history[symbols[0]][i][0] for i in range(n)]
    prices_np = [[price_history[s][i][1] for s in symbols] for i in range(n)]
    prices = cp.asarray(prices_np, dtype=cp.float64)

    alloc_by_day = {d.isoformat(): w for d, w in cpu_allocations}
    symbol_to_idx = {s: i for i, s in enumerate(symbols)}
    targets = cp.zeros((n, len(symbols)), dtype=cp.float64)
    for i, d in enumerate(dates):
        weights = alloc_by_day.get(d.isoformat(), {})
        if not weights:
            continue
        row = cp.zeros((len(symbols),), dtype=cp.float64)
        for sym, w in weights.items():
            if sym in symbol_to_idx:
                row[symbol_to_idx[sym]] = float(w)
        targets[i] = row

    cash = float(initial_equity)
    holdings = cp.zeros((len(symbols),), dtype=cp.float64)
    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trade_count = 0
    equity_curve: list[tuple[str, float]] = []

    for i, d in enumerate(dates):
        px = prices[i]
        equity_before = cash + float(cp.sum(holdings * px).item())
        tgt = targets[i]
        if float(cp.sum(tgt).item()) <= 0.0:
            equity_curve.append((d.isoformat(), equity_before))
            continue

        current_notional = holdings * px
        target_notional = tgt * equity_before
        delta_notional = target_notional - current_notional
        qty = cp.abs(delta_notional / px)
        sell_mask = delta_notional < 0
        buy_mask = delta_notional > 0

        # Sells first.
        if bool(cp.any(sell_mask).item()):
            sell_qty = cp.minimum(qty, cp.maximum(holdings, 0.0))
            exec_sell_price = px * (1.0 - slip)
            notional = sell_qty * exec_sell_price * sell_mask
            fee = cp.abs(notional) * sell_fee
            cash += float(cp.sum(notional - fee).item())
            holdings = holdings - (sell_qty * sell_mask)
            trade_count += int(cp.sum((sell_qty * sell_mask) > 0).item())

        # Buys in symbol-sorted order, capped by available cash (matches CPU semantics better).
        exec_buy_price = px * (1.0 + slip)
        qty_host = cp.asnumpy(qty)
        buy_mask_host = cp.asnumpy(buy_mask)
        exec_buy_price_host = cp.asnumpy(exec_buy_price)
        for sym_idx in range(len(symbols)):
            if not bool(buy_mask_host[sym_idx]):
                continue
            desired_qty = float(qty_host[sym_idx])
            px_exec = float(exec_buy_price_host[sym_idx])
            if px_exec <= 0:
                continue
            max_affordable = cash / px_exec
            actual_qty = min(desired_qty, max_affordable)
            if actual_qty <= 0:
                continue
            cash -= actual_qty * px_exec
            holdings[sym_idx] = holdings[sym_idx] + actual_qty
            trade_count += 1

        equity_after = cash + float(cp.sum(holdings * px).item())
        equity_curve.append((d.isoformat(), equity_after))

    final_equity = equity_curve[-1][1] if equity_curve else 0.0
    start_equity = equity_curve[0][1] if equity_curve else 0.0
    total_return_pct = 100.0 * (final_equity / start_equity - 1.0) if start_equity > 0 else 0.0
    return GpuReplayResult(
        equity_curve=equity_curve,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        trade_count=trade_count,
        allocation_days=len(cpu_allocations),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CPU and GPU backtests for composer original strategy")
    parser.add_argument("--prices-csv", default=str(ROOT / "composer_original" / "fixtures" / "deep_check_prices.csv"))
    parser.add_argument("--initial-equity", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=260)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--sell-fee-bps", type=float, default=0.0)
    parser.add_argument("--include-equity-curve", action="store_true", help="Include full GPU equity curve in report JSON")
    parser.add_argument("--output-json", default=str(ROOT / "composer_original" / "reports" / "cpu_gpu_backtest_report.json"))
    args = parser.parse_args()

    price_history = _load_history(Path(args.prices_csv))
    cfg = BacktestConfig(
        initial_equity=args.initial_equity,
        warmup_days=args.warmup_days,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
    )
    cpu = run_backtest(price_history=price_history, config=cfg)
    gpu = _gpu_replay_from_cpu_allocations(
        price_history=price_history,
        cpu_allocations=cpu.allocations,
        initial_equity=args.initial_equity,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
    )

    cpu_final = float(cpu.final_equity)
    gpu_final = float(gpu.final_equity)
    abs_diff = abs(cpu_final - gpu_final)
    rel_diff_bps = 10_000.0 * abs_diff / cpu_final if cpu_final > 0 else float("inf")
    gpu_section: dict[str, Any] = {
        "final_equity": gpu.final_equity,
        "total_return_pct": gpu.total_return_pct,
        "trade_count": gpu.trade_count,
        "allocation_days": gpu.allocation_days,
        "equity_points": len(gpu.equity_curve),
    }
    if args.include_equity_curve:
        gpu_section["equity_curve"] = gpu.equity_curve

    report: dict[str, Any] = {
        "prices_csv": str(args.prices_csv),
        "config": {
            "initial_equity": args.initial_equity,
            "warmup_days": args.warmup_days,
            "slippage_bps": args.slippage_bps,
            "sell_fee_bps": args.sell_fee_bps,
        },
        "cpu": {
            "final_equity": cpu.final_equity,
            "total_return_pct": cpu.total_return_pct,
            "trade_count": len(cpu.trades),
            "allocation_days": len(cpu.allocations),
            "equity_points": len(cpu.equity_curve),
        },
        "gpu": gpu_section,
        "parity": {
            "final_equity_abs_diff": abs_diff,
            "final_equity_diff_bps_vs_cpu": rel_diff_bps,
        },
    }
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
