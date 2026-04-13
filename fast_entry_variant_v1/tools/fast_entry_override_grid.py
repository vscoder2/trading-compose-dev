#!/usr/bin/env python3
from __future__ import annotations

"""Standalone fast-entry override research runner.

Design constraints:
- New code path only; does not modify existing runtime implementation.
- Reuses existing data loaders + intraday simulator for parity-quality comparison.
- Adds a *single-day fast-entry override* on top of v2 control-plane logic.

Fast-entry intent:
If control-plane is still risk_off but has first risk_on confirmation, and market
is strongly bullish while base target is inverse-heavy, promote to inverse_ma20
immediately (one day earlier) to avoid delayed inverse exposure.
"""

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.runtime_switch_loop_v2_controlplane as rt_v2
from composer_original.tools import intraday_profit_lock_verification as iv
from improvements2_impl.src.regime_policy import (
    ConfidenceInputs,
    HysteresisConfig,
    HysteresisState,
    compute_adaptive_rebalance_threshold,
    compute_regime_confidence,
    step_hysteresis_state,
)
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools.historical_runtime_v1_v2_ab import (
    WINDOW_TO_DAYS,
    _simulate_intraday,
    _window_range,
)


INVERSE_SET = {"SOXS", "SQQQ", "SPXS", "TMV"}


@dataclass(frozen=True)
class FastEntryCandidate:
    cid: str
    fast_signal_threshold: float
    fast_trend_gap_pct: float
    fast_inverse_min_weight: float


def _load_env_file(path: str, *, override: bool) -> int:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {p}")
    loaded = 0
    for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        if (not override) and (key in os.environ):
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _parse_hhmm(value: str) -> dt_time:
    raw = str(value or "").strip()
    hh, mm = raw.split(":", 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f"Invalid HH:MM: {value!r}")
    return dt_time(h, m)


def _csv_floats(text: str) -> list[float]:
    out: list[float] = []
    for part in str(text).split(","):
        p = part.strip()
        if not p:
            continue
        out.append(float(p))
    if not out:
        raise ValueError(f"No numeric values parsed from: {text!r}")
    return out


def _build_candidates(
    signal_thresholds: list[float],
    trend_gap_pcts: list[float],
    inverse_min_weights: list[float],
) -> list[FastEntryCandidate]:
    cands: list[FastEntryCandidate] = []
    idx = 1
    for sig in signal_thresholds:
        for gap in trend_gap_pcts:
            for inv_w in inverse_min_weights:
                cands.append(
                    FastEntryCandidate(
                        cid=f"FEV1-{idx:04d}",
                        fast_signal_threshold=float(sig),
                        fast_trend_gap_pct=float(gap),
                        fast_inverse_min_weight=float(inv_w),
                    )
                )
                idx += 1
    return cands


def _inverse_weight(target: dict[str, float]) -> float:
    return float(sum(float(w) for s, w in target.items() if str(s).upper() in INVERSE_SET))


def _trend_gap_pct(metrics: rt_v1.RegimeMetrics) -> float:
    ma20 = float(metrics.ma20) if metrics.ma20 is not None else float(metrics.close)
    if ma20 <= 0.0:
        return 0.0
    return 100.0 * (float(metrics.close) / ma20 - 1.0)


