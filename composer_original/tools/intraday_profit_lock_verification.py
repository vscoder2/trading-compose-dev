#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import evaluate_strategy
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import AlpacaConfig, BacktestConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader, BarRequestSpec
from soxl_growth.execution.orders import build_rebalance_order_intents


@dataclass(frozen=True)
class LockedProfile:
    name: str
    enable_profit_lock: bool
    profit_lock_mode: str
    profit_lock_threshold_pct: float
    profit_lock_trail_pct: float
    profit_lock_adaptive_threshold: bool
    profit_lock_adaptive_symbol: str
    profit_lock_adaptive_rv_window: int
    profit_lock_adaptive_rv_baseline_pct: float
    profit_lock_adaptive_min_threshold_pct: float
    profit_lock_adaptive_max_threshold_pct: float


@dataclass(frozen=True)
class EventRecord:
    day: date
    event_type: str
    symbol: str
    side: str
    qty: float
    price: float
    ts: datetime | None
    reason: str
    trigger_price: float = 0.0
    trail_stop_price: float = 0.0


@dataclass(frozen=True)
class DayRecord:
    day: date
    equity: float
    pnl: float
    ret_pct: float
    drawdown_usd: float
    drawdown_pct: float
    fell_usd: float
    fell_pct: float
    sale_time_stock: str
    new_purchase_time_stock: str


@dataclass(frozen=True)
class SimulationResult:
    daily: list[DayRecord]
    events: list[EventRecord]
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    max_drawdown_usd: float


