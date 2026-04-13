#!/usr/bin/env python3
"""Research-only constrained sweep for short+long drawdown control.

Goal:
1) Improve short windows (1m/2m) drawdown versus baseline.
2) Keep medium/long windows (3m/6m/1y) drawdown controlled.
3) Preserve or improve 6m/1y returns versus baseline.

Important:
- This script does NOT modify production/runtime code.
- All outputs are written under research/reports/.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from multiprocessing import get_context
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research.short_window_overlay_sweep as sw
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as ab
from composer_original.tools import intraday_profit_lock_verification as iv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


REPORT_DIR = ROOT / "research" / "reports" / f"short_long_dd_constrained_{date.today().strftime('%Y%m%d')}"
INVERSE_SYMBOLS = {"SOXS", "SQQQ", "SPXS", "TMV"}
AGGRESSIVE_SYMBOLS = {"SOXL", "TQQQ", "SPXL", "TECL", "FNGU"}
DEFENSIVE_PREFERRED = ("TMF", "TLT", "IEF", "SHY", "BIL")

# Global state for forked workers.
_G: dict[str, Any] = {}


@dataclass(frozen=True)
class Candidate:
    name: str
    # OVL07-family controls.
    inverse_cap_weight: float
    ramp_days: int
    sticky_threshold_days: int
    guard_enabled: bool
    guard_rv20_ann: float
    guard_chop_crossovers20: float
    # Short-window specific controls.
    recent_inverse_cap_weight: float
    recent_days: int
    shock_down_pct: float
    shock_hold_days: int
    fixed_enter_days: int
    recent_primary_cap_weight: float
    recent_defensive_blend: float
    defensive_trigger_down_pct: float
    recent_aggressive_total_cap: float
    recent_always_defensive_blend: float


def _serialize_candidate(c: Candidate) -> dict[str, Any]:
    return asdict(c)


def _compute_prev_day_returns(aligned_days: list[date], close_series: list[float]) -> dict[date, float]:
    """Return map of day -> previous-trading-day close return (% as decimal)."""
    out: dict[date, float] = {}
    for i, d in enumerate(aligned_days):
        if i == 0:
            out[d] = 0.0
            continue
        p0 = float(close_series[i - 1])
        p1 = float(close_series[i])
        if p0 <= 0.0:
            out[d] = 0.0
        else:
            out[d] = (p1 / p0) - 1.0
    return out


def _apply_extended_overlays(
    *,
    aligned_days: list[date],
    close_series_by_symbol: dict[str, list[float]],
    baseline_target_by_day: dict[date, dict[str, float]],
    fixed_targets: dict[date, dict[str, float]],
    fixed_thresholds: dict[date, float],
    fixed_variants: dict[date, str],
    end_date: date,
    c: Candidate,
) -> tuple[dict[date, dict[str, float]], dict[date, float], dict[str, int]]:
    """Apply OVL07-like overlay + short-window protections.

    Protections:
    - Recent window inverse cap.
    - Shock brake (if prior SOXL close drop breaches threshold, fallback baseline for N days).
    """
    base_cfg = sw.OverlayCfg(
        name=c.name,
        inverse_cap_weight=float(c.inverse_cap_weight),
        ramp_days=int(c.ramp_days),
        sticky_threshold_days=int(c.sticky_threshold_days),
        guard_enabled=bool(c.guard_enabled),
        guard_rv20_ann=float(c.guard_rv20_ann),
        guard_chop_crossovers20=float(c.guard_chop_crossovers20),
    )
    targets, thresholds = sw._apply_overlays(
        aligned_days=aligned_days,
        close_series_by_symbol=close_series_by_symbol,
        baseline_target_by_day=baseline_target_by_day,
        fixed_targets=fixed_targets,
        fixed_thresholds=fixed_thresholds,
        fixed_variants=fixed_variants,
        cfg=base_cfg,
    )
    targets = {d: dict(t) for d, t in targets.items()}
    thresholds = {d: float(v) for d, v in thresholds.items()}

    changed_recent_cap = 0
    changed_primary_cap = 0
    changed_defensive_blend = 0
    changed_total_aggr_cap = 0
    changed_always_def_blend = 0
    changed_shock = 0

    soxl_prev_ret = _compute_prev_day_returns(aligned_days, close_series_by_symbol["SOXL"])

    # 1) Recent-window inverse cap.
    recent_start = end_date - timedelta(days=int(c.recent_days))
    for d in aligned_days:
        if d < recent_start or d > end_date:
            continue
        variant = str(fixed_variants.get(d, "baseline"))
        if not variant.startswith("inverse"):
            continue
        t_cur = dict(targets.get(d, {}))
        inv_w = sum(float(t_cur.get(s, 0.0)) for s in INVERSE_SYMBOLS)
        cap = float(c.recent_inverse_cap_weight)
        if inv_w > cap and inv_w > 1e-12:
            alpha = cap / inv_w
            t_new = sw._blend_targets(baseline_target_by_day.get(d, t_cur), t_cur, alpha)
            targets[d] = t_new
            changed_recent_cap += 1

    # 1b) Recent aggressive-symbol cap to reduce short-window DD bursts.
    for d in aligned_days:
        if d < recent_start or d > end_date:
            continue
        t_cur = dict(targets.get(d, {}))
        if not t_cur:
            continue
        # 1b-i) Cap total aggressive exposure in recent window.
        aggr_total = sum(float(t_cur.get(s, 0.0)) for s in AGGRESSIVE_SYMBOLS)
        total_cap = float(c.recent_aggressive_total_cap)
        if aggr_total > total_cap and aggr_total > 1e-12:
            scale = total_cap / aggr_total
            released = 0.0
            for s in AGGRESSIVE_SYMBOLS:
                w = float(t_cur.get(s, 0.0))
                if w <= 0.0:
                    continue
                w_new = w * scale
                released += (w - w_new)
                t_cur[s] = w_new
            t_cur["TMF"] = float(t_cur.get("TMF", 0.0)) + released
            changed_total_aggr_cap += 1

        # Find dominant aggressive symbol on this day.
        aggr = [(sym, float(t_cur.get(sym, 0.0))) for sym in AGGRESSIVE_SYMBOLS if float(t_cur.get(sym, 0.0)) > 0.0]
        if not aggr:
            # still allow always-on defensive blend
            always_blend = max(0.0, min(0.9, float(c.recent_always_defensive_blend)))
            if always_blend > 0.0:
                for k in list(t_cur.keys()):
                    t_cur[k] = float(t_cur[k]) * (1.0 - always_blend)
                t_cur["TMF"] = float(t_cur.get("TMF", 0.0)) + always_blend
                changed_always_def_blend += 1
                ssum = sum(max(0.0, float(v)) for v in t_cur.values())
                if ssum > 0.0:
                    t_cur = {k: max(0.0, float(v)) / ssum for k, v in t_cur.items() if max(0.0, float(v)) > 0.0}
                targets[d] = t_cur
            continue
        sym_max, w_max = max(aggr, key=lambda x: x[1])
        cap = float(c.recent_primary_cap_weight)
        if w_max <= cap:
            continue
        excess = w_max - cap
        t_cur[sym_max] = cap

        # Route excess to preferred defensive symbol if present, else to largest non-inverse symbol.
        routed = False
        for dsym in DEFENSIVE_PREFERRED:
            if dsym in t_cur:
                t_cur[dsym] = float(t_cur.get(dsym, 0.0)) + excess
                routed = True
                break
        if not routed:
            non_inv = [(s, float(w)) for s, w in t_cur.items() if s not in INVERSE_SYMBOLS and s != sym_max and float(w) > 0.0]
            if non_inv:
                dsym = max(non_inv, key=lambda x: x[1])[0]
                t_cur[dsym] = float(t_cur.get(dsym, 0.0)) + excess
            else:
                # Fall back to baseline mix if no safe routing exists.
                t_cur = dict(baseline_target_by_day.get(d, t_cur))

        # Normalize after routing.
        ssum = sum(max(0.0, float(v)) for v in t_cur.values())
        if ssum > 0.0:
            t_cur = {k: max(0.0, float(v)) / ssum for k, v in t_cur.items() if max(0.0, float(v)) > 0.0}
        # 1b-ii) Always-on defensive blend in recent window.
        always_blend = max(0.0, min(0.9, float(c.recent_always_defensive_blend)))
        if always_blend > 0.0:
            for k in list(t_cur.keys()):
                t_cur[k] = float(t_cur[k]) * (1.0 - always_blend)
            t_cur["TMF"] = float(t_cur.get("TMF", 0.0)) + always_blend
            changed_always_def_blend += 1
            ssum = sum(max(0.0, float(v)) for v in t_cur.values())
            if ssum > 0.0:
                t_cur = {k: max(0.0, float(v)) / ssum for k, v in t_cur.items() if max(0.0, float(v)) > 0.0}
        targets[d] = t_cur
        changed_primary_cap += 1

    # 1c) Recent downside defensive blend: shift part of exposure to TMF on downside days.
    for d in aligned_days:
        if d < recent_start or d > end_date:
            continue
        if soxl_prev_ret.get(d, 0.0) > float(c.defensive_trigger_down_pct):
            continue
        blend = max(0.0, min(1.0, float(c.recent_defensive_blend)))
        if blend <= 0.0:
            continue
        t_cur = dict(targets.get(d, {}))
        # Scale existing weights down then route blend weight to TMF.
        for k in list(t_cur.keys()):
            t_cur[k] = float(t_cur[k]) * (1.0 - blend)
        t_cur["TMF"] = float(t_cur.get("TMF", 0.0)) + blend
        ssum = sum(max(0.0, float(v)) for v in t_cur.values())
        if ssum > 0.0:
            t_cur = {k: max(0.0, float(v)) / ssum for k, v in t_cur.items() if max(0.0, float(v)) > 0.0}
        targets[d] = t_cur
        changed_defensive_blend += 1

    # 2) Shock brake based on prior-trading-day SOXL close return.
    soxl_close = close_series_by_symbol["SOXL"]
    prev_ret = _compute_prev_day_returns(aligned_days, soxl_close)
    day_to_idx = {d: i for i, d in enumerate(aligned_days)}
    forced_days: set[date] = set()
    for d in aligned_days:
        if d > end_date:
            continue
        if prev_ret.get(d, 0.0) <= float(c.shock_down_pct):
            i = day_to_idx[d]
            for k in range(int(c.shock_hold_days)):
                j = i + k
                if j < len(aligned_days):
                    dj = aligned_days[j]
                    if dj <= end_date:
                        forced_days.add(dj)
    for d in forced_days:
        if d in baseline_target_by_day:
            targets[d] = dict(baseline_target_by_day[d])
            thresholds[d] = 0.05
            changed_shock += 1

    return targets, thresholds, {
        "changed_recent_cap_days": changed_recent_cap,
        "changed_primary_cap_days": changed_primary_cap,
        "changed_defensive_blend_days": changed_defensive_blend,
        "changed_total_aggr_cap_days": changed_total_aggr_cap,
        "changed_always_def_blend_days": changed_always_def_blend,
        "changed_shock_days": changed_shock,
    }


def _simulate_candidate(c: Candidate) -> dict[str, Any]:
    """Worker entrypoint: evaluate one candidate against baseline."""
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

    # Build fixed control-plane targets with candidate-specific enter-days.
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
    targets, thresholds, overlay_stats = _apply_extended_overlays(
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

    # Strict acceptance gates for short+long DD/return.
    short_dd_ok = (m["1m"]["dd"] < base_metrics["1m"]["dd"]) and (m["2m"]["dd"] < base_metrics["2m"]["dd"])
    med_dd_ok = m["3m"]["dd"] <= base_metrics["3m"]["dd"]
    long_dd_ok = (m["6m"]["dd"] <= base_metrics["6m"]["dd"]) and (m["1y"]["dd"] <= base_metrics["1y"]["dd"])
    long_ret_ok = (m["6m"]["ret"] >= base_metrics["6m"]["ret"]) and (m["1y"]["ret"] >= base_metrics["1y"]["ret"])
    pass_all = bool(short_dd_ok and med_dd_ok and long_dd_ok and long_ret_ok)

    # Score prioritizes passing gates, then large DD reductions and return preservation.
    score = (
        500.0 * int(pass_all)
        + 160.0 * (base_metrics["1m"]["dd"] - m["1m"]["dd"])
        + 160.0 * (base_metrics["2m"]["dd"] - m["2m"]["dd"])
        + 80.0 * (base_metrics["3m"]["dd"] - m["3m"]["dd"])
        + 60.0 * (base_metrics["6m"]["dd"] - m["6m"]["dd"])
        + 60.0 * (base_metrics["1y"]["dd"] - m["1y"]["dd"])
        + 20.0 * (m["6m"]["ret"] - base_metrics["6m"]["ret"])
        + 15.0 * (m["1y"]["ret"] - base_metrics["1y"]["ret"])
    )

    row: dict[str, Any] = {
        **_serialize_candidate(c),
        **overlay_stats,
        "short_dd_ok": short_dd_ok,
        "med_dd_ok": med_dd_ok,
        "long_dd_ok": long_dd_ok,
        "long_ret_ok": long_ret_ok,
        "pass_all": pass_all,
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


def _build_candidates(max_candidates: int | None = None) -> list[Candidate]:
    candidates: list[Candidate] = []
    i = 1
    # Targeted grid: prioritize short-window DD fixes while preserving long-window gains.
    for recent_cap in (0.25, 0.35, 0.45):
        for recent_days in (20, 30, 45):
            for aggr_total_cap in (0.35, 0.50, 0.65):
                for always_def_blend in (0.0, 0.10, 0.20):
                    for primary_cap in (0.35, 0.45, 0.55):
                        for defensive_blend in (0.15, 0.30):
                            for defensive_trigger in (-0.008, -0.01, -0.015):
                                for shock_down in (-0.05, -0.06):
                                    for shock_hold in (1,):
                                        for enter_days in (3, 4, 5):
                                            candidates.append(
                                                Candidate(
                                                    name=f"C{i:03d}",
                                                    inverse_cap_weight=0.65,
                                                    ramp_days=3,
                                                    sticky_threshold_days=4,
                                                    guard_enabled=True,
                                                    guard_rv20_ann=0.85,
                                                    guard_chop_crossovers20=8.0,
                                                    recent_inverse_cap_weight=float(recent_cap),
                                                    recent_days=int(recent_days),
                                                    shock_down_pct=float(shock_down),
                                                    shock_hold_days=int(shock_hold),
                                                    fixed_enter_days=int(enter_days),
                                                    recent_primary_cap_weight=float(primary_cap),
                                                    recent_defensive_blend=float(defensive_blend),
                                                    defensive_trigger_down_pct=float(defensive_trigger),
                                                    recent_aggressive_total_cap=float(aggr_total_cap),
                                                    recent_always_defensive_blend=float(always_def_blend),
                                                )
                                            )
                                            i += 1
    if max_candidates is not None and max_candidates > 0:
        return candidates[: int(max_candidates)]
    return candidates


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Runtime controls.
    end_date = date.fromisoformat(os.environ.get("RESEARCH_END_DATE", "2026-03-27"))
    windows = ["1m", "2m", "3m", "4m", "5m", "6m", "1y"]
    initial_equity = 10_000.0
    rebalance_time_ny = ab._parse_hhmm("15:55")
    max_candidates_env = os.environ.get("RESEARCH_MAX_CANDIDATES", "").strip()
    max_candidates = int(max_candidates_env) if max_candidates_env else None

    profile_name = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
    profile = sw._profile_to_locked(profile_name)

    # Load Alpaca SIP data once.
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

    # Baseline (v1 reference).
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

    # Prepare worker globals.
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

    candidates = _build_candidates(max_candidates=max_candidates)
    workers = int(os.environ.get("RESEARCH_WORKERS", str(max(1, os.cpu_count() or 1))))

    # Evaluate in parallel using fork to reuse loaded data.
    with get_context("fork").Pool(processes=workers) as pool:
        rows = list(pool.map(_simulate_candidate, candidates))

    rows_sorted = sorted(rows, key=lambda r: (int(r["pass_all"]), r["score"]), reverse=True)
    pass_rows = [r for r in rows_sorted if r["pass_all"]]

    csv_path = REPORT_DIR / "short_long_dd_constrained_results.csv"
    json_path = REPORT_DIR / "short_long_dd_constrained_results.json"
    summary_path = REPORT_DIR / "short_long_dd_constrained_summary.json"

    if rows_sorted:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_sorted[0].keys()))
            w.writeheader()
            w.writerows(rows_sorted)

    summary = {
        "report_dir": str(REPORT_DIR),
        "end_date": end_date.isoformat(),
        "profile": profile_name,
        "windows": windows,
        "initial_equity": initial_equity,
        "workers": workers,
        "candidates": len(candidates),
        "pass_count": len(pass_rows),
        "base_metrics": base_metrics,
        "top5_overall": rows_sorted[:5],
        "top5_pass": pass_rows[:5],
        "csv": str(csv_path),
    }
    json_path.write_text(json.dumps(rows_sorted, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
