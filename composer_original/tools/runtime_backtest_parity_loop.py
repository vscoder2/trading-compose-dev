#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import evaluate_strategy
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader, BarRequestSpec
from soxl_growth.db import StateStore
from soxl_growth.execution.broker import AlpacaBroker
from soxl_growth.execution.orders import build_rebalance_order_intents
from soxl_growth.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class StrategyProfile:
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
    intraday_profit_lock_check_minutes: int = 0


@dataclass(frozen=True)
class ProfitLockSignal:
    symbol: str
    qty: float
    trigger_price: float
    trail_stop_price: float
    last_price: float


PROFILES: dict[str, StrategyProfile] = {
    "original_composer": StrategyProfile(
        name="original_composer",
        enable_profit_lock=False,
        profit_lock_mode="fixed",
        profit_lock_threshold_pct=15.0,
        profit_lock_trail_pct=5.0,
        profit_lock_adaptive_threshold=False,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
    ),
    "trailing12_4_adapt": StrategyProfile(
        name="trailing12_4_adapt",
        enable_profit_lock=True,
        profit_lock_mode="trailing",
        profit_lock_threshold_pct=12.0,
        profit_lock_trail_pct=4.0,
        profit_lock_adaptive_threshold=True,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
    ),
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30": StrategyProfile(
        name="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
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
    ),
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m": StrategyProfile(
        name="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m",
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


def _parse_hhmm(value: str) -> dt_time:
    hh_str, mm_str = str(value).strip().split(":", 1)
    hh = int(hh_str)
    mm = int(mm_str)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid HH:MM: {value!r}")
    return dt_time(hour=hh, minute=mm)


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


def _current_threshold_pct(profile: StrategyProfile, daily_closes: dict[str, list[float]]) -> float:
    base = float(profile.profit_lock_threshold_pct)
    if not profile.enable_profit_lock or not profile.profit_lock_adaptive_threshold:
        return base
    sym = profile.profit_lock_adaptive_symbol
    closes = list(daily_closes.get(sym, []))
    window = max(2, int(profile.profit_lock_adaptive_rv_window))
    if len(closes) < window + 1:
        return base
    # Use prior daily closes only for today's threshold.
    lookback = closes[-(window + 1) : -1]
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


def _fetch_daily_ohlc(
    loader: AlpacaBarLoader,
    *,
    symbols: list[str],
    start: datetime,
    end: datetime,
    feed: str,
) -> dict[str, list[tuple[date, float, float]]]:
    bars = loader.get_bars(
        BarRequestSpec(
            symbols=symbols,
            start=start,
            end=end,
            timeframe="1Day",
            adjustment="all",
            feed=feed,
        )
    )
    out: dict[str, list[tuple[date, float, float]]] = {s: [] for s in symbols}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        day = bar.timestamp.astimezone(NY).date()
        out.setdefault(bar.symbol, []).append((day, float(bar.close), float(bar.high)))
    return out


def _fetch_intraday_day_stats(
    loader: AlpacaBarLoader,
    *,
    symbols: list[str],
    session_open: datetime,
    now: datetime,
    feed: str,
) -> tuple[dict[str, float], dict[str, float], dict[str, datetime]]:
    bars = loader.get_bars(
        BarRequestSpec(
            symbols=symbols,
            start=session_open,
            end=now,
            timeframe="1Min",
            adjustment="raw",
            feed=feed,
        )
    )
    closes: dict[str, float] = {}
    highs: dict[str, float] = {}
    latest_ts: dict[str, datetime] = {}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        symbol = str(bar.symbol)
        px = float(bar.close)
        closes[symbol] = px
        highs[symbol] = max(float(highs.get(symbol, float("-inf"))), float(bar.high))
        latest_ts[symbol] = bar.timestamp
    for symbol in symbols:
        if symbol in closes and symbol not in highs:
            highs[symbol] = closes[symbol]
    return closes, highs, latest_ts


def _previous_close(ohlc: list[tuple[date, float, float]], today: date) -> float | None:
    if not ohlc:
        return None
    # If today's daily bar is present, use the prior bar as previous close.
    if ohlc[-1][0] == today:
        if len(ohlc) < 2:
            return None
        return float(ohlc[-2][1])
    return float(ohlc[-1][1])


def _market_session_window(broker: AlpacaBroker, now: datetime) -> tuple[datetime, datetime]:
    try:
        cal = broker.get_calendar(start=now, end=now)
        if cal:
            row = cal[0]
            open_hh, open_mm = [int(x) for x in str(row["open"]).split(":")[:2]]
            close_hh, close_mm = [int(x) for x in str(row["close"]).split(":")[:2]]
            return (
                now.replace(hour=open_hh, minute=open_mm, second=0, microsecond=0),
                now.replace(hour=close_hh, minute=close_mm, second=0, microsecond=0),
            )
    except Exception:
        logger.exception("Calendar fetch failed; using default NYSE session")
    return (
        now.replace(hour=9, minute=30, second=0, microsecond=0),
        now.replace(hour=16, minute=0, second=0, microsecond=0),
    )


