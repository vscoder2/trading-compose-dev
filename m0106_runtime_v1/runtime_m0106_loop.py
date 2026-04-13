#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.runtime_switch_loop as base
import switch_runtime_v1.runtime_switch_loop_v2_controlplane as rt_v2
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
from soxl_growth.db import StateStore
from soxl_growth.execution.broker import AlpacaBroker
from soxl_growth.execution.orders import build_rebalance_order_intents
from soxl_growth.logging_setup import configure_logging

logger = base.logger


@dataclass(frozen=True)
class M0106Config:
    """Research-selected overlay controls promoted into standalone runtime."""

    # v2/hysteresis scaffold used as base stream prior to M0106 overlays.
    threshold_cap: float = 0.10
    hysteresis_enter: float = 0.72
    hysteresis_exit: float = 0.64
    hysteresis_enter_days: int = 6
    hysteresis_exit_days: int = 2

    # M0106 overlay parameters.
    recent_inverse_cap_weight: float = 0.10
    recent_days: int = 75
    shock_down_pct: float = -0.05
    shock_hold_days: int = 3
    recent_primary_cap_weight: float = 0.30
    recent_defensive_blend: float = 0.20
    defensive_trigger_down_pct: float = -0.006
    recent_aggressive_total_cap: float = 0.40
    recent_always_defensive_blend: float = 0.35


# Separate profile namespace so we do not modify existing runtime profiles.
PROFILES: dict[str, base.StrategyProfile] = {
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_m0106": base.StrategyProfile(
        name="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_m0106",
        enable_profit_lock=True,
        profit_lock_mode="trailing",
        profit_lock_threshold_pct=10.0,
        profit_lock_trail_pct=2.0,
        profit_lock_adaptive_threshold=True,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
        intraday_profit_lock_check_minutes=5,
    ),
}

M0106_CFG = M0106Config()

INVERSE_SYMBOLS = {"SOXS", "SQQQ", "SPXS", "TMV"}
AGGRESSIVE_SYMBOLS = {"SOXL", "TQQQ", "SPXL", "TECL", "FNGU"}
DEFENSIVE_PREFERRED = ("TMF", "TLT", "IEF", "SHY", "BIL")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, float(v)) for v in weights.values())
    if s <= 0.0:
        return {}
    return {k: max(0.0, float(v)) / s for k, v in weights.items() if max(0.0, float(v)) > 0.0}


def _blend_to_baseline(baseline: dict[str, float], target: dict[str, float], alpha: float) -> dict[str, float]:
    """Blend target toward baseline by alpha and renormalize."""
    a = _clamp(alpha, 0.0, 1.0)
    keys = set(baseline.keys()) | set(target.keys())
    out = {k: a * float(target.get(k, 0.0)) + (1.0 - a) * float(baseline.get(k, 0.0)) for k in keys}
    return _normalize_weights(out)


def _get_recent_soxl_return(daily_closes: dict[str, list[float]]) -> float:
    closes = list(daily_closes.get("SOXL", []))
    if len(closes) < 2:
        return 0.0
    prev = float(closes[-2])
    cur = float(closes[-1])
    if prev <= 0.0:
        return 0.0
    return (cur / prev) - 1.0


def _load_m0106_state(store: StateStore) -> tuple[base.RegimeState, HysteresisState, int]:
    reg_raw = store.get("m0106_regime_state", None)
    if isinstance(reg_raw, dict):
        reg = base.RegimeState()
        for key in (
            "current_variant",
            "cond2_streak",
            "cond3_streak",
            "cond2_false_streak",
            "forced_baseline_days",
            "high_vol_lock",
        ):
            if key in reg_raw:
                setattr(reg, key, reg_raw[key])
        if reg.current_variant not in {"baseline", "inverse_ma20", "inverse_ma60"}:
            reg.current_variant = "baseline"
    else:
        reg = base.RegimeState()

    h_raw = store.get("m0106_hysteresis_state", None)
    if isinstance(h_raw, dict):
        regime = str(h_raw.get("regime", "risk_off"))
        if regime not in {"risk_off", "risk_on"}:
            regime = "risk_off"
        h_state = HysteresisState(
            regime=regime,
            enter_streak=max(0, int(h_raw.get("enter_streak", 0) or 0)),
            exit_streak=max(0, int(h_raw.get("exit_streak", 0) or 0)),
        )
    else:
        h_state = HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)

    shock_left = max(0, int(store.get("m0106_shock_hold_days_left", 0) or 0))
    return reg, h_state, shock_left


