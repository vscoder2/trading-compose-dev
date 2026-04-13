#!/usr/bin/env python3
from __future__ import annotations

import csv
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
class CandidateConfig:
    cid: str
    trail_scale: float
    threshold_scale: float
    stop_pct: float
    rv_gate: float
    shock_drop_pct: float
    shock_hold_days: int
    dd_trigger_pct: float
    dd_window_days: int
    reentry_pos_days: int
    defensive_symbol: str


def _align_start(aligned_days: list[date], cal_start: date, end_day: date) -> date:
    for d in aligned_days:
        if cal_start <= d <= end_day:
            return d
    return aligned_days[0]


def _window_ranges(aligned_days: list[date], end_day: date) -> list[tuple[str, date, date]]:
    out: list[tuple[str, date, date]] = []
    for wname, rel in WINDOWS.items():
        cstart = end_day - rel
        sday = _align_start(aligned_days, cstart, end_day)
        out.append((wname, sday, end_day))
    return out


def _slice_metrics(day_rows: list[dict[str, Any]], sday: date, eday: date) -> dict[str, float]:
    rows = [r for r in day_rows if sday <= date.fromisoformat(str(r["Date"])) <= eday]
    if not rows:
        return {
            "final_equity": 0.0,
            "return_pct": 0.0,
            "max_dd_pct": 0.0,
            "days": 0.0,
        }
    start_eq = float(rows[0]["Day Start Equity"])
    final_eq = float(rows[-1]["Day End Equity"])
    ret = ((final_eq / start_eq) - 1.0) * 100.0 if start_eq > 0 else 0.0
    max_dd = max(float(r["Drawdown %"]) for r in rows)
    return {
        "final_equity": final_eq,
        "return_pct": ret,
        "max_dd_pct": max_dd,
        "days": float(len(rows)),
    }