def _build_targets_v2_and_fast(
    *,
    aligned_days: list[date],
    symbols: list[str],
    close_series: dict[str, list[float]],
    baseline_target_by_day: dict[date, dict[str, float]],
    base_rebalance_threshold: float,
    controlplane_threshold_cap: float,
    controlplane_hysteresis_enter: float,
    controlplane_hysteresis_exit: float,
    controlplane_hysteresis_enter_days: int,
    controlplane_hysteresis_exit_days: int,
    fast: FastEntryCandidate,
) -> tuple[
    dict[date, dict[str, float]],
    dict[date, float],
    dict[date, str],
    dict[date, str],
    dict[date, dict[str, float]],
    dict[date, float],
    dict[date, str],
    dict[date, str],
]:
    """Build per-day targets for baseline v2 and fast-entry variant.

    Returns:
    - v2 targets/thresholds/variant/reason
    - fast targets/thresholds/variant/reason
    """
    v2_targets: dict[date, dict[str, float]] = {}
    v2_thresholds: dict[date, float] = {}
    v2_variants: dict[date, str] = {}
    v2_reasons: dict[date, str] = {}

    fast_targets: dict[date, dict[str, float]] = {}
    fast_thresholds: dict[date, float] = {}
    fast_variants: dict[date, str] = {}
    fast_reasons: dict[date, str] = {}

    # Separate state streams: baseline-v2 and fast-variant can evolve differently.
    v2_state = rt_v1.RegimeState()
    fast_state = rt_v1.RegimeState()

    h_cfg = HysteresisConfig(
        enter_threshold=float(controlplane_hysteresis_enter),
        exit_threshold=float(controlplane_hysteresis_exit),
        min_enter_days=int(controlplane_hysteresis_enter_days),
        min_exit_days=int(controlplane_hysteresis_exit_days),
    )
    h_v2 = HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)
    h_fast = HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)

    for idx, d in enumerate(aligned_days):
        base_target = dict(baseline_target_by_day.get(d, {}))
        if not base_target:
            v2_targets[d] = {}
            v2_thresholds[d] = float(base_rebalance_threshold)
            v2_variants[d] = "baseline"
            v2_reasons[d] = "no_target"

            fast_targets[d] = {}
            fast_thresholds[d] = float(base_rebalance_threshold)
            fast_variants[d] = "baseline"
            fast_reasons[d] = "no_target"
            continue

        hist_by_symbol = {s: list(close_series.get(s, [])[: idx + 1]) for s in symbols}
        soxl_hist = hist_by_symbol.get("SOXL", [])

        # default values if history is short
        v2_variant = "baseline"
        v2_reason = "insufficient_history"
        v2_threshold = float(base_rebalance_threshold)

        fast_variant = "baseline"
        fast_reason = "insufficient_history"
        fast_threshold = float(base_rebalance_threshold)

        if len(soxl_hist) >= 20:
            metrics_v2 = rt_v1._compute_regime_metrics(soxl_hist)
            metrics_fast = metrics_v2

            # 1) Base variant from v1 regime engine.
            v2_variant, v2_reason = rt_v1._choose_variant(metrics_v2, v2_state)
            fast_variant, fast_reason = rt_v1._choose_variant(metrics_fast, fast_state)

            # 2) Hysteresis step.
            signal_v2 = float(rt_v2._regime_signal_01(metrics_v2))
            signal_fast = float(rt_v2._regime_signal_01(metrics_fast))
            h_v2 = step_hysteresis_state(prior=h_v2, signal=signal_v2, cfg=h_cfg)
            h_fast = step_hysteresis_state(prior=h_fast, signal=signal_fast, cfg=h_cfg)

            # 3) Baseline v2 cp override.
            if h_v2.regime == "risk_off" and v2_variant != "baseline":
                v2_variant = "baseline"
                v2_reason = "cp_risk_off_forces_baseline"
            elif h_v2.regime == "risk_on" and v2_variant == "baseline":
                v2_variant = "inverse_ma20"
                v2_reason = "cp_risk_on_promotes_inverse_ma20"

            # 4) Fast-entry variant override.
            if h_fast.regime == "risk_off" and fast_variant != "baseline":
                fast_variant = "baseline"
                fast_reason = "cp_risk_off_forces_baseline"
            elif h_fast.regime == "risk_on" and fast_variant == "baseline":
                fast_variant = "inverse_ma20"
                fast_reason = "cp_risk_on_promotes_inverse_ma20"
            else:
                # Additional one-day-early promotion gate.
                trend_gap = _trend_gap_pct(metrics_fast)
                inv_wt = _inverse_weight(base_target)
                # NOTE:
                # enter_streak==1 means first day above enter-threshold while still risk_off.
                # This is the "likely one-day-late" pattern we are targeting.
                if (
                    h_fast.regime == "risk_off"
                    and h_fast.enter_streak == 1
                    and fast_variant == "baseline"
                    and signal_fast >= float(fast.fast_signal_threshold)
                    and trend_gap >= float(fast.fast_trend_gap_pct)
                    and inv_wt >= float(fast.fast_inverse_min_weight)
                ):
                    fast_variant = "inverse_ma20"
                    fast_reason = "fast_entry_override_promote_inverse_ma20"

            # 5) Adaptive threshold (same base mechanism for both variants).
            conf_inputs_v2 = ConfidenceInputs(
                trend_strength=float(rt_v2._trend_strength_from_metrics(metrics_v2)),
                realized_vol_ann=float(metrics_v2.rv20_ann),
                chop_score=float(metrics_v2.crossovers20),
                data_fresh=True,
            )
            conf_v2, _ = compute_regime_confidence(conf_inputs_v2)
            v2_threshold = float(
                compute_adaptive_rebalance_threshold(
                    base_threshold_pct=float(base_rebalance_threshold),
                    realized_vol_ann=float(metrics_v2.rv20_ann),
                    chop_score=float(metrics_v2.crossovers20),
                    confidence_score=float(conf_v2),
                    min_threshold_pct=float(base_rebalance_threshold),
                    max_threshold_pct=float(controlplane_threshold_cap),
                )
            )

            conf_inputs_fast = ConfidenceInputs(
                trend_strength=float(rt_v2._trend_strength_from_metrics(metrics_fast)),
                realized_vol_ann=float(metrics_fast.rv20_ann),
                chop_score=float(metrics_fast.crossovers20),
                data_fresh=True,
            )
            conf_fast, _ = compute_regime_confidence(conf_inputs_fast)
            fast_threshold = float(
                compute_adaptive_rebalance_threshold(
                    base_threshold_pct=float(base_rebalance_threshold),
                    realized_vol_ann=float(metrics_fast.rv20_ann),
                    chop_score=float(metrics_fast.crossovers20),
                    confidence_score=float(conf_fast),
                    min_threshold_pct=float(base_rebalance_threshold),
                    max_threshold_pct=float(controlplane_threshold_cap),
                )
            )

        # 6) Apply inverse-blocker overlay depending on chosen variant.
        tgt_v2, _note_v2 = rt_v1._apply_variant_to_target(base_target, hist_by_symbol, v2_variant)
        tgt_fast, _note_fast = rt_v1._apply_variant_to_target(base_target, hist_by_symbol, fast_variant)

        v2_targets[d] = dict(tgt_v2)
        v2_thresholds[d] = float(v2_threshold)
        v2_variants[d] = str(v2_variant)
        v2_reasons[d] = str(v2_reason)

        fast_targets[d] = dict(tgt_fast)
        fast_thresholds[d] = float(fast_threshold)
        fast_variants[d] = str(fast_variant)
        fast_reasons[d] = str(fast_reason)

    return (
        v2_targets,
        v2_thresholds,
        v2_variants,
        v2_reasons,
        fast_targets,
        fast_thresholds,
        fast_variants,
        fast_reasons,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Standalone fast-entry override grid search (new variant path, no edits to existing runtime code)."
        )
    )
    p.add_argument("--env-file", default="", help="Optional .env file for Alpaca keys.")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--windows", default="10d,1m,2m,3m,4m,5m,6m,1y")
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

    # Fast-entry override grid.
    p.add_argument("--fast-signal-thresholds", default="0.66,0.68,0.70")
    p.add_argument("--fast-trend-gap-pcts", default="6,8,10")
    p.add_argument("--fast-inverse-min-weights", default="0.80,0.90")

    p.add_argument("--reports-dir", default=str(ROOT / "fast_entry_variant_v1" / "reports"))
    p.add_argument("--output-prefix", default="fast_entry_override_grid")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(f"Unknown strategy profile: {args.strategy_profile}")

    # 10d support in this standalone script.
    window_to_days = dict(WINDOW_TO_DAYS)
    window_to_days["10d"] = 10
    windows = [w.strip() for w in str(args.windows).split(",") if w.strip()]
    for w in windows:
        if w not in window_to_days:
            raise ValueError(f"Unsupported window: {w}")

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

    max_days = max(int(window_to_days[w]) for w in windows)
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
        for d, _close_px, high_px in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(high_px)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    # Baseline v2 targets (for direct comparison against each fast candidate).
    baseline_fast = FastEntryCandidate(
        cid="V2-BASE",
        fast_signal_threshold=999.0,
        fast_trend_gap_pct=999.0,
        fast_inverse_min_weight=1.1,
    )
    (
        v2_targets,
        v2_thresholds,
        v2_variants,
        v2_reasons,
        _ignore_tgt,
        _ignore_thr,
        _ignore_var,
        _ignore_reason,
    ) = _build_targets_v2_and_fast(
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
        fast=baseline_fast,
    )

    # Build candidate grid.
    candidates = _build_candidates(
        signal_thresholds=_csv_floats(args.fast_signal_thresholds),
        trend_gap_pcts=_csv_floats(args.fast_trend_gap_pcts),
        inverse_min_weights=_csv_floats(args.fast_inverse_min_weights),
    )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.end_date.strftime("%Y%m%d")

    all_rows: list[dict[str, Any]] = []

    # Precompute v2 baseline results once per window for fair delta columns.
    v2_by_window: dict[str, Any] = {}
    for w in windows:
        if w == "10d":
            start_day = args.end_date - timedelta(days=10)
            end_day = args.end_date
        else:
            start_day, end_day = _window_range(args.end_date, w)
        v2_res = _simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=v2_targets,
            rebalance_threshold_by_day=v2_thresholds,
            profile=profile,
            start_day=start_day,
            end_day=end_day,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
            runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        )
        v2_by_window[w] = {
            "start": start_day,
            "end": end_day,
            "res": v2_res,
        }

    for cand in candidates:
        (
            _v2_targets_x,
            _v2_thresholds_x,
            _v2_variants_x,
            _v2_reasons_x,
            fast_targets,
            fast_thresholds,
            fast_variants,
            fast_reasons,
        ) = _build_targets_v2_and_fast(
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
            fast=cand,
        )

        for w in windows:
            baseline = v2_by_window[w]
            start_day = baseline["start"]
            end_day = baseline["end"]
            v2_res = baseline["res"]

            fast_res = _simulate_intraday(
                symbols=symbols,
                aligned_days=aligned_days,
                price_history=price_history,
                close_map_by_symbol=close_map_by_symbol,
                high_map_by_symbol=high_map_by_symbol,
                minute_by_day_symbol=minute_by_day_symbol,
                target_by_day=fast_targets,
                rebalance_threshold_by_day=fast_thresholds,
                profile=profile,
                start_day=start_day,
                end_day=end_day,
                initial_equity=float(args.initial_equity),
                slippage_bps=float(args.slippage_bps),
                sell_fee_bps=float(args.sell_fee_bps),
                runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
                runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
                rebalance_time_ny=rebalance_time_ny,
                split_ratio_by_day_symbol=split_ratio_by_day_symbol,
            )

            # Count days where fast-entry override specifically triggered.
            days_fast_override = int(sum(1 for d in aligned_days if fast_reasons.get(d) == "fast_entry_override_promote_inverse_ma20" and start_day <= d <= end_day))
            fast_regimes_seen = len({fast_variants[d] for d in aligned_days if start_day <= d <= end_day and d in fast_variants})
            v2_regimes_seen = len({v2_variants[d] for d in aligned_days if start_day <= d <= end_day and d in v2_variants})

            all_rows.append(
                {
                    "cid": cand.cid,
                    "window": w,
                    "period": f"{start_day.isoformat()} to {end_day.isoformat()}",
                    "initial_equity": float(args.initial_equity),
                    "fast_signal_threshold": cand.fast_signal_threshold,
                    "fast_trend_gap_pct": cand.fast_trend_gap_pct,
                    "fast_inverse_min_weight": cand.fast_inverse_min_weight,
                    "fast_override_days": days_fast_override,
                    "v2_final_equity": float(v2_res.final_equity),
                    "v2_return_pct": float(v2_res.total_return_pct),
                    "v2_maxdd_pct": float(v2_res.max_drawdown_pct),
                    "v2_events": len(v2_res.events),
                    "v2_variant_regimes_seen": int(v2_regimes_seen),
                    "fast_final_equity": float(fast_res.final_equity),
                    "fast_return_pct": float(fast_res.total_return_pct),
                    "fast_maxdd_pct": float(fast_res.max_drawdown_pct),
                    "fast_events": len(fast_res.events),
                    "fast_variant_regimes_seen": int(fast_regimes_seen),
                    "fast_minus_v2_equity": float(fast_res.final_equity - v2_res.final_equity),
                    "fast_minus_v2_return_pct": float(fast_res.total_return_pct - v2_res.total_return_pct),
                    "fast_minus_v2_maxdd_pct": float(fast_res.max_drawdown_pct - v2_res.max_drawdown_pct),
                }
            )

    # Aggregate ranking per candidate across windows.
    by_cid: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        cid = str(row["cid"])
        agg = by_cid.setdefault(
            cid,
            {
                "cid": cid,
                "fast_signal_threshold": float(row["fast_signal_threshold"]),
                "fast_trend_gap_pct": float(row["fast_trend_gap_pct"]),
                "fast_inverse_min_weight": float(row["fast_inverse_min_weight"]),
                "windows": 0,
                "sum_fast_minus_v2_return_pct": 0.0,
                "sum_fast_minus_v2_maxdd_pct": 0.0,
                "sum_fast_minus_v2_equity": 0.0,
                "sum_fast_override_days": 0,
            },
        )
        agg["windows"] += 1
        agg["sum_fast_minus_v2_return_pct"] += float(row["fast_minus_v2_return_pct"])
        agg["sum_fast_minus_v2_maxdd_pct"] += float(row["fast_minus_v2_maxdd_pct"])
        agg["sum_fast_minus_v2_equity"] += float(row["fast_minus_v2_equity"])
        agg["sum_fast_override_days"] += int(row["fast_override_days"])

    ranked: list[dict[str, Any]] = []
    for cid, agg in by_cid.items():
        n = max(1, int(agg["windows"]))
        avg_ret = float(agg["sum_fast_minus_v2_return_pct"]) / n
        avg_dd = float(agg["sum_fast_minus_v2_maxdd_pct"]) / n
        avg_eq = float(agg["sum_fast_minus_v2_equity"]) / n
        # Ranking objective: reward return delta and equity delta, penalize DD delta.
        score = avg_ret + (avg_eq / 1000.0) - (0.50 * avg_dd)
        ranked.append(
            {
                **agg,
                "avg_fast_minus_v2_return_pct": avg_ret,
                "avg_fast_minus_v2_maxdd_pct": avg_dd,
                "avg_fast_minus_v2_equity": avg_eq,
                "score": float(score),
            }
        )

    ranked.sort(key=lambda x: float(x["score"]), reverse=True)

    details_csv = reports_dir / f"{args.output_prefix}_{stamp}_details.csv"
    ranked_csv = reports_dir / f"{args.output_prefix}_{stamp}_ranked.csv"
    summary_json = reports_dir / f"{args.output_prefix}_{stamp}_summary.json"

    if all_rows:
        with details_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)

    if ranked:
        with ranked_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(ranked[0].keys()))
            w.writeheader()
            w.writerows(ranked)

    summary = {
        "profile": args.strategy_profile,
        "mode": args.mode,
        "data_feed": args.data_feed,
        "end_date": args.end_date.isoformat(),
        "windows": windows,
        "initial_equity": float(args.initial_equity),
        "rebalance_time_ny": args.rebalance_time_ny,
        "runtime_profit_lock_order_type": args.runtime_profit_lock_order_type,
        "grid": {
            "fast_signal_thresholds": _csv_floats(args.fast_signal_thresholds),
            "fast_trend_gap_pcts": _csv_floats(args.fast_trend_gap_pcts),
            "fast_inverse_min_weights": _csv_floats(args.fast_inverse_min_weights),
            "candidate_count": len(candidates),
        },
        "outputs": {
            "details_csv": str(details_csv),
            "ranked_csv": str(ranked_csv),
            "summary_json": str(summary_json),
        },
        "top5": ranked[:5],
    }
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