def _annualized_rv_pct(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    returns: list[float] = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        cur = float(closes[i])
        if prev > 0:
            returns.append(cur / prev - 1.0)
    if not returns:
        return 0.0
    mu = sum(returns) / len(returns)
    var = sum((x - mu) ** 2 for x in returns) / len(returns)
    return 100.0 * (var**0.5) * (252.0**0.5)


def _threshold_pct_for_day(
    profile: LockedProfile,
    daily_closes: dict[str, list[float]],
    *,
    day_idx: int,
) -> float:
    base = float(profile.profit_lock_threshold_pct)
    if not profile.enable_profit_lock or not profile.profit_lock_adaptive_threshold:
        return base
    sym = profile.profit_lock_adaptive_symbol
    closes = list(daily_closes.get(sym, []))
    window = max(2, int(profile.profit_lock_adaptive_rv_window))
    if day_idx < window:
        return base
    # Strictly prior closes only for day_idx threshold.
    lookback = closes[day_idx - window : day_idx]
    rv = _annualized_rv_pct(lookback)
    baseline = max(1e-9, float(profile.profit_lock_adaptive_rv_baseline_pct))
    ratio = rv / baseline
    tmin = min(
        float(profile.profit_lock_adaptive_min_threshold_pct),
        float(profile.profit_lock_adaptive_max_threshold_pct),
    )
    tmax = max(
        float(profile.profit_lock_adaptive_min_threshold_pct),
        float(profile.profit_lock_adaptive_max_threshold_pct),
    )
    return min(tmax, max(tmin, base * ratio))


def _load_runtime_profiles() -> dict[str, LockedProfile]:
    path = ROOT / "composer_original" / "tools" / "runtime_backtest_parity_loop.py"
    spec = importlib.util.spec_from_file_location("_runtime_backtest_parity_loop_profiles", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load runtime profile module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    out: dict[str, LockedProfile] = {}
    raw_profiles = getattr(module, "PROFILES", {})
    for key, p in raw_profiles.items():
        out[key] = LockedProfile(
            name=str(getattr(p, "name")),
            enable_profit_lock=bool(getattr(p, "enable_profit_lock")),
            profit_lock_mode=str(getattr(p, "profit_lock_mode")),
            profit_lock_threshold_pct=float(getattr(p, "profit_lock_threshold_pct")),
            profit_lock_trail_pct=float(getattr(p, "profit_lock_trail_pct")),
            profit_lock_adaptive_threshold=bool(getattr(p, "profit_lock_adaptive_threshold")),
            profit_lock_adaptive_symbol=str(getattr(p, "profit_lock_adaptive_symbol")),
            profit_lock_adaptive_rv_window=int(getattr(p, "profit_lock_adaptive_rv_window")),
            profit_lock_adaptive_rv_baseline_pct=float(getattr(p, "profit_lock_adaptive_rv_baseline_pct")),
            profit_lock_adaptive_min_threshold_pct=float(getattr(p, "profit_lock_adaptive_min_threshold_pct")),
            profit_lock_adaptive_max_threshold_pct=float(getattr(p, "profit_lock_adaptive_max_threshold_pct")),
        )
    if not out:
        raise RuntimeError("No locked profiles found in runtime_backtest_parity_loop.py")
    return out


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


def _fetch_daily_ohlc(
    loader: AlpacaBarLoader,
    *,
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
    feed: str,
    adjustment: str = "all",
) -> dict[str, list[tuple[date, float, float]]]:
    bars = loader.get_bars(
        BarRequestSpec(
            symbols=symbols,
            start=start_dt,
            end=end_dt,
            timeframe="1Day",
            adjustment=adjustment,
            feed=feed,
        )
    )
    out: dict[str, list[tuple[date, float, float]]] = {s: [] for s in symbols}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        day = bar.timestamp.astimezone(NY).date()
        out.setdefault(bar.symbol, []).append((day, float(bar.close), float(bar.high)))
    return out


def _align_daily_close_history(
    daily_ohlc: dict[str, list[tuple[date, float, float]]],
    symbols: list[str],
) -> tuple[list[date], dict[str, list[tuple[date, float]]], dict[str, dict[date, float]]]:
    if not symbols:
        raise ValueError("No symbols provided")
    day_sets: list[set[date]] = []
    close_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        rows = daily_ohlc.get(sym, [])
        cmap: dict[date, float] = {}
        for d, close_px, _ in rows:
            cmap[d] = float(close_px)
        close_map_by_symbol[sym] = cmap
        day_sets.append(set(cmap.keys()))
    aligned_days = sorted(set.intersection(*day_sets)) if day_sets else []
    if not aligned_days:
        raise RuntimeError("No aligned daily close dates across symbols")
    price_history: dict[str, list[tuple[date, float]]] = {}
    for sym in symbols:
        cmap = close_map_by_symbol[sym]
        price_history[sym] = [(d, float(cmap[d])) for d in aligned_days]
    return aligned_days, price_history, close_map_by_symbol


def _fetch_minute_bars_by_day_symbol(
    loader: AlpacaBarLoader,
    *,
    symbols: list[str],
    start_day: date,
    end_day: date,
    feed: str,
) -> dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]]:
    start_dt = datetime.combine(start_day, dt_time(9, 30), tzinfo=NY)
    end_dt = datetime.combine(end_day + timedelta(days=1), dt_time(16, 0), tzinfo=NY)
    bars = loader.get_bars(
        BarRequestSpec(
            symbols=symbols,
            start=start_dt,
            end=end_dt,
            timeframe="1Min",
            adjustment="raw",
            feed=feed,
        )
    )
    out: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]] = {}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        ts = bar.timestamp.astimezone(NY)
        d = ts.date()
        if d < start_day or d > end_day:
            continue
        out.setdefault(d, {}).setdefault(bar.symbol, []).append(
            (ts, float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        )
    return out


def _build_baseline_target_by_day(
    price_history: dict[str, list[tuple[date, float]]],
    *,
    initial_equity: float,
    warmup_days: int,
) -> dict[date, dict[str, float]]:
    cfg = BacktestConfig(
        initial_equity=initial_equity,
        warmup_days=warmup_days,
        slippage_bps=0.0,
        sell_fee_bps=0.0,
    )
    # We only consume the allocation path, not backtest PnL.
    baseline_bt = run_backtest(price_history=price_history, config=cfg, evaluate_fn=evaluate_strategy)
    return {d: dict(w) for d, w in baseline_bt.allocations}


def _build_split_ratio_by_day_symbol(
    *,
    aligned_days: list[date],
    symbols: list[str],
    adjusted_close_map: dict[str, dict[date, float]],
    raw_close_map: dict[str, dict[date, float]],
) -> dict[date, dict[str, float]]:
    """Build day-start share-adjustment ratios from adjusted/raw close factors.

    Ratio semantics:
    - ratio on day D is applied to prior-day ending shares to obtain day D starting shares.
    - ratio = (adj/raw on D) / (adj/raw on D-1)
    """
    out: dict[date, dict[str, float]] = {}
    if not aligned_days:
        return out
    out[aligned_days[0]] = {s: 1.0 for s in symbols}
    for i in range(1, len(aligned_days)):
        d_prev = aligned_days[i - 1]
        d_cur = aligned_days[i]
        day_ratios: dict[str, float] = {}
        for s in symbols:
            adj_prev = float(adjusted_close_map.get(s, {}).get(d_prev, 0.0) or 0.0)
            raw_prev = float(raw_close_map.get(s, {}).get(d_prev, 0.0) or 0.0)
            adj_cur = float(adjusted_close_map.get(s, {}).get(d_cur, 0.0) or 0.0)
            raw_cur = float(raw_close_map.get(s, {}).get(d_cur, 0.0) or 0.0)
            if adj_prev <= 0.0 or raw_prev <= 0.0 or adj_cur <= 0.0 or raw_cur <= 0.0:
                day_ratios[s] = 1.0
                continue
            f_prev = adj_prev / raw_prev
            f_cur = adj_cur / raw_cur
            if f_prev <= 0.0 or f_cur <= 0.0:
                day_ratios[s] = 1.0
                continue
            ratio = f_cur / f_prev
            if not math.isfinite(ratio) or ratio <= 0.0:
                ratio = 1.0
            day_ratios[s] = float(ratio)
        out[d_cur] = day_ratios
    return out


def _fmt_time(ts: datetime | None) -> str:
    if ts is None:
        return "no change"
    return ts.astimezone(NY).strftime("%H:%M")


def _build_action_label(entries: list[tuple[datetime | None, str]]) -> str:
    if not entries:
        return "no change"
    parts: list[str] = []
    for ts, sym in entries:
        parts.append(f"{_fmt_time(ts)} | {sym}")
    return "; ".join(parts)


def _synthetic_close_ts(day: date) -> datetime:
    # Synthetic daily-close timestamp used for parity labeling in daily emulation mode.
    return datetime.combine(day, dt_time(15, 59), tzinfo=NY)


def _parse_hhmm(value: str) -> dt_time:
    raw = str(value or "").strip()
    try:
        hh, mm = raw.split(":", 1)
        h = int(hh)
        m = int(mm)
    except Exception as exc:
        raise ValueError(f"Invalid HH:MM time value: {value!r}") from exc
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f"Invalid HH:MM time value: {value!r}")
    return dt_time(h, m)


