#!/usr/bin/env python3
"""Research-only overlay sweep to improve short windows without sacrificing medium/long.

No production runtime code is modified. This script composes target/threshold overlays
on top of the fixed control-plane config and backtests across selected windows.
"""

from __future__ import annotations

import csv
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

REPORT_DIR = ROOT / "research" / "reports" / f"short_window_overlay_sweep_{date.today().strftime('%Y%m%d')}"

INVERSE_SYMBOLS = {"SOXS", "SQQQ", "SPXS", "TMV"}


@dataclass(frozen=True)
class BaseFixedCfg:
    cap: float = 0.10
    enter: float = 0.72
    exit: float = 0.64
    enter_days: int = 3
    exit_days: int = 2


@dataclass(frozen=True)
class OverlayCfg:
    name: str
    inverse_cap_weight: float
    ramp_days: int
    sticky_threshold_days: int
    guard_enabled: bool
    guard_rv20_ann: float
    guard_chop_crossovers20: float


def _blend_targets(baseline: dict[str, float], target: dict[str, float], alpha: float) -> dict[str, float]:
    """Blend baseline->target by alpha, then renormalize."""
    alpha = max(0.0, min(1.0, float(alpha)))
    keys = set(baseline.keys()) | set(target.keys())
    out = {k: alpha * float(target.get(k, 0.0)) + (1.0 - alpha) * float(baseline.get(k, 0.0)) for k in keys}
    s = sum(max(0.0, v) for v in out.values())
    if s <= 0:
        return dict(baseline)
    return {k: max(0.0, v) / s for k, v in out.items() if max(0.0, v) > 0.0}


def _compute_soxl_metrics(close_series: list[float], day_idx: int) -> tuple[float, float]:
    """Return (rv20_ann, crossovers20) for SOXL at day index."""
    if day_idx < 19:
        return 0.0, 0.0
    m = rt_v1._compute_regime_metrics(close_series[: day_idx + 1])
    return float(m.rv20_ann), float(m.crossovers20)


def _apply_overlays(
    *,
    aligned_days: list[date],
    close_series_by_symbol: dict[str, list[float]],
    baseline_target_by_day: dict[date, dict[str, float]],
    fixed_targets: dict[date, dict[str, float]],
    fixed_thresholds: dict[date, float],
    fixed_variants: dict[date, str],
    cfg: OverlayCfg,
) -> tuple[dict[date, dict[str, float]], dict[date, float]]:
    """Compose overlays on top of fixed control-plane targets/thresholds."""
    out_targets: dict[date, dict[str, float]] = {}
    out_thresholds: dict[date, float] = {}

    soxl_close = close_series_by_symbol["SOXL"]

    inverse_streak = 0
    for i, d in enumerate(aligned_days):
        base_t = dict(fixed_targets.get(d, {}))
        base_thr = float(fixed_thresholds.get(d, 0.05))
        baseline_t = dict(baseline_target_by_day.get(d, base_t))
        variant = str(fixed_variants.get(d, "baseline"))

        is_inverse = variant.startswith("inverse")
        if is_inverse:
            inverse_streak += 1
        else:
            inverse_streak = 0

        t = dict(base_t)
        thr = float(base_thr)

        # 1) On-ramp inverse entry over first N inverse days.
        if is_inverse and cfg.ramp_days > 0 and inverse_streak <= cfg.ramp_days:
            alpha = float(inverse_streak) / float(cfg.ramp_days)
            t = _blend_targets(baseline_t, t, alpha)

        # 2) Cap total inverse-symbol weight.
        if is_inverse and cfg.inverse_cap_weight < 1.0:
            inv_w = sum(float(t.get(s, 0.0)) for s in INVERSE_SYMBOLS)
            if inv_w > float(cfg.inverse_cap_weight) and inv_w > 1e-12:
                # scale toward baseline to reduce inverse block down to cap.
                alpha = float(cfg.inverse_cap_weight) / float(inv_w)
                t = _blend_targets(baseline_t, t, alpha)

        # 3) Short-horizon guard: high realized vol/chop -> fallback to baseline target.
        force_baseline = False
        if is_inverse and cfg.guard_enabled:
            rv20_ann, chop20 = _compute_soxl_metrics(soxl_close, i)
            if rv20_ann >= float(cfg.guard_rv20_ann) or chop20 >= float(cfg.guard_chop_crossovers20):
                t = dict(baseline_t)
                force_baseline = True

        # 4) Threshold pinning near inverse transitions / guard fallback.
        if force_baseline:
            thr = 0.05
        elif is_inverse and cfg.sticky_threshold_days > 0 and inverse_streak <= cfg.sticky_threshold_days:
            thr = 0.05

        out_targets[d] = t
        out_thresholds[d] = float(thr)

    return out_targets, out_thresholds


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
    targets: dict[date, dict[str, float]],
    thresholds: dict[date, float],
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
            target_by_day=targets,
            rebalance_threshold_by_day=thresholds,
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
            "final": float(r.final_equity),
            "ret": float(r.total_return_pct),
            "dd": float(r.max_drawdown_pct),
        }
    return out


