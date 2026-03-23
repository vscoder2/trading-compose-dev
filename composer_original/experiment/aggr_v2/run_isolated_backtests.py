#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.experiment.aggr_v2.backtester import run_backtest_v2
from composer_original.experiment.aggr_v2.data import MarketData, load_market_data
from composer_original.experiment.aggr_v2.gpu_replay import replay_with_gpu
from composer_original.experiment.aggr_v2.profiles import get_profile
from composer_original.experiment.aggr_v2.reporting import build_summary_row, daily_table_rows, serialize_result, write_csv, write_json
from composer_original.experiment.aggr_v2.review import run_four_pass_review
from composer_original.experiment.aggr_v2.model_types import BacktestConfigV2, BacktestResultV2, OverlayConfig, WindowSpec
from composer_original.experiment.aggr_v2.validation import summarize_validation
from composer_original.experiment.aggr_v2.windows import WINDOW_TO_DAYS, resolve_windows


def _parse_day(value: str) -> date:
    return date.fromisoformat(str(value))


def _trim_result(result: BacktestResultV2, window: WindowSpec) -> BacktestResultV2:
    """Trim warmup-extended result to report window only."""
    keep_curve = [(d, eq) for d, eq in result.equity_curve if window.start <= d <= window.end]
    keep_daily = [r for r in result.daily if window.start <= r.day <= window.end]
    keep_trades = [t for t in result.trades if window.start <= t.day <= window.end]

    if not keep_curve:
        raise RuntimeError(f"No equity rows in output window {window.label} {window.start}..{window.end}")

    final_eq = keep_curve[-1][1]
    init = result.initial_equity
    total_ret = 100.0 * (final_eq / init - 1.0) if init > 0 else 0.0

    return BacktestResultV2(
        profile_name=result.profile_name,
        window_label=window.label,
        mode=result.mode,
        initial_equity=result.initial_equity,
        final_equity=final_eq,
        total_return_pct=total_ret,
        max_drawdown_pct=result.max_drawdown_pct,
        cagr_pct=result.cagr_pct,
        trade_count=len(keep_trades),
        equity_curve=keep_curve,
        trades=keep_trades,
        daily=keep_daily,
        meta=dict(result.meta),
    )