def _minute_close_at_or_before(
    minutes: list[tuple[datetime, float, float, float, float]],
    cutoff: dt_time,
) -> tuple[datetime | None, float]:
    """Return close timestamp/price at or before cutoff (NY time) for a symbol/day."""
    if not minutes:
        return None, 0.0
    chosen_ts: datetime | None = None
    chosen_close = 0.0
    for ts, _o, _h, _l, close in minutes:
        ts_ny = ts.astimezone(NY)
        if ts_ny.time() <= cutoff:
            chosen_ts = ts_ny
            chosen_close = float(close)
        else:
            # list is sorted; once cutoff exceeded, remaining rows are later
            break
    if chosen_ts is not None:
        return chosen_ts, chosen_close
    # If cutoff is before first available bar, fall back to first available bar.
    ts0, _o0, _h0, _l0, c0 = minutes[0]
    return ts0.astimezone(NY), float(c0)


def _simulate_intraday_verification(
    *,
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    high_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    baseline_target_by_day: dict[date, dict[str, float]],
    profile: LockedProfile,
    start_day: date,
    end_day: date,
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    profit_lock_exec_model: str,
    runtime_profit_lock_order_type: str = "market_order",
    runtime_stop_price_offset_bps: float = 2.0,
    daily_synthetic_parity: bool = False,
    rebalance_min_trade_weight_delta: float = 0.0,
    rebalance_time_ny: dt_time = dt_time(15, 56),
    split_ratio_by_day_symbol: dict[date, dict[str, float]] | None = None,
) -> SimulationResult:
    symbol_to_idx = {s: i for i, s in enumerate(symbols)}
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    day_to_index = {d: i for i, d in enumerate(aligned_days)}

    cash = float(initial_equity)
    holdings = {s: 0.0 for s in symbols}
    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trail_ratio = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)

    daily_rows: list[DayRecord] = []
    events: list[EventRecord] = []

    peak_equity = float(initial_equity)
    peak_drawdown_usd = 0.0
    prev_equity = float(initial_equity)

    for d in aligned_days:
        if d < start_day or d > end_day:
            continue
        day_idx = day_to_index[d]
        day_minutes = minute_by_day_symbol.get(d, {})

        # Apply overnight split-adjustment ratio to shares at start of day.
        if split_ratio_by_day_symbol is not None and day_idx > 0:
            ratios = split_ratio_by_day_symbol.get(d, {})
            for sym in symbols:
                ratio = float(ratios.get(sym, 1.0) or 1.0)
                if (not math.isfinite(ratio)) or ratio <= 0.0:
                    ratio = 1.0
                if abs(ratio - 1.0) <= 1e-12:
                    continue
                old_qty = float(holdings.get(sym, 0.0))
                if old_qty <= 0.0:
                    continue
                new_qty = old_qty * ratio
                holdings[sym] = new_qty
                events.append(
                    EventRecord(
                        day=d,
                        event_type="split_adjustment",
                        symbol=sym,
                        side="none",
                        qty=float(new_qty - old_qty),
                        price=0.0,
                        ts=None,
                        reason=f"overnight_ratio={ratio:.10f}",
                    )
                )

        threshold_pct = _threshold_pct_for_day(
            profile,
            close_series,
            day_idx=day_idx,
        )
        threshold_ratio = 1.0 + threshold_pct / 100.0

        sale_entries: list[tuple[datetime | None, str]] = []
        buy_entries: list[tuple[datetime | None, str]] = []
        symbols_blocked_for_rebalance: set[str] = set()

        if profile.enable_profit_lock and day_idx > 0:
            for sym in symbols:
                held_qty = float(holdings.get(sym, 0.0))
                if held_qty <= 0.0:
                    continue
                prev_close = float(close_series[sym][day_idx - 1])
                if prev_close <= 0.0:
                    continue
                trigger_price = prev_close * threshold_ratio
                exit_ts: datetime | None = None
                exit_price = 0.0
                trail_stop_price = 0.0

                if daily_synthetic_parity:
                    # Match daily synthetic replay semantics: trigger on day high, trailing evaluated via day close.
                    day_high = float(high_map_by_symbol.get(sym, {}).get(d, 0.0))
                    day_close = float(close_map_by_symbol.get(sym, {}).get(d, 0.0))
                    if profile.profit_lock_mode == "fixed":
                        if day_high >= trigger_price:
                            exit_ts = _synthetic_close_ts(d)
                            if profit_lock_exec_model == "market_close":
                                exit_price = day_close * (1.0 - slip)
                            else:
                                exit_price = trigger_price * (1.0 - slip)
                            trail_stop_price = 0.0
                    elif profile.profit_lock_mode == "trailing":
                        if day_high >= trigger_price:
                            trail_stop = day_high * (1.0 - trail_ratio)
                            if day_close <= trail_stop:
                                exit_ts = _synthetic_close_ts(d)
                                trail_stop_price = trail_stop
                                if profit_lock_exec_model == "market_close":
                                    exit_price = day_close * (1.0 - slip)
                                else:
                                    exit_price = trail_stop * (1.0 - slip)
                    else:
                        raise ValueError(f"Unsupported locked profit_lock_mode: {profile.profit_lock_mode}")
                else:
                    minutes = day_minutes.get(sym, [])
                    if not minutes:
                        continue
                    signal_idx: int | None = None
                    signal_close_px: float = 0.0
                    if profile.profit_lock_mode == "fixed":
                        for idx_m, (ts, _o, high_px, _low_px, close_px) in enumerate(minutes):
                            if high_px >= trigger_price:
                                exit_ts = ts
                                signal_idx = idx_m
                                signal_close_px = float(close_px)
                                if profit_lock_exec_model == "market_close":
                                    exit_price = float(minutes[-1][4]) * (1.0 - slip)
                                else:
                                    exit_price = trigger_price * (1.0 - slip)
                                trail_stop_price = 0.0
                                break
                    elif profile.profit_lock_mode == "trailing":
                        triggered = False
                        high_water = 0.0
                        for idx_m, (ts, _o, high_px, low_px, close_px) in enumerate(minutes):
                            if (not triggered) and (high_px >= trigger_price):
                                triggered = True
                                high_water = max(high_px, trigger_price)
                            if not triggered:
                                continue
                            high_water = max(high_water, high_px)
                            trail_stop = high_water * (1.0 - trail_ratio)
                            if low_px <= trail_stop:
                                exit_ts = ts
                                signal_idx = idx_m
                                signal_close_px = float(close_px)
                                trail_stop_price = trail_stop
                                if profit_lock_exec_model == "market_close":
                                    exit_price = close_px * (1.0 - slip)
                                else:
                                    exit_price = trail_stop * (1.0 - slip)
                                break
                    else:
                        raise ValueError(f"Unsupported locked profit_lock_mode: {profile.profit_lock_mode}")

                    # Runtime order-type emulation for minute path:
                    # - close_position/market_order => immediate modeled close (existing behavior)
                    # - stop_order/trailing_stop => submit modeled resting exit and block same-day rebalance for symbol
                    if runtime_profit_lock_order_type in {"stop_order", "trailing_stop"} and signal_idx is not None:
                        symbols_blocked_for_rebalance.add(sym)
                        events.append(
                            EventRecord(
                                day=d,
                                event_type="profit_lock_order_submitted",
                                symbol=sym,
                                side="sell",
                                qty=float(held_qty),
                                price=float(signal_close_px),
                                ts=exit_ts,
                                reason=f"runtime_order_type={runtime_profit_lock_order_type}",
                                trigger_price=float(trigger_price),
                                trail_stop_price=float(trail_stop_price),
                            )
                        )
                        # Convert immediate fill into a modeled resting-order fill after signal point.
                        if runtime_profit_lock_order_type == "stop_order":
                            stop_ref = float(trigger_price)
                            if profile.profit_lock_mode == "trailing" and trail_stop_price > 0.0:
                                stop_ref = float(trail_stop_price)
                            cap = float(signal_close_px) * (
                                1.0 - max(0.0, float(runtime_stop_price_offset_bps)) / 10_000.0
                            )
                            stop_price = min(stop_ref, cap)
                            if stop_price <= 0.0:
                                stop_price = max(0.01, float(signal_close_px) * 0.995)
                            exit_ts = None
                            exit_price = 0.0
                            for ts2, _o2, _h2, low2, close2 in minutes[signal_idx:]:
                                if low2 <= stop_price:
                                    exit_ts = ts2
                                    if profit_lock_exec_model == "market_close":
                                        exit_price = float(close2) * (1.0 - slip)
                                    else:
                                        exit_price = float(stop_price) * (1.0 - slip)
                                    break
                        else:
                            trail_ratio_rt = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)
                            high_water_rt = float(minutes[signal_idx][2])
                            exit_ts = None
                            exit_price = 0.0
                            trail_stop_price = 0.0
                            for ts2, _o2, high2, low2, close2 in minutes[signal_idx:]:
                                high_water_rt = max(high_water_rt, float(high2))
                                trail_stop_rt = high_water_rt * (1.0 - trail_ratio_rt)
                                trail_stop_price = float(trail_stop_rt)
                                if low2 <= trail_stop_rt:
                                    exit_ts = ts2
                                    if profit_lock_exec_model == "market_close":
                                        exit_price = float(close2) * (1.0 - slip)
                                    else:
                                        exit_price = float(trail_stop_rt) * (1.0 - slip)
                                    break

                if exit_ts is None or exit_price <= 0.0:
                    continue
                sell_qty = held_qty
                notional = sell_qty * exit_price
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym] = max(0.0, held_qty - sell_qty)
                sale_entries.append((exit_ts, sym))
                events.append(
                    EventRecord(
                        day=d,
                        event_type="profit_lock_sell",
                        symbol=sym,
                        side="sell",
                        qty=float(sell_qty),
                        price=float(exit_price),
                        ts=exit_ts,
                        reason="intraday_profit_lock",
                        trigger_price=float(trigger_price),
                        trail_stop_price=float(trail_stop_price),
                    )
                )

        last_prices: dict[str, float] = {}
        rebalance_ts_by_symbol: dict[str, datetime] = {}
        for sym in symbols:
            if daily_synthetic_parity:
                last_prices[sym] = float(close_map_by_symbol[sym][d])
                rebalance_ts_by_symbol[sym] = _synthetic_close_ts(d)
            else:
                minutes = day_minutes.get(sym, [])
                if minutes:
                    reb_ts, reb_px = _minute_close_at_or_before(minutes, rebalance_time_ny)
                    if reb_ts is not None and reb_px > 0.0:
                        last_prices[sym] = float(reb_px)
                        rebalance_ts_by_symbol[sym] = reb_ts
                    else:
                        last_prices[sym] = float(minutes[-1][4])
                        rebalance_ts_by_symbol[sym] = minutes[-1][0].astimezone(NY)
                else:
                    last_prices[sym] = float(close_map_by_symbol[sym][d])
                    rebalance_ts_by_symbol[sym] = _synthetic_close_ts(d)

        equity_before = cash + sum(float(holdings[s]) * float(last_prices[s]) for s in symbols)
        target = dict(baseline_target_by_day.get(d, {}))
        if target:
            intents = build_rebalance_order_intents(
                equity=float(equity_before),
                target_weights=target,
                current_qty={s: float(holdings[s]) for s in symbols},
                last_prices=last_prices,
                min_trade_weight_delta=float(rebalance_min_trade_weight_delta),
            )
            if runtime_profit_lock_order_type in {"stop_order", "trailing_stop"} and symbols_blocked_for_rebalance:
                intents = [intent for intent in intents if intent.symbol not in symbols_blocked_for_rebalance]
            close_ts: datetime | None = None
            if daily_synthetic_parity:
                close_ts = _synthetic_close_ts(d)
            else:
                latest = [rebalance_ts_by_symbol.get(s) for s in symbols if rebalance_ts_by_symbol.get(s) is not None]
                close_ts = max(latest) if latest else None

            for intent in intents:
                sym = intent.symbol
                qty = float(intent.qty)
                px = float(last_prices.get(sym, 0.0))
                if qty <= 0.0 or px <= 0.0:
                    continue
                if intent.side == "sell":
                    qty = min(qty, max(float(holdings.get(sym, 0.0)), 0.0))
                    if qty <= 0.0:
                        continue
                    exec_px = px * (1.0 - slip)
                    notional = qty * exec_px
                    fee = abs(notional) * sell_fee
                    cash += notional - fee
                    holdings[sym] = max(0.0, float(holdings[sym]) - qty)
                    events.append(
                        EventRecord(
                            day=d,
                            event_type="rebalance_sell",
                            symbol=sym,
                            side="sell",
                            qty=float(qty),
                            price=float(exec_px),
                            ts=close_ts,
                            reason="baseline_rebalance",
                        )
                    )
                else:
                    exec_px = px * (1.0 + slip)
                    if exec_px <= 0.0:
                        continue
                    max_affordable = cash / exec_px
                    qty = min(qty, max_affordable)
                    if qty <= 0.0:
                        continue
                    cash -= qty * exec_px
                    holdings[sym] = float(holdings[sym]) + qty
                    buy_entries.append((close_ts, sym))
                    events.append(
                        EventRecord(
                            day=d,
                            event_type="rebalance_buy",
                            symbol=sym,
                            side="buy",
                            qty=float(qty),
                            price=float(exec_px),
                            ts=close_ts,
                            reason="baseline_rebalance",
                        )
                    )

        equity_after = cash + sum(float(holdings[s]) * float(last_prices[s]) for s in symbols)
        peak_equity = max(peak_equity, equity_after)
        drawdown_usd = max(0.0, peak_equity - equity_after)
        drawdown_pct = 100.0 * drawdown_usd / peak_equity if peak_equity > 0 else 0.0
        fell_usd = max(0.0, prev_equity - equity_after)
        fell_pct = 100.0 * fell_usd / prev_equity if prev_equity > 0 else 0.0
        pnl = equity_after - prev_equity
        ret_pct = 100.0 * (equity_after / prev_equity - 1.0) if prev_equity > 0 else 0.0
        peak_drawdown_usd = max(peak_drawdown_usd, drawdown_usd)
        prev_equity = equity_after

        daily_rows.append(
            DayRecord(
                day=d,
                equity=float(equity_after),
                pnl=float(pnl),
                ret_pct=float(ret_pct),
                drawdown_usd=float(drawdown_usd),
                drawdown_pct=float(drawdown_pct),
                fell_usd=float(fell_usd),
                fell_pct=float(fell_pct),
                sale_time_stock=_build_action_label(sale_entries),
                new_purchase_time_stock=_build_action_label(buy_entries),
            )
        )

    final_equity = float(daily_rows[-1].equity) if daily_rows else float(initial_equity)
    total_return_pct = 100.0 * (final_equity / float(initial_equity) - 1.0) if initial_equity > 0 else 0.0
    max_drawdown_pct = 100.0 * peak_drawdown_usd / peak_equity if peak_equity > 0 else 0.0
    return SimulationResult(
        daily=daily_rows,
        events=events,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_usd=peak_drawdown_usd,
    )


