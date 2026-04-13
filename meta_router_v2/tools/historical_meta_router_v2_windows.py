#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
import m0106_runtime_v1.runtime_m0106_loop as m0106
from m0106_runtime_v1.tools.historical_m0106_windows import _build_m0106_targets_and_thresholds
import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as hv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


@dataclass(frozen=True)
class RouterV2Config:
    # Rolling score model.
    lookback_days: int = 20
    vol_penalty: float = 1.0
    dd_penalty: float = 0.12
    switch_penalty: float = 0.03
    min_edge_to_switch: float = 0.05
    min_hold_days: int = 2

    # Regime-driven bonuses.
    burst_ret3d_min: float = 0.12
    burst_rv20_max: float = 130.0
    burst_dd20_max: float = 18.0
    burst_v1_bonus: float = 0.25

    riskoff_rv20_min: float = 95.0
    riskoff_crossovers20_min: int = 8
    riskoff_dd20_min: float = 20.0
    riskoff_m0106_bonus: float = 0.25


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


def _parse_hhmm(raw: str) -> dt_time:
    s = str(raw or "").strip()
    hh, mm = s.split(":", 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f"Invalid HH:MM: {raw!r}")
    return dt_time(h, m)


def _window_range(end_day: date, label: str) -> tuple[date, date]:
    if label not in hv.WINDOW_TO_DAYS:
        raise ValueError(f"Unsupported window: {label}")
    return end_day - timedelta(days=int(hv.WINDOW_TO_DAYS[label])), end_day


def _ret_n(closes: list[float], n: int) -> float:
    if len(closes) <= n:
        return 0.0
    prev = float(closes[-(n + 1)])
    cur = float(closes[-1])
    if prev <= 0.0:
        return 0.0
    return (cur / prev) - 1.0


def _rolling_mdd_pct(returns: list[float]) -> float:
    if not returns:
        return 0.0
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        eq *= 1.0 + float(r)
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > mdd:
                mdd = dd
    return 100.0 * float(mdd)


def _compute_proxy_returns(
    *,
    aligned_days: list[date],
    symbols: list[str],
    close_map_by_symbol: dict[str, dict[date, float]],
    target_by_day: dict[date, dict[str, float]],
) -> list[float]:
    """Approximate engine daily PnL path from previous-day targets and daily closes.

    This is intentionally lightweight so we can evaluate many parameter combinations in
    parallel without replaying minute bars inside the selector itself.
    """
    out = [0.0 for _ in aligned_days]
    for i in range(1, len(aligned_days)):
        prev_day = aligned_days[i - 1]
        d = aligned_days[i]
        w = target_by_day.get(prev_day, {})
        if not w:
            out[i] = 0.0
            continue
        day_ret = 0.0
        for sym in symbols:
            c0 = float(close_map_by_symbol.get(sym, {}).get(prev_day, 0.0) or 0.0)
            c1 = float(close_map_by_symbol.get(sym, {}).get(d, 0.0) or 0.0)
            if c0 <= 0.0 or c1 <= 0.0:
                continue
            day_ret += float(w.get(sym, 0.0)) * ((c1 / c0) - 1.0)
        out[i] = float(day_ret)
    return out


def _score_engine(
    *,
    proxy_returns: list[float],
    idx: int,
    lookback_days: int,
    vol_penalty: float,
    dd_penalty: float,
) -> float:
    if idx <= 1:
        return 0.0
    start = max(1, idx - int(lookback_days))
    win = [float(x) for x in proxy_returns[start:idx]]
    if not win:
        return 0.0
    mean_r = float(np.mean(win))
    vol_r = float(np.std(win, ddof=0))
    mdd_pct = _rolling_mdd_pct(win)
    # Percent-scale score.
    return (mean_r * 100.0) - (float(vol_penalty) * vol_r * 100.0) - (float(dd_penalty) * mdd_pct)


