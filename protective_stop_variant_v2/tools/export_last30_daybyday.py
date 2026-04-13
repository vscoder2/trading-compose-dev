#!/usr/bin/env python3
from __future__ import annotations

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
from protective_stop_variant_v2.tools.protective_stop_v2_ab import INVERSE_SET, _rv_ann_pct_from_closes


def _ticker_from_holdings(holdings: dict[str, float], prices: dict[str, float]) -> str:
    best_sym = ""
    best_val = 0.0
    for s, q in holdings.items():
        qf = float(q)
        if qf <= 0.0:
            continue
        v = qf * float(prices.get(s, 0.0))
        if v > best_val:
            best_val = v
            best_sym = s
    return best_sym or "CASH"


def _build_action_label(entries: list[tuple[datetime | None, str]]) -> str:
    if not entries:
        return "no change"
    out = []
    for ts, sym in entries:
        if ts is None:
            out.append(f"no change | {sym}")
        else:
            out.append(f"{ts.astimezone(NY).strftime('%H:%M')} | {sym}")
    return "; ".join(out)


def _window_last_n_trading_days(aligned_days: list[date], n: int, end_day: date) -> tuple[date, date]:
    days = [d for d in aligned_days if d <= end_day]
    if not days:
        raise RuntimeError("No aligned trading days available")
    take = days[-n:] if len(days) >= n else days
    return take[0], take[-1]


