#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dateutil.relativedelta import relativedelta

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from csp47_overlay_research_v1.tools.sweep_csp47_overlays import (
    OverlayCandidate,
    _build_scaled_profile,
    _overlay_targets,
)
from protective_stop_variant_v2.tools.export_last30_daybyday import (
    _build_targets_for_engine,
    _parse_hhmm,
    _simulate_with_table,
)
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1


WINDOWS = {
    "1m": relativedelta(months=1),
    "2m": relativedelta(months=2),
    "3m": relativedelta(months=3),
    "4m": relativedelta(months=4),
    "5m": relativedelta(months=5),
    "6m": relativedelta(months=6),
    "1y": relativedelta(years=1),
    "2y": relativedelta(years=2),
}
SHORT_WINDOWS = ["1m", "2m", "3m", "4m", "5m", "6m", "1y"]


@dataclass(frozen=True)
class Candidate:
    cid: str
    trail_scale: float
    threshold_scale: float
    stop_pct: float
    rv_gate: float
    overlay: OverlayCandidate


def _align_start(aligned_days: list[date], cal_start: date, end_day: date) -> date:
    for d in aligned_days:
        if cal_start <= d <= end_day:
            return d
    return aligned_days[0]


def _window_ranges(aligned_days: list[date], end_day: date) -> list[tuple[str, date, date]]:
    out: list[tuple[str, date, date]] = []
    for wname, rel in WINDOWS.items():
        cstart = end_day - rel
        out.append((wname, _align_start(aligned_days, cstart, end_day), end_day))
    return out


def _slice_metrics(day_rows: list[dict[str, Any]], sday: date, eday: date) -> dict[str, float]:
    rows = [r for r in day_rows if sday <= date.fromisoformat(str(r["Date"])) <= eday]
    if not rows:
        return {"final_equity": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0}
    s = float(rows[0]["Day Start Equity"])
    e = float(rows[-1]["Day End Equity"])
    ret = ((e / s) - 1.0) * 100.0 if s > 0 else 0.0
    mdd = max(float(r["Drawdown %"]) for r in rows)
    return {"final_equity": e, "return_pct": ret, "max_dd_pct": mdd}