def _save_m0106_state(store: StateStore, reg: base.RegimeState, h_state: HysteresisState, shock_left: int) -> None:
    store.put("m0106_regime_state", asdict(reg))
    store.put("m0106_hysteresis_state", asdict(h_state))
    store.put("m0106_shock_hold_days_left", max(0, int(shock_left)))


def _compute_v2_variant_and_threshold(
    *,
    metrics: base.RegimeMetrics,
    reg_state: base.RegimeState,
    h_state: HysteresisState,
    base_threshold: float,
    cfg: M0106Config,
) -> tuple[str, str, float, base.RegimeState, HysteresisState, float]:
    """Reproduce v2-style variant override + adaptive threshold in standalone runtime."""

    variant_raw, reason = base._choose_variant(metrics, reg_state)

    h_cfg = HysteresisConfig(
        enter_threshold=float(cfg.hysteresis_enter),
        exit_threshold=float(cfg.hysteresis_exit),
        min_enter_days=int(cfg.hysteresis_enter_days),
        min_exit_days=int(cfg.hysteresis_exit_days),
    )
    signal01 = rt_v2._regime_signal_01(metrics)
    h_next = step_hysteresis_state(prior=h_state, signal=signal01, cfg=h_cfg)

    variant = str(variant_raw)
    if h_next.regime == "risk_off" and variant != "baseline":
        variant = "baseline"
        reason = f"{reason}|hysteresis_risk_off"
    elif h_next.regime == "risk_on" and variant == "baseline":
        variant = "inverse_ma20"
        reason = f"{reason}|hysteresis_risk_on_force_inv20"

    conf_inputs = ConfidenceInputs(
        trend_strength=float(rt_v2._trend_strength_from_metrics(metrics)),
        realized_vol_ann=float(metrics.rv20_ann),
        chop_score=float(metrics.crossovers20),
        data_fresh=True,
    )
    conf_score, _ = compute_regime_confidence(conf_inputs)
    threshold = compute_adaptive_rebalance_threshold(
        base_threshold_pct=float(base_threshold),
        realized_vol_ann=float(metrics.rv20_ann),
        chop_score=float(metrics.crossovers20),
        confidence_score=float(conf_score),
        min_threshold_pct=float(base_threshold),
        max_threshold_pct=float(cfg.threshold_cap),
    )
    return variant, reason, float(threshold), reg_state, h_next, float(conf_score)


