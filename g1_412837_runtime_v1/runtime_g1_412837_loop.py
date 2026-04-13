#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from csp47_overlay_research_v1.tools.sweep_csp47_overlays import (
    OverlayCandidate,
    _build_scaled_profile,
    _overlay_targets,
)
from protective_stop_variant_v2.tools.export_last30_daybyday import _build_targets_for_engine
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from soxl_growth.db import StateStore
from soxl_growth.execution.broker import AlpacaBroker
from soxl_growth.execution.orders import build_rebalance_order_intents
from soxl_growth.logging_setup import configure_logging, get_logger
import switch_runtime_v1.runtime_switch_loop as base_rt

logger = get_logger(__name__)


@dataclass(frozen=True)
class G1Params:
    # Locked candidate parameters from research (do not change unless intentionally overridden by CLI).
    bias: float = 6.787898344285149
    a20: float = 0.0
    a60: float = -0.09181296789807147
    b_rv: float = 0.09407960349884371
    b_dd: float = 0.13271417559907764
    temp: float = 0.03
    floor: float = 0.006844050593517129
    ceil: float = 1.0


def _rolling_features(close: list[float]) -> tuple[list[float], list[float], list[float], list[float]]:
    """Build day-t features from data up to t-1 to avoid lookahead."""
    n = len(close)
    if n == 0:
        return [], [], [], []
    ret = [0.0] * n
    for i in range(1, n):
        prev = float(close[i - 1])
        cur = float(close[i])
        ret[i] = (cur / prev - 1.0) if prev > 0 else 0.0

    mom20 = [0.0] * n
    mom60 = [0.0] * n
    rv20 = [0.0] * n
    dd60 = [0.0] * n

    for i in range(n):
        p = i - 1
        if p >= 20 and close[p - 20] > 0:
            mom20[i] = (float(close[p]) / float(close[p - 20]) - 1.0) * 100.0
        if p >= 60 and close[p - 60] > 0:
            mom60[i] = (float(close[p]) / float(close[p - 60]) - 1.0) * 100.0
        if p >= 20:
            win = ret[p - 19 : p + 1]
            if win:
                mu = sum(win) / len(win)
                var = sum((x - mu) ** 2 for x in win) / len(win)
                rv20[i] = (var**0.5) * 100.0
        if p >= 60:
            win = close[p - 59 : p + 1]
            peak = max(float(x) for x in win) if win else 0.0
            cur = float(win[-1]) if win else 0.0
            dd60[i] = ((peak - cur) / peak * 100.0) if peak > 0 else 0.0

    return mom20, mom60, rv20, dd60


def _weight_for_day(idx: int, mom20: list[float], mom60: list[float], rv20: list[float], dd60: list[float], p: G1Params) -> float:
    z = float(p.bias)
    z += float(p.a20) * float(mom20[idx])
    z += float(p.a60) * float(mom60[idx])
    z -= float(p.b_rv) * float(rv20[idx])
    z -= float(p.b_dd) * float(dd60[idx])

    temp = max(1e-6, float(p.temp))
    try:
        s = 1.0 / (1.0 + math.exp(-(z / temp)))
    except OverflowError:
        s = 0.0 if z < 0 else 1.0
    w = float(p.floor) + (float(p.ceil) - float(p.floor)) * s
    return min(1.0, max(0.0, w))


def _pick_top_symbol(target: dict[str, float]) -> str:
    if not target:
        return "CASH"
    return str(max(target.items(), key=lambda kv: float(kv[1]))[0])


def _compose_target(c_sym: str, o_sym: str, weight_c: float) -> dict[str, float]:
    # Normalize non-tradable CASH combinations into a valid target map.
    if c_sym == "CASH" and o_sym == "CASH":
        return {}
    if c_sym == "CASH":
        return {o_sym: 1.0}
    if o_sym == "CASH":
        return {c_sym: 1.0}
    if c_sym == o_sym:
        return {c_sym: 1.0}
    wc = min(1.0, max(0.0, float(weight_c)))
    return {c_sym: wc, o_sym: 1.0 - wc}


