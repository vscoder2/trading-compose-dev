#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import statistics
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
class DualCandidate:
    cid: str
    mom20_thr: float
    mom60_thr: float
    rv20_cap: float
    dd60_cap: float
    enter_days: int
    exit_days: int


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


def _slice_metrics(rows: list[dict[str, Any]], sday: date, eday: date) -> dict[str, float]:
    rr = [r for r in rows if sday <= date.fromisoformat(str(r["Date"])) <= eday]
    if not rr:
        return {"final_equity": 0.0, "return_pct": 0.0, "max_dd_pct": 0.0}
    s = float(rr[0]["Day Start Equity"])
    e = float(rr[-1]["Day End Equity"])
    ret = ((e / s) - 1.0) * 100.0 if s > 0 else 0.0
    mdd = max(float(r["Drawdown %"]) for r in rr)
    return {"final_equity": e, "return_pct": ret, "max_dd_pct": mdd}


def _daily_features(soxl: list[float]) -> tuple[list[float], list[float], list[float], list[float]]:
    rets = [0.0]
    for i in range(1, len(soxl)):
        prev = soxl[i - 1]
        rets.append((soxl[i] / prev - 1.0) if prev > 0 else 0.0)
    mom20 = [float("nan")] * len(soxl)
    mom60 = [float("nan")] * len(soxl)
    rv20 = [float("nan")] * len(soxl)
    dd60 = [float("nan")] * len(soxl)
    for i in range(len(soxl)):
        if i >= 20 and soxl[i - 20] > 0:
            mom20[i] = (soxl[i] / soxl[i - 20] - 1.0) * 100.0
        if i >= 60 and soxl[i - 60] > 0:
            mom60[i] = (soxl[i] / soxl[i - 60] - 1.0) * 100.0
        if i >= 20:
            win = rets[i - 19 : i + 1]
            rv20[i] = statistics.pstdev(win) * 100.0
        if i >= 60:
            winp = soxl[i - 59 : i + 1]
            peak = max(winp)
            dd60[i] = ((peak - winp[-1]) / peak * 100.0) if peak > 0 else 0.0
    return mom20, mom60, rv20, dd60


def _regime_map(
    *,
    aligned_days: list[date],
    mom20: list[float],
    mom60: list[float],
    rv20: list[float],
    dd60: list[float],
    cand: DualCandidate,
) -> dict[date, bool]:
    # True => C-engine, False => OV-engine
    state = False
    on_streak = 0
    off_streak = 0
    out: dict[date, bool] = {}
    for i, d in enumerate(aligned_days):
        if i < 61:
            out[d] = state
            continue
        p = i - 1  # use only prior-day information
        raw_on = (
            mom20[p] >= cand.mom20_thr
            and mom60[p] >= cand.mom60_thr
            and rv20[p] <= cand.rv20_cap
            and dd60[p] <= cand.dd60_cap
        )
        if raw_on:
            on_streak += 1
            off_streak = 0
        else:
            off_streak += 1
            on_streak = 0
        if not state and on_streak >= cand.enter_days:
            state = True
            on_streak = 0
        elif state and off_streak >= cand.exit_days:
            state = False
            off_streak = 0
        out[d] = state
    return out


def _build_candidates() -> list[DualCandidate]:
    out: list[DualCandidate] = []
    i = 1
    for mom20_thr in [2.0, 4.0, 6.0, 8.0, 10.0]:
        for mom60_thr in [0.0, 2.0, 4.0, 6.0]:
            for rv20_cap in [5.0, 6.0, 7.0, 8.0]:
                for dd60_cap in [20.0, 30.0, 40.0, 50.0, 60.0]:
                    for enter_days in [1, 2, 3]:
                        for exit_days in [1, 2, 3]:
                            out.append(
                                DualCandidate(
                                    cid=f"D6-{i:04d}",
                                    mom20_thr=mom20_thr,
                                    mom60_thr=mom60_thr,
                                    rv20_cap=rv20_cap,
                                    dd60_cap=dd60_cap,
                                    enter_days=enter_days,
                                    exit_days=exit_days,
                                )
                            )
                            i += 1
    return out