def _apply_m0106_overlay(
    *,
    baseline_target: dict[str, float],
    switched_target: dict[str, float],
    variant: str,
    daily_closes: dict[str, list[float]],
    shock_left: int,
    cfg: M0106Config,
) -> tuple[dict[str, float], float | None, int, dict[str, Any]]:
    """Apply M0106 risk-shaping overlays.

    Returns: (final_target, threshold_override_or_none, new_shock_left, debug_flags)
    """

    t = dict(switched_target)
    baseline = dict(baseline_target)

    changed_recent_inv = False
    changed_total_cap = False
    changed_primary_cap = False
    changed_always_def = False
    changed_downside_blend = False
    shock_applied = False

    # 1) Recent inverse cap while in inverse variants.
    if str(variant).startswith("inverse"):
        inv_w = sum(float(t.get(s, 0.0)) for s in INVERSE_SYMBOLS)
        cap = float(cfg.recent_inverse_cap_weight)
        if inv_w > cap and inv_w > 1e-12:
            alpha = cap / inv_w
            t = _blend_to_baseline(baseline, t, alpha)
            changed_recent_inv = True

    # 2) Cap total aggressive exposure and route released weight to TMF.
    aggr_total = sum(float(t.get(s, 0.0)) for s in AGGRESSIVE_SYMBOLS)
    if aggr_total > float(cfg.recent_aggressive_total_cap) and aggr_total > 1e-12:
        scale = float(cfg.recent_aggressive_total_cap) / aggr_total
        released = 0.0
        for s in AGGRESSIVE_SYMBOLS:
            w = float(t.get(s, 0.0))
            if w <= 0.0:
                continue
            w_new = w * scale
            released += (w - w_new)
            t[s] = w_new
        t["TMF"] = float(t.get("TMF", 0.0)) + released
        t = _normalize_weights(t)
        changed_total_cap = True

    # 3) Cap dominant aggressive symbol and reroute excess to preferred defensive sleeve.
    aggr = [(s, float(t.get(s, 0.0))) for s in AGGRESSIVE_SYMBOLS if float(t.get(s, 0.0)) > 0.0]
    if aggr:
        sym_max, w_max = max(aggr, key=lambda x: x[1])
        cap = float(cfg.recent_primary_cap_weight)
        if w_max > cap:
            excess = w_max - cap
            t[sym_max] = cap
            routed = False
            for dsym in DEFENSIVE_PREFERRED:
                if dsym in t:
                    t[dsym] = float(t.get(dsym, 0.0)) + excess
                    routed = True
                    break
            if not routed:
                t["TMF"] = float(t.get("TMF", 0.0)) + excess
            t = _normalize_weights(t)
            changed_primary_cap = True

    # 4) Always-on defensive blend.
    always_blend = _clamp(float(cfg.recent_always_defensive_blend), 0.0, 0.95)
    if always_blend > 0.0 and t:
        for k in list(t.keys()):
            t[k] = float(t[k]) * (1.0 - always_blend)
        t["TMF"] = float(t.get("TMF", 0.0)) + always_blend
        t = _normalize_weights(t)
        changed_always_def = True

    # 5) Downside defensive blend if SOXL recent return breaches trigger.
    soxl_ret = _get_recent_soxl_return(daily_closes)
    if soxl_ret <= float(cfg.defensive_trigger_down_pct) and t:
        blend = _clamp(float(cfg.recent_defensive_blend), 0.0, 0.95)
        if blend > 0.0:
            for k in list(t.keys()):
                t[k] = float(t[k]) * (1.0 - blend)
            t["TMF"] = float(t.get("TMF", 0.0)) + blend
            t = _normalize_weights(t)
            changed_downside_blend = True

    # 6) Shock brake with multi-day hold fallback to baseline target.
    if soxl_ret <= float(cfg.shock_down_pct):
        shock_left = max(shock_left, int(cfg.shock_hold_days))
    threshold_override: float | None = None
    if shock_left > 0:
        t = dict(baseline)
        threshold_override = 0.05
        shock_left = max(0, shock_left - 1)
        shock_applied = True

    debug = {
        "changed_recent_inverse_cap": changed_recent_inv,
        "changed_aggressive_total_cap": changed_total_cap,
        "changed_primary_cap": changed_primary_cap,
        "changed_always_defensive_blend": changed_always_def,
        "changed_downside_defensive_blend": changed_downside_blend,
        "shock_applied": shock_applied,
        "soxl_recent_return": float(soxl_ret),
        "shock_hold_days_left": int(shock_left),
    }
    return t, threshold_override, shock_left, debug


