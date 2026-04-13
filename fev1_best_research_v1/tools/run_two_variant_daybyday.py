#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from protective_stop_variant_v2.tools.export_last30_daybyday import (
    _build_targets_for_engine,
    _parse_hhmm,
    _simulate_with_table,
)
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1


@dataclass
class VariantCfg:
    name: str
    engine: str
    stop_pct: float
    rv_gate: float
    trail_scale: float
    threshold_scale: float


def _build_scaled_profile(base: iv.LockedProfile, trail_scale: float, threshold_scale: float) -> iv.LockedProfile:
    new_trail = max(1.0, float(base.profit_lock_trail_pct) * float(trail_scale))
    new_thresh = max(2.0, float(base.profit_lock_threshold_pct) * float(threshold_scale))
    new_min = max(2.0, float(base.profit_lock_adaptive_min_threshold_pct) * float(threshold_scale))
    new_max = max(new_min + 0.1, float(base.profit_lock_adaptive_max_threshold_pct) * float(threshold_scale))
    return iv.LockedProfile(
        name=f"{base.name}_{trail_scale:.2f}_{threshold_scale:.2f}",
        enable_profit_lock=base.enable_profit_lock,
        profit_lock_mode=base.profit_lock_mode,
        profit_lock_threshold_pct=new_thresh,
        profit_lock_trail_pct=new_trail,
        profit_lock_adaptive_threshold=base.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=base.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=base.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=base.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=new_min,
        profit_lock_adaptive_max_threshold_pct=new_max,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Run two-variant day-by-day export on custom date range")
    p.add_argument("--env-file", default=str(ROOT / ".env.dev"))
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--start-date", type=date.fromisoformat, required=True)
    p.add_argument("--end-date", type=date.fromisoformat, required=True)
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--rebalance-time-ny", default="15:55")
    p.add_argument("--runtime-profit-lock-order-type", choices=["close_position", "market_order", "stop_order", "trailing_stop"], default="market_order")
    p.add_argument("--runtime-stop-price-offset-bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--daily-lookback-days", type=int, default=1200)
    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)
    p.add_argument("--reports-dir", default=str(ROOT / "fev1_best_research_v1" / "reports"))
    args = p.parse_args()

    if args.env_file:
        ab._load_env_file(args.env_file, override=bool(args.env_override))

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(args.strategy_profile)

    variants = [
        VariantCfg("fev1_best", "fev1", 5.0, 80.0, 1.0, 1.0),
        VariantCfg("C_sp4.7_rv75_tr1.10_th1.20", "fev1", 4.7, 75.0, 1.10, 1.20),
    ]

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        args.start_date - timedelta(days=max(int(args.daily_lookback_days), int(args.warmup_days) + 60)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_ohlc_adj = iv._fetch_daily_ohlc(loader, symbols=symbols, start_dt=lookback_start, end_dt=lookback_end, feed=alpaca.data_feed, adjustment="all")
    daily_ohlc_raw = iv._fetch_daily_ohlc(loader, symbols=symbols, start_dt=lookback_start, end_dt=lookback_end, feed=alpaca.data_feed, adjustment="raw")

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_adj, symbols=symbols)
    _, _, raw_close_map = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)

    split_ratio = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map,
    )

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=args.start_date,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    profile_rt = rt_v1.PROFILES[args.strategy_profile]
    base_profile = iv.LockedProfile(
        name=profile_rt.name,
        enable_profit_lock=profile_rt.enable_profit_lock,
        profit_lock_mode=profile_rt.profit_lock_mode,
        profit_lock_threshold_pct=profile_rt.profit_lock_threshold_pct,
        profit_lock_trail_pct=profile_rt.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=profile_rt.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=profile_rt.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=profile_rt.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=profile_rt.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=profile_rt.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=profile_rt.profit_lock_adaptive_max_threshold_pct,
    )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    out = {
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "initial_equity": float(args.initial_equity),
        "variants": {},
    }

    for v in variants:
        profile = _build_scaled_profile(base_profile, v.trail_scale, v.threshold_scale)

        target_by_day, threshold_by_day = _build_targets_for_engine(
            engine=v.engine,
            aligned_days=aligned_days,
            symbols=symbols,
            close_series=close_series,
            baseline_target_by_day=baseline_target_by_day,
            rebalance_threshold=float(args.rebalance_threshold),
            controlplane_threshold_cap=float(args.controlplane_threshold_cap),
            controlplane_hysteresis_enter=float(args.controlplane_hysteresis_enter),
            controlplane_hysteresis_exit=float(args.controlplane_hysteresis_exit),
            controlplane_hysteresis_enter_days=int(args.controlplane_hysteresis_enter_days),
            controlplane_hysteresis_exit_days=int(args.controlplane_hysteresis_exit_days),
        )

        rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=threshold_by_day,
            profile=profile,
            start_day=args.start_date,
            end_day=args.end_date,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
            runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
            rebalance_time_ny=_parse_hhmm(args.rebalance_time_ny),
            split_ratio_by_day_symbol=split_ratio,
            enable_protective_stop=(v.stop_pct > 0),
            protective_stop_pct=float(v.stop_pct),
            stop_scope="inverse_only",
            rv_gate_min_pct=float(v.rv_gate),
            rv_gate_window=20,
        )

        out_csv = reports_dir / f"{v.name}_{args.start_date.isoformat()}_to_{args.end_date.isoformat()}_10k.csv"
        if rows:
            with out_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

        if rows:
            final_equity = float(rows[-1]["Day End Equity"])
            maxdd = max(float(r["Drawdown %"]) for r in rows)
            days = len(rows)
        else:
            final_equity = float(args.initial_equity)
            maxdd = 0.0
            days = 0

        pnl = final_equity - float(args.initial_equity)
        ret = (final_equity / float(args.initial_equity) - 1.0) * 100.0 if args.initial_equity > 0 else 0.0

        summary_rows.append(
            {
                "variant": v.name,
                "period": f"{args.start_date.isoformat()} to {args.end_date.isoformat()}",
                "initial_equity": round(float(args.initial_equity), 2),
                "final_equity": round(final_equity, 2),
                "pnl": round(pnl, 2),
                "return_pct": round(ret, 4),
                "max_dd_pct": round(maxdd, 4),
                "days": days,
            }
        )

        out["variants"][v.name] = {
            "csv": str(out_csv),
            "rows": days,
            "final_equity": round(final_equity, 2),
            "pnl": round(pnl, 2),
            "return_pct": round(ret, 4),
            "max_dd_pct": round(maxdd, 4),
            "stop_pct": v.stop_pct,
            "rv_gate": v.rv_gate,
            "trail_scale": v.trail_scale,
            "threshold_scale": v.threshold_scale,
        }

    summary_csv = reports_dir / f"compare_fev1_best_vs_c_sp4_7_{args.start_date.isoformat()}_to_{args.end_date.isoformat()}_10k_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)

    out["summary_csv"] = str(summary_csv)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