def _proxy_metrics(
    *,
    dates: list[date],
    c_ret_by_day: dict[date, float],
    ov_ret_by_day: dict[date, float],
    regime: dict[date, bool],
    initial_equity: float,
    windows: list[tuple[str, date, date]],
) -> dict[str, dict[str, float]]:
    rows: list[dict[str, Any]] = []
    eq = float(initial_equity)
    peak = eq
    for d in dates:
        start_eq = eq
        rr = c_ret_by_day[d] if regime.get(d, False) else ov_ret_by_day[d]
        eq = eq * (1.0 + rr / 100.0)
        peak = max(peak, eq)
        dd = ((peak - eq) / peak * 100.0) if peak > 0 else 0.0
        rows.append(
            {
                "Date": d.isoformat(),
                "Day Start Equity": start_eq,
                "Day End Equity": eq,
                "Return %": rr,
                "Drawdown %": dd,
            }
        )
    out: dict[str, dict[str, float]] = {}
    for wname, sday, eday in windows:
        out[wname] = _slice_metrics(rows, sday=sday, eday=eday)
    return out


def main() -> int:
    end_day = date(2026, 4, 10)
    start_2y = date(2024, 4, 10)
    initial_equity = 10000.0
    slippage_bps = 1.0
    sell_fee_bps = 1.0
    rebalance_time = "15:55"
    strategy_profile = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
    report_dir = ROOT / "hybrid_c_ov_research_v1" / "reports_pass6"
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
    # fixed engines
    c_profile = _build_scaled_profile(base_profile, trail_scale=1.10, threshold_scale=1.20)
    ov_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=base_targets,
        candidate=OverlayCandidate(6.0, 1, 0.0, 20, 1, "SOXS"),
    )

    windows = _window_ranges(aligned_days, end_day=end_day)
    rebalance_time_ny = _parse_hhmm(rebalance_time)

    def run_variant(target_by_day: dict[date, dict[str, float]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
        rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=base_thresholds,
            profile=c_profile,
            start_day=start_2y,
            end_day=end_day,
            initial_equity=float(initial_equity),
            slippage_bps=float(slippage_bps),
            sell_fee_bps=float(sell_fee_bps),
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio,
            enable_protective_stop=True,
            protective_stop_pct=4.7,
            stop_scope="inverse_only",
            rv_gate_min_pct=75.0,
            rv_gate_window=20,
        )
        wm = {w: _slice_metrics(rows, sday=sd, eday=ed) for w, sd, ed in windows}
        return rows, wm

    # Baselines once
    c_rows, c_metrics = run_variant(base_targets)
    ov_rows, ov_metrics = run_variant(ov_targets)
    ov_2y = float(ov_metrics["2y"]["return_pct"])
    c_ret_by_day = {date.fromisoformat(str(r["Date"])): float(r["Return %"]) for r in c_rows}
    ov_ret_by_day = {date.fromisoformat(str(r["Date"])): float(r["Return %"]) for r in ov_rows}
    eval_days = [
        d
        for d in aligned_days
        if (start_2y <= d <= end_day and d in c_ret_by_day and d in ov_ret_by_day)
    ]
    windows_eval = _window_ranges(eval_days, end_day=end_day)

    soxl = close_series["SOXL"]
    mom20, mom60, rv20, dd60 = _daily_features(soxl)

    # Stage-A: fast proxy on large grid
    proxy_rows: list[dict[str, Any]] = []
    candidates = _build_candidates()
    for i, cc in enumerate(candidates, start=1):
        regime = _regime_map(
            aligned_days=aligned_days,
            mom20=mom20,
            mom60=mom60,
            rv20=rv20,
            dd60=dd60,
            cand=cc,
        )
        met = _proxy_metrics(
            dates=eval_days,
            c_ret_by_day=c_ret_by_day,
            ov_ret_by_day=ov_ret_by_day,
            regime=regime,
            initial_equity=initial_equity,
            windows=windows_eval,
        )
        short_pen = 0.0
        for w in SHORT_WINDOWS:
            short_pen += max(0.0, float(c_metrics[w]["return_pct"]) - float(met[w]["return_pct"]))
        long_diff = float(met["2y"]["return_pct"]) - ov_2y
        gate_ok = long_diff >= 0.0
        score = (-short_pen) if gate_ok else (-10000.0 - short_pen + long_diff)
        rec: dict[str, Any] = {
            "candidate_id": cc.cid,
            "gate_ok_2y_proxy": gate_ok,
            "score_proxy": score,
            "short_penalty_proxy": short_pen,
            "long_diff_2y_vs_OV_proxy": long_diff,
            "mom20_thr": cc.mom20_thr,
            "mom60_thr": cc.mom60_thr,
            "rv20_cap": cc.rv20_cap,
            "dd60_cap": cc.dd60_cap,
            "enter_days": cc.enter_days,
            "exit_days": cc.exit_days,
        }
        for w in WINDOWS:
            rec[f"{w}_ret_proxy"] = float(met[w]["return_pct"])
        proxy_rows.append(rec)
        if i % 120 == 0 or i == len(candidates):
            print(f"[pass6-proxy] {i}/{len(candidates)} done")

    proxy_df = pd.DataFrame(proxy_rows).sort_values(["gate_ok_2y_proxy", "score_proxy"], ascending=[False, False]).reset_index(drop=True)
    proxy_csv = report_dir / "pass6_proxy_ranked.csv"
    proxy_df.to_csv(proxy_csv, index=False)

    # Stage-B: full intraday validation for top proxy set
    topN = 24
    top = proxy_df.head(topN).copy()
    full_rows: list[dict[str, Any]] = []
    for j, (_, rr) in enumerate(top.iterrows(), start=1):
        cc = DualCandidate(
            cid=str(rr["candidate_id"]),
            mom20_thr=float(rr["mom20_thr"]),
            mom60_thr=float(rr["mom60_thr"]),
            rv20_cap=float(rr["rv20_cap"]),
            dd60_cap=float(rr["dd60_cap"]),
            enter_days=int(rr["enter_days"]),
            exit_days=int(rr["exit_days"]),
        )
        regime = _regime_map(aligned_days=aligned_days, mom20=mom20, mom60=mom60, rv20=rv20, dd60=dd60, cand=cc)
        dual_targets: dict[date, dict[str, float]] = {}
        for d in aligned_days:
            dual_targets[d] = dict(base_targets[d] if regime.get(d, False) else ov_targets[d])
        _, met = run_variant(dual_targets)
        short_pen = 0.0
        short_excess = 0.0
        for w in SHORT_WINDOWS:
            diff = float(met[w]["return_pct"]) - float(c_metrics[w]["return_pct"])
            if diff < 0:
                short_pen += -diff
            else:
                short_excess += diff
        long_diff = float(met["2y"]["return_pct"]) - ov_2y
        gate_ok = long_diff >= 0.0
        score = (-short_pen + short_excess * 0.10) if gate_ok else (-10000.0 - short_pen + long_diff)
        rec: dict[str, Any] = {
            "candidate_id": cc.cid,
            "gate_ok_2y": gate_ok,
            "score": score,
            "short_penalty_sum": short_pen,
            "short_excess_sum": short_excess,
            "long_diff_2y_vs_OV": long_diff,
            "mom20_thr": cc.mom20_thr,
            "mom60_thr": cc.mom60_thr,
            "rv20_cap": cc.rv20_cap,
            "dd60_cap": cc.dd60_cap,
            "enter_days": cc.enter_days,
            "exit_days": cc.exit_days,
        }
        for w in WINDOWS:
            rec[f"{w}_ret"] = float(met[w]["return_pct"])
            rec[f"{w}_mdd"] = float(met[w]["max_dd_pct"])
        full_rows.append(rec)
        print(f"[pass6-full] {j}/{len(top)} done")

    full_df = pd.DataFrame(full_rows).sort_values(["gate_ok_2y", "score", "2y_ret"], ascending=[False, False, False]).reset_index(drop=True)
    full_csv = report_dir / "pass6_full_top_validated.csv"
    full_df.to_csv(full_csv, index=False)

    gated = full_df[full_df["gate_ok_2y"]].copy()
    nearest_gate = gated.sort_values(["short_penalty_sum", "2y_ret"], ascending=[True, False]).head(1)
    nearest_gate_rec = nearest_gate.iloc[0].to_dict() if not nearest_gate.empty else None
    strict = None
    for _, r in gated.iterrows():
        if all(float(r[f"{w}_ret"]) >= float(c_metrics[w]["return_pct"]) for w in SHORT_WINDOWS):
            strict = r.to_dict()
            break

    bench_rows = []
    for name, mm in [("C_sp4.7_rv75_tr1.10_th1.20", c_metrics), ("OV_sh6_h1_dd0w20_re1_SOXS", ov_metrics)]:
        br = {"variant": name}
        for w in WINDOWS:
            br[f"{w}_ret"] = float(mm[w]["return_pct"])
            br[f"{w}_mdd"] = float(mm[w]["max_dd_pct"])
        bench_rows.append(br)
    bench_csv = report_dir / "benchmarks_c_vs_ov.csv"
    pd.DataFrame(bench_rows).to_csv(bench_csv, index=False)

    summary = {
        "candidates_proxy_evaluated": int(len(proxy_df)),
        "candidates_full_evaluated": int(len(full_df)),
        "gate_ok_count_full": int(len(gated)),
        "strict_match_candidate": strict,
        "nearest_gate_candidate": nearest_gate_rec,
        "reports": {
            "proxy_csv": str(proxy_csv),
            "full_csv": str(full_csv),
            "benchmarks_csv": str(bench_csv),
        },
    }
    summary_json = report_dir / "search_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