def _run_loop(args: argparse.Namespace) -> int:
    if args.env_file:
        loaded = base._load_env_file(args.env_file, override=bool(args.env_override))
        logger.info("Loaded %d env vars from %s (override=%s)", loaded, args.env_file, bool(args.env_override))

    profile = PROFILES[args.strategy_profile]
    paper = args.mode == "paper"
    alpaca = AlpacaConfig.from_env(paper=paper, data_feed=args.data_feed)

    store = StateStore(args.state_db)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=alpaca.paper)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    strategy = StrategyConfig()
    symbols = list(strategy.symbols)
    eval_time = base._parse_hhmm(args.eval_time)

    logger.info(
        (
            "Starting M0106 runtime mode=%s profile=%s execute_orders=%s eval_time=%s "
            "profit_lock_order_type=%s rebalance_order_type=%s"
        ),
        args.mode,
        profile.name,
        bool(args.execute_orders),
        args.eval_time,
        args.profit_lock_order_type,
        args.rebalance_order_type,
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
        open_time, _close_time = base._market_session_window(broker, now)
        current_time = dt_time(hour=now.hour, minute=now.minute)
        intraday_check_minutes = max(0, int(profile.intraday_profit_lock_check_minutes))

        # Keep intraday profit-lock behavior consistent with base runtime.
        if (
            profile.enable_profit_lock
            and intraday_check_minutes > 0
            and now >= open_time
            and current_time < eval_time
        ):
            slot_idx = int((now.hour * 60 + now.minute) // intraday_check_minutes)
            slot_key = f"{today_iso}:{slot_idx}"
            last_slot_key = str(store.get("m0106_intraday_profit_lock_last_slot", ""))
            if slot_key != last_slot_key:
                lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
                daily_ohlc = base._fetch_daily_ohlc(
                    loader,
                    symbols=symbols,
                    start=lookback_start,
                    end=now,
                    feed=alpaca.data_feed,
                )
                daily_closes = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}
                threshold_pct = base._current_threshold_pct(profile, daily_closes)

                last_prices, day_highs, latest_ts = base._fetch_intraday_day_stats(
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
                    intraday_signals = base._build_profit_lock_signals(
                        profile=profile,
                        positions=positions,
                        daily_ohlc=daily_ohlc,
                        last_prices=last_prices,
                        day_highs=day_highs,
                        threshold_pct=threshold_pct,
                        today=today,
                    )
                    if intraday_signals and args.execute_orders:
                        base._submit_profit_lock_signals(
                            broker=broker,
                            store=store,
                            profile=profile,
                            signals=intraday_signals,
                            now=now,
                            profit_lock_order_type=args.profit_lock_order_type,
                            cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                            stop_price_offset_bps=float(args.stop_price_offset_bps),
                            event_type="m0106_profit_lock_intraday_close",
                            threshold_pct=float(threshold_pct),
                            extra_payload={"intraday_slot": slot_key},
                        )
                        if args.profit_lock_order_type in {"close_position", "market_order"}:
                            time.sleep(float(args.post_close_refresh_seconds))
                store.put("m0106_intraday_profit_lock_last_slot", slot_key)

        if current_time < eval_time:
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if str(store.get("m0106_executed_day", "")) == today_iso:
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
        daily_ohlc = base._fetch_daily_ohlc(
            loader,
            symbols=symbols,
            start=lookback_start,
            end=now,
            feed=alpaca.data_feed,
        )
        daily_closes = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}

        baseline_target = base.evaluate_strategy(base.DictContext(closes=daily_closes))
        threshold_pct = base._current_threshold_pct(profile, daily_closes)

        # Load persisted variant/hysteresis/shock state for deterministic daily transitions.
        reg_state, h_state, shock_left = _load_m0106_state(store)

        soxl_hist = list(daily_closes.get("SOXL", []))
        if len(soxl_hist) < 20:
            variant = "baseline"
            variant_reason = "insufficient_history"
            metrics = None
            rebalance_threshold = float(args.rebalance_threshold)
            confidence_score = 0.0
        else:
            metrics = base._compute_regime_metrics(soxl_hist)
            prev_variant = reg_state.current_variant
            variant, variant_reason, rebalance_threshold, reg_state, h_state, confidence_score = _compute_v2_variant_and_threshold(
                metrics=metrics,
                reg_state=reg_state,
                h_state=h_state,
                base_threshold=float(args.rebalance_threshold),
                cfg=M0106_CFG,
            )
            if variant != prev_variant:
                store.append_event(
                    "m0106_variant_changed",
                    {
                        "ts": now.isoformat(),
                        "from": prev_variant,
                        "to": variant,
                        "reason": variant_reason,
                    },
                )

        switched_target, inv_note = base._apply_variant_to_target(
            baseline_target,
            daily_closes,
            variant,
        )

        final_target, threshold_override, shock_left, m0106_debug = _apply_m0106_overlay(
            baseline_target=baseline_target,
            switched_target=switched_target,
            variant=variant,
            daily_closes=daily_closes,
            shock_left=shock_left,
            cfg=M0106_CFG,
        )
        if threshold_override is not None:
            rebalance_threshold = float(threshold_override)

        _save_m0106_state(store, reg_state, h_state, shock_left)

        last_prices, day_highs, latest_ts = base._fetch_intraday_day_stats(
            loader,
            symbols=symbols,
            session_open=open_time,
            now=now,
            feed=alpaca.data_feed,
        )
        if len(last_prices) < 2:
            logger.warning("Insufficient intraday bars for M0106 cycle; skipping")
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if latest_ts:
            freshest = max(latest_ts.values()).astimezone(NY)
            stale_minutes = int(max(0, (now - freshest).total_seconds() // 60))
            if stale_minutes > int(args.stale_data_threshold_minutes):
                logger.warning("M0106 cycle skipped due to stale data stale_minutes=%d", stale_minutes)
                if args.run_once:
                    return 0
                time.sleep(int(args.loop_sleep_seconds))
                continue

        positions = broker.list_positions()
        profit_lock_signals = base._build_profit_lock_signals(
            profile=profile,
            positions=positions,
            daily_ohlc=daily_ohlc,
            last_prices=last_prices,
            day_highs=day_highs,
            threshold_pct=threshold_pct,
            today=today,
        )

        symbols_to_close = [s.symbol for s in profit_lock_signals]
        if profit_lock_signals and args.execute_orders:
            base._submit_profit_lock_signals(
                broker=broker,
                store=store,
                profile=profile,
                signals=profit_lock_signals,
                now=now,
                profit_lock_order_type=args.profit_lock_order_type,
                cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                stop_price_offset_bps=float(args.stop_price_offset_bps),
                event_type="m0106_profit_lock_close",
                threshold_pct=float(threshold_pct),
            )
            if args.profit_lock_order_type in {"close_position", "market_order"}:
                time.sleep(float(args.post_close_refresh_seconds))
                positions = broker.list_positions()

        account = broker.get_account()
        equity = float(account["equity"])
        current_qty = {str(p["symbol"]).upper(): float(p["qty"]) for p in positions}

        intents = build_rebalance_order_intents(
            equity=equity,
            target_weights=final_target,
            current_qty=current_qty,
            last_prices=last_prices,
            min_trade_weight_delta=float(rebalance_threshold),
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
                    "m0106_rebalance_order",
                    {
                        "ts": now.isoformat(),
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "qty": qty,
                        "target_weight": float(intent.target_weight),
                        "profile": profile.name,
                        "variant": variant,
                        "order_type": order_type,
                        "take_profit_price": take_profit_price,
                        "stop_loss_price": stop_loss_price,
                    },
                )

        store.put("m0106_executed_day", today_iso)
        store.put("m0106_last_profile", profile.name)
        store.put("m0106_last_variant", variant)
        store.put("m0106_last_baseline_target", baseline_target)
        store.put("m0106_last_switched_target", switched_target)
        store.put("m0106_last_final_target", final_target)

        cycle_payload: dict[str, Any] = {
            "ts": now.isoformat(),
            "day": today_iso,
            "profile": profile.name,
            "variant": variant,
            "variant_reason": variant_reason,
            "inverse_note": inv_note,
            "threshold_pct": float(threshold_pct),
            "rebalance_threshold": float(rebalance_threshold),
            "confidence_score": float(confidence_score),
            "m0106_debug": m0106_debug,
            "profit_lock_closed_symbols": symbols_to_close,
            "profit_lock_order_type": args.profit_lock_order_type,
            "rebalance_order_type": args.rebalance_order_type,
            "intent_count": len(intents),
            "orders_submitted": submitted,
            "execute_orders": bool(args.execute_orders),
        }
        if metrics is not None:
            cycle_payload["regime_metrics"] = asdict(metrics)

        store.append_event("m0106_cycle_complete", cycle_payload)
        print(json.dumps(cycle_payload, sort_keys=True))

        if args.run_once:
            return 0
        time.sleep(int(args.loop_sleep_seconds))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone M0106 runtime loop (paper/live) in separate folder. "
            "No existing runtime files are modified."
        )
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--env-file", default="", help="Optional .env file loaded literally (no shell eval).")
    parser.add_argument("--env-override", action="store_true", help="Override existing process env vars when used with --env-file.")
    parser.add_argument("--strategy-profile", choices=sorted(PROFILES.keys()), default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_m0106")
    parser.add_argument("--execute-orders", action="store_true", help="Submit real paper/live orders.")
    parser.add_argument("--run-once", action="store_true", help="Run at most one iteration and exit.")

    parser.add_argument("--state-db", default="m0106_runtime_v1_runtime.db")
    parser.add_argument("--eval-time", default="15:56", help="Daily NY time for main cycle (HH:MM).")
    parser.add_argument("--loop-sleep-seconds", type=int, default=30)
    parser.add_argument("--data-feed", default="sip", help="Alpaca feed: sip or iex.")
    parser.add_argument("--daily-lookback-days", type=int, default=800)
    parser.add_argument("--stale-data-threshold-minutes", type=int, default=3)
    parser.add_argument("--post-close-refresh-seconds", type=float, default=2.0)
    parser.add_argument("--rebalance-threshold", type=float, default=0.05)

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
        logger.exception("M0106 runtime loop failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