def _profile_to_locked(profile_name: str) -> iv.LockedProfile:
    p = rt_v1.PROFILES[profile_name]
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


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    end_date = date.fromisoformat("2026-03-27")
    windows = ["1m", "2m", "3m", "6m", "1y", "2y"]
    initial_equity = 10_000.0
    rebalance_time_ny = ab._parse_hhmm("15:55")

    profile_name = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
    profile = _profile_to_locked(profile_name)
    fixed = BaseFixedCfg()

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

    # Baseline v1 reference.
    v1_targets, v1_thresholds, _v1_var, _x1, _x2, _x3 = ab._build_switch_targets_and_thresholds(
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
    base_metrics = _simulate_windows(
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

    # Fixed config base (before overlays).
    _a, _b, _c, fixed_targets, fixed_thresholds, fixed_variants = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=float(fixed.cap),
        controlplane_hysteresis_enter=float(fixed.enter),
        controlplane_hysteresis_exit=float(fixed.exit),
        controlplane_hysteresis_enter_days=int(fixed.enter_days),
        controlplane_hysteresis_exit_days=int(fixed.exit_days),
    )

    # Candidate overlays (research-only).
    candidates = [
        OverlayCfg("OVL01", inverse_cap_weight=0.75, ramp_days=3, sticky_threshold_days=3, guard_enabled=False, guard_rv20_ann=0.0, guard_chop_crossovers20=0.0),
        OverlayCfg("OVL02", inverse_cap_weight=0.60, ramp_days=3, sticky_threshold_days=5, guard_enabled=False, guard_rv20_ann=0.0, guard_chop_crossovers20=0.0),
        OverlayCfg("OVL03", inverse_cap_weight=0.75, ramp_days=3, sticky_threshold_days=3, guard_enabled=True, guard_rv20_ann=0.90, guard_chop_crossovers20=8.0),
        OverlayCfg("OVL04", inverse_cap_weight=0.60, ramp_days=4, sticky_threshold_days=5, guard_enabled=True, guard_rv20_ann=0.80, guard_chop_crossovers20=7.0),
        OverlayCfg("OVL05", inverse_cap_weight=0.50, ramp_days=4, sticky_threshold_days=5, guard_enabled=True, guard_rv20_ann=0.75, guard_chop_crossovers20=7.0),
        OverlayCfg("OVL06", inverse_cap_weight=0.70, ramp_days=2, sticky_threshold_days=2, guard_enabled=True, guard_rv20_ann=1.00, guard_chop_crossovers20=9.0),
        OverlayCfg("OVL07", inverse_cap_weight=0.65, ramp_days=3, sticky_threshold_days=4, guard_enabled=True, guard_rv20_ann=0.85, guard_chop_crossovers20=8.0),
        OverlayCfg("OVL08", inverse_cap_weight=0.55, ramp_days=2, sticky_threshold_days=4, guard_enabled=True, guard_rv20_ann=0.80, guard_chop_crossovers20=8.0),
        OverlayCfg("OVL09", inverse_cap_weight=0.80, ramp_days=4, sticky_threshold_days=6, guard_enabled=False, guard_rv20_ann=0.0, guard_chop_crossovers20=0.0),
        OverlayCfg("OVL10", inverse_cap_weight=0.50, ramp_days=5, sticky_threshold_days=7, guard_enabled=True, guard_rv20_ann=0.75, guard_chop_crossovers20=6.0),
    ]

    rows: list[dict[str, Any]] = []
    for cfg in candidates:
        targets, thresholds = _apply_overlays(
            aligned_days=aligned_days,
            close_series_by_symbol=close_series,
            baseline_target_by_day=baseline_target,
            fixed_targets=fixed_targets,
            fixed_thresholds=fixed_thresholds,
            fixed_variants=fixed_variants,
            cfg=cfg,
        )

        m = _simulate_windows(
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
            targets=targets,
            thresholds=thresholds,
            rebalance_time_ny=rebalance_time_ny,
        )

        # Acceptance criteria for this research pass.
        short_dd_ok = (m["1m"]["dd"] <= base_metrics["1m"]["dd"]) and (m["2m"]["dd"] <= base_metrics["2m"]["dd"])
        medlong_ret_ok = (
            (m["3m"]["ret"] >= base_metrics["3m"]["ret"])
            and (m["6m"]["ret"] >= base_metrics["6m"]["ret"])
            and (m["1y"]["ret"] >= base_metrics["1y"]["ret"])
        )
        both_ok = bool(short_dd_ok and medlong_ret_ok)

        combo_score = (
            2.0 * (m["1m"]["ret"] - base_metrics["1m"]["ret"])
            + 2.0 * (m["2m"]["ret"] - base_metrics["2m"]["ret"])
            + 1.2 * (m["3m"]["ret"] - base_metrics["3m"]["ret"])
            + 1.0 * (m["6m"]["ret"] - base_metrics["6m"]["ret"])
            + 0.8 * (m["1y"]["ret"] - base_metrics["1y"]["ret"])
            - 2.5 * max(0.0, m["1m"]["dd"] - base_metrics["1m"]["dd"])
            - 2.5 * max(0.0, m["2m"]["dd"] - base_metrics["2m"]["dd"])
            - 1.0 * max(0.0, m["3m"]["dd"] - base_metrics["3m"]["dd"])
        )

        row: dict[str, Any] = {
            "name": cfg.name,
            "inverse_cap_weight": cfg.inverse_cap_weight,
            "ramp_days": cfg.ramp_days,
            "sticky_threshold_days": cfg.sticky_threshold_days,
            "guard_enabled": cfg.guard_enabled,
            "guard_rv20_ann": cfg.guard_rv20_ann,
            "guard_chop_crossovers20": cfg.guard_chop_crossovers20,
            "short_dd_ok": short_dd_ok,
            "medlong_ret_ok": medlong_ret_ok,
            "both_ok": both_ok,
            "combo_score": float(combo_score),
        }
        for w in windows:
            row[f"base_{w}_ret"] = float(base_metrics[w]["ret"])
            row[f"base_{w}_dd"] = float(base_metrics[w]["dd"])
            row[f"cfg_{w}_ret"] = float(m[w]["ret"])
            row[f"cfg_{w}_dd"] = float(m[w]["dd"])
        rows.append(row)

    rows_sorted = sorted(rows, key=lambda r: (int(r["both_ok"]), r["combo_score"]), reverse=True)

    csv_path = REPORT_DIR / "overlay_sweep_results.csv"
    json_path = REPORT_DIR / "overlay_sweep_results.json"

    if rows_sorted:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_sorted[0].keys()))
            w.writeheader()
            w.writerows(rows_sorted)

    payload = {
        "report_dir": str(REPORT_DIR),
        "base_metrics": base_metrics,
        "count": len(rows_sorted),
        "top10": rows_sorted[:10],
        "csv": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