def _build_meta_v2_targets_and_thresholds(
    *,
    aligned_days: list[date],
    symbols: list[str],
    close_series: dict[str, list[float]],
    close_map_by_symbol: dict[str, dict[date, float]],
    v1_targets: dict[date, dict[str, float]],
    v1_thresholds: dict[date, float],
    v2_targets: dict[date, dict[str, float]],
    v2_thresholds: dict[date, float],
    m0106_targets: dict[date, dict[str, float]],
    m0106_thresholds: dict[date, float],
    cfg: RouterV2Config,
) -> tuple[dict[date, dict[str, float]], dict[date, float], dict[date, str], dict[date, str]]:
    targets: dict[date, dict[str, float]] = {}
    thresholds: dict[date, float] = {}
    selected_engine: dict[date, str] = {}
    reasons: dict[date, str] = {}

    proxy = {
        "v1": _compute_proxy_returns(
            aligned_days=aligned_days,
            symbols=symbols,
            close_map_by_symbol=close_map_by_symbol,
            target_by_day=v1_targets,
        ),
        "v2": _compute_proxy_returns(
            aligned_days=aligned_days,
            symbols=symbols,
            close_map_by_symbol=close_map_by_symbol,
            target_by_day=v2_targets,
        ),
        "m0106": _compute_proxy_returns(
            aligned_days=aligned_days,
            symbols=symbols,
            close_map_by_symbol=close_map_by_symbol,
            target_by_day=m0106_targets,
        ),
    }

    prev_engine = "v2"
    hold_days = 0

    for idx, d in enumerate(aligned_days):
        if not v1_targets.get(d):
            targets[d] = {}
            thresholds[d] = float(v1_thresholds.get(d, 0.05))
            selected_engine[d] = "v1"
            reasons[d] = "no_target_day"
            prev_engine = "v1"
            hold_days = 0
            continue

        # Warmup routing keeps behavior deterministic early in history.
        if idx < max(5, int(cfg.lookback_days)):
            engine = "v1"
            reason = f"warmup_idx={idx}"
        else:
            scores = {
                k: _score_engine(
                    proxy_returns=proxy[k],
                    idx=idx,
                    lookback_days=int(cfg.lookback_days),
                    vol_penalty=float(cfg.vol_penalty),
                    dd_penalty=float(cfg.dd_penalty),
                )
                for k in ("v1", "v2", "m0106")
            }

            # Regime bonuses from trailing SOXL behavior (no forward look).
            soxl_hist = list(close_series.get("SOXL", [])[:idx])
            if len(soxl_hist) >= 20:
                metrics = rt_v1._compute_regime_metrics(soxl_hist)
                ret3d = _ret_n(soxl_hist, 3)
                burst = (
                    ret3d >= float(cfg.burst_ret3d_min)
                    and float(metrics.rv20_ann) <= float(cfg.burst_rv20_max)
                    and float(metrics.dd20_pct) <= float(cfg.burst_dd20_max)
                )
                riskoff = (
                    float(metrics.rv20_ann) >= float(cfg.riskoff_rv20_min)
                    or int(metrics.crossovers20) >= int(cfg.riskoff_crossovers20_min)
                    or float(metrics.dd20_pct) >= float(cfg.riskoff_dd20_min)
                )
                if burst:
                    scores["v1"] += float(cfg.burst_v1_bonus)
                if riskoff:
                    scores["m0106"] += float(cfg.riskoff_m0106_bonus)

            # Switching friction: discourage rapid churn.
            if prev_engine in scores:
                for k in scores:
                    if k != prev_engine:
                        scores[k] -= float(cfg.switch_penalty)

            # Candidate best by score.
            cand_engine = max(scores, key=scores.get)
            cand_score = float(scores[cand_engine])
            prev_score = float(scores.get(prev_engine, -1e9))

            # Hysteresis & min hold gate.
            must_hold = hold_days < int(cfg.min_hold_days)
            if prev_engine in scores and (must_hold or cand_score < prev_score + float(cfg.min_edge_to_switch)):
                engine = prev_engine
            else:
                engine = cand_engine

            reason = (
                f"scores(v1={scores['v1']:.4f},v2={scores['v2']:.4f},m0106={scores['m0106']:.4f})"
                f"_hold={hold_days}_prev={prev_engine}_pick={engine}"
            )

        if engine == "v1":
            targets[d] = dict(v1_targets.get(d, {}))
            thresholds[d] = float(v1_thresholds.get(d, 0.05))
        elif engine == "v2":
            targets[d] = dict(v2_targets.get(d, {}))
            thresholds[d] = float(v2_thresholds.get(d, 0.05))
        else:
            targets[d] = dict(m0106_targets.get(d, {}))
            thresholds[d] = float(m0106_thresholds.get(d, 0.05))

        if engine == prev_engine:
            hold_days += 1
        else:
            hold_days = 1
        prev_engine = engine

        selected_engine[d] = engine
        reasons[d] = reason

    return targets, thresholds, selected_engine, reasons