def _build_g1_target_for_today(
    *,
    daily_ohlc: dict[str, list[tuple[date, float, float]]],
    symbols: list[str],
    today: date,
    profile: iv.LockedProfile,
    params: G1Params,
    warmup_days: int,
    rebalance_threshold: float,
    controlplane_threshold_cap: float,
    controlplane_hysteresis_enter: float,
    controlplane_hysteresis_exit: float,
    controlplane_hysteresis_enter_days: int,
    controlplane_hysteresis_exit_days: int,
) -> tuple[dict[str, float], float, dict[str, Any]]:
    # Build aligned daily close history for target generation.
    aligned_days, price_history, _ = iv._align_daily_close_history(daily_ohlc, symbols=symbols)
    if not aligned_days:
        return {}, float(profile.profit_lock_threshold_pct), {"reason": "no_aligned_days"}

    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=10000.0,
        warmup_days=int(warmup_days),
    )

    c_targets, _ = _build_targets_for_engine(
        engine="fev1",
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=float(rebalance_threshold),
        controlplane_threshold_cap=float(controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(controlplane_hysteresis_exit_days),
    )

    # OV branch (dd-window 20, defensive SOXS) to match research family.
    ov_candidate = OverlayCandidate(
        shock_drop_pct=6.0,
        shock_hold_days=1,
        dd_trigger_pct=0.0,
        dd_window_days=20,
        reentry_pos_days=1,
        defensive_symbol="SOXS",
    )
    ov_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=c_targets,
        candidate=ov_candidate,
    )

    # Use latest aligned day <= today.
    day = None
    for d in reversed(aligned_days):
        if d <= today:
            day = d
            break
    if day is None:
        day = aligned_days[-1]

    idx = aligned_days.index(day)
    soxl_closes = close_series.get("SOXL", [])
    mom20, mom60, rv20, dd60 = _rolling_features(soxl_closes)
    weight_c = _weight_for_day(idx, mom20, mom60, rv20, dd60, params)

    c_target = dict(c_targets.get(day, {}))
    o_target = dict(ov_targets.get(day, {}))
    c_sym = _pick_top_symbol(c_target)
    o_sym = _pick_top_symbol(o_target)
    final_target = _compose_target(c_sym, o_sym, weight_c)

    daily_closes = {s: [close for _d, close, _h in daily_ohlc.get(s, [])] for s in symbols}
    threshold_pct = base_rt._current_threshold_pct(profile, daily_closes)

    diag = {
        "aligned_day": day.isoformat(),
        "weight_c": float(weight_c),
        "c_symbol": c_sym,
        "ov_symbol": o_sym,
        "final_target": final_target,
    }
    return final_target, float(threshold_pct), diag