def _slice_market_data(market_data: MarketData, start: date, end: date) -> MarketData:
    keep_days = [d for d in market_data.days if start <= d <= end]
    if not keep_days:
        raise RuntimeError(f"No market data between {start} and {end}")
    keep_set = set(keep_days)
    bars = {
        sym: [b for b in sym_bars if b.day in keep_set]
        for sym, sym_bars in market_data.bars_by_symbol.items()
    }
    return MarketData(days=keep_days, bars_by_symbol=bars)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Isolated AGGR v2 backtests (new-code-only path)")
    p.add_argument("--source", choices=["fixture_close", "ohlc_csv", "yfinance"], default="fixture_close")
    p.add_argument(
        "--prices-csv",
        default="/home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv",
    )
    p.add_argument("--ohlc-csv", default="")
    p.add_argument("--profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30")
    p.add_argument("--mode", default="paper_live_style_optimistic", choices=["synthetic", "paper_live_style_optimistic", "realistic_close"])
    p.add_argument("--windows", default="1m,2m,3m,6m,1y")
    p.add_argument("--end-day", default="")

    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--min-trade-weight-delta", type=float, default=0.0)
    p.add_argument("--rebalance-threshold", type=float, default=0.0)

    # Overlay knobs.
    p.add_argument("--enable-vol-target", action="store_true")
    p.add_argument("--target-vol-ann", type=float, default=0.35)
    p.add_argument("--vol-lookback-days", type=int, default=20)
    p.add_argument("--max-gross-exposure", type=float, default=1.0)

    p.add_argument("--enable-loss-limiter", action="store_true")
    p.add_argument("--stop-loss-pct", type=float, default=0.12)
    p.add_argument("--max-holding-days", type=int, default=30)

    p.add_argument("--enable-persistence", action="store_true")
    p.add_argument("--persistence-days", type=int, default=1)
    p.add_argument("--hysteresis-band-pct", type=float, default=0.0)

    p.add_argument("--enable-inverse-blocker", action="store_true")
    p.add_argument("--trend-symbol", default="SOXL")
    p.add_argument("--trend-ma-days", type=int, default=50)

    p.add_argument("--run-four-reviews", action="store_true")
    p.add_argument(
        "--output-dir",
        default="/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/reports",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    profile = get_profile(args.profile)
    base_cfg = BacktestConfigV2(
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        min_trade_weight_delta=float(args.min_trade_weight_delta),
        rebalance_threshold=float(args.rebalance_threshold),
        profit_lock_exec_model=str(args.mode),
    )
    overlay = OverlayConfig(
        enable_vol_target=bool(args.enable_vol_target),
        target_vol_ann=float(args.target_vol_ann),
        vol_lookback_days=int(args.vol_lookback_days),
        max_gross_exposure=float(args.max_gross_exposure),
        enable_loss_limiter=bool(args.enable_loss_limiter),
        stop_loss_pct=float(args.stop_loss_pct),
        max_holding_days=int(args.max_holding_days),
        enable_persistence=bool(args.enable_persistence),
        persistence_days=int(args.persistence_days),
        hysteresis_band_pct=float(args.hysteresis_band_pct),
        enable_inverse_blocker=bool(args.enable_inverse_blocker),
        trend_symbol=str(args.trend_symbol),
        trend_ma_days=int(args.trend_ma_days),
    )

    window_labels = [w.strip().lower() for w in str(args.windows).split(",") if w.strip()]
    for label in window_labels:
        if label not in WINDOW_TO_DAYS:
            valid = ", ".join(sorted(WINDOW_TO_DAYS))
            raise ValueError(f"Unsupported window '{label}'. Valid windows: {valid}")

    # We load enough pre-history for warmup + largest target window.
    max_window_days = max(WINDOW_TO_DAYS[w] for w in window_labels)
    if args.end_day:
        end_day = _parse_day(args.end_day)
    else:
        # Temporary load to infer latest available day from fixture/csv.
        seed_start = date.today() - timedelta(days=max_window_days + args.warmup_days + 30)
        seed_data = load_market_data(
            prices_csv=Path(args.prices_csv) if args.prices_csv else None,
            ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
            source=args.source,
            start=seed_start,
            end=date.today(),
        )
        end_day = seed_data.days[-1]

    start_for_load = end_day - timedelta(days=max_window_days + int(args.warmup_days) + 30)
    market_data = load_market_data(
        prices_csv=Path(args.prices_csv) if args.prices_csv else None,
        ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
        source=args.source,
        start=start_for_load,
        end=end_day,
    )

    windows = resolve_windows(end_day, window_labels)
    summary_rows: list[dict[str, object]] = []
    json_rows: list[dict[str, object]] = []

    for window in windows:
        # Extend start backward for warmup so window itself has tradeable days.
        run_start = window.start - timedelta(days=int(args.warmup_days) + 30)
        run_data = _slice_market_data(market_data, run_start, window.end)

        run = run_backtest_v2(
            market_data=run_data,
            profile=profile,
            config=base_cfg,
            overlay=overlay,
            window_label=window.label,
        )
        trimmed = _trim_result(run, window)

        gpu = replay_with_gpu(trimmed, _slice_market_data(market_data, window.start, window.end))
        val = summarize_validation(trimmed)

        summary = build_summary_row(result=trimmed, gpu=gpu, validation=val)
        summary_rows.append(summary)
        blob = serialize_result(trimmed, gpu, val)

        if args.run_four_reviews:
            review = run_four_pass_review(
                market_data=run_data,
                profile=profile,
                config=base_cfg,
                overlay=overlay,
                window_label=window.label,
            )
            blob["four_pass_review"] = {
                "all_ok": review.all_ok,
                "passes": [asdict(p) for p in review.passes],
            }

        json_rows.append(blob)

        daily_path = out_dir / f"daily_{profile.name}_{window.label}_{args.mode}.csv"
        write_csv(daily_path, daily_table_rows(trimmed))

    csv_path = out_dir / f"summary_{profile.name}_{args.mode}.csv"
    json_path = out_dir / f"summary_{profile.name}_{args.mode}.json"

    write_csv(csv_path, summary_rows)
    write_json(
        json_path,
        {
            "profile": profile.name,
            "mode": args.mode,
            "source": args.source,
            "config": asdict(base_cfg),
            "overlay": asdict(overlay),
            "windows": [asdict(w) for w in windows],
            "results": json_rows,
        },
    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    for row in summary_rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
