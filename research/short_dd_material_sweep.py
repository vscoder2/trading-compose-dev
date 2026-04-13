#!/usr/bin/env python3
"""Research-only sweep targeting material short-window DD improvement.

This script does not modify production/runtime code.
It reuses existing research helpers and writes outputs only to research/reports/.
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta
from multiprocessing import get_context
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research.short_long_dd_constrained_sweep as sc
import research.short_window_overlay_sweep as sw
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as ab
from composer_original.tools import intraday_profit_lock_verification as iv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


REPORT_DIR = ROOT / "research" / "reports" / f"short_dd_material_{date.today().strftime('%Y%m%d')}"

# Worker globals for forked multiprocessing.
_G: dict[str, Any] = {}


def _serialize_candidate(c: sc.Candidate) -> dict[str, Any]:
    return asdict(c)


def _simulate_candidate(c: sc.Candidate) -> dict[str, Any]:
    windows = _G["windows"]
    end_date = _G["end_date"]
    initial_equity = _G["initial_equity"]
    rebalance_time_ny = _G["rebalance_time_ny"]
    symbols = _G["symbols"]
    aligned_days = _G["aligned_days"]
    price_history = _G["price_history"]
    close_map_by_symbol = _G["close_map_by_symbol"]
    high_map_by_symbol = _G["high_map_by_symbol"]
    minute_by_day_symbol = _G["minute_by_day_symbol"]
    split_ratio_by_day_symbol = _G["split_ratio_by_day_symbol"]
    profile = _G["profile"]
    baseline_target_by_day = _G["baseline_target_by_day"]
    close_series_by_symbol = _G["close_series_by_symbol"]
    base_metrics = _G["base_metrics"]

    fixed = sw.BaseFixedCfg(enter_days=int(c.fixed_enter_days))
    _a, _b, _c, fixed_targets, fixed_thresholds, fixed_variants = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series_by_symbol,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=float(fixed.cap),
        controlplane_hysteresis_enter=float(fixed.enter),
        controlplane_hysteresis_exit=float(fixed.exit),
        controlplane_hysteresis_enter_days=int(fixed.enter_days),
        controlplane_hysteresis_exit_days=int(fixed.exit_days),
    )

    targets, thresholds, overlay_stats = sc._apply_extended_overlays(
        aligned_days=aligned_days,
        close_series_by_symbol=close_series_by_symbol,
        baseline_target_by_day=baseline_target_by_day,
        fixed_targets=fixed_targets,
        fixed_thresholds=fixed_thresholds,
        fixed_variants=fixed_variants,
        end_date=end_date,
        c=c,
    )

    m = sw._simulate_windows(
        windows=windows,
        end_date=end_date,
        initial_equity=initial_equity,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        profile=profile,
        targets=targets,
        thresholds=thresholds,
        rebalance_time_ny=rebalance_time_ny,
    )

    # Gate set focused on drawdown.
    all_dd_ok = all(m[w]["dd"] <= base_metrics[w]["dd"] for w in windows)
    material_1m_dd = (base_metrics["1m"]["dd"] - m["1m"]["dd"]) >= 0.10
    # Keep long return collapse under control (still allow some trade-off for DD work).
    long_ret_guard = (m["6m"]["ret"] >= base_metrics["6m"]["ret"] - 35.0) and (
        m["1y"]["ret"] >= base_metrics["1y"]["ret"] - 35.0
    )
    pass_all = bool(all_dd_ok and material_1m_dd and long_ret_guard)

    dd_gain = (
        (base_metrics["1m"]["dd"] - m["1m"]["dd"])
        + (base_metrics["2m"]["dd"] - m["2m"]["dd"])
        + (base_metrics["3m"]["dd"] - m["3m"]["dd"])
        + (base_metrics["4m"]["dd"] - m["4m"]["dd"])
        + (base_metrics["5m"]["dd"] - m["5m"]["dd"])
        + (base_metrics["6m"]["dd"] - m["6m"]["dd"])
        + (base_metrics["1y"]["dd"] - m["1y"]["dd"])
    )
    ret_penalty = max(0.0, base_metrics["6m"]["ret"] - m["6m"]["ret"]) + max(
        0.0, base_metrics["1y"]["ret"] - m["1y"]["ret"]
    )

    # Score = DD-first, with mild return preservation.
    score = (
        800.0 * int(pass_all)
        + 300.0 * (base_metrics["1m"]["dd"] - m["1m"]["dd"])
        + 120.0 * (base_metrics["2m"]["dd"] - m["2m"]["dd"])
        + 90.0 * (base_metrics["3m"]["dd"] - m["3m"]["dd"])
        + 75.0 * (base_metrics["4m"]["dd"] - m["4m"]["dd"])
        + 60.0 * (base_metrics["5m"]["dd"] - m["5m"]["dd"])
        + 50.0 * (base_metrics["6m"]["dd"] - m["6m"]["dd"])
        + 50.0 * (base_metrics["1y"]["dd"] - m["1y"]["dd"])
        - 6.0 * ret_penalty
    )

    row: dict[str, Any] = {
        **_serialize_candidate(c),
        **overlay_stats,
        "all_dd_ok": all_dd_ok,
        "material_1m_dd": material_1m_dd,
        "long_ret_guard": long_ret_guard,
        "pass_all": pass_all,
        "dd_gain_total": float(dd_gain),
        "ret_penalty": float(ret_penalty),
        "score": float(score),
    }
    for w in windows:
        row[f"base_{w}_ret"] = float(base_metrics[w]["ret"])
        row[f"base_{w}_dd"] = float(base_metrics[w]["dd"])
        row[f"cfg_{w}_ret"] = float(m[w]["ret"])
        row[f"cfg_{w}_dd"] = float(m[w]["dd"])
        row[f"delta_{w}_ret"] = float(m[w]["ret"] - base_metrics[w]["ret"])
        row[f"delta_{w}_dd"] = float(m[w]["dd"] - base_metrics[w]["dd"])
    return row


def _sample_candidates(n: int, seed: int) -> list[sc.Candidate]:
    r = random.Random(seed)
    out: list[sc.Candidate] = []
    for i in range(1, n + 1):
        out.append(
            sc.Candidate(
                name=f"M{i:04d}",
                inverse_cap_weight=r.choice((0.45, 0.55, 0.65)),
                ramp_days=r.choice((2, 3, 4, 5)),
                sticky_threshold_days=r.choice((3, 4, 5, 6)),
                guard_enabled=True,
                guard_rv20_ann=r.choice((0.75, 0.85, 0.95)),
                guard_chop_crossovers20=r.choice((6.0, 8.0, 10.0)),
                recent_inverse_cap_weight=r.choice((0.10, 0.15, 0.20, 0.25, 0.30)),
                recent_days=r.choice((20, 30, 45, 60, 75)),
                shock_down_pct=r.choice((-0.03, -0.04, -0.05, -0.06)),
                shock_hold_days=r.choice((1, 2, 3)),
                fixed_enter_days=r.choice((2, 3, 4, 5, 6)),
                recent_primary_cap_weight=r.choice((0.20, 0.25, 0.30, 0.35, 0.40)),
                recent_defensive_blend=r.choice((0.20, 0.30, 0.40, 0.50, 0.60)),
                defensive_trigger_down_pct=r.choice((-0.004, -0.006, -0.008, -0.010, -0.015)),
                recent_aggressive_total_cap=r.choice((0.20, 0.30, 0.40, 0.50)),
                recent_always_defensive_blend=r.choice((0.15, 0.25, 0.35, 0.45, 0.55)),
            )
        )
    return out


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    end_date = date.fromisoformat(os.environ.get("RESEARCH_END_DATE", "2026-03-27"))
    windows = ["1m", "2m", "3m", "4m", "5m", "6m", "1y"]
    initial_equity = 10_000.0
    rebalance_time_ny = ab._parse_hhmm("15:55")
    n_candidates = int(os.environ.get("RESEARCH_MATERIAL_CANDIDATES", "5000"))
    seed = int(os.environ.get("RESEARCH_MATERIAL_SEED", "20260329"))

    profile_name = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
    profile = sw._profile_to_locked(profile_name)

    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    max_days = max(ab.WINDOW_TO_DAYS[w] for w in windows)
    earliest_start = end_date - timedelta(days=max_days)
    lookback_start = datetime.combine(earliest_start - timedelta(days=max(800, 260 + 20)), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_adj = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_raw = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )
    aligned_days, price_history, close_map = iv._align_daily_close_history(daily_adj, symbols=symbols)
    _, _, raw_close_map = iv._align_daily_close_history(daily_raw, symbols=symbols)
    split = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map,
        raw_close_map=raw_close_map,
    )
    high_map: dict[str, dict[date, float]] = {}
    for s in symbols:
        h: dict[date, float] = {}
        for d, _c, hh in daily_adj.get(s, []):
            h[d] = float(hh)
        high_map[s] = {d: float(h[d]) for d in aligned_days if d in h}

    baseline_target = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=initial_equity,
        warmup_days=260,
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=end_date,
        feed=alpaca.data_feed,
    )

    v1_targets, v1_thresholds, *_ = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=0.50,
        controlplane_hysteresis_enter=0.62,
        controlplane_hysteresis_exit=0.58,
        controlplane_hysteresis_enter_days=2,
        controlplane_hysteresis_exit_days=2,
    )
    base_metrics = sw._simulate_windows(
        windows=windows,
        end_date=end_date,
        initial_equity=initial_equity,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map,
        high_map_by_symbol=high_map,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split,
        profile=profile,
        targets=v1_targets,
        thresholds=v1_thresholds,
        rebalance_time_ny=rebalance_time_ny,
    )

    _G.update(
        {
            "windows": windows,
            "end_date": end_date,
            "initial_equity": initial_equity,
            "rebalance_time_ny": rebalance_time_ny,
            "symbols": symbols,
            "aligned_days": aligned_days,
            "price_history": price_history,
            "close_map_by_symbol": close_map,
            "high_map_by_symbol": high_map,
            "minute_by_day_symbol": minute_by_day_symbol,
            "split_ratio_by_day_symbol": split,
            "profile": profile,
            "baseline_target_by_day": baseline_target,
            "close_series_by_symbol": close_series,
            "base_metrics": base_metrics,
        }
    )

    candidates = _sample_candidates(n_candidates, seed)
    workers = int(os.environ.get("RESEARCH_WORKERS", str(max(1, os.cpu_count() or 1))))

    with get_context("fork").Pool(processes=workers) as pool:
        rows = list(pool.map(_simulate_candidate, candidates))

    rows_sorted = sorted(rows, key=lambda r: (int(r["pass_all"]), r["score"]), reverse=True)
    pass_rows = [r for r in rows_sorted if r["pass_all"]]

    csv_path = REPORT_DIR / "short_dd_material_results.csv"
    json_path = REPORT_DIR / "short_dd_material_results.json"
    summary_path = REPORT_DIR / "short_dd_material_summary.json"

    if rows_sorted:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_sorted[0].keys()))
            w.writeheader()
            w.writerows(rows_sorted)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows_sorted, f, indent=2)

    summary = {
        "end_date": end_date.isoformat(),
        "windows": windows,
        "profile": profile_name,
        "workers": workers,
        "candidates": len(candidates),
        "pass_count": len(pass_rows),
        "baseline": {
            w: {"ret": float(base_metrics[w]["ret"]), "dd": float(base_metrics[w]["dd"])}
            for w in windows
        },
        "best_overall": rows_sorted[0] if rows_sorted else None,
        "best_pass": pass_rows[0] if pass_rows else None,
        "report_dir": str(REPORT_DIR),
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

