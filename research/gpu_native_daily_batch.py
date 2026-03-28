#!/usr/bin/env python3
"""GPU-native daily synthetic batch simulation for research sweeps.

This module is intentionally isolated under `research/` and does not modify
runtime/backtest production code.

Design goals:
- Run many parameter combinations in one process.
- Use CuPy for vectorized GPU execution when available.
- Keep deterministic CPU reference path (NumPy) for parity checks.
- Reuse existing data/profile loaders from intraday verifier (read-only import).

Scope (intentionally explicit):
- Daily synthetic replay mechanics (daily high/close trigger + daily close rebalance)
- Supports locked profiles from runtime profile table.
- Supports exec models: synthetic, paper_live_style_optimistic (mapped to synthetic), market_close.
- Runtime order type is accepted and recorded; in daily synthetic batch path it is
  treated as same-day close-style execution (no minute-level resting-order lifecycle).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "composer_original" / "tools" / "intraday_profit_lock_verification.py"


@dataclass(frozen=True)
class BackendProbe:
    name: str
    enabled: bool
    reason: str


def _import_verifier_module() -> Any:
    spec = importlib.util.spec_from_file_location("_research_intraday_verifier_mod", VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to import verifier module from {VERIFIER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def probe_cupy_backend() -> BackendProbe:
    """Validate that CuPy can actually execute kernels, not only import."""
    try:
        import cupy as cp  # type: ignore
    except Exception as exc:  # pragma: no cover - env dependent
        return BackendProbe(name="cpu_fallback", enabled=False, reason=f"cupy_import_failed: {exc}")

    try:
        dev_count = int(cp.cuda.runtime.getDeviceCount())
        if dev_count <= 0:
            return BackendProbe(name="cpu_fallback", enabled=False, reason="no_cuda_device")

        # Force at least one real elementwise kernel execution to catch
        # CUDA toolkit/runtime discovery issues early.
        a = cp.full((8,), 1.25, dtype=cp.float64)
        b = cp.full((8,), 0.75, dtype=cp.float64)
        s = float((a + b).sum())
        if not math.isfinite(s):
            return BackendProbe(name="cpu_fallback", enabled=False, reason="cupy_invalid_sum")

        dev_id = int(cp.cuda.Device().id)
        return BackendProbe(name=f"cupy_gpu:{dev_id}", enabled=True, reason="ok")
    except Exception as exc:  # pragma: no cover - env dependent
        return BackendProbe(name="cpu_fallback", enabled=False, reason=f"cupy_runtime_failed: {exc}")


def _combo_value(combo: dict[str, Any], key: str, default: Any) -> Any:
    return combo[key] if key in combo else default


def _build_combo_arrays(
    combos: list[dict[str, Any]],
    *,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    n = len(combos)
    slippage_bps = np.zeros(n, dtype=np.float64)
    sell_fee_bps = np.zeros(n, dtype=np.float64)
    min_trade_delta = np.zeros(n, dtype=np.float64)
    split_adjustment_on = np.zeros(n, dtype=np.bool_)
    exec_market_close = np.zeros(n, dtype=np.bool_)

    # Keep string params for reporting.
    profit_lock_exec_model: list[str] = []
    runtime_profit_lock_order_type: list[str] = []
    rebalance_time_ny: list[str] = []
    warmup_days: list[int] = []
    daily_lookback_days: list[int] = []
    strategy_profile: list[str] = []
    notes: list[str] = []

    for i, combo in enumerate(combos):
        s_bps = float(_combo_value(combo, "slippage_bps", defaults["slippage_bps"]))
        f_bps = float(_combo_value(combo, "sell_fee_bps", defaults["sell_fee_bps"]))

        # In daily synthetic parity path, threshold is effectively zero by design.
        # Allow override for experiments (still isolated in research).
        mtd = float(_combo_value(combo, "rebalance_min_trade_weight_delta", defaults["rebalance_min_trade_weight_delta"]))

        pl_model_raw = str(_combo_value(combo, "profit_lock_exec_model", defaults["profit_lock_exec_model"]))
        pl_model = "synthetic" if pl_model_raw == "paper_live_style_optimistic" else pl_model_raw
        order_type = str(
            _combo_value(combo, "runtime_profit_lock_order_type", defaults["runtime_profit_lock_order_type"])
        )

        # Daily synthetic path has no minute-level resting order lifecycle.
        # Keep value for reporting; execution semantics are same-day synthetic close-style.
        note = ""
        if order_type in {"stop_order", "trailing_stop"}:
            note = "daily_batch_no_minute_lifecycle"

        split_on = bool(
            _combo_value(
                combo,
                "daily_synthetic_parity_split_adjustment",
                defaults["daily_synthetic_parity_split_adjustment"],
            )
        )

        slippage_bps[i] = s_bps
        sell_fee_bps[i] = f_bps
        min_trade_delta[i] = max(0.0, mtd)
        split_adjustment_on[i] = split_on
        exec_market_close[i] = pl_model == "market_close"

        profit_lock_exec_model.append(pl_model_raw)
        runtime_profit_lock_order_type.append(order_type)
        rebalance_time_ny.append(str(_combo_value(combo, "rebalance_time_ny", defaults["rebalance_time_ny"])))
        warmup_days.append(int(_combo_value(combo, "warmup_days", defaults["warmup_days"])))
        daily_lookback_days.append(int(_combo_value(combo, "daily_lookback_days", defaults["daily_lookback_days"])))
        strategy_profile.append(str(_combo_value(combo, "strategy_profile", defaults["strategy_profile"])))
        notes.append(note)

    return {
        "slippage": slippage_bps / 10_000.0,
        "sell_fee": sell_fee_bps / 10_000.0,
        "min_trade_delta": min_trade_delta,
        "split_adjustment_on": split_adjustment_on,
        "exec_market_close": exec_market_close,
        "profit_lock_exec_model": profit_lock_exec_model,
        "runtime_profit_lock_order_type": runtime_profit_lock_order_type,
        "rebalance_time_ny": rebalance_time_ny,
        "warmup_days": warmup_days,
        "daily_lookback_days": daily_lookback_days,
        "strategy_profile": strategy_profile,
        "engine_note": notes,
    }


def _simulate_batch_backend(
    *,
    xp: Any,
    close_mat: Any,
    high_mat: Any,
    split_ratio_mat: Any,
    target_w_mat: Any,
    threshold_pct_by_day: Any,
    start_idx: int,
    end_idx: int,
    initial_equity: float,
    trail_ratio: float,
    profile_mode: str,
    slippage: Any,
    sell_fee: Any,
    min_trade_delta: Any,
    split_adjustment_on: Any,
    exec_market_close: Any,
) -> dict[str, Any]:
    """Vectorized batch simulation across combos.

    Axes:
    - B: combinations
    - D: days
    - S: symbols
    """
    B = int(slippage.shape[0])
    D = int(close_mat.shape[0])
    S = int(close_mat.shape[1])

    holdings = xp.zeros((B, S), dtype=xp.float64)
    cash = xp.full((B,), float(initial_equity), dtype=xp.float64)

    peak_equity = xp.full((B,), float(initial_equity), dtype=xp.float64)
    max_drawdown_pct = xp.zeros((B,), dtype=xp.float64)
    max_drawdown_usd = xp.zeros((B,), dtype=xp.float64)

    one = xp.asarray(1.0, dtype=xp.float64)
    eps = xp.asarray(1e-12, dtype=xp.float64)

    # Expand combo vectors once to avoid repeated broadcasts.
    slippage_2d = slippage[:, None]
    fee_2d = sell_fee[:, None]
    mtd_2d = min_trade_delta[:, None]
    split_on_2d = split_adjustment_on[:, None]
    mclose_2d = exec_market_close[:, None]

    for t in range(start_idx, end_idx + 1):
        day_close = close_mat[t][None, :]  # [1,S]
        day_high = high_mat[t][None, :]  # [1,S]

        # Start-of-day split-adjustment to holdings, if enabled per combo.
        if t > 0:
            ratio_row = split_ratio_mat[t][None, :]  # [1,S]
            holdings = xp.where(split_on_2d, holdings * ratio_row, holdings)

        prev_close = close_mat[t - 1][None, :] if t > 0 else day_close
        threshold_ratio = one + (threshold_pct_by_day[t] / 100.0)
        trigger_price = prev_close * threshold_ratio

        held_mask = holdings > 0.0

        if profile_mode == "fixed":
            trigger_mask = day_high >= trigger_price
            base_sell_px = xp.where(mclose_2d, day_close, trigger_price)
            exit_mask = held_mask & trigger_mask
            trail_stop_px = xp.zeros_like(base_sell_px)
        elif profile_mode == "trailing":
            trigger_mask = day_high >= trigger_price
            trail_stop_px = day_high * (one - float(trail_ratio))
            trail_hit = day_close <= trail_stop_px
            exit_mask = held_mask & trigger_mask & trail_hit
            base_sell_px = xp.where(mclose_2d, day_close, trail_stop_px)
        else:
            raise ValueError(f"Unsupported profile_mode: {profile_mode}")

        sell_px = xp.maximum(base_sell_px * (one - slippage_2d), 0.0)
        sell_notional = holdings * sell_px * exit_mask
        sell_fee_amt = sell_notional * fee_2d
        cash = cash + xp.sum(sell_notional - sell_fee_amt, axis=1)
        holdings = xp.where(exit_mask, 0.0, holdings)

        # Rebalance on day close (daily synthetic parity mechanics).
        rebalance_px = day_close
        equity_before = cash + xp.sum(holdings * rebalance_px, axis=1)
        eq_safe = xp.maximum(equity_before, eps)

        current_w = (holdings * rebalance_px) / eq_safe[:, None]
        target_w = target_w_mat[t][None, :]
        delta_w = target_w - current_w

        sell_active = delta_w < (-mtd_2d)
        sell_w = xp.where(sell_active, -delta_w, 0.0)
        sell_qty_desired = (sell_w * equity_before[:, None]) / xp.maximum(rebalance_px, eps)
        sell_qty = xp.minimum(holdings, xp.maximum(sell_qty_desired, 0.0))

        reb_sell_px = xp.maximum(rebalance_px * (one - slippage_2d), 0.0)
        reb_sell_notional = sell_qty * reb_sell_px
        reb_sell_fee = reb_sell_notional * fee_2d
        cash = cash + xp.sum(reb_sell_notional - reb_sell_fee, axis=1)
        holdings = holdings - sell_qty

        buy_active = delta_w > mtd_2d
        buy_w = xp.where(buy_active, delta_w, 0.0)
        buy_qty_desired = (buy_w * equity_before[:, None]) / xp.maximum(rebalance_px, eps)

        reb_buy_px = rebalance_px * (one + slippage_2d)
        buy_cost_desired = buy_qty_desired * reb_buy_px
        total_buy_cost = xp.sum(buy_cost_desired, axis=1)

        scale = xp.where(total_buy_cost > cash, cash / xp.maximum(total_buy_cost, eps), one)
        buy_qty = buy_qty_desired * scale[:, None]
        buy_cost = buy_qty * reb_buy_px

        cash = cash - xp.sum(buy_cost, axis=1)
        holdings = holdings + buy_qty

        equity_after = cash + xp.sum(holdings * rebalance_px, axis=1)
        peak_equity = xp.maximum(peak_equity, equity_after)

        dd_usd = xp.maximum(peak_equity - equity_after, 0.0)
        dd_pct = (dd_usd / xp.maximum(peak_equity, eps)) * 100.0
        max_drawdown_usd = xp.maximum(max_drawdown_usd, dd_usd)
        max_drawdown_pct = xp.maximum(max_drawdown_pct, dd_pct)

    final_equity = cash + xp.sum(holdings * close_mat[end_idx][None, :], axis=1)
    total_return_pct = ((final_equity / float(initial_equity)) - 1.0) * 100.0

    return {
        "final_equity": final_equity,
        "return_pct": total_return_pct,
        "maxdd_pct": max_drawdown_pct,
        "maxdd_usd": max_drawdown_usd,
    }


def run_gpu_native_daily_batch(
    *,
    combos: list[dict[str, Any]],
    args: Any,
    run_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Execute all combos using shared data + vectorized CPU/GPU simulation.

    Returns: (results, failures, meta)
    """
    t0 = time.time()
    verifier = _import_verifier_module()

    if getattr(args, "env_file", None):
        verifier._load_env_file(str(args.env_file), override=bool(getattr(args, "env_override", True)))

    runtime_profiles = verifier._load_runtime_profiles()

    # Validate profile consistency across combos. This keeps the engine deterministic
    # and avoids accidental mixed-profile matrix issues.
    strategy_profiles = {
        str(_combo_value(c, "strategy_profile", args.strategy_profile_default)) for c in combos
    }
    if len(strategy_profiles) != 1:
        return (
            [],
            [
                {
                    "ok": False,
                    "idx": -1,
                    "error": f"gpu_native_daily_batch requires one strategy profile per run, got: {sorted(strategy_profiles)}",
                }
            ],
            {"backend": "cpu_fallback", "gpu_probe_reason": "mixed_profiles"},
        )

    profile_name = next(iter(strategy_profiles))
    if profile_name not in runtime_profiles:
        return (
            [],
            [{"ok": False, "idx": -1, "error": f"Unknown profile: {profile_name}"}],
            {"backend": "cpu_fallback", "gpu_probe_reason": "unknown_profile"},
        )

    profile = runtime_profiles[profile_name]

    # Daily synthetic parity mechanics are the intended target for this GPU-native daily engine.
    # We still accept combo-level split-adjustment toggles for experiment flexibility.
    mode = str(getattr(args, "mode", "paper"))
    data_feed = str(getattr(args, "data_feed", "sip"))

    alpaca = verifier.AlpacaConfig.from_env(paper=(mode == "paper"), data_feed=data_feed)
    loader = verifier.AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(verifier.StrategyConfig().symbols)

    start_date = args.start_date
    end_date = args.end_date
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    # Use conservative lookback from arguments/defaults so baseline path has enough context.
    # We pick max across combos so data is fetched once.
    warmup_max = max(int(_combo_value(c, "warmup_days", args.warmup_days_default)) for c in combos)
    lookback_max = max(int(_combo_value(c, "daily_lookback_days", args.daily_lookback_days_default)) for c in combos)
    lookback_days = max(lookback_max, warmup_max + 20)

    lookback_start = datetime.combine(start_date - timedelta(days=lookback_days), verifier.dt_time(0, 0), tzinfo=verifier.NY)
    lookback_end = datetime.combine(end_date + timedelta(days=1), verifier.dt_time(23, 59), tzinfo=verifier.NY)

    daily_ohlc_adjusted = verifier._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_ohlc_raw = verifier._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )

    aligned_days, price_history, close_map_by_symbol = verifier._align_daily_close_history(daily_ohlc_adjusted, symbols=symbols)
    if not aligned_days:
        return (
            [],
            [{"ok": False, "idx": -1, "error": "No aligned daily dates"}],
            {"backend": "cpu_fallback", "gpu_probe_reason": "no_aligned_days"},
        )

    day_to_idx = {d: i for i, d in enumerate(aligned_days)}
    in_window_days = [d for d in aligned_days if (d >= start_date and d <= end_date)]
    if not in_window_days:
        return (
            [],
            [{"ok": False, "idx": -1, "error": "No in-window days after alignment"}],
            {"backend": "cpu_fallback", "gpu_probe_reason": "no_window_days"},
        )

    start_idx = day_to_idx[in_window_days[0]]
    end_idx = day_to_idx[in_window_days[-1]]

    # Build close/high matrices [D,S].
    D = len(aligned_days)
    S = len(symbols)
    close_mat_np = np.zeros((D, S), dtype=np.float64)
    high_mat_np = np.zeros((D, S), dtype=np.float64)

    raw_close_map_by_symbol: dict[str, dict[date, float]]
    _, _, raw_close_map_by_symbol = verifier._align_daily_close_history(daily_ohlc_raw, symbols=symbols)

    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _close_px, high_px in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(high_px)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    for j, sym in enumerate(symbols):
        cmap = close_map_by_symbol[sym]
        hmap = high_map_by_symbol.get(sym, {})
        for i, d in enumerate(aligned_days):
            close_mat_np[i, j] = float(cmap.get(d, 0.0))
            high_mat_np[i, j] = float(hmap.get(d, close_mat_np[i, j]))

    split_ratio_by_day_symbol = verifier._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )
    split_ratio_np = np.ones((D, S), dtype=np.float64)
    for i, d in enumerate(aligned_days):
        ratios = split_ratio_by_day_symbol.get(d, {})
        for j, sym in enumerate(symbols):
            r = float(ratios.get(sym, 1.0) or 1.0)
            split_ratio_np[i, j] = r if (math.isfinite(r) and r > 0.0) else 1.0

    # Baseline targets by day from existing backtest engine (read-only use).
    baseline_warmup = max(
        warmup_max,
        (next((i for i, d in enumerate(aligned_days) if d >= start_date), 0) + 1),
    )
    baseline_target_by_day = verifier._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=baseline_warmup,
    )

    target_w_np = np.zeros((D, S), dtype=np.float64)
    for i, d in enumerate(aligned_days):
        w = baseline_target_by_day.get(d, {})
        for j, sym in enumerate(symbols):
            target_w_np[i, j] = float(w.get(sym, 0.0))

    # Daily threshold sequence from locked profile adaptation logic.
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    threshold_pct_np = np.zeros((D,), dtype=np.float64)
    for i in range(D):
        threshold_pct_np[i] = float(
            verifier._threshold_pct_for_day(
                profile,
                close_series,
                day_idx=i,
            )
        )

    defaults = {
        "strategy_profile": str(args.strategy_profile_default),
        "profit_lock_exec_model": str(args.profit_lock_exec_model_default),
        "runtime_profit_lock_order_type": str(args.runtime_profit_lock_order_type_default),
        "rebalance_time_ny": str(args.rebalance_time_ny_default),
        "warmup_days": int(args.warmup_days_default),
        "daily_lookback_days": int(args.daily_lookback_days_default),
        "slippage_bps": float(args.slippage_bps_default),
        "sell_fee_bps": float(args.sell_fee_bps_default),
        # Daily synthetic parity semantics => 0.0 by default, but can be overridden per combo.
        "rebalance_min_trade_weight_delta": 0.0,
        "daily_synthetic_parity_split_adjustment": bool(args.daily_synthetic_parity_split_adjustment_default),
    }

    c = _build_combo_arrays(combos, defaults=defaults)

    cpu_start = time.time()
    cpu_out = _simulate_batch_backend(
        xp=np,
        close_mat=close_mat_np,
        high_mat=high_mat_np,
        split_ratio_mat=split_ratio_np,
        target_w_mat=target_w_np,
        threshold_pct_by_day=threshold_pct_np,
        start_idx=start_idx,
        end_idx=end_idx,
        initial_equity=float(args.initial_equity),
        trail_ratio=max(0.0, float(profile.profit_lock_trail_pct) / 100.0),
        profile_mode=str(profile.profit_lock_mode),
        slippage=c["slippage"],
        sell_fee=c["sell_fee"],
        min_trade_delta=c["min_trade_delta"],
        split_adjustment_on=c["split_adjustment_on"],
        exec_market_close=c["exec_market_close"],
    )
    cpu_elapsed = time.time() - cpu_start

    probe = probe_cupy_backend()
    gpu_backend = probe.name

    if probe.enabled:
        try:
            import cupy as cp  # type: ignore

            gpu_start = time.time()
            gpu_out_raw = _simulate_batch_backend(
                xp=cp,
                close_mat=cp.asarray(close_mat_np),
                high_mat=cp.asarray(high_mat_np),
                split_ratio_mat=cp.asarray(split_ratio_np),
                target_w_mat=cp.asarray(target_w_np),
                threshold_pct_by_day=cp.asarray(threshold_pct_np),
                start_idx=start_idx,
                end_idx=end_idx,
                initial_equity=float(args.initial_equity),
                trail_ratio=max(0.0, float(profile.profit_lock_trail_pct) / 100.0),
                profile_mode=str(profile.profit_lock_mode),
                slippage=cp.asarray(c["slippage"]),
                sell_fee=cp.asarray(c["sell_fee"]),
                min_trade_delta=cp.asarray(c["min_trade_delta"]),
                split_adjustment_on=cp.asarray(c["split_adjustment_on"]),
                exec_market_close=cp.asarray(c["exec_market_close"]),
            )
            gpu_elapsed = time.time() - gpu_start
            gpu_out = {
                k: cp.asnumpy(v) if hasattr(v, "shape") else v for k, v in gpu_out_raw.items()
            }
        except Exception as exc:  # pragma: no cover - env dependent
            gpu_backend = "cpu_fallback"
            probe = BackendProbe(name="cpu_fallback", enabled=False, reason=f"gpu_run_failed: {exc}")
            gpu_elapsed = cpu_elapsed
            gpu_out = {k: np.array(v, copy=True) for k, v in cpu_out.items()}
    else:
        gpu_elapsed = cpu_elapsed
        gpu_out = {k: np.array(v, copy=True) for k, v in cpu_out.items()}

    B = len(combos)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    window_label = f"{start_date.isoformat()} to {end_date.isoformat()}"

    for i, combo in enumerate(combos):
        try:
            cpu_final = float(cpu_out["final_equity"][i])
            gpu_final = float(gpu_out["final_equity"][i])
            cpu_ret = float(cpu_out["return_pct"][i])
            gpu_ret = float(gpu_out["return_pct"][i])
            cpu_maxdd = float(cpu_out["maxdd_pct"][i])
            gpu_maxdd = float(gpu_out["maxdd_pct"][i])
            cpu_maxdd_usd = float(cpu_out["maxdd_usd"][i])
            gpu_maxdd_usd = float(gpu_out["maxdd_usd"][i])

            diff_bps = 0.0 if cpu_final == 0.0 else 10_000.0 * abs(cpu_final - gpu_final) / abs(cpu_final)

            summary = {
                "engine": "gpu_native_daily_batch",
                "window": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
                "profile": profile_name,
                "combo_index": i,
                "combo": {**combo},
                "cpu": {
                    "final_equity": cpu_final,
                    "total_return_pct": cpu_ret,
                    "max_drawdown_pct": cpu_maxdd,
                    "max_drawdown_usd": cpu_maxdd_usd,
                    "elapsed_sec": cpu_elapsed,
                },
                "gpu": {
                    "final_equity": gpu_final,
                    "total_return_pct": gpu_ret,
                    "max_drawdown_pct": gpu_maxdd,
                    "max_drawdown_usd": gpu_maxdd_usd,
                    "elapsed_sec": gpu_elapsed,
                    "backend": gpu_backend,
                    "probe_reason": probe.reason,
                },
                "parity": {
                    "final_equity_diff_bps_vs_cpu": diff_bps,
                },
                "notes": {
                    "runtime_profit_lock_order_type": c["runtime_profit_lock_order_type"][i],
                    "engine_note": c["engine_note"][i],
                    "daily_synthetic_mechanics": True,
                },
            }

            summary_path = run_dir / f"gpu_native_{i:05d}_{profile_name}_{start_date.isoformat()}_to_{end_date.isoformat()}.json"
            summary_path.write_text(json.dumps(summary, indent=2))

            row = {
                "ok": True,
                "idx": i,
                "attempt": 1,
                "elapsed_sec": round(max(cpu_elapsed, gpu_elapsed), 4),
                "window": window_label,
                "cpu_final_equity": cpu_final,
                "cpu_return_pct": cpu_ret,
                "cpu_pnl": cpu_final - float(args.initial_equity),
                "cpu_maxdd_pct": cpu_maxdd,
                "cpu_maxdd_usd": cpu_maxdd_usd,
                "gpu_final_equity": gpu_final,
                "gpu_return_pct": gpu_ret,
                "gpu_pnl": gpu_final - float(args.initial_equity),
                "gpu_maxdd_pct": gpu_maxdd,
                "gpu_maxdd_usd": gpu_maxdd_usd,
                "cpu_gpu_diff_bps": diff_bps,
                "gpu_backend": gpu_backend,
                "summary_json": str(summary_path),
                "engine": "gpu_native_daily_batch",
                "engine_note": c["engine_note"][i],
                "strategy_profile": c["strategy_profile"][i],
                "profit_lock_exec_model": c["profit_lock_exec_model"][i],
                "runtime_profit_lock_order_type": c["runtime_profit_lock_order_type"][i],
                "runtime_stop_price_offset_bps": float(
                    _combo_value(combo, "runtime_stop_price_offset_bps", args.runtime_stop_price_offset_bps_default)
                ),
                "rebalance_time_ny": c["rebalance_time_ny"][i],
                "slippage_bps": float(_combo_value(combo, "slippage_bps", defaults["slippage_bps"])),
                "sell_fee_bps": float(_combo_value(combo, "sell_fee_bps", defaults["sell_fee_bps"])),
                "warmup_days": int(c["warmup_days"][i]),
                "daily_lookback_days": int(c["daily_lookback_days"][i]),
                "anchor_window_start_equity": bool(
                    _combo_value(combo, "anchor_window_start_equity", args.anchor_window_start_equity_default)
                ),
                "daily_synthetic_parity": True,
                "daily_synthetic_parity_split_adjustment": bool(c["split_adjustment_on"][i]),
                "rebalance_min_trade_weight_delta": float(c["min_trade_delta"][i]),
            }
            results.append(row)
        except Exception as exc:
            failures.append({"ok": False, "idx": i, "error": str(exc), **combo})

    meta = {
        "engine": "gpu_native_daily_batch",
        "backend": gpu_backend,
        "gpu_probe_enabled": bool(probe.enabled),
        "gpu_probe_reason": probe.reason,
        "combo_count": B,
        "symbol_count": S,
        "aligned_days": D,
        "window_days": len(in_window_days),
        "profile": profile_name,
        "elapsed_sec_total": round(time.time() - t0, 4),
    }

    return results, failures, meta