def _build_profit_lock_signals(
    *,
    profile: StrategyProfile,
    positions: list[dict[str, Any]],
    daily_ohlc: dict[str, list[tuple[date, float, float]]],
    last_prices: dict[str, float],
    day_highs: dict[str, float],
    threshold_pct: float,
    today: date,
) -> list[ProfitLockSignal]:
    out: dict[str, ProfitLockSignal] = {}
    if not profile.enable_profit_lock:
        return []
    threshold_ratio = 1.0 + (float(threshold_pct) / 100.0)
    trail_ratio = max(0.0, float(profile.profit_lock_trail_pct) / 100.0)
    for pos in positions:
        symbol = str(pos.get("symbol", "")).upper()
        qty = float(pos.get("qty", 0.0) or 0.0)
        if qty <= 0.0 or symbol not in last_prices:
            continue
        prev_close = _previous_close(daily_ohlc.get(symbol, []), today=today)
        day_high = float(day_highs.get(symbol, 0.0) or 0.0)
        day_close = float(last_prices.get(symbol, 0.0) or 0.0)
        if not prev_close or prev_close <= 0.0 or day_high <= 0.0 or day_close <= 0.0:
            continue
        trigger_price = float(prev_close) * threshold_ratio
        if day_high < trigger_price:
            continue
        should_close = False
        trail_stop_price = 0.0
        if profile.profit_lock_mode == "fixed":
            should_close = True
        elif profile.profit_lock_mode == "trailing":
            trail_stop_price = day_high * (1.0 - trail_ratio)
            should_close = day_close <= trail_stop_price
        if should_close:
            out[symbol] = ProfitLockSignal(
                symbol=symbol,
                qty=qty,
                trigger_price=trigger_price,
                trail_stop_price=trail_stop_price,
                last_price=day_close,
            )
    return [out[s] for s in sorted(out)]