def _simulate_with_table(
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
) -> list[dict[str, Any]]:
    day_to_index = {d: i for i, d in enumerate(aligned_days)}
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    cash = float(initial_equity)
    holdings = {s: 0.0 for s in symbols}
    avg_cost = {s: 0.0 for s in symbols}

    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trail_ratio = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)

    peak_equity = float(initial_equity)
    prev_equity = float(initial_equity)

    table_rows: list[dict[str, Any]] = []

    for d in aligned_days:
        if d < start_day or d > end_day:
            continue
        day_idx = day_to_index[d]
        day_minutes = minute_by_day_symbol.get(d, {})

        # start prices snapshot for start ticker
        start_prices = {}
        for sym in symbols:
            mins = day_minutes.get(sym, [])
            if mins:
                start_prices[sym] = float(mins[0][1])
            else:
                start_prices[sym] = float(close_map_by_symbol[sym][d])

        # split adjustment
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

        day_start_equity = float(prev_equity)
        start_ticker = _ticker_from_holdings(holdings, start_prices)

        threshold_pct = iv._threshold_pct_for_day(profile, close_series, day_idx=day_idx)
        threshold_ratio = 1.0 + threshold_pct / 100.0

        sale_entries: list[tuple[datetime | None, str]] = []
        buy_entries: list[tuple[datetime | None, str]] = []
        symbols_blocked_for_rebalance: set[str] = set()

        if enable_protective_stop and protective_stop_pct > 0.0 and day_idx > 0:
            stop_ratio = 1.0 - (float(protective_stop_pct) / 100.0)
            rv_window = max(2, int(rv_gate_window))
            for sym in symbols:
                held_qty = float(holdings.get(sym, 0.0))
                if held_qty <= 0.0:
                    continue
                if stop_scope == "inverse_only" and sym.upper() not in INVERSE_SET:
                    continue

                rv_ann = 0.0
                hist = close_series.get(sym, [])
                if day_idx >= rv_window + 1:
                    look = hist[day_idx - rv_window : day_idx]
                    rv_ann = _rv_ann_pct_from_closes(look)
                if rv_ann < float(rv_gate_min_pct):
                    continue

                mins = day_minutes.get(sym, [])
                if not mins:
                    continue
                entry_px = float(avg_cost.get(sym, 0.0) or 0.0)
                if entry_px <= 0.0 and day_idx > 0:
                    entry_px = float(close_series[sym][day_idx - 1])
                if entry_px <= 0.0:
                    continue

                stop_price = entry_px * stop_ratio
                exit_ts = None
                exit_px = 0.0
                for ts, _o, _h, low_px, close_px in mins:
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

        if profile.enable_profit_lock and day_idx > 0:
            for sym in symbols:
                held_qty = float(holdings.get(sym, 0.0))
                if held_qty <= 0.0:
                    continue
                prev_close = float(close_series[sym][day_idx - 1])
                if prev_close <= 0.0:
                    continue

                trigger_price = prev_close * threshold_ratio
                exit_ts = None
                exit_price = 0.0
                trail_stop_price = 0.0
                mins = day_minutes.get(sym, [])
                if not mins:
                    continue

                signal_idx = None
                signal_close_px = 0.0
                if profile.profit_lock_mode == "fixed":
                    for idx_m, (ts, _o, high_px, _low, close_px) in enumerate(mins):
                        if high_px >= trigger_price:
                            exit_ts = ts
                            signal_idx = idx_m
                            signal_close_px = float(close_px)
                            exit_price = trigger_price * (1.0 - slip)
                            break
                else:
                    triggered = False
                    high_water = 0.0
                    for idx_m, (ts, _o, high_px, low_px, close_px) in enumerate(mins):
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

                if runtime_profit_lock_order_type in {"stop_order", "trailing_stop"} and signal_idx is not None:
                    symbols_blocked_for_rebalance.add(sym)
                    if runtime_profit_lock_order_type == "stop_order":
                        stop_ref = float(trigger_price)
                        if trail_stop_price > 0.0:
                            stop_ref = float(trail_stop_price)
                        cap = float(signal_close_px) * (1.0 - max(0.0, float(runtime_stop_price_offset_bps)) / 10_000.0)
                        stop_order_price = min(stop_ref, cap)
                        if stop_order_price <= 0.0:
                            stop_order_price = max(0.01, float(signal_close_px) * 0.995)
                        exit_ts = None
                        exit_price = 0.0
                        for ts2, _o2, _h2, low2, close2 in mins[signal_idx:]:
                            if low2 <= stop_order_price:
                                exit_ts = ts2
                                exit_price = float(stop_order_price) * (1.0 - slip)
                                if close2 > 0.0:
                                    exit_price = min(exit_price, float(close2) * (1.0 - slip))
                                break
                    else:
                        trail_ratio_rt = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)
                        high_water_rt = float(mins[signal_idx][2])
                        exit_ts = None
                        exit_price = 0.0
                        for ts2, _o2, high2, low2, close2 in mins[signal_idx:]:
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

                notional = held_qty * exit_price
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym] = 0.0
                avg_cost[sym] = 0.0
                sale_entries.append((exit_ts, sym))

        last_prices = {}
        rebalance_ts_by_symbol = {}
        for sym in symbols:
            mins = day_minutes.get(sym, [])
            if mins:
                reb_ts, reb_px = iv._minute_close_at_or_before(mins, rebalance_time_ny)
                if reb_ts is not None and reb_px > 0.0:
                    last_prices[sym] = float(reb_px)
                    rebalance_ts_by_symbol[sym] = reb_ts
                else:
                    last_prices[sym] = float(mins[-1][4])
                    rebalance_ts_by_symbol[sym] = mins[-1][0].astimezone(NY)
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
                intents = [it for it in intents if it.symbol not in symbols_blocked_for_rebalance]

            latest = [rebalance_ts_by_symbol.get(s) for s in symbols if rebalance_ts_by_symbol.get(s) is not None]
            close_ts = max(latest) if latest else None
            for it in intents:
                sym = it.symbol
                qty = float(it.qty)
                px = float(last_prices.get(sym, 0.0))
                if qty <= 0.0 or px <= 0.0:
                    continue
                if it.side == "sell":
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
        drawdown_pct = 100.0 * max(0.0, peak_equity - equity_after) / peak_equity if peak_equity > 0 else 0.0
        pnl = equity_after - prev_equity
        ret_pct = 100.0 * (equity_after / prev_equity - 1.0) if prev_equity > 0 else 0.0
        prev_equity = equity_after

        end_ticker = _ticker_from_holdings(holdings, last_prices)

        switch_time = "no change"
        switch_sym = "no change"
        if sale_entries:
            # first sale only for compact view
            ts, sym = sale_entries[0]
            switch_time = ts.astimezone(NY).strftime("%H:%M") if ts else "no change"
            switch_sym = sym

        buy_time = "no change"
        buy_ticker = "no change"
        if buy_entries:
            ts, sym = buy_entries[0]
            buy_time = ts.astimezone(NY).strftime("%H:%M") if ts else "no change"
            buy_ticker = sym

        table_rows.append(
            {
                "Date": d.isoformat(),
                "Day Start Equity": round(float(day_start_equity), 2),
                "Start Ticker": start_ticker,
                "Intraday Switch Time": switch_time,
                "Intraday Switch": switch_sym,
                "Buy Time": buy_time,
                "Buy Ticker": buy_ticker,
                "End-of-Day Ticker": end_ticker,
                "Day End Equity": round(float(equity_after), 2),
                "PnL ($)": round(float(pnl), 2),
                "Return %": round(float(ret_pct), 2),
                "Drawdown %": round(float(drawdown_pct), 2),
            }
        )

    return table_rows


def _parse_hhmm(value: str) -> dt_time:
    h, m = str(value).split(":", 1)
    return dt_time(int(h), int(m))


