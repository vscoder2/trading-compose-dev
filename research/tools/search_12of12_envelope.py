#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from meta_router_v2.tools.historical_meta_router_v2_windows import RouterV2Config, _parse_hhmm
from meta_router_v2.tools import sweep_meta_router_v2_params as swp
from m0106_runtime_v1.tools.historical_m0106_windows import _build_m0106_targets_and_thresholds
import m0106_runtime_v1.runtime_m0106_loop as m0106
import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as hv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


WINDOWS_12 = ["1m", "2m", "3m", "4m", "5m", "6m", "1y", "2y", "3y", "4y", "5y", "7y"]


def _load_envelope_from_reports(
    *,
    v2_windows_csv: Path,
    mrv2_full_csv: Path,
    mrv2_candidate_id: str,
) -> dict[str, float]:
    """Build envelope = max(v2_final, mrv2_final) for each of 12 windows."""
    v2_map: dict[str, float] = {}
    with v2_windows_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            w = str(row["window"]).strip()
            if w in WINDOWS_12:
                v2_map[w] = float(row["v2_final_equity"])

    mrv2_map: dict[str, float] = {}
    with mrv2_full_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("candidate_id", "")).strip() != mrv2_candidate_id:
                continue
            for w in WINDOWS_12:
                mrv2_map[w] = float(row[f"{w}_meta_final"])
            break

    missing = [w for w in WINDOWS_12 if w not in v2_map or w not in mrv2_map]
    if missing:
        raise RuntimeError(f"Envelope source missing windows: {missing}")

    return {w: max(v2_map[w], mrv2_map[w]) for w in WINDOWS_12}