def _submit_profit_lock_signals(
    *,
    broker: AlpacaBroker,
    store: StateStore,
    profile: StrategyProfile,
    signals: list[ProfitLockSignal],
    now: datetime,
    profit_lock_order_type: str,
    cancel_existing_exit_orders: bool,
    stop_price_offset_bps: float,
    event_type: str,
    threshold_pct: float,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    if not signals:
        return
    payload_extra = dict(extra_payload or {})
    for signal in signals:
        symbol = signal.symbol
        qty = max(0.0, float(signal.qty))
        cancelled = 0
        if cancel_existing_exit_orders:
            cancelled = int(broker.cancel_open_orders_for_symbol(symbol))

        if profit_lock_order_type == "close_position":
            broker.close_position(symbol)
        elif profit_lock_order_type == "market_order":
            if qty > 0:
                broker.submit_market_order(symbol, "sell", qty)
        elif profit_lock_order_type == "stop_order":
            if qty > 0:
                stop_ref = float(signal.trigger_price)
                if profile.profit_lock_mode == "trailing" and signal.trail_stop_price > 0.0:
                    stop_ref = float(signal.trail_stop_price)
                cap = float(signal.last_price) * (1.0 - max(0.0, float(stop_price_offset_bps)) / 10_000.0)
                stop_price = min(stop_ref, cap)
                if stop_price <= 0.0:
                    stop_price = max(0.01, float(signal.last_price) * 0.995)
                broker.submit_stop_order(symbol, "sell", qty, stop_price=stop_price)
        elif profit_lock_order_type == "trailing_stop":
            if qty > 0:
                trail_percent = max(0.01, float(profile.profit_lock_trail_pct))
                broker.submit_trailing_stop_order(symbol, "sell", qty, trail_percent=trail_percent)
        else:
            raise ValueError(f"Unsupported profit-lock order type: {profit_lock_order_type}")

        payload: dict[str, Any] = {
            "ts": now.isoformat(),
            "symbol": symbol,
            "qty": qty,
            "profile": profile.name,
            "threshold_pct": threshold_pct,
            "profit_lock_order_type": profit_lock_order_type,
            "trigger_price": float(signal.trigger_price),
            "trail_stop_price": float(signal.trail_stop_price),
            "last_price": float(signal.last_price),
            "cancelled_open_orders": cancelled,
        }
        payload.update(payload_extra)
        store.append_event(event_type, payload)

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
        # Preserve literal value after "=" without shell evaluation.
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        if (not override) and (key in os.environ):
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _run_loop(args: argparse.Namespace) -> int:
    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        logger.info("Loaded %d env vars from %s (override=%s)", loaded, args.env_file, bool(args.env_override))
    profile = PROFILES[args.strategy_profile]
    paper = args.mode == "paper"
    alpaca = AlpacaConfig.from_env(paper=paper, data_feed=args.data_feed)

    store = StateStore(args.state_db)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=alpaca.paper)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    strategy = StrategyConfig()
    symbols = list(strategy.symbols)
    eval_time = _parse_hhmm(args.eval_time)

    logger.info(
        (
            "Starting parity runtime mode=%s profile=%s execute_orders=%s eval_time=%s "
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
            time.sleep(max(5, int(args.loop_sleep_seconds)))
            continue

        today = now.date()
        today_iso = today.isoformat()
        open_time, _close_time = _market_session_window(broker, now)
        current_time = dt_time(hour=now.hour, minute=now.minute)
        intraday_check_minutes = max(0, int(profile.intraday_profit_lock_check_minutes))

        # Optional intraday profit-lock checks for dedicated profiles.
        if (
            profile.enable_profit_lock
            and intraday_check_minutes > 0
            and now >= open_time
            and current_time < eval_time
        ):
            slot_idx = int((now.hour * 60 + now.minute) // intraday_check_minutes)
            slot_key = f"{today_iso}:{slot_idx}"
            last_slot_key = str(store.get("intraday_profit_lock_last_slot", ""))
            if slot_key != last_slot_key:
                lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
                daily_ohlc = _fetch_daily_ohlc(
                    loader,
                    symbols=symbols,
                    start=lookback_start,
                    end=now,
                    feed=alpaca.data_feed,
                )
                daily_closes: dict[str, list[float]] = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}
                threshold_pct = _current_threshold_pct(profile, daily_closes)
                last_prices, day_highs, latest_ts = _fetch_intraday_day_stats(
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
                    intraday_signals = _build_profit_lock_signals(
                        profile=profile,
                        positions=positions,
                        daily_ohlc=daily_ohlc,
                        last_prices=last_prices,
                        day_highs=day_highs,
                        threshold_pct=threshold_pct,
                        today=today,
                    )
                    if intraday_signals and args.execute_orders:
                        _submit_profit_lock_signals(
                            broker=broker,
                            store=store,
                            profile=profile,
                            signals=intraday_signals,
                            now=now,
                            profit_lock_order_type=args.profit_lock_order_type,
                            cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                            stop_price_offset_bps=float(args.stop_price_offset_bps),
                            event_type="parity_profit_lock_intraday_close",
                            threshold_pct=float(threshold_pct),
                            extra_payload={"intraday_slot": slot_key},
                        )
                        if args.profit_lock_order_type in {"close_position", "market_order"}:
                            time.sleep(float(args.post_close_refresh_seconds))
                store.put("intraday_profit_lock_last_slot", slot_key)

        if current_time < eval_time:
            logger.info("Waiting for parity eval window now=%s eval_time=%s", current_time, eval_time)
            time.sleep(int(args.loop_sleep_seconds))
            continue
        if str(store.get("parity_executed_day", "")) == today_iso:
            logger.info("Parity cycle already executed for %s", today_iso)
            time.sleep(int(args.loop_sleep_seconds))
            continue

        lookback_start = now - timedelta(days=max(365 * 3, int(args.daily_lookback_days)))
        daily_ohlc = _fetch_daily_ohlc(
            loader,
            symbols=symbols,
            start=lookback_start,
            end=now,
            feed=alpaca.data_feed,
        )
        daily_closes: dict[str, list[float]] = {s: [close for _, close, _ in rows] for s, rows in daily_ohlc.items()}
        baseline_target = evaluate_strategy(DictContext(closes=daily_closes))
        threshold_pct = _current_threshold_pct(profile, daily_closes)

        last_prices, day_highs, latest_ts = _fetch_intraday_day_stats(
            loader,
            symbols=symbols,
            session_open=open_time,
            now=now,
            feed=alpaca.data_feed,
        )
        if len(last_prices) < 2:
            logger.warning("Insufficient intraday bars for parity cycle; skipping this loop")
            time.sleep(int(args.loop_sleep_seconds))
            continue
        if latest_ts:
            freshest = max(latest_ts.values()).astimezone(NY)
            stale_minutes = int(max(0, (now - freshest).total_seconds() // 60))
            if stale_minutes > int(args.stale_data_threshold_minutes):
                logger.warning("Parity cycle skipped due to stale intraday data stale_minutes=%d", stale_minutes)
                time.sleep(int(args.loop_sleep_seconds))
                continue

        positions = broker.list_positions()
        profit_lock_signals = _build_profit_lock_signals(
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
            _submit_profit_lock_signals(
                broker=broker,
                store=store,
                profile=profile,
                signals=profit_lock_signals,
                now=now,
                profit_lock_order_type=args.profit_lock_order_type,
                cancel_existing_exit_orders=bool(args.cancel_existing_exit_orders),
                stop_price_offset_bps=float(args.stop_price_offset_bps),
                event_type="parity_profit_lock_close",
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
            target_weights=baseline_target,
            current_qty=current_qty,
            last_prices=last_prices,
            min_trade_weight_delta=float(strategy.rebalance_threshold),
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
                    "parity_rebalance_order",
                    {
                        "ts": now.isoformat(),
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "qty": qty,
                        "target_weight": float(intent.target_weight),
                        "profile": profile.name,
                        "order_type": order_type,
                        "take_profit_price": take_profit_price,
                        "stop_loss_price": stop_loss_price,
                    },
                )

        store.put("parity_executed_day", today_iso)
        store.put("parity_last_profile", profile.name)
        store.put("parity_last_target", baseline_target)
        store.append_event(
            "parity_cycle_complete",
            {
                "ts": now.isoformat(),
                "day": today_iso,
                "profile": profile.name,
                "threshold_pct": float(threshold_pct),
                "profit_lock_closed_symbols": symbols_to_close,
                "profit_lock_order_type": args.profit_lock_order_type,
                "rebalance_order_type": args.rebalance_order_type,
                "intent_count": len(intents),
                "orders_submitted": submitted,
                "execute_orders": bool(args.execute_orders),
            },
        )

        print(
            json.dumps(
                {
                    "day": today_iso,
                    "mode": args.mode,
                    "profile": profile.name,
                    "eval_time": args.eval_time,
                    "threshold_pct": float(threshold_pct),
                    "profit_lock_closed_symbols": symbols_to_close,
                    "profit_lock_order_type": args.profit_lock_order_type,
                    "rebalance_order_type": args.rebalance_order_type,
                    "intent_count": len(intents),
                    "orders_submitted": submitted,
                    "execute_orders": bool(args.execute_orders),
                },
                sort_keys=True,
            )
        )
        time.sleep(int(args.loop_sleep_seconds))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backtest-parity runtime loop (paper/live) for composer_original. "
            "Locked strategy profiles are supported, including dedicated intraday profit-lock variants."
        )
    )
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional .env file to load before resolving Alpaca credentials (safe literal parser, no shell eval).",
    )
    parser.add_argument(
        "--env-override",
        action="store_true",
        help="When used with --env-file, override existing process env vars.",
    )
    parser.add_argument(
        "--strategy-profile",
        choices=sorted(PROFILES.keys()),
        default="original_composer",
        help="Locked strategy profile.",
    )
    parser.add_argument("--execute-orders", action="store_true", help="Submit live/paper market orders.")
    parser.add_argument("--state-db", default="composer_original_parity_runtime.db")
    parser.add_argument("--eval-time", default="15:55", help="Daily NY time to run the parity cycle (HH:MM).")
    parser.add_argument("--loop-sleep-seconds", type=int, default=30)
    parser.add_argument("--data-feed", default="sip", help="Alpaca data feed: sip or iex.")
    parser.add_argument("--daily-lookback-days", type=int, default=800)
    parser.add_argument("--stale-data-threshold-minutes", type=int, default=3)
    parser.add_argument("--post-close-refresh-seconds", type=float, default=2.0)
    parser.add_argument(
        "--profit-lock-order-type",
        choices=["close_position", "market_order", "stop_order", "trailing_stop"],
        default="close_position",
        help="Execution style when profit-lock exit is triggered.",
    )
    parser.add_argument(
        "--cancel-existing-exit-orders",
        action="store_true",
        help="Cancel existing open orders on the symbol before placing new profit-lock exit orders.",
    )
    parser.add_argument(
        "--stop-price-offset-bps",
        type=float,
        default=2.0,
        help="For stop_order exits, cap stop at last_price*(1-offset_bps/10000) to avoid invalid sell-stop above market.",
    )
    parser.add_argument(
        "--rebalance-order-type",
        choices=["market", "bracket"],
        default="market",
        help="Order style for rebalance buys/sells (sells remain market in bracket mode).",
    )
    parser.add_argument(
        "--bracket-take-profit-pct",
        type=float,
        default=12.0,
        help="Bracket take-profit percent above last price for rebalance buys.",
    )
    parser.add_argument(
        "--bracket-stop-loss-pct",
        type=float,
        default=6.0,
        help="Bracket stop-loss percent below last price for rebalance buys.",
    )
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
        logger.exception("Parity runtime loop failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