def _safe_bps_diff(a: float, b: float) -> float:
    if a == 0.0:
        return float("inf") if b != 0.0 else 0.0
    return 10_000.0 * abs(a - b) / abs(a)


def _report_metrics(result: SimulationResult, *, initial_equity: float) -> dict[str, float | int]:
    return {
        "days": len(result.daily),
        "initial_equity": float(initial_equity),
        "final_equity": float(result.final_equity),
        "total_return_pct": float(result.total_return_pct),
        "max_drawdown_pct": float(result.max_drawdown_pct),
        "max_drawdown_usd": float(result.max_drawdown_usd),
        "event_count": len(result.events),
    }


def _write_daily_table_csv(
    path: Path,
    *,
    cpu: SimulationResult,
    gpu: SimulationResult,
) -> None:
    day_to_cpu = {r.day: r for r in cpu.daily}
    day_to_gpu = {r.day: r for r in gpu.daily}
    all_days = sorted(set(day_to_cpu.keys()) | set(day_to_gpu.keys()))
    headers = [
        "date",
        "cpu_equity",
        "cpu_pnl",
        "cpu_return_pct",
        "cpu_drawdown_usd",
        "cpu_drawdown_pct",
        "cpu_day_fall_usd",
        "cpu_day_fall_pct",
        "gpu_equity",
        "gpu_pnl",
        "gpu_return_pct",
        "gpu_drawdown_usd",
        "gpu_drawdown_pct",
        "gpu_day_fall_usd",
        "gpu_day_fall_pct",
        "cpu_gpu_diff_bps",
        "sale_time_stock",
        "new_purchase_time_stock",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for d in all_days:
            c = day_to_cpu.get(d)
            g = day_to_gpu.get(d)
            c_equity = float(c.equity) if c else 0.0
            g_equity = float(g.equity) if g else 0.0
            row = {
                "date": d.isoformat(),
                "cpu_equity": c_equity,
                "cpu_pnl": float(c.pnl) if c else 0.0,
                "cpu_return_pct": float(c.ret_pct) if c else 0.0,
                "cpu_drawdown_usd": float(c.drawdown_usd) if c else 0.0,
                "cpu_drawdown_pct": float(c.drawdown_pct) if c else 0.0,
                "cpu_day_fall_usd": float(c.fell_usd) if c else 0.0,
                "cpu_day_fall_pct": float(c.fell_pct) if c else 0.0,
                "gpu_equity": g_equity,
                "gpu_pnl": float(g.pnl) if g else 0.0,
                "gpu_return_pct": float(g.ret_pct) if g else 0.0,
                "gpu_drawdown_usd": float(g.drawdown_usd) if g else 0.0,
                "gpu_drawdown_pct": float(g.drawdown_pct) if g else 0.0,
                "gpu_day_fall_usd": float(g.fell_usd) if g else 0.0,
                "gpu_day_fall_pct": float(g.fell_pct) if g else 0.0,
                "cpu_gpu_diff_bps": _safe_bps_diff(c_equity, g_equity),
                "sale_time_stock": c.sale_time_stock if c else "no change",
                "new_purchase_time_stock": c.new_purchase_time_stock if c else "no change",
            }
            w.writerow(row)


def _to_jsonable_events(events: list[EventRecord]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        out.append(
            {
                "day": e.day.isoformat(),
                "event_type": e.event_type,
                "symbol": e.symbol,
                "side": e.side,
                "qty": float(e.qty),
                "price": float(e.price),
                "timestamp": e.ts.isoformat() if e.ts else None,
                "reason": e.reason,
                "trigger_price": float(e.trigger_price),
                "trail_stop_price": float(e.trail_stop_price),
            }
        )
    return out


def _to_jsonable_daily(rows: list[DayRecord]) -> list[dict[str, Any]]:
    return [
        {
            "date": r.day.isoformat(),
            "equity": float(r.equity),
            "pnl": float(r.pnl),
            "return_pct": float(r.ret_pct),
            "drawdown_usd": float(r.drawdown_usd),
            "drawdown_pct": float(r.drawdown_pct),
            "fell_usd": float(r.fell_usd),
            "fell_pct": float(r.fell_pct),
            "sale_time_stock": r.sale_time_stock,
            "new_purchase_time_stock": r.new_purchase_time_stock,
        }
        for r in rows
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Intraday replay/verification path for locked profiles. "
            "Designed to verify intraday sell timing for paper_live_style_optimistic semantics."
        )
    )
    parser.add_argument("--env-file", default="", help="Optional .env file for Alpaca credentials.")
    parser.add_argument("--env-override", action="store_true")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument(
        "--strategy-profile",
        choices=[
            "original_composer",
            "trailing12_4_adapt",
            "aggr_adapt_t10_tr2_rv14_b85_m8_M30",
            "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m",
        ],
        default="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
    )
    parser.add_argument(
        "--profit-lock-exec-model",
        choices=["paper_live_style_optimistic", "synthetic", "market_close"],
        default="paper_live_style_optimistic",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--data-feed", default="sip", choices=["sip", "iex"])
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--initial-principal", type=float, default=10_000.0)
    parser.add_argument("--warmup-days", type=int, default=260)
    parser.add_argument("--daily-lookback-days", type=int, default=800)
    parser.add_argument(
        "--anchor-window-start-equity",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "When enabled, defer first in-window rebalance by increasing effective warmup so "
            "the first in-window equity point stays at initial equity."
        ),
    )
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--sell-fee-bps", type=float, default=0.0)
    parser.add_argument(
        "--rebalance-time-ny",
        default="15:56",
        help="Rebalance price/time cutoff in NY timezone for minute-based runs (HH:MM). Default: 15:56.",
    )
    parser.add_argument(
        "--runtime-profit-lock-order-type",
        choices=["close_position", "market_order", "stop_order", "trailing_stop"],
        default="market_order",
        help="Runtime order-type emulation for historical verifier comparison.",
    )
    parser.add_argument(
        "--runtime-stop-price-offset-bps",
        type=float,
        default=2.0,
        help="Stop-order cap offset used in runtime emulation.",
    )
    parser.add_argument(
        "--daily-synthetic-parity",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Force verifier mechanics to align with daily synthetic replay: "
            "daily high/close trigger logic, adjusted close rebalance pricing, "
            "zero rebalance threshold, and no split-holdings adjustments."
        ),
    )
    parser.add_argument(
        "--daily-synthetic-parity-split-adjustment",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Override strict daily-synthetic parity and keep split-adjustment ON while parity is enabled. "
            "Use for parity-like experiments; this is not exact strict parity."
        ),
    )
    parser.add_argument(
        "--paper-live-style-daily-synth-profile",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Convenience profile for Paper/Live-style backtest parity with Daily Synthetic. "
            "Equivalent to enabling daily-synthetic parity + anchor-window-start-equity, "
            "and enforcing a minimum 420-day lookback."
        ),
    )
    parser.add_argument("--reports-dir", default=str(ROOT / "composer_original" / "reports"))
    parser.add_argument("--output-prefix", default="intraday_paper_live_verification")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.start_date > args.end_date:
        raise ValueError("start-date must be <= end-date")
    if abs(float(args.initial_principal) - float(args.initial_equity)) > 1e-9:
        raise ValueError("initial-principal and initial-equity must match for this parity verification run")
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    runtime_profiles = _load_runtime_profiles()
    if args.strategy_profile not in runtime_profiles:
        raise ValueError(f"Unknown locked strategy profile: {args.strategy_profile}")
    profile = runtime_profiles[args.strategy_profile]
    if profile.name in {"aggr_adapt_t10_tr2_rv14_b85_m8_M30", "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"}:
        # Hard guard to preserve the requested baseline semantics.
        if not profile.enable_profit_lock or profile.profit_lock_mode != "trailing":
            raise RuntimeError("Locked aggr profile was modified unexpectedly; aborting replay verification.")

    effective_daily_synth_profile = bool(args.paper_live_style_daily_synth_profile)
    effective_daily_synthetic_parity = bool(args.daily_synthetic_parity or effective_daily_synth_profile)
    effective_daily_synthetic_parity_split_adjustment = bool(args.daily_synthetic_parity_split_adjustment)
    effective_anchor_window_start_equity = bool(args.anchor_window_start_equity or effective_daily_synth_profile)
    effective_daily_lookback_days = max(
        int(args.daily_lookback_days),
        420 if effective_daily_synth_profile else int(args.daily_lookback_days),
    )
    effective_split_adjustment = (not effective_daily_synthetic_parity) or effective_daily_synthetic_parity_split_adjustment

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        args.start_date - timedelta(days=max(effective_daily_lookback_days, int(args.warmup_days) + 20)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)
    daily_ohlc_adjusted = _fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_ohlc_raw = _fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )
    aligned_days, price_history, close_map_by_symbol = _align_daily_close_history(daily_ohlc_adjusted, symbols=symbols)
    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _close_px, high_px in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(high_px)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    _, _, raw_close_map_by_symbol = _align_daily_close_history(daily_ohlc_raw, symbols=symbols)
    split_ratio_by_day_symbol = _build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )
    if not effective_split_adjustment:
        split_ratio_by_day_symbol = None

    baseline_target_by_day = _build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=(
            max(
                int(args.warmup_days),
                (next((i for i, d in enumerate(aligned_days) if d >= args.start_date), 0) + 1),
            )
            if effective_anchor_window_start_equity
            else int(args.warmup_days)
        ),
    )
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]] = {}
    if not effective_daily_synthetic_parity:
        minute_by_day_symbol = _fetch_minute_bars_by_day_symbol(
            loader,
            symbols=symbols,
            start_day=args.start_date,
            end_day=args.end_date,
            feed=alpaca.data_feed,
        )
    rebalance_min_trade_weight_delta = 0.0 if effective_daily_synthetic_parity else float(StrategyConfig().rebalance_threshold)

    cpu_result = _simulate_intraday_verification(
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        baseline_target_by_day=baseline_target_by_day,
        profile=profile,
        start_day=args.start_date,
        end_day=args.end_date,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        profit_lock_exec_model=("synthetic" if args.profit_lock_exec_model == "paper_live_style_optimistic" else args.profit_lock_exec_model),
        runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
        runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
        daily_synthetic_parity=effective_daily_synthetic_parity,
        rebalance_min_trade_weight_delta=rebalance_min_trade_weight_delta,
        rebalance_time_ny=rebalance_time_ny,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol if split_ratio_by_day_symbol else None,
    )

    gpu_backend = "cpu_emulated_fallback"
    try:
        import cupy  # type: ignore  # noqa: F401

        gpu_backend = "cupy_available_cpu_logic"
    except Exception:
        gpu_backend = "cpu_emulated_fallback"
    gpu_result = _simulate_intraday_verification(
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        baseline_target_by_day=baseline_target_by_day,
        profile=profile,
        start_day=args.start_date,
        end_day=args.end_date,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        profit_lock_exec_model=("synthetic" if args.profit_lock_exec_model == "paper_live_style_optimistic" else args.profit_lock_exec_model),
        runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
        runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
        daily_synthetic_parity=effective_daily_synthetic_parity,
        rebalance_min_trade_weight_delta=rebalance_min_trade_weight_delta,
        rebalance_time_ny=rebalance_time_ny,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol if split_ratio_by_day_symbol else None,
    )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    base = (
        f"{args.output_prefix}_{args.strategy_profile}_{args.start_date.isoformat()}_to_{args.end_date.isoformat()}_"
        f"{args.profit_lock_exec_model}"
    )
    summary_path = reports_dir / f"{base}.json"
    daily_csv_path = reports_dir / f"{base}_daily.csv"

    _write_daily_table_csv(daily_csv_path, cpu=cpu_result, gpu=gpu_result)

    cpu_metrics = _report_metrics(cpu_result, initial_equity=float(args.initial_equity))
    gpu_metrics = _report_metrics(gpu_result, initial_equity=float(args.initial_equity))
    parity_diff_bps = _safe_bps_diff(float(cpu_metrics["final_equity"]), float(gpu_metrics["final_equity"]))
    summary = {
        "strategy_profile": args.strategy_profile,
        "profit_lock_exec_model_requested": args.profit_lock_exec_model,
        "profit_lock_exec_model_effective": (
            "synthetic" if args.profit_lock_exec_model == "paper_live_style_optimistic" else args.profit_lock_exec_model
        ),
        "runtime_profit_lock_order_type": str(args.runtime_profit_lock_order_type),
        "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
        "rebalance_time_ny": args.rebalance_time_ny,
        "window": {
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
        },
        "paper_live_style_daily_synth_profile": bool(args.paper_live_style_daily_synth_profile),
        "warmup_days_requested": int(args.warmup_days),
        "warmup_days_effective": (
            max(
                int(args.warmup_days),
                (next((i for i, d in enumerate(aligned_days) if d >= args.start_date), 0) + 1),
            )
            if effective_anchor_window_start_equity
            else int(args.warmup_days)
        ),
        "anchor_window_start_equity": effective_anchor_window_start_equity,
        "daily_lookback_days_requested": int(args.daily_lookback_days),
        "daily_lookback_days_effective": int(effective_daily_lookback_days),
        "initial_principal": float(args.initial_principal),
        "initial_equity": float(args.initial_equity),
        "data_feed": alpaca.data_feed,
        "cpu": cpu_metrics,
        "gpu": {
            **gpu_metrics,
            "backend": gpu_backend,
        },
        "parity": {
            "final_equity_abs_diff": abs(float(cpu_metrics["final_equity"]) - float(gpu_metrics["final_equity"])),
            "final_equity_diff_bps_vs_cpu": parity_diff_bps,
        },
        "outputs": {
            "summary_json": str(summary_path),
            "daily_csv": str(daily_csv_path),
        },
        "daily_synthetic_parity": {
            "enabled": effective_daily_synthetic_parity,
            "strict_mode": bool(effective_daily_synthetic_parity and (not effective_daily_synthetic_parity_split_adjustment)),
            "split_adjustment_override": bool(effective_daily_synthetic_parity_split_adjustment),
            "trigger_model": "daily_high_close" if effective_daily_synthetic_parity else "intraday_minute_high_low",
            "rebalance_price_model": "adjusted_daily_close" if effective_daily_synthetic_parity else "minute_close_raw",
            "rebalance_min_trade_weight_delta": float(rebalance_min_trade_weight_delta),
        },
        "split_adjustment": {
            "enabled": bool(effective_split_adjustment),
            "method": (
                "holdings_scaled_by_adjusted_raw_factor_ratio"
                if effective_split_adjustment
                else "disabled_for_daily_synthetic_parity"
            ),
        },
        "daily_rows": _to_jsonable_daily(cpu_result.daily),
        "events": _to_jsonable_events(cpu_result.events),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
