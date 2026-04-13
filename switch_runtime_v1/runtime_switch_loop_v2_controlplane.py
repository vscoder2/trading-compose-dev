#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.runtime_switch_loop as base
from improvements2_impl.src.decision_ledger import compute_decision_hash
from improvements2_impl.src.execution_policy import resolve_order_conflicts
from improvements2_impl.src.models import ActionIntent, OpenOrder
from improvements2_impl.src.regime_policy import (
    ConfidenceInputs,
    HysteresisConfig,
    HysteresisState,
    build_confidence_log_payload,
    compute_adaptive_rebalance_threshold,
    compute_regime_confidence,
    step_hysteresis_state,
)
from improvements2_impl.src.risk_controls import ExposureInputs, compute_exposure_scalar
from improvements2_impl.src.shadow_eval import run_shadow_cycle
from improvements2_impl.src.state_adapter import ControlPlaneStore
from soxl_growth.config import NY, AlpacaConfig, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from soxl_growth.db import StateStore
from soxl_growth.execution.broker import AlpacaBroker
from soxl_growth.execution.orders import OrderIntent, build_rebalance_order_intents
from soxl_growth.logging_setup import configure_logging

logger = base.logger


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _trend_strength_from_metrics(metrics: base.RegimeMetrics) -> float:
    """Map regime metrics to a normalized trend-strength signal in [-1, 1]."""

    close = float(metrics.close)
    ma20 = float(metrics.ma20) if metrics.ma20 is not None else close
    ma60 = float(metrics.ma60) if metrics.ma60 is not None else ma20
    slope20 = float(metrics.slope20_pct) if metrics.slope20_pct is not None else 0.0

    price_bias = 0.0
    if close > ma20:
        price_bias += 0.35
    elif close < ma20:
        price_bias -= 0.35

    if ma20 > ma60:
        price_bias += 0.25
    elif ma20 < ma60:
        price_bias -= 0.25

    slope_bias = _clamp(slope20 / 2.0, -0.40, 0.40)
    return _clamp(price_bias + slope_bias, -1.0, 1.0)


def _regime_signal_01(metrics: base.RegimeMetrics) -> float:
    """Convert metrics to a bounded regime signal used by hysteresis."""

    trend01 = (_trend_strength_from_metrics(metrics) + 1.0) / 2.0
    vol_penalty = _clamp((float(metrics.rv20_ann) - 0.60) / 1.40, 0.0, 1.0)
    chop_penalty = _clamp(float(metrics.crossovers20) / 8.0, 0.0, 1.0)
    signal = (0.70 * trend01) + (0.20 * (1.0 - vol_penalty)) + (0.10 * (1.0 - chop_penalty))
    return _clamp(signal, 0.0, 1.0)


def _load_hysteresis_state(store: StateStore, key: str) -> HysteresisState:
    raw = store.get(key, None)
    if not isinstance(raw, dict):
        return HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)
    regime = str(raw.get("regime", "risk_off"))
    enter_streak = int(raw.get("enter_streak", 0) or 0)
    exit_streak = int(raw.get("exit_streak", 0) or 0)
    if regime not in {"risk_off", "risk_on"}:
        regime = "risk_off"
    return HysteresisState(regime=regime, enter_streak=max(0, enter_streak), exit_streak=max(0, exit_streak))


def _to_cp_open_orders(rows: list[dict[str, Any]]) -> list[OpenOrder]:
    out: list[OpenOrder] = []
    for row in rows:
        try:
            out.append(
                OpenOrder(
                    order_id=str(row.get("id", "")),
                    symbol=str(row.get("symbol", "")).upper(),
                    side=str(row.get("side", "")).lower(),
                    qty=float(row.get("qty", 0.0) or 0.0),
                    status=str(row.get("status", "")),
                    created_ts=None,
                )
            )
        except Exception:
            continue
    return out