def _build_targets_for_engine(
    *,
    engine: str,
    aligned_days: list[date],
    symbols: list[str],
    close_series: dict[str, list[float]],
    baseline_target_by_day: dict[date, dict[str, float]],
    rebalance_threshold: float,
    controlplane_threshold_cap: float,
    controlplane_hysteresis_enter: float,
    controlplane_hysteresis_exit: float,
    controlplane_hysteresis_enter_days: int,
    controlplane_hysteresis_exit_days: int,
) -> tuple[dict[date, dict[str, float]], dict[date, float]]:
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
        base_rebalance_threshold=float(rebalance_threshold),
        controlplane_threshold_cap=float(controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(controlplane_hysteresis_exit_days),
    )
    if engine == "v1":
        return v1_targets, v1_thresholds
    if engine == "v2":
        return v2_targets, v2_thresholds
    if engine == "fev1":
        fev1 = FastEntryCandidate(
            cid="FEV1-0001",
            fast_signal_threshold=0.66,
            fast_trend_gap_pct=6.0,
            fast_inverse_min_weight=0.80,
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
            base_rebalance_threshold=float(rebalance_threshold),
            controlplane_threshold_cap=float(controlplane_threshold_cap),
            controlplane_hysteresis_enter=float(controlplane_hysteresis_enter),
            controlplane_hysteresis_exit=float(controlplane_hysteresis_exit),
            controlplane_hysteresis_enter_days=int(controlplane_hysteresis_enter_days),
            controlplane_hysteresis_exit_days=int(controlplane_hysteresis_exit_days),
            fast=fev1,
        )
        return fev1_targets, fev1_thresholds
    raise ValueError(engine)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default="")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--rebalance-time-ny", default="15:55")
    p.add_argument("--runtime-profit-lock-order-type", choices=["close_position", "market_order", "stop_order", "trailing_stop"], default="market_order")
    p.add_argument("--runtime-stop-price-offset-bps", type=float, default=2.0)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--daily-lookback-days", type=int, default=800)

    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    p.add_argument("--reports-dir", default=str(ROOT / "protective_stop_variant_v2" / "reports"))
    args = p.parse_args(argv)

    if args.env_file:
        ab._load_env_file(args.env_file, override=bool(args.env_override))

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(args.strategy_profile)

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(args.end_date - timedelta(days=max(int(args.daily_lookback_days), int(args.warmup_days) + 60)), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

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

    baseline_target_by_day = iv._build_baseline_target_by_day(price_history=price_history, initial_equity=float(args.initial_equity), warmup_days=int(args.warmup_days))
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    start_day, end_day = _window_last_n_trading_days(aligned_days, 30, args.end_date)

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(loader, symbols=symbols, start_day=start_day, end_day=end_day, feed=alpaca.data_feed)

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

    # best configs from strict acceptance in run1
    configs = [
        {"variant": "v1_best", "engine": "v1", "stop_pct": 3.0, "rv_gate": 0.0},
        {"variant": "v2_best", "engine": "v2", "stop_pct": 5.0, "rv_gate": 80.0},
        {"variant": "fev1_best", "engine": "fev1", "stop_pct": 5.0, "rv_gate": 80.0},
    ]

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = end_day.isoformat()

    printed = {}

    for cfg in configs:
        target_by_day, threshold_by_day = _build_targets_for_engine(
            engine=cfg["engine"],
            aligned_days=aligned_days,
            symbols=symbols,
            close_series=close_series,
            baseline_target_by_day=baseline_target_by_day,
            rebalance_threshold=float(args.rebalance_threshold),
            controlplane_threshold_cap=float(args.controlplane_threshold_cap),
            controlplane_hysteresis_enter=float(args.controlplane_hysteresis_enter),
            controlplane_hysteresis_exit=float(args.controlplane_hysteresis_exit),
            controlplane_hysteresis_enter_days=int(args.controlplane_hysteresis_enter_days),
            controlplane_hysteresis_exit_days=int(args.controlplane_hysteresis_exit_days),
        )

        rows = _simulate_with_table(
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
            rebalance_time_ny=_parse_hhmm(args.rebalance_time_ny),
            split_ratio_by_day_symbol=split_ratio,
            enable_protective_stop=(cfg["stop_pct"] > 0),
            protective_stop_pct=float(cfg["stop_pct"]),
            stop_scope="inverse_only",
            rv_gate_min_pct=float(cfg["rv_gate"]),
            rv_gate_window=20,
        )

        out_csv = reports_dir / f"{cfg['variant']}_last30_10k_{start_day.isoformat()}_to_{end_day.isoformat()}.csv"
        if rows:
            with out_csv.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        printed[cfg["variant"]] = {
            "engine": cfg["engine"],
            "stop_pct": cfg["stop_pct"],
            "rv_gate": cfg["rv_gate"],
            "csv": str(out_csv),
            "rows": rows,
        }

    print(json.dumps({
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
        "initial_equity": float(args.initial_equity),
        "variants": printed,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
