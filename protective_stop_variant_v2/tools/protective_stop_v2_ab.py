#!/usr/bin/env python3
from __future__ import annotations

"""Second-generation protective-stop research harness.

Standalone path (no existing runtime edits) that evaluates:
- inverse-only protective stop
- volatility-gated arming
- same-day reentry block after protective exit

Engines compared on identical data/execution assumptions:
- v1: runtime_switch_loop target logic
- v2: runtime_switch_loop_v2_controlplane target logic
- fev1: FEV1-0001 fast-entry target logic
"""

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.runtime_switch_loop as rt_v1
from composer_original.tools import intraday_profit_lock_verification as iv
from fast_entry_variant_v1.tools.fast_entry_override_grid import (
    FastEntryCandidate,
    _build_targets_v2_and_fast,
)
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as ab

INVERSE_SET = {"SOXS", "SQQQ", "SPXS", "TMV"}


def _csv_floats(text: str) -> list[float]:
    vals: list[float] = []
    for part in str(text).split(","):
        p = part.strip()
        if not p:
            continue
        vals.append(float(p))
    if not vals:
        raise ValueError(f"No values parsed from: {text!r}")
    return vals


def _window_range_with_10d(end_day: date, label: str) -> tuple[date, date]:
    if label == "10d":
        return end_day - timedelta(days=10), end_day
    return ab._window_range(end_day, label)