def _to_cp_actions(intents: list[OrderIntent]) -> list[ActionIntent]:
    out: list[ActionIntent] = []
    for intent in intents:
        side = str(intent.side).lower()
        priority = "rebalance_add" if side == "buy" else "rebalance_reduction"
        out.append(
            ActionIntent(
                symbol=str(intent.symbol).upper(),
                side=side,
                qty=float(intent.qty),
                priority_class=priority,
                source="runtime_rebalance",
                reason_code="rebalance_intent",
                metadata={"target_weight": float(intent.target_weight)},
            )
        )
    return out


def _from_cp_actions(actions: list[ActionIntent], original: list[OrderIntent]) -> list[OrderIntent]:
    # Preserve target-weight context when converting filtered actions back to broker intents.
    target_by_symbol = {str(x.symbol).upper(): float(x.target_weight) for x in original}
    out: list[OrderIntent] = []
    for a in actions:
        sym = str(a.symbol).upper()
        out.append(
            OrderIntent(
                symbol=sym,
                side=str(a.side).lower(),
                qty=float(a.qty),
                target_weight=float(target_by_symbol.get(sym, 0.0)),
            )
        )
    out.sort(key=lambda x: 0 if x.side == "sell" else 1)
    return out


def _alt_variant(current: str) -> str:
    if current == "baseline":
        return "inverse_ma20"
    if current == "inverse_ma20":
        return "inverse_ma60"
    return "baseline"


def _build_parser() -> argparse.ArgumentParser:
    parser = base._build_parser()
    parser.description = (
        "Control-plane integrated runtime entrypoint. "
        "Defaults to legacy behavior unless --controlplane-enable is passed."
    )
    parser.add_argument("--controlplane-enable", action="store_true", help="Enable control-plane decision/execution sidecar features.")
    parser.add_argument(
        "--controlplane-state-db",
        default="switch_runtime_v1_controlplane.db",
        help="SQLite DB path for control-plane tables (decision_cycles, shadow_cycles, eod).",
    )
    parser.add_argument(
        "--controlplane-no-apply-migrations",
        action="store_true",
        help="Skip auto-apply for control-plane migrations (001/002).",
    )
    parser.add_argument(
        "--controlplane-threshold-cap",
        type=float,
        default=0.50,
        help="Upper bound for adaptive rebalance threshold when control-plane is enabled.",
    )
    parser.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    parser.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    parser.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    parser.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)
    parser.add_argument(
        "--controlplane-enable-shadow-eval",
        action="store_true",
        help="Run non-submitting shadow variant comparison each daily cycle.",
    )
    parser.add_argument(
        "--controlplane-log-confidence",
        action="store_true",
        help="Persist detailed confidence payload in control-plane decision reasons.",
    )
    return parser