def _summarize_result(initial_equity: float, sim: hv.SimulationResult) -> dict[str, float]:
    return {
        "final_equity": float(sim.final_equity),
        "pnl": float(sim.final_equity - float(initial_equity)),
        "return_pct": float(sim.total_return_pct),
        "maxdd_pct": float(sim.max_drawdown_pct),
        "maxdd_usd": float(sim.max_drawdown_usd),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone Meta Router V2 historical runner.")
    p.add_argument("--env-file", default="")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--windows", default="1m,2m,3m,4m,5m,6m,1y,2y,3y,4y,5y,7y")
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

    # v2 base controls.
    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    # Router V2 controls.
    p.add_argument("--lookback-days", type=int, default=20)
    p.add_argument("--vol-penalty", type=float, default=1.0)
    p.add_argument("--dd-penalty", type=float, default=0.12)
    p.add_argument("--switch-penalty", type=float, default=0.03)
    p.add_argument("--min-edge-to-switch", type=float, default=0.05)
    p.add_argument("--min-hold-days", type=int, default=2)

    p.add_argument("--burst-ret3d-min", type=float, default=0.12)
    p.add_argument("--burst-rv20-max", type=float, default=130.0)
    p.add_argument("--burst-dd20-max", type=float, default=18.0)
    p.add_argument("--burst-v1-bonus", type=float, default=0.25)

    p.add_argument("--riskoff-rv20-min", type=float, default=95.0)
    p.add_argument("--riskoff-crossovers20-min", type=int, default=8)
    p.add_argument("--riskoff-dd20-min", type=float, default=20.0)
    p.add_argument("--riskoff-m0106-bonus", type=float, default=0.25)

    p.add_argument("--reports-dir", default=str(ROOT / "meta_router_v2" / "reports"))
    p.add_argument("--output-prefix", default="compare_meta_router_v2")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(f"Unknown strategy profile: {args.strategy_profile}")

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

    windows = [w.strip() for w in str(args.windows).split(",") if w.strip()]
    if not windows:
        raise ValueError("No windows provided")
    for w in windows:
        if w not in hv.WINDOW_TO_DAYS:
            raise ValueError(f"Unsupported window: {w}")

    max_days = max(int(hv.WINDOW_TO_DAYS[w]) for w in windows)
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
        for d, _close_px, high_px in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(high_px)
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

    router_cfg = RouterV2Config(
        lookback_days=int(args.lookback_days),
        vol_penalty=float(args.vol_penalty),
        dd_penalty=float(args.dd_penalty),
        switch_penalty=float(args.switch_penalty),
        min_edge_to_switch=float(args.min_edge_to_switch),
        min_hold_days=int(args.min_hold_days),
        burst_ret3d_min=float(args.burst_ret3d_min),
        burst_rv20_max=float(args.burst_rv20_max),
        burst_dd20_max=float(args.burst_dd20_max),
        burst_v1_bonus=float(args.burst_v1_bonus),
        riskoff_rv20_min=float(args.riskoff_rv20_min),
        riskoff_crossovers20_min=int(args.riskoff_crossovers20_min),
        riskoff_dd20_min=float(args.riskoff_dd20_min),
        riskoff_m0106_bonus=float(args.riskoff_m0106_bonus),
    )

    meta_targets, meta_thresholds, selected_engine, _selected_reason = _build_meta_v2_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        close_map_by_symbol=close_map_by_symbol,
        v1_targets=v1_targets,
        v1_thresholds=v1_thresholds,
        v2_targets=v2_targets,
        v2_thresholds=v2_thresholds,
        m0106_targets=m0106_targets,
        m0106_thresholds=m0106_thresholds,
        cfg=router_cfg,
    )

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    gpu_backend = "cpu_emulated_fallback"
    try:
        import cupy  # type: ignore # noqa: F401

        gpu_backend = "cupy_available_cpu_logic"
    except Exception:
        gpu_backend = "cpu_emulated_fallback"

    rows: list[dict[str, Any]] = []

    for window in windows:
        start_day, end_day = _window_range(args.end_date, window)

        sim_v1 = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=v1_targets,
            rebalance_threshold_by_day=v1_thresholds,
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

        sim_v2 = hv._simulate_intraday(
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

        sim_m0106 = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=m0106_targets,
            rebalance_threshold_by_day=m0106_thresholds,
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

        sim_meta_cpu = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=meta_targets,
            rebalance_threshold_by_day=meta_thresholds,
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

        sim_meta_gpu = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=meta_targets,
            rebalance_threshold_by_day=meta_thresholds,
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

        meta_cpu = _summarize_result(float(args.initial_equity), sim_meta_cpu)
        meta_gpu = _summarize_result(float(args.initial_equity), sim_meta_gpu)

        eng_counts = {"v1": 0, "v2": 0, "m0106": 0}
        for d in aligned_days:
            if start_day <= d <= end_day:
                k = selected_engine.get(d, "v2")
                if k in eng_counts:
                    eng_counts[k] += 1

        finals = {
            "runtime_switch_loop.py": float(sim_v1.final_equity),
            "runtime_switch_loop_v2_controlplane.py": float(sim_v2.final_equity),
            "runtime_m0106_loop.py": float(sim_m0106.final_equity),
            "meta_router_v2": float(sim_meta_cpu.final_equity),
        }

        rows.append(
            {
                "window": window,
                "period": f"{start_day.isoformat()} to {end_day.isoformat()}",
                "start_equity": float(args.initial_equity),
                "v1_final_equity": float(sim_v1.final_equity),
                "v1_return_pct": float(sim_v1.total_return_pct),
                "v1_maxdd_pct": float(sim_v1.max_drawdown_pct),
                "v2_final_equity": float(sim_v2.final_equity),
                "v2_return_pct": float(sim_v2.total_return_pct),
                "v2_maxdd_pct": float(sim_v2.max_drawdown_pct),
                "m0106_final_equity": float(sim_m0106.final_equity),
                "m0106_return_pct": float(sim_m0106.total_return_pct),
                "m0106_maxdd_pct": float(sim_m0106.max_drawdown_pct),
                "meta_cpu_final_equity": meta_cpu["final_equity"],
                "meta_cpu_pnl": meta_cpu["pnl"],
                "meta_cpu_return_pct": meta_cpu["return_pct"],
                "meta_cpu_maxdd_pct": meta_cpu["maxdd_pct"],
                "meta_gpu_final_equity": meta_gpu["final_equity"],
                "meta_gpu_return_pct": meta_gpu["return_pct"],
                "meta_gpu_maxdd_pct": meta_gpu["maxdd_pct"],
                "meta_cpu_gpu_diff_bps": float(iv._safe_bps_diff(meta_cpu["final_equity"], meta_gpu["final_equity"])),
                "router_days_v1": int(eng_counts["v1"]),
                "router_days_v2": int(eng_counts["v2"]),
                "router_days_m0106": int(eng_counts["m0106"]),
                "winner_by_final_equity": max(finals, key=finals.get),
            }
        )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.end_date.strftime("%Y%m%d")
    base = reports_dir / f"{args.output_prefix}_{stamp}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")

    headers = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    payload = {
        "run": {
            "mode": args.mode,
            "data_feed": args.data_feed,
            "strategy_profile": args.strategy_profile,
            "windows": windows,
            "end_date": args.end_date.isoformat(),
            "initial_equity": float(args.initial_equity),
            "slippage_bps": float(args.slippage_bps),
            "sell_fee_bps": float(args.sell_fee_bps),
            "rebalance_time_ny": args.rebalance_time_ny,
            "runtime_profit_lock_order_type": args.runtime_profit_lock_order_type,
            "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
            "base_rebalance_threshold": float(args.rebalance_threshold),
            "controlplane_threshold_cap": float(args.controlplane_threshold_cap),
            "controlplane_hysteresis_enter": float(args.controlplane_hysteresis_enter),
            "controlplane_hysteresis_exit": float(args.controlplane_hysteresis_exit),
            "controlplane_hysteresis_enter_days": int(args.controlplane_hysteresis_enter_days),
            "controlplane_hysteresis_exit_days": int(args.controlplane_hysteresis_exit_days),
            "router_cfg": asdict(router_cfg),
            "gpu_backend": gpu_backend,
        },
        "rows": rows,
        "outputs": {
            "csv": str(csv_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
