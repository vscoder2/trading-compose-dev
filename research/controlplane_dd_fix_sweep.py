#!/usr/bin/env python3
"""Research-only constrained sweep for v2 control-plane parameters.

Goal:
- Fix 2y DD regression while keeping/improving return.
- Do not modify production runtime code.

This script reuses existing switch runtime simulation primitives and writes output
only under research/reports.
"""

from __future__ import annotations

import csv
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1
from composer_original.tools import intraday_profit_lock_verification as iv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader

REPORT_DIR = ROOT / "research" / "reports" / f"controlplane_dd_fix_sweep_{date.today().strftime('%Y%m%d')}"


@dataclass(frozen=True)
class SweepCfg:
    cap: float
    enter: float
    exit: float
    enter_days: int
    exit_days: int


def _load_profile(name: str = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m") -> iv.LockedProfile:
    p = rt_v1.PROFILES[name]
    return iv.LockedProfile(
        name=p.name,
        enable_profit_lock=p.enable_profit_lock,
        profit_lock_mode=p.profit_lock_mode,
        profit_lock_threshold_pct=p.profit_lock_threshold_pct,
        profit_lock_trail_pct=p.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=p.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=p.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=p.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=p.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=p.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=p.profit_lock_adaptive_max_threshold_pct,
    )


def _simulate_windows(
    *,
    windows: list[str],
    end_date: date,
    initial_equity: float,
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    high_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    split_ratio_by_day_symbol: dict[date, dict[str, float]],
    profile: iv.LockedProfile,
    target_by_day: dict[date, dict[str, float]],
    threshold_by_day: dict[date, float],
    rebalance_time_ny: dt_time,
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for w in windows:
        start_day, _ = ab._window_range(end_date, w)
        r = ab._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=threshold_by_day,
            profile=profile,
            start_day=start_day,
            end_day=end_date,
            initial_equity=initial_equity,
            slippage_bps=1.0,
            sell_fee_bps=0.0,
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        )
        out[w] = {
            "final_equity": float(r.final_equity),
            "return_pct": float(r.total_return_pct),
            "maxdd_pct": float(r.max_drawdown_pct),
            "events": float(len(r.events)),
        }
    return out


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    end_date = date.fromisoformat("2026-03-27")
    windows = ["1y", "2y", "3y", "4y", "5y"]
    gate_window = ["2y"]
    eval_windows_if_pass = ["1y", "3y", "4y", "5y"]
    initial_equity = 10_000.0
    rebalance_time_ny = ab._parse_hhmm("15:55")

    # Grid tuned for DD fix objective.
    grid: list[SweepCfg] = []
    for cap, enter, exit, enter_days, exit_days in itertools.product(
        [0.10, 0.12],
        [0.70, 0.72],
        [0.64],
        [3],
        [1, 2],
    ):
        if exit >= enter:
            continue
        grid.append(SweepCfg(cap, enter, exit, enter_days, exit_days))

    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)
    profile = _load_profile()

    max_days = max(ab.WINDOW_TO_DAYS[w] for w in windows)
    earliest_start = end_date - timedelta(days=max_days)
    lookback_start = datetime.combine(earliest_start - timedelta(days=max(800, 260 + 20)), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

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
    _, _, raw_close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)

    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )

    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _c, h in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(h)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    baseline_target_by_day = iv._build_baseline_target_by_day(
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

    # v1 baseline once.
    v1_targets, v1_thresholds, _v1v, _v2t, _v2thr, _v2v = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=0.50,
        controlplane_hysteresis_enter=0.62,
        controlplane_hysteresis_exit=0.58,
        controlplane_hysteresis_enter_days=2,
        controlplane_hysteresis_exit_days=2,
    )
    v1_metrics = _simulate_windows(
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
        target_by_day=v1_targets,
        threshold_by_day=v1_thresholds,
        rebalance_time_ny=rebalance_time_ny,
    )

    rows: list[dict[str, Any]] = []
    for cfg in grid:
        _a, _b, _c, v2_targets, v2_thresholds, _d = ab._build_switch_targets_and_thresholds(
            aligned_days=aligned_days,
            symbols=symbols,
            close_series=close_series,
            baseline_target_by_day=baseline_target_by_day,
            base_rebalance_threshold=0.05,
            controlplane_threshold_cap=float(cfg.cap),
            controlplane_hysteresis_enter=float(cfg.enter),
            controlplane_hysteresis_exit=float(cfg.exit),
            controlplane_hysteresis_enter_days=int(cfg.enter_days),
            controlplane_hysteresis_exit_days=int(cfg.exit_days),
        )

        # Stage 1: evaluate only 2y hard-gate first (fast path).
        v2_metrics_gate = _simulate_windows(
            windows=gate_window,
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
            target_by_day=v2_targets,
            threshold_by_day=v2_thresholds,
            rebalance_time_ny=rebalance_time_ny,
        )

        # Hard acceptance rule (user objective).
        gate_2y_dd = v2_metrics_gate["2y"]["maxdd_pct"] <= (v1_metrics["2y"]["maxdd_pct"] + 2.0)
        gate_2y_ret = v2_metrics_gate["2y"]["return_pct"] >= v1_metrics["2y"]["return_pct"]
        accepted = bool(gate_2y_dd and gate_2y_ret)

        v2_metrics = {"2y": v2_metrics_gate["2y"]}
        if accepted:
            v2_metrics_rest = _simulate_windows(
                windows=eval_windows_if_pass,
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
                target_by_day=v2_targets,
                threshold_by_day=v2_thresholds,
                rebalance_time_ny=rebalance_time_ny,
            )
            v2_metrics.update(v2_metrics_rest)

        diffs: list[float] = []
        score_diffs: list[float] = []
        if accepted:
            for w in windows:
                ret_diff = v2_metrics[w]["return_pct"] - v1_metrics[w]["return_pct"]
                # Risk-adjusted score = return - 1.0 * maxdd
                s1 = v1_metrics[w]["return_pct"] - v1_metrics[w]["maxdd_pct"]
                s2 = v2_metrics[w]["return_pct"] - v2_metrics[w]["maxdd_pct"]
                diffs.append(ret_diff)
                score_diffs.append(s2 - s1)

        row = {
            "cap": cfg.cap,
            "enter": cfg.enter,
            "exit": cfg.exit,
            "enter_days": cfg.enter_days,
            "exit_days": cfg.exit_days,
            "gate_2y_dd": gate_2y_dd,
            "gate_2y_ret": gate_2y_ret,
            "accepted": accepted,
            "avg_return_diff_pct_vs_v1": float(sum(diffs) / len(diffs)) if diffs else float("-inf"),
            "avg_score_diff_vs_v1": float(sum(score_diffs) / len(score_diffs)) if score_diffs else float("-inf"),
        }

        for w in windows:
            row[f"v1_{w}_ret"] = v1_metrics[w]["return_pct"]
            row[f"v1_{w}_dd"] = v1_metrics[w]["maxdd_pct"]
            if w in v2_metrics:
                row[f"v2_{w}_ret"] = v2_metrics[w]["return_pct"]
                row[f"v2_{w}_dd"] = v2_metrics[w]["maxdd_pct"]
            else:
                row[f"v2_{w}_ret"] = None
                row[f"v2_{w}_dd"] = None
        rows.append(row)

    # Rank accepted first by score then by return.
    rows_sorted = sorted(
        rows,
        key=lambda r: (
            int(r["accepted"]),
            r["avg_score_diff_vs_v1"],
            r["avg_return_diff_pct_vs_v1"],
        ),
        reverse=True,
    )

    csv_path = REPORT_DIR / "controlplane_dd_fix_sweep_results.csv"
    json_path = REPORT_DIR / "controlplane_dd_fix_sweep_results.json"

    if rows_sorted:
        headers = list(rows_sorted[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for r in rows_sorted:
                w.writerow(r)

    payload = {
        "report_dir": str(REPORT_DIR),
        "end_date": end_date.isoformat(),
        "windows": windows,
        "grid_size": len(grid),
        "accepted_count": sum(1 for r in rows_sorted if r["accepted"]),
        "baseline_v1": v1_metrics,
        "top10": rows_sorted[:10],
        "csv": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
