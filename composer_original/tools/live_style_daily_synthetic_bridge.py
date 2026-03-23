#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import BacktestConfig, StrategyConfig

from composer_original.tools import run_last_6m_cpu_gpu_backtests as core_daily


def _date_from_iso(value: str) -> date:
    return date.fromisoformat(value)


def _load_env_file(path: str, *, override: bool) -> int:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {p}")
    loaded = 0
    for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        if (not override) and (key in os.environ):
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _write_curve_csv(path: Path, cpu_curve: list[tuple[date, float]], gpu_curve: list[tuple[date, float]]) -> None:
    cpu_map = {d: eq for d, eq in cpu_curve}
    gpu_map = {d: eq for d, eq in gpu_curve}
    all_days = sorted(set(cpu_map.keys()) | set(gpu_map.keys()))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "cpu_equity", "gpu_equity", "cpu_gpu_diff_bps"])
        for d in all_days:
            c = float(cpu_map.get(d, 0.0))
            g = float(gpu_map.get(d, 0.0))
            bps = 0.0 if c == 0.0 else (10_000.0 * abs(c - g) / abs(c))
            w.writerow([d.isoformat(), f"{c:.10f}", f"{g:.10f}", f"{bps:.10f}"])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone live-style bridge that reproduces Alpaca daily-high synthetic backtest semantics "
            "without modifying existing runtime/backtest scripts."
        )
    )
    parser.add_argument("--env-file", default="", help="Optional .env file.")
    parser.add_argument("--env-override", action="store_true")
    parser.add_argument("--start-date", type=_date_from_iso, required=True)
    parser.add_argument("--end-date", type=_date_from_iso, required=True)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--initial-principal", type=float, default=10_000.0)
    parser.add_argument("--warmup-days", type=int, default=260)
    parser.add_argument("--lookback-buffer-days", type=int, default=420)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--sell-fee-bps", type=float, default=0.0)
    parser.add_argument(
        "--anchor-window-start-equity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Anchor first in-window equity to initial principal by deferring first rebalance.",
    )
    parser.add_argument(
        "--strategy-preset",
        choices=sorted(core_daily.STRATEGY_PRESETS.keys()),
        default="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
        help="Locked strategy preset.",
    )
    parser.add_argument(
        "--alpaca-data-feed",
        default=os.getenv("ALPACA_DATA_FEED", "sip"),
        help="Alpaca feed (sip/iex).",
    )
    parser.add_argument(
        "--alpaca-data-url",
        default=os.getenv("ALPACA_DATA_URL", core_daily.ALPACA_DATA_URL_DEFAULT),
        help="Alpaca data URL.",
    )
    parser.add_argument(
        "--alpaca-api-key",
        default=os.getenv("ALPACA_API_KEY", ""),
        help="Alpaca API key.",
    )
    parser.add_argument(
        "--alpaca-secret-key",
        default=os.getenv("ALPACA_SECRET_KEY", ""),
        help="Alpaca secret key.",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(ROOT / "composer_original" / "reports"),
    )
    parser.add_argument(
        "--output-prefix",
        default="live_style_daily_synthetic_bridge",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    if args.start_date > args.end_date:
        raise ValueError("start-date must be <= end-date")
    if abs(float(args.initial_principal) - float(args.initial_equity)) > 1e-9:
        raise ValueError("initial-principal and initial-equity must match")
    if not os.getenv("ALPACA_API_KEY", args.alpaca_api_key):
        args.alpaca_api_key = ""
    else:
        args.alpaca_api_key = os.getenv("ALPACA_API_KEY", args.alpaca_api_key)
    if not os.getenv("ALPACA_SECRET_KEY", args.alpaca_secret_key):
        args.alpaca_secret_key = ""
    else:
        args.alpaca_secret_key = os.getenv("ALPACA_SECRET_KEY", args.alpaca_secret_key)
    if not args.alpaca_api_key or not args.alpaca_secret_key:
        raise RuntimeError("Alpaca credentials are required.")

    symbols = list(StrategyConfig().symbols)
    feed = str(args.alpaca_data_feed).lower()
    preload_start = args.start_date - timedelta(days=max(int(args.lookback_buffer_days), int(args.warmup_days) + 20))
    end_day = args.end_date

    frames: dict[str, Any] = {}
    for sym in symbols:
        frames[sym] = core_daily._fetch_symbol_daily_alpaca(
            symbol=sym,
            start_day=preload_start,
            end_day=end_day,
            api_key=str(args.alpaca_api_key),
            secret_key=str(args.alpaca_secret_key),
            data_url=str(args.alpaca_data_url),
            data_feed=feed,
        )

    wide = core_daily._build_wide_table(frames, symbols=symbols)
    wide_high = core_daily._build_wide_high_table(frames, symbols=symbols)
    wide = wide[(wide["date"] >= preload_start) & (wide["date"] <= end_day)].reset_index(drop=True)
    wide_high = wide_high[(wide_high["date"] >= preload_start) & (wide_high["date"] <= end_day)].reset_index(drop=True)
    if wide.empty:
        raise RuntimeError("No rows after date filtering")

    history = core_daily._wide_to_history(wide, symbols=symbols)
    high_history = core_daily._wide_to_history(wide_high, symbols=symbols)

    preset = core_daily.STRATEGY_PRESETS[str(args.strategy_preset)]
    locks = dict(preset["locks"])
    enable_profit_lock = bool(locks["enable_profit_lock"])
    threshold_series = core_daily._build_profit_lock_threshold_series(
        history,
        base_threshold_pct=float(locks["profit_lock_threshold_pct"]),
        adaptive_enabled=bool(enable_profit_lock and locks["profit_lock_adaptive_threshold"]),
        adaptive_symbol=str(locks["profit_lock_adaptive_symbol"]),
        adaptive_rv_window=int(locks["profit_lock_adaptive_rv_window"]),
        adaptive_rv_baseline_pct=float(locks["profit_lock_adaptive_rv_baseline_pct"]),
        adaptive_min_threshold_pct=float(locks["profit_lock_adaptive_min_threshold_pct"]),
        adaptive_max_threshold_pct=float(locks["profit_lock_adaptive_max_threshold_pct"]),
    )
    trend_flags = core_daily._build_trend_filter_flags(
        history,
        trend_filter_enabled=bool(enable_profit_lock and locks["profit_lock_trend_filter"]),
        trend_symbol="SOXL",
        trend_ma_window=20,
    )
    regime_flags = core_daily._build_profit_lock_gate_flags(
        history,
        regime_gated=bool(enable_profit_lock and locks["profit_lock_regime_gated"]),
        regime_symbol="SOXL",
        regime_rv_window=14,
        regime_rv_threshold_pct=85.0,
    )
    if len(trend_flags) == len(regime_flags):
        gate_flags = [bool(a and b) for a, b in zip(regime_flags, trend_flags, strict=True)]
    else:
        gate_flags = regime_flags

    date_series = list(wide["date"])
    effective_warmup = int(args.warmup_days)
    if bool(args.anchor_window_start_equity):
        first_idx = next((i for i, d in enumerate(date_series) if d >= args.start_date), None)
        if first_idx is None:
            raise RuntimeError("Unable to locate first in-window day")
        effective_warmup = max(effective_warmup, first_idx + 1)

    cfg = BacktestConfig(
        initial_equity=float(args.initial_equity),
        warmup_days=effective_warmup,
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
    )
    cpu_full = run_backtest(price_history=history, config=cfg)

    cpu_replay = core_daily._cpu_replay_from_allocations(
        price_history=history,
        high_history=high_history,
        cpu_allocations=cpu_full.allocations,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        enable_profit_lock=enable_profit_lock,
        profit_lock_mode=str(locks["profit_lock_mode"]),
        profit_lock_threshold_series_pct=threshold_series,
        profit_lock_threshold_pct=float(locks["profit_lock_threshold_pct"]),
        profit_lock_partial_sell_pct=float(locks["profit_lock_partial_sell_pct"]),
        profit_lock_trail_pct=float(locks["profit_lock_trail_pct"]),
        profit_lock_exec_model=core_daily._effective_profit_lock_exec_model("synthetic"),
        profit_lock_gate_flags=gate_flags,
    )
    gpu_replay = core_daily._gpu_replay_from_cpu_allocations(
        price_history=history,
        high_history=high_history,
        cpu_allocations=cpu_full.allocations,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        enable_profit_lock=enable_profit_lock,
        profit_lock_mode=str(locks["profit_lock_mode"]),
        profit_lock_threshold_series_pct=threshold_series,
        profit_lock_threshold_pct=float(locks["profit_lock_threshold_pct"]),
        profit_lock_partial_sell_pct=float(locks["profit_lock_partial_sell_pct"]),
        profit_lock_trail_pct=float(locks["profit_lock_trail_pct"]),
        profit_lock_exec_model=core_daily._effective_profit_lock_exec_model("synthetic"),
        profit_lock_gate_flags=gate_flags,
    )

    cpu_curve_window = core_daily._slice_curve(cpu_replay.equity_curve, start_day=args.start_date, end_day=args.end_date)
    gpu_curve_window = core_daily._slice_curve(gpu_replay.equity_curve, start_day=args.start_date, end_day=args.end_date)
    if not cpu_curve_window or not gpu_curve_window:
        raise RuntimeError("No in-window equity points")

    cpu_window_trades = sum(c for d, c in cpu_replay.trade_count_by_day.items() if args.start_date <= d <= args.end_date)
    gpu_window_trades = sum(c for d, c in gpu_replay.trade_count_by_day.items() if args.start_date <= d <= args.end_date)
    cpu_metrics = core_daily._curve_summary(cpu_curve_window)
    gpu_metrics = core_daily._curve_summary(gpu_curve_window)

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = (
        f"{args.output_prefix}_{args.strategy_preset}_{args.start_date.isoformat()}_to_{args.end_date.isoformat()}_"
        f"alpaca_{feed}"
    )
    summary_path = reports_dir / f"{base}.json"
    curve_csv_path = reports_dir / f"{base}_curve.csv"
    _write_curve_csv(curve_csv_path, cpu_curve_window, gpu_curve_window)

    cpu_final = float(cpu_metrics["final_equity"])
    gpu_final = float(gpu_metrics["final_equity"])
    bps = 0.0 if cpu_final == 0.0 else 10_000.0 * abs(cpu_final - gpu_final) / abs(cpu_final)

    summary = {
        "path_type": "live_style_daily_synthetic_bridge",
        "note": (
            "Standalone bridge path that preserves Alpaca daily-high synthetic semantics for live-style benchmarking. "
            "This is intentionally optimistic relative to minute-path executable replay."
        ),
        "strategy_source": str(ROOT / "composer_original" / "files" / "composer_original_file.txt"),
        "strategy_preset": str(args.strategy_preset),
        "strategy_mode": "original",
        "window": {"start_date": args.start_date.isoformat(), "end_date": args.end_date.isoformat()},
        "initial_principal": float(args.initial_principal),
        "initial_equity": float(args.initial_equity),
        "data_source": "alpaca",
        "alpaca_data_feed": feed,
        "anchor_window_start_equity": bool(args.anchor_window_start_equity),
        "warmup_days_requested": int(args.warmup_days),
        "warmup_days_effective": int(effective_warmup),
        "profit_lock": {
            "enabled": bool(enable_profit_lock),
            "mode": str(locks["profit_lock_mode"]),
            "threshold_pct": float(locks["profit_lock_threshold_pct"]),
            "trail_pct": float(locks["profit_lock_trail_pct"]),
            "partial_sell_pct": float(locks["profit_lock_partial_sell_pct"]),
            "adaptive_threshold": bool(locks["profit_lock_adaptive_threshold"]),
            "adaptive_symbol": str(locks["profit_lock_adaptive_symbol"]),
            "adaptive_rv_window": int(locks["profit_lock_adaptive_rv_window"]),
            "adaptive_rv_baseline_pct": float(locks["profit_lock_adaptive_rv_baseline_pct"]),
            "adaptive_min_threshold_pct": float(locks["profit_lock_adaptive_min_threshold_pct"]),
            "adaptive_max_threshold_pct": float(locks["profit_lock_adaptive_max_threshold_pct"]),
            "exec_model_requested": "synthetic",
            "exec_model_effective": core_daily._effective_profit_lock_exec_model("synthetic"),
            "model": "daily_high_threshold_emulation",
        },
        "cpu": {
            **cpu_metrics,
            "window_trade_count": int(cpu_window_trades),
            "full_trade_count": int(cpu_replay.trade_count_total),
        },
        "gpu": {
            **gpu_metrics,
            "window_trade_count": int(gpu_window_trades),
            "full_trade_count": int(gpu_replay.trade_count_total),
        },
        "parity": {
            "final_equity_abs_diff": abs(cpu_final - gpu_final),
            "final_equity_diff_bps_vs_cpu": float(bps),
        },
        "outputs": {
            "summary_json": str(summary_path),
            "curve_csv": str(curve_csv_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