def _run_loop_controlplane(args: argparse.Namespace) -> int:
    if args.env_file:
        loaded = base._load_env_file(args.env_file, override=bool(args.env_override))
        logger.info("Loaded %d env vars from %s (override=%s)", loaded, args.env_file, bool(args.env_override))

    profile = base.PROFILES[args.strategy_profile]
    paper = args.mode == "paper"
    alpaca = AlpacaConfig.from_env(paper=paper, data_feed=args.data_feed)

    store = StateStore(args.state_db)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=alpaca.paper)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    strategy = StrategyConfig()
    symbols = list(strategy.symbols)
    eval_time = base._parse_hhmm(args.eval_time)

    cp_store = ControlPlaneStore(args.controlplane_state_db)
    if not args.controlplane_no_apply_migrations:
        cp_store.apply_migration(Path("improvements2_impl/migrations/001_control_plane.sql"))
        cp_store.apply_migration(Path("improvements2_impl/migrations/002_execution_observability.sql"))

    logger.info(
        (
            "Starting control-plane runtime mode=%s profile=%s execute_orders=%s eval_time=%s "
            "profit_lock_order_type=%s rebalance_order_type=%s cp_state_db=%s"
        ),
        args.mode,
        profile.name,
        bool(args.execute_orders),
        args.eval_time,
        args.profit_lock_order_type,
        args.rebalance_order_type,
        args.controlplane_state_db,
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

        # Keep intraday profit-lock semantics aligned with legacy runtime.
        if (
            profile.enable_profit_lock
            and intraday_check_minutes > 0
            and now >= open_time
            and current_time < eval_time
        ):
            slot_idx = int((now.hour * 60 + now.minute) // intraday_check_minutes)
            slot_key = f"{today_iso}:{slot_idx}"
            last_slot_key = str(store.get("switch_intraday_profit_lock_last_slot", ""))
            if slot_key != last_slot_key:
                lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
                daily_ohlc = base._fetch_daily_ohlc(
                    loader,
                    symbols=symbols,
                    start=lookback_start,
                    end=now,
                    feed=alpaca.data_feed,
                )
                daily_closes: dict[str, list[float]] = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}
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
                            event_type="switch_profit_lock_intraday_close",
                            threshold_pct=float(threshold_pct),
                            extra_payload={"intraday_slot": slot_key},
                        )
                        if args.profit_lock_order_type in {"close_position", "market_order"}:
                            time.sleep(float(args.post_close_refresh_seconds))
                store.put("switch_intraday_profit_lock_last_slot", slot_key)

        if current_time < eval_time:
            logger.info("Waiting for eval window now=%s eval_time=%s", current_time, eval_time)
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if str(store.get("switch_executed_day", "")) == today_iso:
            logger.info("Switch cycle already executed for %s", today_iso)
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
        daily_closes: dict[str, list[float]] = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}

        baseline_target = base.evaluate_strategy(base.DictContext(closes=daily_closes))
        threshold_pct = base._current_threshold_pct(profile, daily_closes)

        soxl_hist = list(daily_closes.get("SOXL", []))
        if len(soxl_hist) < 20:
            logger.warning("Insufficient SOXL history to compute regime metrics; defaulting baseline")
            regime_state = base.RegimeState(current_variant="baseline")
            variant = "baseline"
            variant_reason = "insufficient_history"
            metrics = None
        else:
            regime_state = base._load_regime_state(store)
            metrics = base._compute_regime_metrics(soxl_hist)
            prev_variant = regime_state.current_variant
            variant, variant_reason = base._choose_variant(metrics, regime_state)
            if variant != prev_variant:
                store.append_event(
                    "switch_variant_changed",
                    {
                        "ts": now.isoformat(),
                        "from": prev_variant,
                        "to": variant,
                        "reason": variant_reason,
                    },
                )
            store.put("switch_regime_state", asdict(regime_state))

        used_rebalance_threshold = float(args.rebalance_threshold)
        confidence_score = None
        confidence_components: dict[str, float] = {}
        cp_override_reason = ""

        if metrics is not None:
            cfg = HysteresisConfig(
                enter_threshold=float(args.controlplane_hysteresis_enter),
                exit_threshold=float(args.controlplane_hysteresis_exit),
                min_enter_days=int(args.controlplane_hysteresis_enter_days),
                min_exit_days=int(args.controlplane_hysteresis_exit_days),
            )
            h_state = _load_hysteresis_state(store, "cp_hysteresis_state")
            h_signal = _regime_signal_01(metrics)
            h_next = step_hysteresis_state(prior=h_state, signal=h_signal, cfg=cfg)
            store.put("cp_hysteresis_state", asdict(h_next))

            if h_next.regime == "risk_off" and variant != "baseline":
                cp_override_reason = "cp_hysteresis_risk_off_forces_baseline"
                variant = "baseline"
            elif h_next.regime == "risk_on" and variant == "baseline":
                # Optional promotion only when baseline persists but regime is consistently risk-on.
                cp_override_reason = "cp_hysteresis_risk_on_promotes_inverse_ma20"
                variant = "inverse_ma20"

            conf_inputs = ConfidenceInputs(
                trend_strength=_trend_strength_from_metrics(metrics),
                realized_vol_ann=float(metrics.rv20_ann),
                chop_score=float(metrics.crossovers20),
                data_fresh=True,
            )
            confidence_score, confidence_components = compute_regime_confidence(conf_inputs)
            used_rebalance_threshold = compute_adaptive_rebalance_threshold(
                base_threshold_pct=float(args.rebalance_threshold),
                realized_vol_ann=float(metrics.rv20_ann),
                chop_score=float(metrics.crossovers20),
                confidence_score=float(confidence_score),
                min_threshold_pct=float(args.rebalance_threshold),
                max_threshold_pct=float(args.controlplane_threshold_cap),
            )
            store.put("cp_last_confidence", float(confidence_score))
            store.put("cp_last_rebalance_threshold", float(used_rebalance_threshold))

        switched_target, inv_note = base._apply_variant_to_target(
            baseline_target,
            daily_closes,
            variant,
        )

        last_prices, day_highs, latest_ts = base._fetch_intraday_day_stats(
            loader,
            symbols=symbols,
            session_open=open_time,
            now=now,
            feed=alpaca.data_feed,
        )
        if len(last_prices) < 2:
            logger.warning("Insufficient intraday bars for switch cycle; skipping")
            if args.run_once:
                return 0
            time.sleep(int(args.loop_sleep_seconds))
            continue

        if latest_ts:
            freshest = max(latest_ts.values()).astimezone(NY)
            stale_minutes = int(max(0, (now - freshest).total_seconds() // 60))
            if stale_minutes > int(args.stale_data_threshold_minutes):
                logger.warning("Switch cycle skipped due to stale intraday data stale_minutes=%d", stale_minutes)
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
                event_type="switch_profit_lock_close",
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
            target_weights=switched_target,
            current_qty=current_qty,
            last_prices=last_prices,
            min_trade_weight_delta=float(used_rebalance_threshold),
        )

        blocked_for_rebalance = set(symbols_to_close) if args.profit_lock_order_type in {"stop_order", "trailing_stop"} else set()
        if blocked_for_rebalance:
            intents = [intent for intent in intents if intent.symbol not in blocked_for_rebalance]

        raw_open_orders = broker.list_open_orders()
        cp_open_orders = _to_cp_open_orders(raw_open_orders)
        cp_actions = _to_cp_actions(intents)
        kept_actions, blocked_actions, exec_diag = resolve_order_conflicts(cp_actions, cp_open_orders)
        intents = _from_cp_actions(kept_actions, intents)

        if blocked_actions:
            store.append_event(
                "switch_controlplane_blocked_actions",
                {
                    "ts": now.isoformat(),
                    "blocked_count": len(blocked_actions),
                    "reasons": sorted({str(x.get('reason', '')) for x in blocked_actions}),
                },
            )

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
                    "switch_rebalance_order",
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

        # Control-plane decision-cycle persistence + risk snapshot.
        cycle_id = f"{today_iso}:{profile.name}:{variant}:{int(now.timestamp())}"
        snapshot = {
            "cycle_id": cycle_id,
            "profile": profile.name,
            "variant": variant,
            "variant_reason": variant_reason,
            "cp_override_reason": cp_override_reason,
            "threshold_pct": float(threshold_pct),
            "rebalance_threshold": float(used_rebalance_threshold),
            "baseline_target": baseline_target,
            "switched_target": switched_target,
            "intent_count": len(intents),
            "blocked_count": len(blocked_actions),
        }
        decision_hash = compute_decision_hash(snapshot)

        severity = "warn" if blocked_actions else "ok"
        cp_store.put_decision_cycle(
            cycle_id=cycle_id,
            cycle_type="daily_eval",
            profile=profile.name,
            target=baseline_target,
            effective_target=switched_target,
            decision_hash=decision_hash,
            severity=severity,
            details={
                "variant": variant,
                "variant_reason": variant_reason,
                "cp_override_reason": cp_override_reason,
                "exec_diag": exec_diag,
                "inv_note": inv_note,
                "rebalance_threshold": float(used_rebalance_threshold),
            },
            ts=now.isoformat(),
        )
        cp_store.append_decision_reason(
            cycle_id=cycle_id,
            reason_code=str(variant_reason),
            priority_class="regime",
            symbol="SOXL",
            detail={"cp_override_reason": cp_override_reason},
            ts=now.isoformat(),
        )

        if confidence_score is not None and args.controlplane_log_confidence:
            conf_payload = build_confidence_log_payload(
                cycle_id=cycle_id,
                profile=profile.name,
                confidence_score=float(confidence_score),
                components=confidence_components,
            )
            cp_store.append_decision_reason(
                cycle_id=cycle_id,
                reason_code="cp_confidence",
                priority_class="decision_quality",
                symbol=None,
                detail=conf_payload,
                ts=now.isoformat(),
            )

        peak_equity = max(float(store.get("cp_peak_equity", 0.0) or 0.0), equity)
        store.put("cp_peak_equity", peak_equity)
        drawdown_pct = 0.0 if peak_equity <= 0 else max(0.0, (peak_equity - equity) * 100.0 / peak_equity)
        exposure_scalar = compute_exposure_scalar(
            ExposureInputs(
                drawdown_pct=drawdown_pct,
                realized_vol_ann=float(metrics.rv20_ann) if metrics is not None else 0.0,
                chop_score=float(metrics.crossovers20) if metrics is not None else 0.0,
            )
        )
        cp_store.put_risk_state(
            equity=equity,
            peak_equity=peak_equity,
            drawdown_pct=drawdown_pct,
            exposure_scalar=float(exposure_scalar),
            dd_brake_state="none",
            recovery_phase="none",
            detail={"profile": profile.name, "variant": variant},
            ts=now.isoformat(),
        )

        if args.controlplane_enable_shadow_eval:
            alt_variant = _alt_variant(variant)
            alt_target, _alt_note = base._apply_variant_to_target(baseline_target, daily_closes, alt_variant)
            alt_intents = build_rebalance_order_intents(
                equity=equity,
                target_weights=alt_target,
                current_qty=current_qty,
                last_prices=last_prices,
                min_trade_weight_delta=float(used_rebalance_threshold),
            )
            run_shadow_cycle(
                store=cp_store,
                cycle_id=cycle_id,
                variant_name=f"shadow_{alt_variant}",
                shadow_effective_target=alt_target,
                shadow_actions=_to_cp_actions(alt_intents),
                primary_actions=_to_cp_actions(intents),
                primary_target=switched_target,
                allow_submit=False,
                ts=now.isoformat(),
            )

        store.put("switch_executed_day", today_iso)
        store.put("switch_last_profile", profile.name)
        store.put("switch_last_variant", variant)
        store.put("switch_last_baseline_target", baseline_target)
        store.put("switch_last_final_target", switched_target)

        cycle_payload: dict[str, Any] = {
            "ts": now.isoformat(),
            "day": today_iso,
            "profile": profile.name,
            "variant": variant,
            "variant_reason": variant_reason,
            "cp_override_reason": cp_override_reason,
            "inverse_note": inv_note,
            "threshold_pct": float(threshold_pct),
            "rebalance_threshold": float(used_rebalance_threshold),
            "profit_lock_closed_symbols": symbols_to_close,
            "profit_lock_order_type": args.profit_lock_order_type,
            "rebalance_order_type": args.rebalance_order_type,
            "intent_count": len(intents),
            "blocked_action_count": len(blocked_actions),
            "orders_submitted": submitted,
            "execute_orders": bool(args.execute_orders),
            "controlplane_state_db": args.controlplane_state_db,
        }
        if confidence_score is not None:
            cycle_payload["confidence_score"] = float(confidence_score)
        if metrics is not None:
            cycle_payload["regime_metrics"] = asdict(metrics)

        store.append_event("switch_cycle_complete", cycle_payload)
        print(json.dumps(cycle_payload, sort_keys=True))

        if args.run_once:
            return 0
        time.sleep(int(args.loop_sleep_seconds))


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)
    try:
        if not bool(args.controlplane_enable):
            logger.info("Control-plane disabled. Delegating to legacy runtime path for exact behavior.")
            return int(base._run_loop(args))
        return int(_run_loop_controlplane(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception:
        logger.exception("Control-plane runtime loop failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