def _build_shared_state(args: argparse.Namespace) -> None:
    """Prepare market data once and initialize swp.G for worker forking."""
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = swp._load_env_file(args.env_file, override=bool(args.env_override))
        print(
            json.dumps(
                {"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}
            )
        )

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(f"Unknown strategy profile: {args.strategy_profile}")

    max_days = max(hv.WINDOW_TO_DAYS[w] for w in WINDOWS_12)
    earliest_start = args.end_date - timedelta(days=max_days)

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        earliest_start - timedelta(days=max(int(args.daily_lookback_days), int(args.warmup_days) + 20)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_ohlc_adjusted = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_ohlc_raw = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_adjusted, symbols=symbols)
    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _c, h in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(h)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    _, _, raw_close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)
    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    (
        v1_targets,
        v1_thresholds,
        _v1_variants,
        v2_targets,
        v2_thresholds,
        _v2_variants,
    ) = hv._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=float(args.rebalance_threshold),
        controlplane_threshold_cap=float(args.controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(args.controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(args.controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(args.controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(args.controlplane_hysteresis_exit_days),
    )

    m0106_targets, m0106_thresholds, _m0106_variants = _build_m0106_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=float(args.rebalance_threshold),
        cfg=m0106.M0106_CFG,
    )

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    profile_rt = rt_v1.PROFILES[args.strategy_profile]
    profile = iv.LockedProfile(
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

    local_bundle = {
        "symbols": symbols,
        "aligned_days": aligned_days,
        "price_history": price_history,
        "close_map_by_symbol": close_map_by_symbol,
        "high_map_by_symbol": high_map_by_symbol,
        "minute_by_day_symbol": minute_by_day_symbol,
        "profile": profile,
        "initial_equity": float(args.initial_equity),
        "slippage_bps": float(args.slippage_bps),
        "sell_fee_bps": float(args.sell_fee_bps),
        "runtime_profit_lock_order_type": str(args.runtime_profit_lock_order_type),
        "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
        "rebalance_time_ny": rebalance_time_ny,
        "split_ratio_by_day_symbol": split_ratio_by_day_symbol,
    }

    baseline: dict[str, dict[str, float]] = {}
    for w in WINDOWS_12:
        start_day, end_day = swp._window_range(args.end_date, w)
        s_v1 = swp._simulate_window(
            target_by_day=v1_targets, threshold_by_day=v1_thresholds, start_day=start_day, end_day=end_day, bundle=local_bundle
        )
        s_v2 = swp._simulate_window(
            target_by_day=v2_targets, threshold_by_day=v2_thresholds, start_day=start_day, end_day=end_day, bundle=local_bundle
        )
        s_m0 = swp._simulate_window(
            target_by_day=m0106_targets, threshold_by_day=m0106_thresholds, start_day=start_day, end_day=end_day, bundle=local_bundle
        )
        baseline[w] = {
            "v1_final": float(s_v1.final_equity),
            "v2_final": float(s_v2.final_equity),
            "m0106_final": float(s_m0.final_equity),
            "v1_dd": float(s_v1.max_drawdown_pct),
            "v2_dd": float(s_v2.max_drawdown_pct),
            "m0106_dd": float(s_m0.max_drawdown_pct),
        }

    # This global is inherited by worker processes through fork.
    swp.G.clear()
    swp.G.update(
        {
            **local_bundle,
            "close_series": close_series,
            "v1_targets": v1_targets,
            "v1_thresholds": v1_thresholds,
            "v2_targets": v2_targets,
            "v2_thresholds": v2_thresholds,
            "m0106_targets": m0106_targets,
            "m0106_thresholds": m0106_thresholds,
            "baseline": baseline,
        }
    )


def _build_candidates(args: argparse.Namespace) -> list[RouterV2Config]:
    grid = []
    for values in product(
        swp._parse_csv_ints(args.grid_lookback_days),
        swp._parse_csv_floats(args.grid_vol_penalty),
        swp._parse_csv_floats(args.grid_dd_penalty),
        swp._parse_csv_floats(args.grid_switch_penalty),
        swp._parse_csv_floats(args.grid_min_edge_to_switch),
        swp._parse_csv_ints(args.grid_min_hold_days),
        swp._parse_csv_floats(args.grid_burst_v1_bonus),
        swp._parse_csv_floats(args.grid_riskoff_m0106_bonus),
    ):
        cfg = RouterV2Config(
            lookback_days=int(values[0]),
            vol_penalty=float(values[1]),
            dd_penalty=float(values[2]),
            switch_penalty=float(values[3]),
            min_edge_to_switch=float(values[4]),
            min_hold_days=int(values[5]),
            burst_ret3d_min=float(args.burst_ret3d_min),
            burst_rv20_max=float(args.burst_rv20_max),
            burst_dd20_max=float(args.burst_dd20_max),
            burst_v1_bonus=float(values[6]),
            riskoff_rv20_min=float(args.riskoff_rv20_min),
            riskoff_crossovers20_min=int(args.riskoff_crossovers20_min),
            riskoff_dd20_min=float(args.riskoff_dd20_min),
            riskoff_m0106_bonus=float(values[7]),
        )
        grid.append(cfg)

    if len(grid) <= int(args.max_candidates):
        return grid

    rng = random.Random(int(args.seed))
    return rng.sample(grid, int(args.max_candidates))


def _score_vs_envelope(row: dict[str, Any], envelope: dict[str, float]) -> dict[str, Any]:
    wins = 0
    uplifts = []
    for w in WINDOWS_12:
        meta_final = float(row[f"{w}_meta_final"])
        ref_final = float(envelope[w])
        up = 100.0 * ((meta_final / ref_final) - 1.0) if ref_final > 0 else 0.0
        uplifts.append(up)
        if up > 0:
            wins += 1
        row[f"{w}_uplift_vs_envelope_pct"] = up

    row["wins_vs_envelope"] = int(wins)
    row["min_uplift_vs_envelope_pct"] = float(min(uplifts))
    row["avg_uplift_vs_envelope_pct"] = float(sum(uplifts) / len(uplifts))
    # Hard-priority sort: wins -> worst window -> average.
    row["envelope_score"] = float(
        (row["wins_vs_envelope"] * 1_000_000.0)
        + (row["min_uplift_vs_envelope_pct"] * 10_000.0)
        + (row["avg_uplift_vs_envelope_pct"] * 100.0)
    )
    return row


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Search for 12/12 winner vs v2+MRV2 envelope.")
    p.add_argument("--env-file", default="")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--daily-lookback-days", type=int, default=800)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--rebalance-time-ny", default="15:55")
    p.add_argument(
        "--runtime-profit-lock-order-type",
        choices=["close_position", "market_order", "stop_order", "trailing_stop"],
        default="market_order",
    )
    p.add_argument("--runtime-stop-price-offset-bps", type=float, default=2.0)
    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    p.add_argument("--grid-lookback-days", default="6,8,10,12")
    p.add_argument("--grid-vol-penalty", default="0.8,1.0,1.2")
    p.add_argument("--grid-dd-penalty", default="0.04,0.06,0.08,0.10")
    p.add_argument("--grid-switch-penalty", default="0.0,0.01,0.02,0.03")
    p.add_argument("--grid-min-edge-to-switch", default="0.0,0.01,0.02,0.03")
    p.add_argument("--grid-min-hold-days", default="1,2,3")
    p.add_argument("--grid-burst-v1-bonus", default="0.02,0.05,0.08,0.10")
    p.add_argument("--grid-riskoff-m0106-bonus", default="0.05,0.10,0.15,0.20,0.25")

    p.add_argument("--burst-ret3d-min", type=float, default=0.12)
    p.add_argument("--burst-rv20-max", type=float, default=120.0)
    p.add_argument("--burst-dd20-max", type=float, default=18.0)
    p.add_argument("--riskoff-rv20-min", type=float, default=95.0)
    p.add_argument("--riskoff-crossovers20-min", type=int, default=8)
    p.add_argument("--riskoff-dd20-min", type=float, default=18.0)

    p.add_argument("--max-candidates", type=int, default=320)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)

    p.add_argument(
        "--v2-windows-csv",
        default=str(ROOT / "switch_runtime_v1" / "reports" / "compare_v1_v2_windows_1m_7y_20260327_20260327.csv"),
    )
    p.add_argument(
        "--mrv2-full-csv",
        default=str(ROOT / "meta_router_v2" / "reports" / "sweep_meta_router_v2_safe_20260327_20260327_full.csv"),
    )
    p.add_argument("--mrv2-candidate-id", default="MRV2-00007")

    p.add_argument("--topk", type=int, default=10)
    p.add_argument("--reports-dir", default=str(ROOT / "research" / "reports" / "meta_router_12of12"))
    p.add_argument("--output-prefix", default="search_12of12_envelope")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    envelope = _load_envelope_from_reports(
        v2_windows_csv=Path(args.v2_windows_csv),
        mrv2_full_csv=Path(args.mrv2_full_csv),
        mrv2_candidate_id=str(args.mrv2_candidate_id),
    )

    _build_shared_state(args)
    grid = _build_candidates(args)

    tasks = [(i + 1, cfg, WINDOWS_12, args.end_date) for i, cfg in enumerate(grid)]
    out_rows: list[dict[str, Any]] = []
    worker_count = max(1, int(args.workers))
    if worker_count == 1:
        # Robust single-process mode for low-memory / SSH-safe runs.
        total = len(tasks)
        for i, t in enumerate(tasks, start=1):
            row = swp._evaluate_candidate(t)
            row = _score_vs_envelope(row, envelope)
            out_rows.append(row)
            if i % 20 == 0 or i == total:
                print(json.dumps({"stage": "eval", "completed": i, "total": total, "mode": "sequential"}))
    else:
        ctx = mp.get_context("fork")
        with ProcessPoolExecutor(max_workers=worker_count, mp_context=ctx) as ex:
            futs = [ex.submit(swp._evaluate_candidate, t) for t in tasks]
            total = len(futs)
            for i, fut in enumerate(as_completed(futs), start=1):
                row = fut.result()
                row = _score_vs_envelope(row, envelope)
                out_rows.append(row)
                if i % 20 == 0 or i == total:
                    print(json.dumps({"stage": "eval", "completed": i, "total": total, "mode": "process_pool"}))

    out_rows.sort(
        key=lambda r: (
            float(r["wins_vs_envelope"]),
            float(r["min_uplift_vs_envelope_pct"]),
            float(r["avg_uplift_vs_envelope_pct"]),
            float(r["envelope_score"]),
        ),
        reverse=True,
    )

    stamp = args.end_date.strftime("%Y%m%d")
    base = reports_dir / f"{args.output_prefix}_{stamp}"
    out_csv = base.with_suffix(".csv")
    out_json = base.with_suffix(".json")

    if out_rows:
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            writer.writeheader()
            writer.writerows(out_rows)

    strict = [r for r in out_rows if int(r["wins_vs_envelope"]) == len(WINDOWS_12) and float(r["min_uplift_vs_envelope_pct"]) > 0.0]
    payload = {
        "run": {
            "strategy_profile": args.strategy_profile,
            "end_date": args.end_date.isoformat(),
            "initial_equity": float(args.initial_equity),
            "windows": WINDOWS_12,
            "workers": int(args.workers),
            "max_candidates": int(args.max_candidates),
            "seed": int(args.seed),
        },
        "envelope_refs": {
            "v2_windows_csv": str(args.v2_windows_csv),
            "mrv2_full_csv": str(args.mrv2_full_csv),
            "mrv2_candidate_id": str(args.mrv2_candidate_id),
            "envelope_final_equity": envelope,
        },
        "summary": {
            "evaluated_candidates": len(out_rows),
            "strict_12of12_count": len(strict),
            "topk": out_rows[: max(1, int(args.topk))],
        },
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps({"results_csv": str(out_csv), "results_json": str(out_json), "strict_12of12_count": len(strict)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
