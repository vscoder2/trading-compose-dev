#!/usr/bin/env python3
from __future__ import annotations

import json
import math
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
class RegimeCandidate:
    cid: str
    trail_scale: float
    threshold_scale: float
    stop_pct: float
    rv_gate: float
    mom_thr: float
    rv_cap: float
    strict_overlay: OverlayCandidate
    light_overlay: OverlayCandidate


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
        return {"final_equity": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0, "days": 0.0}
    s = float(rows[0]["Day Start Equity"])
    e = float(rows[-1]["Day End Equity"])
    ret = ((e / s) - 1.0) * 100.0 if s > 0 else 0.0
    mdd = max(float(r["Drawdown %"]) for r in rows)
    return {"final_equity": e, "return_pct": ret, "max_dd_pct": mdd, "days": float(len(rows))}


def _rolling_regime_flags(
    *,
    aligned_days: list[date],
    close_series: dict[str, list[float]],
    mom_thr: float,
    rv_cap: float,
) -> dict[date, bool]:
    # risk_on flag used for day d based only on data up to d-1.
    soxl = close_series["SOXL"]
    flags: dict[date, bool] = {}
    rets: list[float] = [0.0]
    for i in range(1, len(soxl)):
        rets.append((soxl[i] / soxl[i - 1] - 1.0) if soxl[i - 1] > 0 else 0.0)
    for i, d in enumerate(aligned_days):
        if i < 21:
            flags[d] = False
            continue
        prev = i - 1
        mom20 = ((soxl[prev] / soxl[prev - 20]) - 1.0) * 100.0 if soxl[prev - 20] > 0 else 0.0
        win = rets[max(1, prev - 19) : prev + 1]
        if not win:
            flags[d] = False
            continue
        mean = sum(win) / len(win)
        var = sum((x - mean) ** 2 for x in win) / len(win)
        rv20 = (math.sqrt(var) * 100.0)
        flags[d] = (mom20 >= mom_thr) and (rv20 <= rv_cap)
    return flags


def _build_regime_targets(
    *,
    aligned_days: list[date],
    close_series: dict[str, list[float]],
    base_target_by_day: dict[date, dict[str, float]],
    strict_overlay: OverlayCandidate,
    light_overlay: OverlayCandidate,
    mom_thr: float,
    rv_cap: float,
) -> dict[date, dict[str, float]]:
    strict_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=base_target_by_day,
        candidate=strict_overlay,
    )
    light_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=base_target_by_day,
        candidate=light_overlay,
    )
    flags = _rolling_regime_flags(
        aligned_days=aligned_days,
        close_series=close_series,
        mom_thr=mom_thr,
        rv_cap=rv_cap,
    )
    out: dict[date, dict[str, float]] = {}
    for d in aligned_days:
        out[d] = dict(light_targets[d] if flags.get(d, False) else strict_targets[d])
    return out