def _build_candidates() -> list[CandidateConfig]:
    # Focused sweep around current winning families:
    # - keep profile very close to C_sp4.7_rv75_tr1.10_th1.20
    # - vary overlay strictness toward OV-style 2Y resilience
    profile_knobs = [
        # (trail_scale, threshold_scale, stop_pct, rv_gate)
        (1.10, 1.20, 4.7, 75.0),  # exact C baseline
        (1.08, 1.20, 4.7, 75.0),
        (1.12, 1.20, 4.7, 75.0),
        (1.10, 1.15, 4.7, 75.0),
        (1.10, 1.25, 4.7, 75.0),
        (1.10, 1.20, 4.2, 75.0),
        (1.10, 1.20, 5.2, 75.0),
        (1.10, 1.20, 4.7, 72.5),
        (1.10, 1.20, 4.7, 77.5),
    ]
    overlay_knobs = [
        # (shock_drop_pct, shock_hold_days, dd_trigger_pct, dd_window_days, reentry_pos_days)
        (5.0, 1, 0.0, 20, 1),
        (6.0, 1, 0.0, 20, 1),
        (7.0, 1, 0.0, 20, 1),
        (5.0, 2, 0.0, 20, 1),
        (6.0, 2, 0.0, 20, 1),
        (5.0, 1, 15.0, 20, 1),
        (6.0, 1, 15.0, 20, 1),
        (7.0, 1, 15.0, 20, 1),
        (5.0, 1, 15.0, 40, 1),
        (6.0, 1, 15.0, 40, 1),
        (7.0, 1, 15.0, 40, 1),
        (5.0, 1, 25.0, 20, 1),
        (6.0, 1, 25.0, 20, 1),
        (7.0, 1, 25.0, 20, 1),
        (6.0, 1, 15.0, 20, 2),
        (6.0, 1, 25.0, 20, 2),
    ]

    out: list[CandidateConfig] = []
    i = 1
    for ts, ths, sp, rv in profile_knobs:
        for sd, sh, dt, dw, re in overlay_knobs:
            cid = f"HYB-{i:04d}"
            out.append(
                CandidateConfig(
                    cid=cid,
                    trail_scale=ts,
                    threshold_scale=ths,
                    stop_pct=sp,
                    rv_gate=rv,
                    shock_drop_pct=sd,
                    shock_hold_days=sh,
                    dd_trigger_pct=dt,
                    dd_window_days=dw,
                    reentry_pos_days=re,
                    defensive_symbol="SOXS",
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

    report_dir = ROOT / "hybrid_c_ov_research_v1" / "reports"
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

    daily_ohlc_adj = iv._fetch_daily_ohlc(
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
        initial_equity=initial_equity,
        warmup_days=int(cfg["warmup_days"]),
    )
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

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=start_2y,
        end_day=end_day,
        feed=alpaca.data_feed,
    )

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

    # Benchmarks
    c_profile = _build_scaled_profile(base_profile, trail_scale=1.10, threshold_scale=1.20)
    ov_candidate = OverlayCandidate(6.0, 1, 0.0, 20, 1, "SOXS")
    ov_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=base_targets,
        candidate=ov_candidate,
    )

    rebalance_time_ny = _parse_hhmm(rebalance_time)
    windows = _window_ranges(aligned_days, end_day=end_day)

    def run_variant(
        *,
        name: str,
        target_by_day: dict[date, dict[str, float]],
        profile: iv.LockedProfile,
        stop_pct: float,
        rv_gate: float,
    ) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, float]]]:
        rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=base_thresholds,
            profile=profile,
            start_day=start_2y,
            end_day=end_day,
            initial_equity=initial_equity,
            slippage_bps=slippage_bps,
            sell_fee_bps=sell_fee_bps,
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
        wm: dict[str, dict[str, float]] = {}
        for wname, sday, eday in windows:
            wm[wname] = _slice_metrics(rows, sday=sday, eday=eday)
        return name, rows, wm

    _, c_rows, c_metrics = run_variant(
        name="C_sp4.7_rv75_tr1.10_th1.20",
        target_by_day=base_targets,
        profile=c_profile,
        stop_pct=4.7,
        rv_gate=75.0,
    )
    _, ov_rows, ov_metrics = run_variant(
        name="OV_sh6_h1_dd0w20_re1_SOXS",
        target_by_day=ov_targets,
        profile=c_profile,
        stop_pct=4.7,
        rv_gate=75.0,
    )

    # Pass-1 sweep
    candidates = _build_candidates()
    pass1_rows: list[dict[str, Any]] = []
    for idx, cc in enumerate(candidates, start=1):
        prof = _build_scaled_profile(base_profile, trail_scale=cc.trail_scale, threshold_scale=cc.threshold_scale)
        ov = OverlayCandidate(
            cc.shock_drop_pct,
            cc.shock_hold_days,
            cc.dd_trigger_pct,
            cc.dd_window_days,
            cc.reentry_pos_days,
            cc.defensive_symbol,
        )
        targets = _overlay_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            candidate=ov,
        )
        _, _, metrics = run_variant(
            name=cc.cid,
            target_by_day=targets,
            profile=prof,
            stop_pct=cc.stop_pct,
            rv_gate=cc.rv_gate,
        )

        short_penalty = 0.0
        short_excess = 0.0
        for w in SHORT_WINDOWS:
            diff = metrics[w]["return_pct"] - c_metrics[w]["return_pct"]
            if diff < 0:
                short_penalty += -diff
            else:
                short_excess += diff
        long_diff = metrics["2y"]["return_pct"] - ov_metrics["2y"]["return_pct"]
        long_penalty = -long_diff if long_diff < 0 else 0.0
        score = (short_excess * 0.20) - (short_penalty * 1.00) - (long_penalty * 1.20)

        row: dict[str, Any] = {
            "candidate_id": cc.cid,
            "score": score,
            "short_penalty_sum": short_penalty,
            "short_excess_sum": short_excess,
            "long_diff_2y_vs_OV": long_diff,
            "trail_scale": cc.trail_scale,
            "threshold_scale": cc.threshold_scale,
            "stop_pct": cc.stop_pct,
            "rv_gate": cc.rv_gate,
            "shock_drop_pct": cc.shock_drop_pct,
            "shock_hold_days": cc.shock_hold_days,
            "dd_trigger_pct": cc.dd_trigger_pct,
            "dd_window_days": cc.dd_window_days,
            "reentry_pos_days": cc.reentry_pos_days,
            "defensive_symbol": cc.defensive_symbol,
        }
        for w in WINDOWS:
            row[f"{w}_ret"] = metrics[w]["return_pct"]
            row[f"{w}_mdd"] = metrics[w]["max_dd_pct"]
        pass1_rows.append(row)
        if idx % 12 == 0 or idx == len(candidates):
            print(f"[pass1] {idx}/{len(candidates)} done")

    pass1_df = pd.DataFrame(pass1_rows).sort_values("score", ascending=False).reset_index(drop=True)
    pass1_csv = report_dir / "pass1_hybrid_candidates.csv"
    pass1_df.to_csv(pass1_csv, index=False)

    # Pass-2 validation: top 20 from pass-1, rerun and write day-by-day for top 5.
    top20 = pass1_df.head(20).copy()
    pass2_rows: list[dict[str, Any]] = []
    for idx2, (_, r) in enumerate(top20.iterrows(), start=1):
        cc = CandidateConfig(
            cid=str(r["candidate_id"]),
            trail_scale=float(r["trail_scale"]),
            threshold_scale=float(r["threshold_scale"]),
            stop_pct=float(r["stop_pct"]),
            rv_gate=float(r["rv_gate"]),
            shock_drop_pct=float(r["shock_drop_pct"]),
            shock_hold_days=int(r["shock_hold_days"]),
            dd_trigger_pct=float(r["dd_trigger_pct"]),
            dd_window_days=int(r["dd_window_days"]),
            reentry_pos_days=int(r["reentry_pos_days"]),
            defensive_symbol=str(r["defensive_symbol"]),
        )
        prof = _build_scaled_profile(base_profile, trail_scale=cc.trail_scale, threshold_scale=cc.threshold_scale)
        ov = OverlayCandidate(
            cc.shock_drop_pct,
            cc.shock_hold_days,
            cc.dd_trigger_pct,
            cc.dd_window_days,
            cc.reentry_pos_days,
            cc.defensive_symbol,
        )
        targets = _overlay_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            candidate=ov,
        )
        _, rows, metrics = run_variant(
            name=cc.cid,
            target_by_day=targets,
            profile=prof,
            stop_pct=cc.stop_pct,
            rv_gate=cc.rv_gate,
        )

        short_penalty = 0.0
        short_excess = 0.0
        for w in SHORT_WINDOWS:
            diff = metrics[w]["return_pct"] - c_metrics[w]["return_pct"]
            if diff < 0:
                short_penalty += -diff
            else:
                short_excess += diff
        long_diff = metrics["2y"]["return_pct"] - ov_metrics["2y"]["return_pct"]
        long_penalty = -long_diff if long_diff < 0 else 0.0
        score = (short_excess * 0.20) - (short_penalty * 1.00) - (long_penalty * 1.20)

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
            "shock_drop_pct": cc.shock_drop_pct,
            "shock_hold_days": cc.shock_hold_days,
            "dd_trigger_pct": cc.dd_trigger_pct,
            "dd_window_days": cc.dd_window_days,
            "reentry_pos_days": cc.reentry_pos_days,
            "defensive_symbol": cc.defensive_symbol,
        }
        for w in WINDOWS:
            rec[f"{w}_ret"] = metrics[w]["return_pct"]
            rec[f"{w}_mdd"] = metrics[w]["max_dd_pct"]
        pass2_rows.append(rec)
        print(f"[pass2] {idx2}/{len(top20)} done")

        # Persist top-5 day-by-day after pass-2 sort later.
        out_csv = report_dir / f"{cc.cid}_2024-04-10_to_2026-04-10_daybyday.csv"
        if not out_csv.exists():
            with out_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    pass2_df = pd.DataFrame(pass2_rows).sort_values("score", ascending=False).reset_index(drop=True)
    pass2_csv = report_dir / "pass2_top20_validated.csv"
    pass2_df.to_csv(pass2_csv, index=False)

    # Benchmark table for easy comparison
    b_rows = []
    for name, mm in [("C_sp4.7_rv75_tr1.10_th1.20", c_metrics), ("OV_sh6_h1_dd0w20_re1_SOXS", ov_metrics)]:
        row: dict[str, Any] = {"variant": name}
        for w in WINDOWS:
            row[f"{w}_ret"] = mm[w]["return_pct"]
            row[f"{w}_mdd"] = mm[w]["max_dd_pct"]
        b_rows.append(row)
    b_df = pd.DataFrame(b_rows)
    b_csv = report_dir / "benchmarks_c_vs_ov.csv"
    b_df.to_csv(b_csv, index=False)

    # Report best candidate if any strong match found.
    best = pass2_df.iloc[0].to_dict() if not pass2_df.empty else {}
    # strict check
    strict = None
    for _, r in pass2_df.iterrows():
        ok_short = True
        for w in SHORT_WINDOWS:
            if float(r[f"{w}_ret"]) < float(c_metrics[w]["return_pct"]):
                ok_short = False
                break
        ok_long = float(r["2y_ret"]) >= float(ov_metrics["2y"]["return_pct"])
        if ok_short and ok_long:
            strict = r.to_dict()
            break

    out = {
        "candidates_evaluated_pass1": int(len(pass1_df)),
        "candidates_evaluated_pass2": int(len(pass2_df)),
        "best_candidate_pass2": best,
        "strict_match_candidate": strict,
        "reports": {
            "pass1_csv": str(pass1_csv),
            "pass2_csv": str(pass2_csv),
            "benchmarks_csv": str(b_csv),
        },
    }
    out_json = report_dir / "search_summary.json"
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