def _run_loop(args: argparse.Namespace) -> int:
    if args.env_file:
        loaded = base_rt._load_env_file(args.env_file, override=bool(args.env_override))
        logger.info("Loaded %d env vars from %s (override=%s)", loaded, args.env_file, bool(args.env_override))

    profile = base_rt.PROFILES[args.strategy_profile]
    # Match research locked profile scaling (C_sp4.7_rv75_tr1.10_th1.20 family).
    profile_locked = _build_scaled_profile(
        iv.LockedProfile(
            name=profile.name,
            enable_profit_lock=profile.enable_profit_lock,
            profit_lock_mode=profile.profit_lock_mode,
            profit_lock_threshold_pct=profile.profit_lock_threshold_pct,
            profit_lock_trail_pct=profile.profit_lock_trail_pct,
            profit_lock_adaptive_threshold=profile.profit_lock_adaptive_threshold,
            profit_lock_adaptive_symbol=profile.profit_lock_adaptive_symbol,
            profit_lock_adaptive_rv_window=profile.profit_lock_adaptive_rv_window,
            profit_lock_adaptive_rv_baseline_pct=profile.profit_lock_adaptive_rv_baseline_pct,
            profit_lock_adaptive_min_threshold_pct=profile.profit_lock_adaptive_min_threshold_pct,
            profit_lock_adaptive_max_threshold_pct=profile.profit_lock_adaptive_max_threshold_pct,
        ),
        trail_scale=float(args.trail_scale),
        threshold_scale=float(args.threshold_scale),
    )
    # Convert back to StrategyProfile for runtime helper compatibility.
    profile_runtime = base_rt.StrategyProfile(
        name=f"{args.variant_id}_{profile_locked.name}",
        enable_profit_lock=profile_locked.enable_profit_lock,
        profit_lock_mode=profile_locked.profit_lock_mode,
        profit_lock_threshold_pct=profile_locked.profit_lock_threshold_pct,
        profit_lock_trail_pct=profile_locked.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=profile_locked.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=profile_locked.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=profile_locked.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=profile_locked.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=profile_locked.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=profile_locked.profit_lock_adaptive_max_threshold_pct,
        intraday_profit_lock_check_minutes=profile.intraday_profit_lock_check_minutes,
    )

    params = G1Params(
        bias=float(args.g1_bias),
        a20=float(args.g1_a20),
        a60=float(args.g1_a60),
        b_rv=float(args.g1_b_rv),
        b_dd=float(args.g1_b_dd),
        temp=float(args.g1_temp),
        floor=float(args.g1_floor),
        ceil=float(args.g1_ceil),
    )

    paper = args.mode == "paper"
    alpaca = AlpacaConfig.from_env(paper=paper, data_feed=args.data_feed)

    store = StateStore(args.state_db)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=alpaca.paper)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)
    eval_time = base_rt._parse_hhmm(args.eval_time)

    logger.info(
        (
            "Starting G1 runtime mode=%s profile=%s execute_orders=%s eval_time=%s "
            "profit_lock_order_type=%s rebalance_order_type=%s variant=%s"
        ),
        args.mode,
        profile_runtime.name,
        bool(args.execute_orders),
        args.eval_time,
        args.profit_lock_order_type,
        args.rebalance_order_type,
        args.variant_id,
    )

    while True:
        clock = broker.get_clock()
        now = clock["timestamp"].astimezone(NY)

        if not clock["is_open"]:
            logger.info("Market closed. next_open=%s", clock["next_open"])
            if args.run_once:
                return 0
            time.sleep(max(5, int(args.loop_sleep_seconds)))
            continue

        today = now.date()
        today_iso = today.isoformat()
        open_time, _close_time = base_rt._market_session_window(broker, now)
        current_time = dt_time(hour=now.hour, minute=now.minute)
        intraday_check_minutes = max(0, int(profile_runtime.intraday_profit_lock_check_minutes))

        if (
            profile_runtime.enable_profit_lock
            and intraday_check_minutes > 0
            and now >= open_time
            and current_time < eval_time
        ):
            slot_idx = int((now.hour * 60 + now.minute) // intraday_check_minutes)
            slot_key = f"{today_iso}:{slot_idx}"
            last_slot_key = str(store.get("g1_switch_intraday_profit_lock_last_slot", ""))
            if slot_key != last_slot_key:
                lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
                daily_ohlc = base_rt._fetch_daily_ohlc(
                    loader,
                    symbols=symbols,
                    start=lookback_start,
                    end=now,
                    feed=alpaca.data_feed,
                )
                daily_closes = {s: [close for _d, close, _h in rows] for s, rows in daily_ohlc.items()}
                threshold_pct = base_rt._current_threshold_pct(profile_runtime, daily_closes)

                last_prices, day_highs, latest_ts = base_rt._fetch_intraday_day_stats(
                    loader,
                    symbols=symbols,
                    session_open=open_time,
                    now=now,
                    feed=alpaca.data_feed,
                )
                stale_minutes = 0
                if latest_ts:
                    freshest = max(latest_ts.values()).astimezone(NY)
                    stale_minutes = int(max(0, (now - freshest).total_seconds() // 60))

                if len(last_prices) >= 2 and stale_minutes <= int(args.stale_data_threshold_minutes):
                    positions = broker.list_positions()
                    intraday_signals = base_rt._build_profit_lock_signals(
                        profile=profile_runtime,
                        positions=positions,
                        daily_ohlc=daily_ohlc,
                        last_prices=last_prices,
                        day_highs=day_highs,
                        threshold_pct=threshold_pct,
                        today=today,
                    )
                    if intraday_signals and args.execute_orders:
                        base_rt._submit_profit_lock_signals(
                            broker=broker,
                            store=store,
                            profile=profile_runtime,
                            signals=intraday_signals,
                            now=now,
                            profit_lock_order_type=args.profit_lock_order_type,
                            cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                            stop_price_offset_bps=float(args.stop_price_offset_bps),
                            event_type="g1_switch_profit_lock_intraday_close",
                            threshold_pct=float(threshold_pct),
                            extra_payload={"intraday_slot": slot_key, "variant_id": args.variant_id},
                        )
                        if args.profit_lock_order_type in {"close_position", "market_order"}:
                            time.sleep(float(args.post_close_refresh_seconds))
                store.put("g1_switch_intraday_profit_lock_last_slot", slot_key)

        if current_time < eval_time:
            logger.info("Waiting for eval window now=%s eval_time=%s", current_time, eval_time)
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if str(store.get("g1_switch_executed_day", "")) == today_iso:
            logger.info("G1 cycle already executed for %s", today_iso)
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
        daily_ohlc = base_rt._fetch_daily_ohlc(
            loader,
            symbols=symbols,
            start=lookback_start,
            end=now,
            feed=alpaca.data_feed,
        )

        target_weights, threshold_pct, diag = _build_g1_target_for_today(
            daily_ohlc=daily_ohlc,
            symbols=symbols,
            today=today,
            profile=profile_locked,
            params=params,
            warmup_days=int(args.warmup_days),
            rebalance_threshold=float(args.rebalance_threshold),
            controlplane_threshold_cap=float(args.controlplane_threshold_cap),
            controlplane_hysteresis_enter=float(args.controlplane_hysteresis_enter),
            controlplane_hysteresis_exit=float(args.controlplane_hysteresis_exit),
            controlplane_hysteresis_enter_days=int(args.controlplane_hysteresis_enter_days),
            controlplane_hysteresis_exit_days=int(args.controlplane_hysteresis_exit_days),
        )

        last_prices, day_highs, latest_ts = base_rt._fetch_intraday_day_stats(
            loader,
            symbols=symbols,
            session_open=open_time,
            now=now,
            feed=alpaca.data_feed,
        )
        if len(last_prices) < 2:
            logger.warning("Insufficient intraday bars for G1 cycle; skipping")
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if latest_ts:
            freshest = max(latest_ts.values()).astimezone(NY)
            stale_minutes = int(max(0, (now - freshest).total_seconds() // 60))
            if stale_minutes > int(args.stale_data_threshold_minutes):
                logger.warning("G1 cycle skipped due to stale intraday data stale_minutes=%d", stale_minutes)
                if args.run_once:
                    return 0
                time.sleep(int(args.loop_sleep_seconds))
                continue

        positions = broker.list_positions()
        profit_lock_signals = base_rt._build_profit_lock_signals(
            profile=profile_runtime,
            positions=positions,
            daily_ohlc=daily_ohlc,
            last_prices=last_prices,
            day_highs=day_highs,
            threshold_pct=threshold_pct,
            today=today,
        )

        symbols_to_close = [s.symbol for s in profit_lock_signals]
        if profit_lock_signals and args.execute_orders:
            base_rt._submit_profit_lock_signals(
                broker=broker,
                store=store,
                profile=profile_runtime,
                signals=profit_lock_signals,
                now=now,
                profit_lock_order_type=args.profit_lock_order_type,
                cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                stop_price_offset_bps=float(args.stop_price_offset_bps),
                event_type="g1_switch_profit_lock_close",
                threshold_pct=float(threshold_pct),
                extra_payload={"variant_id": args.variant_id},
            )
            if args.profit_lock_order_type in {"close_position", "market_order"}:
                time.sleep(float(args.post_close_refresh_seconds))
                positions = broker.list_positions()

        account = broker.get_account()
        equity = float(account["equity"])
        current_qty = {str(p["symbol"]).upper(): float(p["qty"]) for p in positions}

        intents = build_rebalance_order_intents(
            equity=equity,
            target_weights=target_weights,
            current_qty=current_qty,
            last_prices=last_prices,
            min_trade_weight_delta=float(args.rebalance_threshold),
        )

        blocked_for_rebalance = set(symbols_to_close) if args.profit_lock_order_type in {"stop_order", "trailing_stop"} else set()
        if blocked_for_rebalance:
            intents = [intent for intent in intents if intent.symbol not in blocked_for_rebalance]
        if args.max_intents_per_cycle > 0:
            intents = intents[: int(args.max_intents_per_cycle)]

        submitted = 0
        if args.execute_orders:
            for intent in intents:
                qty = float(intent.qty)
                if intent.side == "sell":
                    qty = min(qty, max(float(current_qty.get(intent.symbol, 0.0)), 0.0))
                if qty <= 0:
                    continue

                order_type = "market"
                take_profit_price = 0.0
                stop_loss_price = 0.0
                if args.rebalance_order_type == "bracket" and intent.side == "buy":
                    last_price = float(last_prices.get(intent.symbol, 0.0) or 0.0)
                    if last_price > 0.0:
                        take_profit_price = last_price * (1.0 + float(args.bracket_take_profit_pct) / 100.0)
                        stop_loss_price = last_price * (1.0 - float(args.bracket_stop_loss_pct) / 100.0)
                    if take_profit_price > 0.0 and stop_loss_price > 0.0:
                        broker.submit_bracket_order(
                            intent.symbol,
                            "buy",
                            qty,
                            take_profit_price=take_profit_price,
                            stop_loss_price=stop_loss_price,
                        )
                        order_type = "bracket"
                    else:
                        broker.submit_market_order(intent.symbol, intent.side, qty)
                else:
                    broker.submit_market_order(intent.symbol, intent.side, qty)

                submitted += 1
                store.append_event(
                    "g1_switch_rebalance_order",
                    {
                        "ts": now.isoformat(),
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "qty": qty,
                        "target_weight": float(intent.target_weight),
                        "profile": profile_runtime.name,
                        "variant": args.variant_id,
                        "order_type": order_type,
                        "take_profit_price": take_profit_price,
                        "stop_loss_price": stop_loss_price,
                    },
                )

        store.put("g1_switch_executed_day", today_iso)
        store.put("g1_switch_last_profile", profile_runtime.name)
        store.put("g1_switch_last_variant", args.variant_id)
        store.put("g1_switch_last_final_target", target_weights)

        payload: dict[str, Any] = {
            "ts": now.isoformat(),
            "day": today_iso,
            "profile": profile_runtime.name,
            "variant": args.variant_id,
            "threshold_pct": float(threshold_pct),
            "profit_lock_closed_symbols": symbols_to_close,
            "profit_lock_order_type": args.profit_lock_order_type,
            "rebalance_order_type": args.rebalance_order_type,
            "intent_count": len(intents),
            "orders_submitted": submitted,
            "execute_orders": bool(args.execute_orders),
            "g1_diag": diag,
        }

        store.append_event("g1_switch_cycle_complete", payload)
        print(json.dumps(payload, sort_keys=True))

        if args.run_once:
            return 0
        time.sleep(int(args.loop_sleep_seconds))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone G1-412837 runtime loop (paper/live) in separate folder. "
            "No edits to existing runtime/profile code."
        )
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--env-file", default="", help="Optional .env file loaded literally (no shell eval).")
    parser.add_argument("--env-override", action="store_true", help="Override existing process env vars when used with --env-file.")
    parser.add_argument("--strategy-profile", choices=sorted(base_rt.PROFILES.keys()), default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    parser.add_argument("--variant-id", default="G1-412837")
    parser.add_argument("--execute-orders", action="store_true", help="Submit real paper/live orders.")
    parser.add_argument("--run-once", action="store_true", help="Run at most one iteration and exit.")

    parser.add_argument("--state-db", default="g1_412837_runtime_v1_runtime.db")
    parser.add_argument("--eval-time", default="15:56", help="Daily NY time for main cycle (HH:MM).")
    parser.add_argument("--loop-sleep-seconds", type=int, default=30)
    parser.add_argument("--data-feed", default="sip", help="Alpaca feed: sip or iex.")
    parser.add_argument("--daily-lookback-days", type=int, default=800)
    parser.add_argument("--warmup-days", type=int, default=260)
    parser.add_argument("--stale-data-threshold-minutes", type=int, default=3)
    parser.add_argument("--post-close-refresh-seconds", type=float, default=2.0)
    parser.add_argument("--rebalance-threshold", type=float, default=0.05)

    parser.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    parser.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    parser.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    parser.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    parser.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    parser.add_argument("--trail-scale", type=float, default=1.10)
    parser.add_argument("--threshold-scale", type=float, default=1.20)

    parser.add_argument("--g1-bias", type=float, default=6.787898344285149)
    parser.add_argument("--g1-a20", type=float, default=0.0)
    parser.add_argument("--g1-a60", type=float, default=-0.09181296789807147)
    parser.add_argument("--g1-b-rv", type=float, default=0.09407960349884371)
    parser.add_argument("--g1-b-dd", type=float, default=0.13271417559907764)
    parser.add_argument("--g1-temp", type=float, default=0.03)
    parser.add_argument("--g1-floor", type=float, default=0.006844050593517129)
    parser.add_argument("--g1-ceil", type=float, default=1.0)

    parser.add_argument(
        "--profit-lock-order-type",
        choices=["close_position", "market_order", "stop_order", "trailing_stop"],
        default="market_order",
        help="How to execute profit-lock exits when triggered.",
    )
    parser.add_argument("--cancel-existing-exit-orders", action="store_true")
    parser.add_argument(
        "--stop-price-offset-bps",
        type=float,
        default=2.0,
        help="For stop_order exits, cap stop near market to avoid invalid sell-stop above market.",
    )

    parser.add_argument(
        "--rebalance-order-type",
        choices=["market", "bracket"],
        default="market",
        help="Order style for rebalance intents.",
    )
    parser.add_argument("--bracket-take-profit-pct", type=float, default=12.0)
    parser.add_argument("--bracket-stop-loss-pct", type=float, default=6.0)

    parser.add_argument("--max-intents-per-cycle", type=int, default=0, help="0 means no cap.")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)
    try:
        return int(_run_loop(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception:
        logger.exception("G1 runtime loop failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