def _candidate_grid() -> list[RegimeCandidate]:
    profile_knobs = [
        (1.10, 1.20, 4.7, 75.0),
        (1.10, 1.20, 4.7, 77.5),
    ]
    # Strict side near OV behavior
    strict_overlays = [
        OverlayCandidate(6.0, 1, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(7.0, 1, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(6.0, 1, 12.0, 20, 1, "SOXS"),
    ]
    # Light side near C behavior
    light_overlays = [
        OverlayCandidate(0.0, 0, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(4.0, 0, 0.0, 20, 1, "SOXS"),
        OverlayCandidate(4.0, 0, 8.0, 20, 1, "SOXS"),
    ]
    mom_thrs = [8.0, 10.0]
    rv_caps = [6.0, 7.5]

    out: list[RegimeCandidate] = []
    i = 1
    for ts, ths, sp, rv in profile_knobs:
        for so in strict_overlays:
            for lo in light_overlays:
                for mt in mom_thrs:
                    for rc in rv_caps:
                        out.append(
                            RegimeCandidate(
                                cid=f"P4-{i:04d}",
                                trail_scale=ts,
                                threshold_scale=ths,
                                stop_pct=sp,
                                rv_gate=rv,
                                mom_thr=mt,
                                rv_cap=rc,
                                strict_overlay=so,
                                light_overlay=lo,
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
    report_dir = ROOT / "hybrid_c_ov_research_v1" / "reports_pass4"
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

    windows = _window_ranges(aligned_days, end_day=end_day)
    rebalance_time_ny = _parse_hhmm(rebalance_time)

    def run_variant(name: str, targets: dict[date, dict[str, float]], profile: iv.LockedProfile, stop_pct: float, rv_gate: float):
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
        return rows, wm

    # Benchmarks
    _, c_metrics = run_variant("C", base_targets, c_profile, 4.7, 75.0)
    _, ov_metrics = run_variant("OV", ov_targets, c_profile, 4.7, 75.0)

    candidates = _candidate_grid()
    pass_rows: list[dict[str, Any]] = []
    for i, cc in enumerate(candidates, start=1):
        prof = _build_scaled_profile(base_profile, trail_scale=cc.trail_scale, threshold_scale=cc.threshold_scale)
        targets = _build_regime_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            strict_overlay=cc.strict_overlay,
            light_overlay=cc.light_overlay,
            mom_thr=cc.mom_thr,
            rv_cap=cc.rv_cap,
        )
        rows, metrics = run_variant(cc.cid, targets, prof, cc.stop_pct, cc.rv_gate)

        short_penalty = 0.0
        short_excess = 0.0
        for w in SHORT_WINDOWS:
            diff = metrics[w]["return_pct"] - c_metrics[w]["return_pct"]
            if diff < 0:
                short_penalty += -diff
            else:
                short_excess += diff
        long_diff = metrics["2y"]["return_pct"] - ov_metrics["2y"]["return_pct"]
        gate_penalty = 0.0 if long_diff >= 0 else (-long_diff * 4.0)
        score = (short_excess * 0.15) - short_penalty - gate_penalty

        rec: dict[str, Any] = {
            "candidate_id": cc.cid,
            "score": score,
            "short_penalty_sum": short_penalty,
            "short_excess_sum": short_excess,
            "long_diff_2y_vs_OV": long_diff,
            "trail_scale": cc.trail_scale,
            "threshold_scale": cc.threshold_scale,
            "stop_pct": cc.stop_pct,
            "rv_gate": cc.rv_gate,
            "mom_thr": cc.mom_thr,
            "rv_cap": cc.rv_cap,
            "strict_overlay": cc.strict_overlay.cid,
            "light_overlay": cc.light_overlay.cid,
        }
        for w in WINDOWS:
            rec[f"{w}_ret"] = metrics[w]["return_pct"]
            rec[f"{w}_mdd"] = metrics[w]["max_dd_pct"]
        pass_rows.append(rec)

        if i % 12 == 0 or i == len(candidates):
            print(f"[pass4] {i}/{len(candidates)} done")

    out_df = pd.DataFrame(pass_rows).sort_values("score", ascending=False).reset_index(drop=True)
    pass_csv = report_dir / "pass4_ranked.csv"
    out_df.to_csv(pass_csv, index=False)

    # strict winner criteria
    strict = None
    for _, r in out_df.iterrows():
        ok_short = all(float(r[f"{w}_ret"]) >= float(c_metrics[w]["return_pct"]) for w in SHORT_WINDOWS)
        ok_long = float(r["2y_ret"]) >= float(ov_metrics["2y"]["return_pct"])
        if ok_short and ok_long:
            strict = r.to_dict()
            break

    # nearest satisfying 2Y gate
    gate_ok = out_df[out_df["2y_ret"] >= float(ov_metrics["2y"]["return_pct"])].copy()
    nearest = gate_ok.sort_values(["short_penalty_sum", "2y_ret"], ascending=[True, False]).head(1)
    nearest_rec = nearest.iloc[0].to_dict() if not nearest.empty else None

    bench_rows = []
    for name, mm in [("C_sp4.7_rv75_tr1.10_th1.20", c_metrics), ("OV_sh6_h1_dd0w20_re1_SOXS", ov_metrics)]:
        row = {"variant": name}
        for w in WINDOWS:
            row[f"{w}_ret"] = mm[w]["return_pct"]
            row[f"{w}_mdd"] = mm[w]["max_dd_pct"]
        bench_rows.append(row)
    bench_csv = report_dir / "benchmarks_c_vs_ov.csv"
    pd.DataFrame(bench_rows).to_csv(bench_csv, index=False)

    summary = {
        "candidates_evaluated": int(len(out_df)),
        "best_candidate": out_df.iloc[0].to_dict() if not out_df.empty else None,
        "strict_match_candidate": strict,
        "nearest_gate_candidate": nearest_rec,
        "reports": {
            "ranked_csv": str(pass_csv),
            "benchmarks_csv": str(bench_csv),
        },
    }
    summary_json = report_dir / "search_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