def _rv_ann_pct_from_closes(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    rets: list[float] = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        cur = float(closes[i])
        if prev > 0.0:
            rets.append(cur / prev - 1.0)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / len(rets)
    return 100.0 * (var ** 0.5) * (252.0 ** 0.5)


def _simulate_intraday_with_policy(
    *,
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    target_by_day: dict[date, dict[str, float]],
    rebalance_threshold_by_day: dict[date, float],
    profile: iv.LockedProfile,
    start_day: date,
    end_day: date,
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    runtime_profit_lock_order_type: str,
    runtime_stop_price_offset_bps: float,
    rebalance_time_ny: dt_time,
    split_ratio_by_day_symbol: dict[date, dict[str, float]] | None,
    enable_protective_stop: bool,
    protective_stop_pct: float,
    stop_scope: str,
    rv_gate_min_pct: float,
    rv_gate_window: int,
) -> ab.SimulationResult:
    day_to_index = {d: i for i, d in enumerate(aligned_days)}
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    cash = float(initial_equity)
    holdings = {s: 0.0 for s in symbols}
    avg_cost = {s: 0.0 for s in symbols}

    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trail_ratio = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)

    daily_rows: list[ab.DayRecord] = []
    events: list[ab.EventRecord] = []

    peak_equity = float(initial_equity)
    peak_drawdown_usd = 0.0
    prev_equity = float(initial_equity)

    for d in aligned_days:
        if d < start_day or d > end_day:
            continue

        day_idx = day_to_index[d]
        day_minutes = minute_by_day_symbol.get(d, {})

        # Split adjustment across corporate actions.
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
                holdings[sym] = old_qty * ratio
                if avg_cost.get(sym, 0.0) > 0.0:
                    avg_cost[sym] = float(avg_cost[sym]) / ratio

        threshold_pct = iv._threshold_pct_for_day(profile, close_series, day_idx=day_idx)
        threshold_ratio = 1.0 + threshold_pct / 100.0

        sale_entries: list[tuple[datetime | None, str]] = []
        buy_entries: list[tuple[datetime | None, str]] = []
        symbols_blocked_for_rebalance: set[str] = set()

        # A) New v2 policy: inverse-only + RV-gated protective stop + no same-day reentry.
        if enable_protective_stop and protective_stop_pct > 0.0 and day_idx > 0:
            stop_ratio = 1.0 - (float(protective_stop_pct) / 100.0)
            rv_window = max(2, int(rv_gate_window))
            for sym in symbols:
                held_qty = float(holdings.get(sym, 0.0))
                if held_qty <= 0.0:
                    continue

                if stop_scope == "inverse_only" and sym.upper() not in INVERSE_SET:
                    continue

                # No look-ahead: RV from closes strictly before current day.
                rv_ann = 0.0
                hist = close_series.get(sym, [])
                if day_idx >= rv_window + 1:
                    look = hist[day_idx - rv_window : day_idx]
                    rv_ann = _rv_ann_pct_from_closes(look)
                if rv_ann < float(rv_gate_min_pct):
                    continue

                minutes = day_minutes.get(sym, [])
                if not minutes:
                    continue

                entry_px = float(avg_cost.get(sym, 0.0) or 0.0)
                if entry_px <= 0.0 and day_idx > 0:
                    entry_px = float(close_series[sym][day_idx - 1])
                if entry_px <= 0.0:
                    continue

                stop_price = entry_px * stop_ratio
                exit_ts: datetime | None = None
                exit_px = 0.0
                for ts, _o, _h, low_px, close_px in minutes:
                    if low_px <= stop_price:
                        exit_ts = ts
                        exit_px = min(float(stop_price), float(close_px)) * (1.0 - slip)
                        break
                if exit_ts is None or exit_px <= 0.0:
                    continue

                notional = held_qty * exit_px
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym] = 0.0
                avg_cost[sym] = 0.0
                sale_entries.append((exit_ts, sym))
                symbols_blocked_for_rebalance.add(sym)
                events.append(
                    ab.EventRecord(
                        day=d,
                        event_type="protective_stop_sell_v2",
                        symbol=sym,
                        side="sell",
                        qty=float(held_qty),
                        price=float(exit_px),
                        ts=exit_ts,
                        reason="inverse_only_rv_gated_protective_stop",
                        trigger_price=float(stop_price),
                        trail_stop_price=0.0,
                    )
                )

        # B) Existing profit-lock logic.
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
                            exit_price = trigger_price * (1.0 - slip)
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
                            exit_price = trail_stop * (1.0 - slip)
                            break
                else:
                    raise ValueError(f"Unsupported profit lock mode: {profile.profit_lock_mode}")

                if runtime_profit_lock_order_type in {"stop_order", "trailing_stop"} and signal_idx is not None:
                    symbols_blocked_for_rebalance.add(sym)
                    if runtime_profit_lock_order_type == "stop_order":
                        stop_ref = float(trigger_price)
                        if profile.profit_lock_mode == "trailing" and trail_stop_price > 0.0:
                            stop_ref = float(trail_stop_price)
                        cap = float(signal_close_px) * (1.0 - max(0.0, float(runtime_stop_price_offset_bps)) / 10_000.0)
                        stop_order_price = min(stop_ref, cap)
                        if stop_order_price <= 0.0:
                            stop_order_price = max(0.01, float(signal_close_px) * 0.995)
                        exit_ts = None
                        exit_price = 0.0
                        for ts2, _o2, _h2, low2, close2 in minutes[signal_idx:]:
                            if low2 <= stop_order_price:
                                exit_ts = ts2
                                exit_price = float(stop_order_price) * (1.0 - slip)
                                if close2 > 0.0:
                                    exit_price = min(exit_price, float(close2) * (1.0 - slip))
                                break
                    else:
                        trail_ratio_rt = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)
                        high_water_rt = float(minutes[signal_idx][2])
                        exit_ts = None
                        exit_price = 0.0
                        for ts2, _o2, high2, low2, close2 in minutes[signal_idx:]:
                            high_water_rt = max(high_water_rt, float(high2))
                            trail_stop_rt = high_water_rt * (1.0 - trail_ratio_rt)
                            if low2 <= trail_stop_rt:
                                exit_ts = ts2
                                exit_price = float(trail_stop_rt) * (1.0 - slip)
                                if close2 > 0.0:
                                    exit_price = min(exit_price, float(close2) * (1.0 - slip))
                                break

                if exit_ts is None or exit_price <= 0.0:
                    continue

                sell_qty = held_qty
                notional = sell_qty * exit_price
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym] = 0.0
                avg_cost[sym] = 0.0
                sale_entries.append((exit_ts, sym))
                events.append(
                    ab.EventRecord(
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

        # Last prices at/near rebalance time.
        last_prices: dict[str, float] = {}
        rebalance_ts_by_symbol: dict[str, datetime] = {}
        for sym in symbols:
            minutes = day_minutes.get(sym, [])
            if minutes:
                reb_ts, reb_px = iv._minute_close_at_or_before(minutes, rebalance_time_ny)
                if reb_ts is not None and reb_px > 0.0:
                    last_prices[sym] = float(reb_px)
                    rebalance_ts_by_symbol[sym] = reb_ts
                else:
                    last_prices[sym] = float(minutes[-1][4])
                    rebalance_ts_by_symbol[sym] = minutes[-1][0].astimezone(NY)
            else:
                last_prices[sym] = float(close_map_by_symbol[sym][d])
                rebalance_ts_by_symbol[sym] = datetime.combine(d, dt_time(15, 59), tzinfo=NY)

        equity_before = cash + sum(float(holdings[s]) * float(last_prices[s]) for s in symbols)
        target = dict(target_by_day.get(d, {}))

        if target:
            intents = iv.build_rebalance_order_intents(
                equity=float(equity_before),
                target_weights=target,
                current_qty={s: float(holdings[s]) for s in symbols},
                last_prices=last_prices,
                min_trade_weight_delta=float(rebalance_threshold_by_day.get(d, 0.0)),
            )
            if symbols_blocked_for_rebalance:
                intents = [intent for intent in intents if intent.symbol not in symbols_blocked_for_rebalance]

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
                    if holdings[sym] <= 1e-12:
                        holdings[sym] = 0.0
                        avg_cost[sym] = 0.0
                else:
                    exec_px = px * (1.0 + slip)
                    if exec_px <= 0.0:
                        continue
                    max_affordable = cash / exec_px
                    qty = min(qty, max_affordable)
                    if qty <= 0.0:
                        continue
                    old_qty = float(holdings.get(sym, 0.0))
                    old_cost = float(avg_cost.get(sym, 0.0))
                    cash -= qty * exec_px
                    new_qty = old_qty + qty
                    holdings[sym] = new_qty
                    if new_qty > 0.0:
                        avg_cost[sym] = (old_qty * old_cost + qty * exec_px) / new_qty
                    else:
                        avg_cost[sym] = 0.0
                    buy_entries.append((close_ts, sym))

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
            ab.DayRecord(
                day=d,
                equity=float(equity_after),
                pnl=float(pnl),
                ret_pct=float(ret_pct),
                drawdown_usd=float(drawdown_usd),
                drawdown_pct=float(drawdown_pct),
                fell_usd=float(fell_usd),
                fell_pct=float(fell_pct),
                sale_time_stock=ab._build_action_label(sale_entries),
                new_purchase_time_stock=ab._build_action_label(buy_entries),
            )
        )

    final_equity = float(daily_rows[-1].equity) if daily_rows else float(initial_equity)
    total_return_pct = 100.0 * (final_equity / float(initial_equity) - 1.0) if initial_equity > 0 else 0.0
    max_drawdown_pct = 100.0 * peak_drawdown_usd / peak_equity if peak_equity > 0 else 0.0

    return ab.SimulationResult(
        daily=daily_rows,
        events=events,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_usd=peak_drawdown_usd,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Protective-stop V2 research harness (standalone).")
    p.add_argument("--env-file", default="")
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
    p.add_argument("--runtime-profit-lock-order-type", choices=["close_position", "market_order", "stop_order", "trailing_stop"], default="market_order")
    p.add_argument("--runtime-stop-price-offset-bps", type=float, default=2.0)

    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    p.add_argument("--fev1-fast-signal-threshold", type=float, default=0.66)
    p.add_argument("--fev1-fast-trend-gap-pct", type=float, default=6.0)
    p.add_argument("--fev1-fast-inverse-min-weight", type=float, default=0.80)

    p.add_argument("--engines", default="v1,v2,fev1")

    # New policy params.
    p.add_argument("--stop-scope", choices=["inverse_only", "all"], default="inverse_only")
    p.add_argument("--protective-stop-pcts", default="0,3,4,5,6,7")
    p.add_argument("--rv-gate-min-pcts", default="0,40,60,80")
    p.add_argument("--rv-gate-window", type=int, default=20)

    p.add_argument("--reports-dir", default=str(ROOT / "protective_stop_variant_v2" / "reports"))
    p.add_argument("--output-prefix", default="protective_stop_v2_ab")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.env_file:
        loaded = ab._load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(f"Unknown strategy profile: {args.strategy_profile}")

    windows = [w.strip() for w in str(args.windows).split(",") if w.strip()]
    valid_windows = set(ab.WINDOW_TO_DAYS.keys()) | {"10d"}
    for w in windows:
        if w not in valid_windows:
            raise ValueError(f"Unsupported window: {w}")

    engines = [e.strip().lower() for e in str(args.engines).split(",") if e.strip()]
    for e in engines:
        if e not in {"v1", "v2", "fev1"}:
            raise ValueError(f"Unsupported engine: {e}")

    stop_pcts = sorted(set(float(x) for x in _csv_floats(args.protective_stop_pcts)))
    rv_gates = sorted(set(float(x) for x in _csv_floats(args.rv_gate_min_pcts)))

    max_days = max((10 if w == "10d" else int(ab.WINDOW_TO_DAYS[w])) for w in windows)
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

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    # Target streams.
    (
        v1_targets,
        v1_thresholds,
        _v1_variants,
        v2_targets,
        v2_thresholds,
        _v2_variants,
    ) = ab._build_switch_targets_and_thresholds(
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

    fev1 = FastEntryCandidate(
        cid="FEV1-0001",
        fast_signal_threshold=float(args.fev1_fast_signal_threshold),
        fast_trend_gap_pct=float(args.fev1_fast_trend_gap_pct),
        fast_inverse_min_weight=float(args.fev1_fast_inverse_min_weight),
    )
    (
        _a,
        _b,
        _c,
        _d,
        fev1_targets,
        fev1_thresholds,
        _e,
        _f,
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
        fast=fev1,
    )

    engine_to_targets = {
        "v1": (v1_targets, v1_thresholds),
        "v2": (v2_targets, v2_thresholds),
        "fev1": (fev1_targets, fev1_thresholds),
    }

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

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

    rebalance_time_ny = ab._parse_hhmm(args.rebalance_time_ny)

    rows: list[dict[str, Any]] = []
    baseline_key = {}

    for engine in engines:
        target_by_day, threshold_by_day = engine_to_targets[engine]
        for stop_pct in stop_pcts:
            for rv_gate in rv_gates:
                for window in windows:
                    start_day, end_day = _window_range_with_10d(args.end_date, window)
                    sim = _simulate_intraday_with_policy(
                        symbols=symbols,
                        aligned_days=aligned_days,
                        price_history=price_history,
                        close_map_by_symbol=close_map_by_symbol,
                        minute_by_day_symbol=minute_by_day_symbol,
                        target_by_day=target_by_day,
                        rebalance_threshold_by_day=threshold_by_day,
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
                        enable_protective_stop=(stop_pct > 0.0),
                        protective_stop_pct=float(stop_pct),
                        stop_scope=str(args.stop_scope),
                        rv_gate_min_pct=float(rv_gate),
                        rv_gate_window=int(args.rv_gate_window),
                    )

                    row = {
                        "engine": engine,
                        "window": window,
                        "period": f"{start_day.isoformat()} to {end_day.isoformat()}",
                        "stop_scope": str(args.stop_scope),
                        "protective_stop_pct": float(stop_pct),
                        "rv_gate_min_pct": float(rv_gate),
                        "rv_gate_window": int(args.rv_gate_window),
                        "initial_equity": float(args.initial_equity),
                        "final_equity": float(sim.final_equity),
                        "return_pct": float(sim.total_return_pct),
                        "maxdd_pct": float(sim.max_drawdown_pct),
                        "maxdd_usd": float(sim.max_drawdown_usd),
                        "events": int(len(sim.events)),
                    }
                    rows.append(row)

                    if abs(float(stop_pct)) <= 1e-12 and abs(float(rv_gate)) <= 1e-12:
                        baseline_key[(engine, window)] = row

    # Deltas vs per-engine/window baseline (stop=0, rv_gate=0).
    for r in rows:
        base = baseline_key.get((str(r["engine"]), str(r["window"])))
        if base is None:
            r["delta_return_vs_base_pct"] = 0.0
            r["delta_maxdd_vs_base_pct"] = 0.0
            r["delta_equity_vs_base_usd"] = 0.0
        else:
            r["delta_return_vs_base_pct"] = float(r["return_pct"] - base["return_pct"])
            r["delta_maxdd_vs_base_pct"] = float(r["maxdd_pct"] - base["maxdd_pct"])
            r["delta_equity_vs_base_usd"] = float(r["final_equity"] - base["final_equity"])

    # Candidate ranking by engine/param set aggregated over all windows.
    agg_by_cfg: dict[tuple[str, float, float], dict[str, Any]] = {}
    for r in rows:
        sp = float(r["protective_stop_pct"])
        rv = float(r["rv_gate_min_pct"])
        if sp <= 0.0:
            continue
        key = (str(r["engine"]), sp, rv)
        agg = agg_by_cfg.setdefault(
            key,
            {
                "engine": str(r["engine"]),
                "stop_scope": str(args.stop_scope),
                "protective_stop_pct": sp,
                "rv_gate_min_pct": rv,
                "rv_gate_window": int(args.rv_gate_window),
                "windows": 0,
                "sum_delta_return": 0.0,
                "sum_delta_maxdd": 0.0,
                "sum_delta_equity": 0.0,
                "ret_improve_windows": 0,
                "dd_improve_windows": 0,
                "both_improve_windows": 0,
            },
        )
        agg["windows"] += 1
        dr = float(r["delta_return_vs_base_pct"])
        dd = float(r["delta_maxdd_vs_base_pct"])
        deq = float(r["delta_equity_vs_base_usd"])
        agg["sum_delta_return"] += dr
        agg["sum_delta_maxdd"] += dd
        agg["sum_delta_equity"] += deq
        if dr > 0:
            agg["ret_improve_windows"] += 1
        if dd < 0:
            agg["dd_improve_windows"] += 1
        if dr > 0 and dd < 0:
            agg["both_improve_windows"] += 1

    ranked: list[dict[str, Any]] = []
    for _k, a in agg_by_cfg.items():
        n = max(1, int(a["windows"]))
        avg_ret = float(a["sum_delta_return"]) / n
        avg_dd = float(a["sum_delta_maxdd"]) / n
        avg_eq = float(a["sum_delta_equity"]) / n
        # Better score: higher return/equity, lower dd.
        score = avg_ret - (0.40 * avg_dd) + (avg_eq / 1000.0)
        ranked.append(
            {
                **a,
                "avg_delta_return_vs_base_pct": avg_ret,
                "avg_delta_maxdd_vs_base_pct": avg_dd,
                "avg_delta_equity_vs_base_usd": avg_eq,
                "score": float(score),
            }
        )

    ranked.sort(key=lambda x: float(x["score"]), reverse=True)

    # Strict acceptance: must improve both return and DD on average.
    strict = [
        r for r in ranked
        if float(r["avg_delta_return_vs_base_pct"]) > 0.0 and float(r["avg_delta_maxdd_vs_base_pct"]) < 0.0
    ]

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.end_date.strftime("%Y%m%d")

    details_csv = reports_dir / f"{args.output_prefix}_{stamp}_details.csv"
    ranked_csv = reports_dir / f"{args.output_prefix}_{stamp}_ranked.csv"
    summary_json = reports_dir / f"{args.output_prefix}_{stamp}_summary.json"

    if rows:
        with details_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

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
        "engines": engines,
        "initial_equity": float(args.initial_equity),
        "stop_scope": str(args.stop_scope),
        "protective_stop_pcts": stop_pcts,
        "rv_gate_min_pcts": rv_gates,
        "rv_gate_window": int(args.rv_gate_window),
        "reports": {
            "details_csv": str(details_csv),
            "ranked_csv": str(ranked_csv),
            "summary_json": str(summary_json),
        },
        "top10_ranked": ranked[:10],
        "strict_acceptance_candidates": strict,
    }
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