def _build_candidates() -> list[Candidate]:
    # Curated profile combinations intended to preserve 2Y resilience while
    # testing more short-window-friendly profit-lock parameterizations.
    profiles = [
        (1.00, 1.00, 4.2, 72.5),
        (1.00, 1.00, 4.7, 72.5),
        (1.00, 1.10, 4.7, 72.5),
        (1.00, 1.10, 5.2, 72.5),
        (1.05, 1.00, 4.2, 72.5),
        (1.05, 1.10, 4.7, 72.5),
        (1.05, 1.10, 4.7, 75.0),
        (1.05, 1.20, 4.7, 75.0),
        (1.10, 1.10, 4.7, 75.0),
        (1.10, 1.20, 4.7, 75.0),
        (1.10, 1.20, 4.7, 77.5),
        (1.10, 1.20, 5.2, 75.0),
        (1.15, 1.10, 4.7, 75.0),
        (1.15, 1.20, 4.7, 75.0),
        (1.15, 1.20, 5.2, 77.5),
        (1.20, 1.20, 4.7, 77.5),
    ]
    overlays = [
        OverlayCandidate(6.0, 1, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(7.0, 1, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(6.0, 1, 10.0, 20, 1, "SOXS"),
        OverlayCandidate(6.0, 1, 15.0, 20, 1, "SOXS"),
        OverlayCandidate(7.0, 1, 10.0, 20, 1, "SOXS"),
    ]
    out: list[Candidate] = []
    i = 1
    for ts, ths, sp, rv in profiles:
        for ov in overlays:
            out.append(
                Candidate(
                    cid=f"P5-{i:04d}",
                    trail_scale=ts,
                    threshold_scale=ths,
                    stop_pct=sp,
                    rv_gate=rv,
                    overlay=ov,
                )
            )
            i += 1
    return out


def main() -> int:
    end_day = date(2026, 4, 10)
    start_2y = date(2024, 4, 10)
    initial_equity = 10000.0
    slippage_bps = 1.0
    sell_fee_bps = 1.0
    rebalance_time = "15:55"
    strategy_profile = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
    report_dir = ROOT / "hybrid_c_ov_research_v1" / "reports_pass5"
    report_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "rebalance_threshold": 0.05,
        "controlplane_threshold_cap": 0.50,
        "controlplane_hysteresis_enter": 0.62,
        "controlplane_hysteresis_exit": 0.58,
        "controlplane_hysteresis_enter_days": 2,
        "controlplane_hysteresis_exit_days": 2,
        "warmup_days": 260,
    }

    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(start_2y - timedelta(days=1200), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(end_day + timedelta(days=1), dt_time(23, 59), tzinfo=NY)
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

    baseline_target_by_day = iv._build_baseline_target_by_day(price_history=price_history, initial_equity=initial_equity, warmup_days=int(cfg["warmup_days"]))
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    base_targets, base_thresholds = _build_targets_for_engine(
        engine="fev1",
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=float(cfg["rebalance_threshold"]),
        controlplane_threshold_cap=float(cfg["controlplane_threshold_cap"]),
        controlplane_hysteresis_enter=float(cfg["controlplane_hysteresis_enter"]),
        controlplane_hysteresis_exit=float(cfg["controlplane_hysteresis_exit"]),
        controlplane_hysteresis_enter_days=int(cfg["controlplane_hysteresis_enter_days"]),
        controlplane_hysteresis_exit_days=int(cfg["controlplane_hysteresis_exit_days"]),
    )
    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(loader, symbols=symbols, start_day=start_2y, end_day=end_day, feed=alpaca.data_feed)
    windows = _window_ranges(aligned_days, end_day=end_day)
    rebalance_time_ny = _parse_hhmm(rebalance_time)

    profile_rt = rt_v1.PROFILES[strategy_profile]
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
    c_profile = _build_scaled_profile(base_profile, trail_scale=1.10, threshold_scale=1.20)
    ov_overlay = OverlayCandidate(6.0, 1, 0.0, 20, 1, "SOXS")
    ov_targets = _overlay_targets(aligned_days=aligned_days, close_series=close_series, base_target_by_day=base_targets, candidate=ov_overlay)

    def run_variant(targets: dict[date, dict[str, float]], profile: iv.LockedProfile, stop_pct: float, rv_gate: float):
        rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=targets,
            rebalance_threshold_by_day=base_thresholds,
            profile=profile,
            start_day=start_2y,
            end_day=end_day,
            initial_equity=float(initial_equity),
            slippage_bps=float(slippage_bps),
            sell_fee_bps=float(sell_fee_bps),
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio,
            enable_protective_stop=(float(stop_pct) > 0),
            protective_stop_pct=float(stop_pct),
            stop_scope="inverse_only",
            rv_gate_min_pct=float(rv_gate),
            rv_gate_window=20,
        )
        wm = {w: _slice_metrics(rows, sday=sd, eday=ed) for w, sd, ed in windows}
        return wm

    # Benchmarks
    c_metrics = run_variant(base_targets, c_profile, 4.7, 75.0)
    ov_metrics = run_variant(ov_targets, c_profile, 4.7, 75.0)
    ov_2y = float(ov_metrics["2y"]["return_pct"])

    rows: list[dict[str, Any]] = []
    candidates = _build_candidates()
    for i, cc in enumerate(candidates, start=1):
        prof = _build_scaled_profile(base_profile, trail_scale=cc.trail_scale, threshold_scale=cc.threshold_scale)
        targets = _overlay_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            candidate=cc.overlay,
        )
        met = run_variant(targets, prof, cc.stop_pct, cc.rv_gate)
        short_penalty = 0.0
        short_excess = 0.0
        for w in SHORT_WINDOWS:
            diff = float(met[w]["return_pct"]) - float(c_metrics[w]["return_pct"])
            if diff < 0:
                short_penalty += -diff
            else:
                short_excess += diff
        long_diff = float(met["2y"]["return_pct"]) - ov_2y
        gate_ok = long_diff >= 0.0
        # hard gate score: non-gated rows are penalized heavily
        score = (-short_penalty + short_excess * 0.10) if gate_ok else (-10000.0 - short_penalty + long_diff)
        rec: dict[str, Any] = {
            "candidate_id": cc.cid,
            "gate_ok_2y": gate_ok,
            "score": score,
            "short_penalty_sum": short_penalty,
            "short_excess_sum": short_excess,
            "long_diff_2y_vs_OV": long_diff,
            "trail_scale": cc.trail_scale,
            "threshold_scale": cc.threshold_scale,
            "stop_pct": cc.stop_pct,
            "rv_gate": cc.rv_gate,
            "overlay": cc.overlay.cid,
        }
        for w in WINDOWS:
            rec[f"{w}_ret"] = float(met[w]["return_pct"])
            rec[f"{w}_mdd"] = float(met[w]["max_dd_pct"])
        rows.append(rec)
        if i % 10 == 0 or i == len(candidates):
            print(f"[pass5] {i}/{len(candidates)} done")

    df = pd.DataFrame(rows).sort_values(["gate_ok_2y", "score", "2y_ret"], ascending=[False, False, False]).reset_index(drop=True)
    ranked_csv = report_dir / "pass5_ranked.csv"
    df.to_csv(ranked_csv, index=False)

    gated = df[df["gate_ok_2y"]].copy()
    top_gate = gated.sort_values(["short_penalty_sum", "2y_ret"], ascending=[True, False]).head(1)
    nearest_gate = top_gate.iloc[0].to_dict() if not top_gate.empty else None

    strict = None
    for _, r in gated.iterrows():
        if all(float(r[f"{w}_ret"]) >= float(c_metrics[w]["return_pct"]) for w in SHORT_WINDOWS):
            strict = r.to_dict()
            break

    bench_rows = []
    for name, mm in [("C_sp4.7_rv75_tr1.10_th1.20", c_metrics), ("OV_sh6_h1_dd0w20_re1_SOXS", ov_metrics)]:
        row = {"variant": name}
        for w in WINDOWS:
            row[f"{w}_ret"] = float(mm[w]["return_pct"])
            row[f"{w}_mdd"] = float(mm[w]["max_dd_pct"])
        bench_rows.append(row)
    bench_csv = report_dir / "benchmarks_c_vs_ov.csv"
    pd.DataFrame(bench_rows).to_csv(bench_csv, index=False)

    summary = {
        "candidates_evaluated": int(len(df)),
        "gate_ok_count": int(len(gated)),
        "strict_match_candidate": strict,
        "nearest_gate_candidate": nearest_gate,
        "reports": {
            "ranked_csv": str(ranked_csv),
            "benchmarks_csv": str(bench_csv),
        },
    }
    summary_json = report_dir / "search_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

