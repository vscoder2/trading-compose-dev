from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
from datetime import time as dt_time
import json
import os
import time
from typing import Any
from zoneinfo import ZoneInfo

from soxl_growth.backtest.parity_calibration import parse_rsi_span_csv, run_rsi_parity_calibration
from soxl_growth.backtest.parity import ComposerParityClient, compare_allocations
from soxl_growth.backtest.intraday_replay import ReplayConfig, run_intraday_overlay_replay
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import evaluate_strategy
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import InsufficientDataError
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import (
    AlpacaConfig,
    BacktestConfig,
    ComposerConfig,
    OverlayConfig,
    RuntimeConfig,
    StrategyConfig,
)
from soxl_growth.data.alpaca_data import AlpacaBarLoader, BarRequestSpec
from soxl_growth.db import StateStore
from soxl_growth.execution.broker import AlpacaBroker
from soxl_growth.execution.orders import build_rebalance_order_intents
from soxl_growth.execution.phased import (
    PhasedExecutionConfig,
    apply_phased_execution,
    compute_staging_fraction,
)
from soxl_growth.execution.policy import build_stop_levels, to_whole_share_qty
from soxl_growth.execution.reliability import ExecutionRetryConfig, execute_with_retries
from soxl_growth.indicators.drawdown import max_drawdown_percent
from soxl_growth.indicators.rsi import rsi_base
from soxl_growth.logging_setup import configure_logging, get_logger
from soxl_growth.overlay.overlay_state_machine import OverlayMetrics, OverlaySnapshot, OverlayStateMachine
from soxl_growth.runtime_controls import (
    SessionWindow,
    is_no_trade_window,
    parse_symbol_csv,
    select_positions_to_flatten,
    should_run_overnight_flatten,
)

logger = get_logger(__name__)
NY = ZoneInfo("America/New_York")


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return sorted(_json_safe(v) for v in value)
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _load_wide_csv_price_history(path: str) -> dict[str, list[tuple[date, float]]]:
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or reader.fieldnames[0].lower() != "date":
            raise ValueError("CSV must have first column named 'date'.")

        symbols = [c for c in reader.fieldnames if c.lower() != "date"]
        history: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
        for row in reader:
            day = date.fromisoformat(row["date"])
            for symbol in symbols:
                price = float(row[symbol])
                history[symbol].append((day, price))
    return history


def _load_wide_minute_csv_history(path: str) -> dict[str, list[tuple[datetime, float]]]:
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or reader.fieldnames[0].lower() not in {"timestamp", "datetime"}:
            raise ValueError("Minute CSV must have first column named 'timestamp' or 'datetime'.")

        ts_col = reader.fieldnames[0]
        symbols = [c for c in reader.fieldnames if c != ts_col]
        history: dict[str, list[tuple[datetime, float]]] = {s: [] for s in symbols}
        for row in reader:
            ts = datetime.fromisoformat(row[ts_col])
            for symbol in symbols:
                price = float(row[symbol])
                history[symbol].append((ts, price))
    return history


def _rolling_returns(values: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        cur = values[i]
        if prev > 0:
            out.append(cur / prev - 1.0)
    return out


def _annualized_vol_from_returns(returns: list[float]) -> float:
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / len(returns)
    return (var ** 0.5) * (252 * 390) ** 0.5 * 100.0


def _downsample_close(values: list[float], step: int) -> list[float]:
    if step <= 0:
        raise ValueError("step must be positive")
    return [values[i] for i in range(step - 1, len(values), step)]


def _ema(values: list[float], span: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (span + 1.0)
    cur = values[0]
    for x in values[1:]:
        cur = alpha * x + (1.0 - alpha) * cur
    return cur


def _compute_fade_confirmation(soxl_minute_closes: list[float]) -> bool:
    # Documented rule:
    # fade_confirmed = (close_15m < ema_15m_20) and (cumret_5m_12 < 0)
    closes_15m = _downsample_close(soxl_minute_closes, 15)
    closes_5m = _downsample_close(soxl_minute_closes, 5)
    if len(closes_15m) < 20 or len(closes_5m) < 13:
        return False
    close_15m = closes_15m[-1]
    ema_15m_20 = _ema(closes_15m[-20:], span=20)
    if ema_15m_20 is None:
        return False
    cumret_5m_12 = 100.0 * (closes_5m[-1] / closes_5m[-13] - 1.0)
    return (close_15m < ema_15m_20) and (cumret_5m_12 < 0.0)


def _detect_overbought_fade_regime(daily_closes: dict[str, list[float]]) -> bool:
    soxl = daily_closes.get("SOXL", [])
    if len(soxl) < 61:
        return False
    mdd60 = max_drawdown_percent(soxl, 60)
    rsi32 = rsi_base(soxl, 32)
    if mdd60 is None or rsi32 is None:
        return False
    return (mdd60 < 50.0) and (rsi32 > 62.1995)


def _select_overlay_target(minute_closes: dict[str, list[float]]) -> dict[str, float]:
    """Select a defensive overlay basket using short-horizon momentum.

    The daily baseline remains the primary strategy brain; this only defines a
    temporary defensive target during high-risk intraday conditions.
    """
    defensive = ["SOXS", "SQQQ", "SPXS", "TMF"]
    scores: list[tuple[float, str]] = []
    for symbol in defensive:
        closes = minute_closes.get(symbol, [])
        if len(closes) < 2 or closes[0] <= 0:
            scores.append((-1e9, symbol))
            continue
        score = 100.0 * (closes[-1] / closes[0] - 1.0)
        scores.append((score, symbol))
    scores.sort(reverse=True)
    top = scores[0][1]
    return {top: 1.0}


def run_backtest_mode(args: argparse.Namespace) -> int:
    price_history = _load_wide_csv_price_history(args.prices_csv)
    config = BacktestConfig(
        initial_equity=args.initial_equity,
        warmup_days=args.warmup_days,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
        min_trade_weight_delta=args.min_trade_weight_delta,
        phased_execution_enabled=args.enable_phased_execution,
        phased_rv_trigger=args.phased_rv_trigger,
        phased_extreme_rv_trigger=args.phased_extreme_rv_trigger,
        phased_stage_fraction=args.phased_stage_fraction,
        phased_extreme_stage_fraction=args.phased_extreme_stage_fraction,
        phased_min_notional=args.phased_min_notional,
    )
    result = run_backtest(price_history=price_history, config=config)

    summary = {
        "final_equity": result.final_equity,
        "total_return_pct": result.total_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "cagr_pct": result.cagr_pct,
        "avg_daily_return_pct": result.avg_daily_return_pct,
        "trade_count": len(result.trades),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _fetch_recent_closes(
    loader: AlpacaBarLoader,
    symbols: list[str],
    minutes: int,
    feed: str,
) -> tuple[dict[str, list[float]], dict[str, datetime], dict[str, list[float]]]:
    end = datetime.now(tz=NY)
    start = end - timedelta(minutes=minutes)
    bars = loader.get_bars(
        BarRequestSpec(
            symbols=symbols,
            start=start,
            end=end,
            timeframe="1Min",
            adjustment="raw",
            feed=feed,
        )
    )
    out: dict[str, list[float]] = {s: [] for s in symbols}
    spread_proxy_bps_by_symbol: dict[str, list[float]] = {s: [] for s in symbols}
    latest_ts: dict[str, datetime] = {}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        out.setdefault(bar.symbol, []).append(bar.close)
        if bar.close > 0:
            spread_bps = 10_000.0 * max(0.0, bar.high - bar.low) / bar.close
            spread_proxy_bps_by_symbol.setdefault(bar.symbol, []).append(spread_bps)
        latest_ts[bar.symbol] = bar.timestamp
    return out, latest_ts, spread_proxy_bps_by_symbol


def _compute_spread_proxy_bps(
    spread_proxy_bps_by_symbol: dict[str, list[float]],
    focus_symbols: list[str] | tuple[str, ...] = ("SOXL", "TQQQ"),
    lookback_bars: int = 5,
) -> float:
    values: list[float] = []
    for symbol in focus_symbols:
        seq = spread_proxy_bps_by_symbol.get(symbol, [])
        if seq:
            values.extend(seq[-max(1, lookback_bars) :])
    if not values:
        for seq in spread_proxy_bps_by_symbol.values():
            values.extend(seq[-max(1, lookback_bars) :])
    if not values:
        return 0.0
    return sum(values) / len(values)


def _parse_hhmm(value: str) -> dt_time:
    text = str(value).strip()
    try:
        hh_str, mm_str = text.split(":", 1)
        hour = int(hh_str)
        minute = int(mm_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return dt_time(hour=hour, minute=minute)
    except Exception as exc:
        raise ValueError(f"Invalid HH:MM value: {value!r}") from exc


def _is_time_in_window(now: datetime, start: dt_time, end: dt_time) -> bool:
    current = dt_time(hour=now.hour, minute=now.minute)
    return start <= current <= end


def _profit_lock_trigger_symbols(
    positions: list[dict[str, Any]],
    threshold_pct: float,
) -> tuple[list[str], float]:
    symbols: list[str] = []
    max_intraday_gain_pct = float("-inf")
    for pos in positions:
        qty = float(pos.get("qty", 0.0) or 0.0)
        market_value = abs(float(pos.get("market_value", 0.0) or 0.0))
        if qty <= 0.0 or market_value <= 0.0:
            continue
        raw = pos.get("unrealized_intraday_plpc")
        if raw is None:
            raw = pos.get("unrealized_plpc")
        if raw is None:
            continue
        gain_pct = 100.0 * float(raw)
        if gain_pct > max_intraday_gain_pct:
            max_intraday_gain_pct = gain_pct
        if gain_pct >= threshold_pct:
            sym = str(pos.get("symbol", "")).upper()
            if sym:
                symbols.append(sym)
    if max_intraday_gain_pct == float("-inf"):
        max_intraday_gain_pct = 0.0
    return sorted(set(symbols)), max_intraday_gain_pct


def _fetch_daily_history(loader: AlpacaBarLoader, symbols: list[str], days: int, feed: str) -> dict[str, list[float]]:
    end = datetime.now(tz=NY)
    start = end - timedelta(days=days * 2)
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
    out: dict[str, list[float]] = {s: [] for s in symbols}
    for bar in sorted(bars, key=lambda x: x.timestamp):
        out.setdefault(bar.symbol, []).append(bar.close)
    return out


def _extract_oracle_allocations(payload: dict) -> dict[str, dict[str, float]]:
    """Best-effort extraction from Composer backtest response JSON."""
    for key in ("allocations", "daily_allocations", "weights"):
        maybe = payload.get(key)
        if isinstance(maybe, dict):
            return {str(k): {str(sk): float(sv) for sk, sv in v.items()} for k, v in maybe.items()}
        if isinstance(maybe, list):
            out: dict[str, dict[str, float]] = {}
            for item in maybe:
                day = str(item.get("date") or item.get("trading_day") or "")
                weights = item.get("weights") or item.get("allocation") or {}
                if day and isinstance(weights, dict):
                    out[day] = {str(sk): float(sv) for sk, sv in weights.items()}
            if out:
                return out
    return {}


def run_parity_report_mode(args: argparse.Namespace) -> int:
    price_history = _load_wide_csv_price_history(args.prices_csv)
    bt_config = BacktestConfig(
        initial_equity=args.initial_equity,
        warmup_days=args.warmup_days,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
        min_trade_weight_delta=args.min_trade_weight_delta,
    )
    local = run_backtest(price_history=price_history, config=bt_config)
    local_alloc = {d.isoformat(): w for d, w in local.allocations}

    token = args.composer_token or os.getenv("COMPOSER_API_TOKEN")
    composer_cfg = ComposerConfig(api_base_url=args.composer_api_base_url, api_token=token)
    client = ComposerParityClient(composer_cfg)
    payload = client.fetch_backtest(
        symphony_id=args.symphony_id,
        start_date=date.fromisoformat(args.start_date) if args.start_date else None,
        end_date=date.fromisoformat(args.end_date) if args.end_date else None,
    )
    oracle_alloc = _extract_oracle_allocations(payload)
    mismatches = compare_allocations(
        oracle_daily_allocations=oracle_alloc,
        local_daily_allocations=local_alloc,
        tolerance=args.tolerance,
    )
    print(
        json.dumps(
            {
                "oracle_days": len(oracle_alloc),
                "local_days": len(local_alloc),
                "mismatch_count": len(mismatches),
                "sample_mismatches": [m.__dict__ for m in mismatches[: args.sample_limit]],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_parity_calibrate_rsi_mode(args: argparse.Namespace) -> int:
    """Calibrate optional RSI smoothing spans against Composer parity mismatches."""
    price_history = _load_wide_csv_price_history(args.prices_csv)
    bt_config = BacktestConfig(
        initial_equity=args.initial_equity,
        warmup_days=args.warmup_days,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
        min_trade_weight_delta=args.min_trade_weight_delta,
    )

    token = args.composer_token or os.getenv("COMPOSER_API_TOKEN")
    composer_cfg = ComposerConfig(api_base_url=args.composer_api_base_url, api_token=token)
    client = ComposerParityClient(composer_cfg)
    payload = client.fetch_backtest(
        symphony_id=args.symphony_id,
        start_date=date.fromisoformat(args.start_date) if args.start_date else None,
        end_date=date.fromisoformat(args.end_date) if args.end_date else None,
    )
    oracle_alloc = _extract_oracle_allocations(payload)
    spans = parse_rsi_span_csv(args.smoothing_spans)

    results, near_points = run_rsi_parity_calibration(
        oracle_daily_allocations=oracle_alloc,
        price_history=price_history,
        backtest_config=bt_config,
        smoothing_spans=spans,
        tolerance=args.tolerance,
        threshold_band=args.threshold_band,
    )

    ranked = [x.__dict__ for x in results]
    best = ranked[0] if ranked else None
    near_sample = [p.__dict__ for p in near_points[: args.sample_limit]]
    output: dict[str, Any] = {
        "oracle_days": len(oracle_alloc),
        "candidate_count": len(ranked),
        "threshold_band": args.threshold_band,
        "ranked_candidates": ranked,
        "best_candidate": best,
        "near_threshold_sample": near_sample,
    }
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(_json_safe(output), f, indent=2, sort_keys=True)
        logger.info("Wrote RSI parity calibration report to %s", args.output_json)
    print(json.dumps(_json_safe(output), indent=2, sort_keys=True))
    return 0


def _e2e_check(
    report: dict[str, object],
    *,
    name: str,
    required: bool,
    fn,
) -> tuple[bool, object]:
    start = time.monotonic()
    try:
        result = fn()
        ok = True
        details = result
    except Exception as exc:
        ok = False
        details = {"error": str(exc)}
        logger.exception("E2E check failed: %s", name)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    entry = {
        "name": name,
        "ok": ok,
        "required": required,
        "elapsed_ms": elapsed_ms,
        "details": _json_safe(details),
    }
    checks = report.setdefault("checks", [])
    assert isinstance(checks, list)
    checks.append(entry)
    return ok, details


def run_runtime_e2e_paper_mode(args: argparse.Namespace) -> int:
    """Run bounded paper-account end-to-end validation and emit JSON report."""
    started_at = datetime.now(tz=NY).isoformat()
    report: dict[str, object] = {
        "mode": "runtime-e2e-paper",
        "started_at": started_at,
        "checks": [],
        "loops": [],
    }

    try:
        alpaca = AlpacaConfig.from_env(paper=True, data_feed=args.data_feed)
    except Exception as exc:
        report["fatal_error"] = f"credentials_init_failed: {exc}"
        if args.report_json:
            with open(args.report_json, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, sort_keys=True)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    runtime = RuntimeConfig(mode="paper", state_db_path=args.state_db, log_level=args.log_level)
    strategy = StrategyConfig(overlay_eval_minutes=args.overlay_eval_minutes)
    overlay_cfg = OverlayConfig()
    retry_cfg = ExecutionRetryConfig(
        max_retries=args.max_order_retries,
        poll_seconds=args.order_poll_seconds,
        stale_seconds=args.order_stale_seconds,
    )
    phased_cfg = PhasedExecutionConfig(
        enable=args.enable_phased_execution,
        rv_trigger=args.phased_rv_trigger,
        spread_trigger_bps=args.phased_spread_trigger_bps,
        extreme_rv_trigger=args.phased_extreme_rv_trigger,
        extreme_spread_trigger_bps=args.phased_extreme_spread_bps,
        stage_fraction=args.phased_stage_fraction,
        extreme_stage_fraction=args.phased_extreme_stage_fraction,
        min_notional=args.phased_min_notional,
    )

    store = StateStore(runtime.state_db_path)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=True)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    overlay_sm = OverlayStateMachine(overlay_cfg)

    symbols = list(strategy.symbols)
    eval_symbols = sorted(set(symbols + ["TQQQ", "SOXL"]))

    mandatory_ok = True

    ok, _ = _e2e_check(report, name="broker_get_account", required=True, fn=broker.get_account)
    mandatory_ok = mandatory_ok and ok
    if not ok:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    ok, clock = _e2e_check(report, name="broker_get_clock", required=True, fn=broker.get_clock)
    mandatory_ok = mandatory_ok and ok
    now = datetime.now(tz=NY)
    if ok and isinstance(clock, dict) and "timestamp" in clock:
        now = clock["timestamp"].astimezone(NY)

    def _calendar_fn():
        return broker.get_calendar(start=now, end=now)

    ok, _ = _e2e_check(report, name="broker_get_calendar", required=True, fn=_calendar_fn)
    mandatory_ok = mandatory_ok and ok

    daily_closes: dict[str, list[float]] = {}
    baseline_target: dict[str, float] = {}
    overbought_flag = False

    def _daily_fetch_fn():
        return _fetch_daily_history(loader, symbols=symbols, days=320, feed=alpaca.data_feed)

    ok, daily_result = _e2e_check(report, name="fetch_daily_history", required=True, fn=_daily_fetch_fn)
    mandatory_ok = mandatory_ok and ok
    if ok and isinstance(daily_result, dict):
        daily_closes = daily_result

        def _baseline_fn():
            return evaluate_strategy(DictContext(closes=daily_closes))

        ok2, baseline_res = _e2e_check(report, name="evaluate_daily_baseline", required=True, fn=_baseline_fn)
        mandatory_ok = mandatory_ok and ok2
        if ok2 and isinstance(baseline_res, dict):
            baseline_target = baseline_res
            overbought_flag = _detect_overbought_fade_regime(daily_closes)
            store.put("e2e_baseline_target", baseline_target)
            store.put("e2e_overbought_fade_regime", overbought_flag)

    for i in range(args.loops):
        loop_report: dict[str, object] = {"loop": i + 1, "ok": True}
        loop_start = datetime.now(tz=NY)
        try:
            minute_closes, latest_ts, spread_bps_by_symbol = _fetch_recent_closes(
                loader,
                symbols=eval_symbols,
                minutes=args.minute_lookback_minutes,
                feed=alpaca.data_feed,
            )
            soxl_closes = minute_closes.get("SOXL", [])
            tqqq_closes = minute_closes.get("TQQQ", [])
            if len(soxl_closes) < 2 or len(tqqq_closes) < 2:
                loop_report["ok"] = False
                loop_report["error"] = "insufficient_intraday_data"
                loops = report.setdefault("loops", [])
                assert isinstance(loops, list)
                loops.append(loop_report)
                time.sleep(args.sleep_seconds)
                continue

            stale_minutes = 0
            if latest_ts:
                latest = max(latest_ts.values())
                stale_minutes = int(max(0, (loop_start - latest.astimezone(NY)).total_seconds() // 60))

            high_water_mark = max(soxl_closes[-60:]) if len(soxl_closes) >= 60 else max(soxl_closes)
            dd_intra = 100.0 * (high_water_mark - soxl_closes[-1]) / high_water_mark
            rv_15m = _annualized_vol_from_returns(_rolling_returns(tqqq_closes[-15:]))
            spread_proxy_bps = _compute_spread_proxy_bps(spread_bps_by_symbol)
            overlay_target = _select_overlay_target(minute_closes)
            fade_confirmed = _compute_fade_confirmation(soxl_closes)
            vwap_60 = sum(soxl_closes[-60:]) / len(soxl_closes[-60:])
            rsi_15_proxy = 50.0 + (_rolling_returns(soxl_closes[-15:])[-1] * 500.0 if len(soxl_closes) >= 16 else 0.0)
            rsi_15_proxy = max(0.0, min(100.0, rsi_15_proxy))

            positions = broker.list_positions()
            account_now = broker.get_account()
            equity = float(account_now["equity"])
            current_qty = {p["symbol"]: float(p["qty"]) for p in positions}

            metrics = OverlayMetrics(
                dd_intra_soxl=dd_intra,
                rv_15m_tqqq=rv_15m,
                price_soxl=soxl_closes[-1],
                vwap_60m_soxl=vwap_60,
                rsi_15m_soxl=rsi_15_proxy,
                realized_pnl_pct=0.0,
                data_failure_minutes=stale_minutes,
                margin_usage=0.0,
                overbought_fade_regime=overbought_flag,
                fade_confirmed=fade_confirmed,
            )
            snapshot = OverlaySnapshot(flip_count_today=0, minutes_since_last_trade=10_000)
            step = overlay_sm.step(metrics, snapshot, baseline_target, overlay_target)
            target = step.target

            last_prices = {symbol: closes[-1] for symbol, closes in minute_closes.items() if closes}
            intents = build_rebalance_order_intents(
                equity=equity,
                target_weights=target,
                current_qty=current_qty,
                last_prices=last_prices,
                min_trade_weight_delta=strategy.rebalance_threshold,
            )
            stage_fraction = compute_staging_fraction(
                rv_annualized_pct=rv_15m,
                spread_bps=spread_proxy_bps,
                config=phased_cfg,
            )
            staged = apply_phased_execution(
                intents=intents,
                last_prices=last_prices,
                staging_fraction=stage_fraction,
                min_notional=phased_cfg.min_notional,
            )
            effective_intents = staged.intents

            loop_report.update(
                {
                    "timestamp": loop_start.isoformat(),
                    "overlay_state": str(step.state),
                    "overlay_reason": step.reason,
                    "overbought_fade_regime": overbought_flag,
                    "fade_confirmed": fade_confirmed,
                    "stale_minutes": stale_minutes,
                    "spread_proxy_bps": spread_proxy_bps,
                    "target": target,
                    "intent_count": len(intents),
                    "effective_intent_count": len(effective_intents),
                    "phased_staging_fraction": staged.staging_fraction,
                    "phased_staged_buy_count": staged.staged_buy_count,
                    "phased_skipped_buy_count": staged.skipped_buy_count,
                }
            )

            outcomes: list[dict[str, object]] = []
            if args.execute_orders and effective_intents:
                for intent in effective_intents[: args.max_intents_per_loop]:

                    def _submit(order_intent):
                        if args.order_mode == "bracket":
                            qty = to_whole_share_qty(order_intent.qty)
                            if qty <= 0:
                                raise ValueError("Bracket mode requires whole-share qty >= 1")
                            tp, sl = build_stop_levels(
                                last_price=last_prices[order_intent.symbol],
                                side=order_intent.side,
                                take_profit_pct=args.take_profit_pct,
                                stop_loss_pct=args.stop_loss_pct,
                            )
                            return broker.submit_bracket_order(
                                symbol=order_intent.symbol,
                                side=order_intent.side,
                                qty=qty,
                                take_profit_price=tp,
                                stop_loss_price=sl,
                            )
                        if args.order_mode == "fractional":
                            notional = abs(order_intent.qty) * float(last_prices[order_intent.symbol])
                            return broker.submit_notional_market_order(order_intent.symbol, order_intent.side, notional)
                        return broker.submit_market_order(order_intent.symbol, order_intent.side, order_intent.qty)

                    if args.order_mode != "fractional" and retry_cfg.max_retries > 0:
                        outcome = execute_with_retries(
                            submit_fn=_submit,
                            intent=intent,
                            retry_cfg=retry_cfg,
                            get_order_fn=broker.get_order,
                            cancel_order_fn=broker.cancel_order,
                            replace_order_fn=broker.replace_order,
                        )
                        outcomes.append(
                            {
                                "symbol": intent.symbol,
                                "success": outcome.success,
                                "status": outcome.final_status,
                                "filled_qty": outcome.filled_qty,
                                "attempts": outcome.attempts,
                            }
                        )
                    else:
                        order = _submit(intent)
                        outcomes.append(
                            {
                                "symbol": intent.symbol,
                                "success": True,
                                "status": "submitted",
                                "filled_qty": order.qty,
                                "attempts": 0,
                            }
                        )

                if args.close_after_order:
                    for item in outcomes:
                        if item.get("success"):
                            sym = str(item["symbol"])
                            try:
                                broker.close_position(sym)
                            except Exception:
                                logger.exception("Failed to close post-e2e position symbol=%s", sym)

            loop_report["order_outcomes"] = outcomes
        except Exception as exc:
            loop_report["ok"] = False
            loop_report["error"] = str(exc)
            logger.exception("E2E loop failed loop=%d", i + 1)

        loops = report.setdefault("loops", [])
        assert isinstance(loops, list)
        loops.append(loop_report)
        if not bool(loop_report.get("ok", False)):
            mandatory_ok = False

        if i < args.loops - 1:
            time.sleep(args.sleep_seconds)

    report["completed_at"] = datetime.now(tz=NY).isoformat()
    report["overall_ok"] = bool(mandatory_ok)

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump(_json_safe(report), f, indent=2, sort_keys=True)
        logger.info("Wrote e2e report to %s", args.report_json)

    print(json.dumps(_json_safe(report), indent=2, sort_keys=True))
    return 0 if mandatory_ok else 1


def run_overlay_replay_mode(args: argparse.Namespace) -> int:
    daily_history = _load_wide_csv_price_history(args.daily_prices_csv)
    minute_history = _load_wide_minute_csv_history(args.minute_prices_csv)

    symbols = sorted(daily_history.keys())
    dates = [d for d, _ in daily_history[symbols[0]]]
    baseline_by_day: dict[date, dict[str, float]] = {}
    overbought_by_day: dict[date, bool] = {}

    # Build daily baseline target path first, then replay overlay on minutes.
    for idx, day in enumerate(dates):
        closes_ctx = {s: [p for _, p in daily_history[s][: idx + 1]] for s in symbols}
        overbought_by_day[day] = _detect_overbought_fade_regime(closes_ctx)
        try:
            baseline_by_day[day] = evaluate_strategy(DictContext(closes=closes_ctx))
        except InsufficientDataError:
            continue

    replay_result = run_intraday_overlay_replay(
        minute_history=minute_history,
        baseline_target_by_day=baseline_by_day,
        overbought_fade_by_day=overbought_by_day,
        replay_config=ReplayConfig(
            eval_minutes=args.overlay_eval_minutes,
            lookback_minutes=args.lookback_minutes,
        ),
    )
    print(
        json.dumps(
            {
                "points": len(replay_result.points),
                "flips": replay_result.flips,
                "hedge_points": replay_result.hedge_points,
                "kill_switch_points": replay_result.kill_switch_points,
                "sample_points": [
                    {
                        "timestamp": p.timestamp.isoformat(),
                        "state": p.state,
                        "reason": p.reason,
                        "target": p.target,
                    }
                    for p in replay_result.points[: args.sample_limit]
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_runtime_mode(args: argparse.Namespace) -> int:
    paper = args.mode == "paper"
    runtime = RuntimeConfig(mode=args.mode, state_db_path=args.state_db, log_level=args.log_level)
    strategy = StrategyConfig(overlay_eval_minutes=args.overlay_eval_minutes)
    overlay_cfg = OverlayConfig()
    alpaca = AlpacaConfig.from_env(paper=paper, data_feed=args.data_feed)

    store = StateStore(runtime.state_db_path)
    broker = AlpacaBroker(alpaca.api_key, alpaca.api_secret, paper=alpaca.paper)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    overlay_sm = OverlayStateMachine(overlay_cfg)
    retry_cfg = ExecutionRetryConfig(
        max_retries=args.max_order_retries,
        poll_seconds=args.order_poll_seconds,
        stale_seconds=args.order_stale_seconds,
    )
    phased_cfg = PhasedExecutionConfig(
        enable=args.enable_phased_execution,
        rv_trigger=args.phased_rv_trigger,
        spread_trigger_bps=args.phased_spread_trigger_bps,
        extreme_rv_trigger=args.phased_extreme_rv_trigger,
        extreme_spread_trigger_bps=args.phased_extreme_spread_bps,
        stage_fraction=args.phased_stage_fraction,
        extreme_stage_fraction=args.phased_extreme_stage_fraction,
        min_notional=args.phased_min_notional,
    )

    symbols = list(strategy.symbols)
    eval_symbols = sorted(set(symbols + ["TQQQ", "SOXL"]))
    leveraged_symbols = parse_symbol_csv(args.leveraged_symbols)
    allow_overnight_symbols = parse_symbol_csv(args.allow_overnight_symbols)

    baseline_target: dict[str, float] = store.get("baseline_target", {}) or {}
    overbought_fade_regime = bool(store.get("overbought_fade_regime", False) or False)
    last_baseline_day = store.get("last_baseline_day")
    session_window: SessionWindow | None = None

    logger.info(
        "Starting runtime mode=%s execute_orders=%s order_mode=%s",
        runtime.mode,
        args.execute_orders,
        args.order_mode,
    )
    stale_recovery_failures = 0
    profit_lock_reeval_start = _parse_hhmm(args.profit_lock_reeval_start)
    profit_lock_reeval_end = _parse_hhmm(args.profit_lock_reeval_end)
    if profit_lock_reeval_start > profit_lock_reeval_end:
        raise ValueError(
            f"profit-lock re-eval window must be ordered start<=end; got {args.profit_lock_reeval_start} -> {args.profit_lock_reeval_end}"
        )

    while True:
        clock = broker.get_clock()
        now = clock["timestamp"].astimezone(NY)
        if not clock["is_open"]:
            logger.info("Market closed. next_open=%s", clock["next_open"])
            sleep_seconds = max(5, min(60, args.loop_sleep_seconds))
            time.sleep(sleep_seconds)
            continue

        today = now.date().isoformat()
        # Cache current-session open/close boundaries from market calendar.
        if session_window is None or session_window.open_time.date().isoformat() != today:
            try:
                cal = broker.get_calendar(start=now, end=now)
                if cal:
                    row = cal[0]
                    open_hh, open_mm = [int(x) for x in str(row["open"]).split(":")[:2]]
                    close_hh, close_mm = [int(x) for x in str(row["close"]).split(":")[:2]]
                    session_window = SessionWindow(
                        open_time=now.replace(hour=open_hh, minute=open_mm, second=0, microsecond=0),
                        close_time=now.replace(hour=close_hh, minute=close_mm, second=0, microsecond=0),
                    )
                else:
                    session_window = SessionWindow(
                        open_time=now.replace(hour=9, minute=30, second=0, microsecond=0),
                        close_time=now.replace(hour=16, minute=0, second=0, microsecond=0),
                    )
            except Exception:
                logger.exception("Failed to fetch calendar session; using default session window")
                session_window = SessionWindow(
                    open_time=now.replace(hour=9, minute=30, second=0, microsecond=0),
                    close_time=now.replace(hour=16, minute=0, second=0, microsecond=0),
                )

        if last_baseline_day != today:
            try:
                daily_closes = _fetch_daily_history(loader, symbols=symbols, days=320, feed=alpaca.data_feed)
                baseline_target = evaluate_strategy(DictContext(closes=daily_closes))
                overbought_fade_regime = _detect_overbought_fade_regime(daily_closes)
                store.put("baseline_target", baseline_target)
                store.put("overbought_fade_regime", overbought_fade_regime)
                store.put("last_baseline_day", today)
                last_baseline_day = today
                logger.info(
                    "Computed baseline target for %s target=%s overbought_fade_regime=%s",
                    today,
                    baseline_target,
                    overbought_fade_regime,
                )
            except Exception:
                logger.exception("Failed to compute baseline target; using previous target.")

        minute_closes, latest_ts, spread_bps_by_symbol = _fetch_recent_closes(
            loader,
            symbols=eval_symbols,
            minutes=75,
            feed=alpaca.data_feed,
        )
        soxl_closes = minute_closes.get("SOXL", [])
        tqqq_closes = minute_closes.get("TQQQ", [])
        if len(soxl_closes) < 2 or len(tqqq_closes) < 2:
            logger.warning("Insufficient intraday bars for overlay evaluation.")
            time.sleep(args.loop_sleep_seconds)
            continue

        stale_minutes = 0
        if latest_ts:
            latest = max(latest_ts.values())
            stale_minutes = int(max(0, (now - latest.astimezone(NY)).total_seconds() // 60))

        if stale_minutes > args.stale_data_threshold_minutes:
            logger.error(
                "Detected stale intraday data stale_minutes=%d threshold=%d",
                stale_minutes,
                args.stale_data_threshold_minutes,
            )
            store.append_event(
                "stale_data_detected",
                {"ts": now.isoformat(), "stale_minutes": stale_minutes, "feed": alpaca.data_feed},
            )
            if args.fallback_data_feed and args.fallback_data_feed != alpaca.data_feed:
                try:
                    logger.warning("Attempting stale-data recovery via fallback feed=%s", args.fallback_data_feed)
                    minute_closes, latest_ts, spread_bps_by_symbol = _fetch_recent_closes(
                        loader,
                        symbols=eval_symbols,
                        minutes=75,
                        feed=args.fallback_data_feed,
                    )
                    if latest_ts:
                        latest = max(latest_ts.values())
                        stale_minutes = int(max(0, (now - latest.astimezone(NY)).total_seconds() // 60))
                except Exception:
                    logger.exception("Fallback feed recovery failed")

            if stale_minutes > args.stale_data_threshold_minutes:
                stale_recovery_failures += 1
                store.put("stale_recovery_failures", stale_recovery_failures)
                if args.execute_orders and stale_recovery_failures >= args.cancel_all_on_stale_after:
                    logger.error("Stale data persisted; cancelling all open orders for safety")
                    try:
                        broker.cancel_all_orders()
                        store.append_event("cancel_all_orders_stale_data", {"ts": now.isoformat()})
                    except Exception:
                        logger.exception("Failed to cancel all orders during stale-data emergency")
                    stale_recovery_failures = 0
                time.sleep(args.loop_sleep_seconds)
                continue
            stale_recovery_failures = 0
        else:
            stale_recovery_failures = 0

        high_water_mark = max(soxl_closes[-60:]) if len(soxl_closes) >= 60 else max(soxl_closes)
        dd_intra = 100.0 * (high_water_mark - soxl_closes[-1]) / high_water_mark
        rv_15m = _annualized_vol_from_returns(_rolling_returns(tqqq_closes[-15:]))
        spread_proxy_bps = _compute_spread_proxy_bps(spread_bps_by_symbol)

        overlay_target = _select_overlay_target(minute_closes)
        fade_confirmed = _compute_fade_confirmation(soxl_closes)

        # Lightweight intraday confirmation proxies.
        vwap_60 = sum(soxl_closes[-60:]) / len(soxl_closes[-60:])
        rsi_15_proxy = 50.0 + (_rolling_returns(soxl_closes[-15:])[-1] * 500.0 if len(soxl_closes) >= 16 else 0.0)
        rsi_15_proxy = max(0.0, min(100.0, rsi_15_proxy))

        positions = broker.list_positions()
        account = broker.get_account()
        equity = float(account["equity"])
        current_qty = {p["symbol"]: float(p["qty"]) for p in positions}

        now_iso = now.isoformat()
        profit_lock_active_key = f"profit_lock_active_{today}"
        profit_lock_trigger_count_key = f"profit_lock_trigger_count_{today}"
        profit_lock_reeval_done_key = f"profit_lock_reeval_done_{today}"
        profit_lock_active_today = bool(store.get(profit_lock_active_key, False) or False)
        profit_lock_trigger_count_today = int(store.get(profit_lock_trigger_count_key, 0) or 0)
        profit_lock_reeval_done_today = bool(store.get(profit_lock_reeval_done_key, False) or False)
        profit_lock_in_reeval_window = False
        profit_lock_reeval_due = False

        if args.enable_profit_lock:
            if (
                (not profit_lock_active_today)
                and (not profit_lock_reeval_done_today)
                and (profit_lock_trigger_count_today < max(1, int(args.profit_lock_max_triggers_per_day)))
            ):
                trigger_symbols, max_intraday_gain_pct = _profit_lock_trigger_symbols(
                    positions=positions,
                    threshold_pct=float(args.profit_lock_threshold_pct),
                )
                if trigger_symbols:
                    profit_lock_trigger_count_today += 1
                    profit_lock_active_today = True
                    store.put(profit_lock_active_key, True)
                    store.put(profit_lock_trigger_count_key, profit_lock_trigger_count_today)
                    store.put(profit_lock_reeval_done_key, False)
                    store.put("last_profit_lock_trigger_ts", now_iso)
                    store.append_event(
                        "profit_lock_triggered",
                        {
                            "ts": now_iso,
                            "threshold_pct": float(args.profit_lock_threshold_pct),
                            "max_intraday_gain_pct": max_intraday_gain_pct,
                            "symbols": trigger_symbols,
                            "trigger_count_today": profit_lock_trigger_count_today,
                        },
                    )
                    logger.warning(
                        "Profit-lock triggered threshold_pct=%.2f symbols=%s max_intraday_gain_pct=%.2f",
                        float(args.profit_lock_threshold_pct),
                        trigger_symbols,
                        max_intraday_gain_pct,
                    )
                    if args.execute_orders:
                        for symbol in trigger_symbols:
                            try:
                                broker.close_position(symbol)
                                store.append_event("profit_lock_flatten_close", {"ts": now_iso, "symbol": symbol})
                            except Exception:
                                logger.exception("Profit-lock close failed for %s", symbol)
                                store.append_event("profit_lock_flatten_close_failed", {"ts": now_iso, "symbol": symbol})
                        positions = broker.list_positions()
                        current_qty = {p["symbol"]: float(p["qty"]) for p in positions}

            profit_lock_in_reeval_window = _is_time_in_window(
                now=now,
                start=profit_lock_reeval_start,
                end=profit_lock_reeval_end,
            )
            profit_lock_reeval_due = (
                profit_lock_active_today
                and (not profit_lock_reeval_done_today)
                and profit_lock_in_reeval_window
            )
            if profit_lock_active_today and (not profit_lock_reeval_done_today) and (not profit_lock_in_reeval_window):
                logger.info(
                    "Profit-lock active; waiting for re-eval window start=%s end=%s",
                    args.profit_lock_reeval_start,
                    args.profit_lock_reeval_end,
                )
                store.append_event(
                    "profit_lock_wait_reeval",
                    {
                        "ts": now_iso,
                        "reeval_start": args.profit_lock_reeval_start,
                        "reeval_end": args.profit_lock_reeval_end,
                    },
                )
                time.sleep(args.loop_sleep_seconds)
                continue

        last_trade_ts = store.get("last_trade_ts")
        if last_trade_ts:
            last_trade_dt = datetime.fromisoformat(str(last_trade_ts))
            minutes_since_last_trade = int((now - last_trade_dt.astimezone(NY)).total_seconds() // 60)
        else:
            minutes_since_last_trade = 10_000

        flip_count_today = int(store.get(f"flip_count_{today}", 0) or 0)

        metrics = OverlayMetrics(
            dd_intra_soxl=dd_intra,
            rv_15m_tqqq=rv_15m,
            price_soxl=soxl_closes[-1],
            vwap_60m_soxl=vwap_60,
            rsi_15m_soxl=rsi_15_proxy,
            realized_pnl_pct=0.0,
            data_failure_minutes=stale_minutes,
            margin_usage=0.0,
            overbought_fade_regime=overbought_fade_regime,
            fade_confirmed=fade_confirmed,
        )

        snapshot = OverlaySnapshot(
            flip_count_today=flip_count_today,
            minutes_since_last_trade=minutes_since_last_trade,
        )

        step = overlay_sm.step(metrics, snapshot, baseline_target, overlay_target)
        target = step.target

        last_prices = {symbol: closes[-1] for symbol, closes in minute_closes.items() if closes}
        intents = build_rebalance_order_intents(
            equity=equity,
            target_weights=target,
            current_qty=current_qty,
            last_prices=last_prices,
            min_trade_weight_delta=strategy.rebalance_threshold,
        )
        stage_fraction = compute_staging_fraction(
            rv_annualized_pct=rv_15m,
            spread_bps=spread_proxy_bps,
            config=phased_cfg,
        )
        staged = apply_phased_execution(
            intents=intents,
            last_prices=last_prices,
            staging_fraction=stage_fraction,
            min_notional=phased_cfg.min_notional,
        )
        effective_intents = staged.intents

        logger.info(
            "runtime_loop state=%s reason=%s intents=%d effective_intents=%d dd=%.2f rv=%.2f spread_proxy_bps=%.2f stage_fraction=%.2f",
            step.state,
            step.reason,
            len(intents),
            len(effective_intents),
            dd_intra,
            rv_15m,
            spread_proxy_bps,
            staged.staging_fraction,
        )
        if staged.staging_fraction < 1.0 and (staged.staged_buy_count > 0 or staged.skipped_buy_count > 0):
            store.append_event(
                "phased_execution_applied",
                {
                    "ts": now_iso,
                    "staging_fraction": staged.staging_fraction,
                    "staged_buy_count": staged.staged_buy_count,
                    "skipped_buy_count": staged.skipped_buy_count,
                    "rv_15m": rv_15m,
                    "spread_proxy_bps": spread_proxy_bps,
                },
            )

        if args.enable_overnight_flatten and session_window is not None:
            already_flattened_today = str(store.get("overnight_flatten_day", "")) == today
            if should_run_overnight_flatten(
                now=now,
                session=session_window,
                flatten_minutes_before_close=args.flatten_minutes_before_close,
                already_flattened_today=already_flattened_today,
            ):
                to_flatten = select_positions_to_flatten(
                    positions=positions,
                    mode=args.overnight_flatten_mode,
                    leveraged_symbols=leveraged_symbols,
                    allow_overnight_symbols=allow_overnight_symbols,
                )
                if to_flatten:
                    logger.warning("Overnight flatten triggered symbols=%s", to_flatten)
                    if args.execute_orders:
                        for symbol in to_flatten:
                            try:
                                broker.close_position(symbol)
                                store.append_event("overnight_flatten_close", {"ts": now_iso, "symbol": symbol})
                            except Exception:
                                logger.exception("Failed overnight flatten close for %s", symbol)
                                store.append_event(
                                    "overnight_flatten_close_failed",
                                    {"ts": now_iso, "symbol": symbol},
                                )
                    store.put("overnight_flatten_day", today)
                time.sleep(args.loop_sleep_seconds)
                continue

        # Optional software-managed stops for fractional mode.
        stop_table = store.get("software_stops", {}) or {}
        if args.order_mode == "fractional":
            for symbol, stop_price in list(stop_table.items()):
                px = last_prices.get(symbol)
                if px is not None and px <= float(stop_price):
                    logger.warning("Software stop triggered symbol=%s px=%.4f stop=%.4f", symbol, px, stop_price)
                    if args.execute_orders:
                        broker.close_position(symbol)
                    stop_table.pop(symbol, None)
            store.put("software_stops", stop_table)

        if session_window is not None:
            in_no_trade_window = is_no_trade_window(
                now=now,
                session=session_window,
                no_trade_open_minutes=args.no_trade_open_minutes,
                no_trade_close_minutes=args.no_trade_close_minutes,
            )
            no_trade_overridden = bool(
                args.enable_profit_lock
                and profit_lock_reeval_due
                and args.profit_lock_override_no_trade_window
            )
            if in_no_trade_window and not no_trade_overridden:
                logger.info("No-trade window active; skipping new rebalances")
                store.append_event("no_trade_window_skip", {"ts": now_iso})
                time.sleep(args.loop_sleep_seconds)
                continue
            if in_no_trade_window and no_trade_overridden:
                logger.info("No-trade window override applied for profit-lock re-evaluation")
                store.append_event("profit_lock_no_trade_override", {"ts": now_iso})

        if effective_intents and args.execute_orders:
            for intent in effective_intents:
                try:
                    def _submit(order_intent):
                        if args.order_mode == "bracket":
                            qty = to_whole_share_qty(order_intent.qty)
                            if qty <= 0:
                                raise ValueError(
                                    f"Bracket mode requires whole shares; got {order_intent.qty} for {order_intent.symbol}"
                                )
                            tp, sl = build_stop_levels(
                                last_price=last_prices[order_intent.symbol],
                                side=order_intent.side,
                                take_profit_pct=args.take_profit_pct,
                                stop_loss_pct=args.stop_loss_pct,
                            )
                            return broker.submit_bracket_order(
                                symbol=order_intent.symbol,
                                side=order_intent.side,
                                qty=qty,
                                take_profit_price=tp,
                                stop_loss_price=sl,
                            )
                        if args.order_mode == "fractional":
                            notional = abs(order_intent.qty) * float(last_prices[order_intent.symbol])
                            return broker.submit_notional_market_order(order_intent.symbol, order_intent.side, notional)
                        return broker.submit_market_order(order_intent.symbol, order_intent.side, order_intent.qty)

                    # Fractional/notional orders are less suitable for qty-based replace flows.
                    use_retries = args.order_mode != "fractional" and retry_cfg.max_retries > 0
                    if use_retries:
                        outcome = execute_with_retries(
                            submit_fn=_submit,
                            intent=intent,
                            retry_cfg=retry_cfg,
                            get_order_fn=broker.get_order,
                            cancel_order_fn=broker.cancel_order,
                            replace_order_fn=broker.replace_order,
                        )
                        store.append_event(
                            "order_outcome",
                            {
                                "ts": now_iso,
                                "symbol": intent.symbol,
                                "side": intent.side,
                                "order_id": outcome.order_id,
                                "success": outcome.success,
                                "status": outcome.final_status,
                                "filled_qty": outcome.filled_qty,
                                "attempts": outcome.attempts,
                            },
                        )
                        if not outcome.success:
                            continue
                        submitted_order_id = outcome.order_id
                        submitted_qty = outcome.filled_qty
                    else:
                        result = _submit(intent)
                        submitted_order_id = result.order_id
                        submitted_qty = result.qty
                        store.append_event(
                            "order_submitted",
                            {
                                "ts": now_iso,
                                "symbol": intent.symbol,
                                "side": intent.side,
                                "qty": submitted_qty,
                                "order_id": submitted_order_id,
                            },
                        )

                    if args.order_mode == "fractional" and intent.side == "buy":
                        _, sl = build_stop_levels(
                            last_price=last_prices[intent.symbol],
                            side=intent.side,
                            take_profit_pct=args.take_profit_pct,
                            stop_loss_pct=args.stop_loss_pct,
                        )
                        stop_table = store.get("software_stops", {}) or {}
                        stop_table[intent.symbol] = sl
                        store.put("software_stops", stop_table)
                except Exception:
                    logger.exception("Order submit failed for %s", intent)
                    store.append_event("order_submit_failed", {"ts": now_iso, "intent": intent.__dict__})

            flip_count_today += 1
            store.put(f"flip_count_{today}", flip_count_today)
            store.put("last_trade_ts", now_iso)
            overlay_sm.on_trade_executed()

        if args.enable_profit_lock and profit_lock_reeval_due:
            store.put(profit_lock_reeval_done_key, True)
            store.put(profit_lock_active_key, False)
            store.append_event(
                "profit_lock_reeval_complete",
                {
                    "ts": now_iso,
                    "intent_count": len(intents),
                    "effective_intent_count": len(effective_intents),
                    "execute_orders": bool(args.execute_orders),
                },
            )

        store.put("last_overlay_state", str(step.state))
        store.put("last_overlay_reason", step.reason)
        store.put("last_target", target)

        time.sleep(args.loop_sleep_seconds)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SOXL Growth strategy runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_phased_execution_args(arg_parser: argparse.ArgumentParser, *, include_spread: bool = True) -> None:
        arg_parser.add_argument("--enable-phased-execution", action="store_true")
        arg_parser.add_argument("--phased-rv-trigger", type=float, default=120.0)
        arg_parser.add_argument("--phased-extreme-rv-trigger", type=float, default=180.0)
        if include_spread:
            arg_parser.add_argument("--phased-spread-trigger-bps", type=float, default=12.0)
            arg_parser.add_argument("--phased-extreme-spread-bps", type=float, default=25.0)
        arg_parser.add_argument("--phased-stage-fraction", type=float, default=0.5)
        arg_parser.add_argument("--phased-extreme-stage-fraction", type=float, default=0.25)
        arg_parser.add_argument("--phased-min-notional", type=float, default=50.0)

    backtest = sub.add_parser("backtest", help="Run local backtest from CSV price history")
    backtest.add_argument("--prices-csv", required=True, help="Wide CSV with date and symbol close columns")
    backtest.add_argument("--initial-equity", type=float, default=100000.0)
    backtest.add_argument("--warmup-days", type=int, default=260)
    backtest.add_argument("--slippage-bps", type=float, default=1.0)
    backtest.add_argument("--sell-fee-bps", type=float, default=0.0)
    backtest.add_argument("--min-trade-weight-delta", type=float, default=0.0)
    _add_phased_execution_args(backtest, include_spread=False)
    backtest.set_defaults(func=run_backtest_mode)

    parity = sub.add_parser("parity-report", help="Compare local allocations to Composer API backtest output")
    parity.add_argument("--symphony-id", required=True)
    parity.add_argument("--prices-csv", required=True)
    parity.add_argument("--composer-api-base-url", default="https://api.composer.trade")
    parity.add_argument("--composer-token", default=None, help="Or set COMPOSER_API_TOKEN")
    parity.add_argument("--start-date", default=None)
    parity.add_argument("--end-date", default=None)
    parity.add_argument("--initial-equity", type=float, default=100000.0)
    parity.add_argument("--warmup-days", type=int, default=260)
    parity.add_argument("--slippage-bps", type=float, default=1.0)
    parity.add_argument("--sell-fee-bps", type=float, default=0.0)
    parity.add_argument("--min-trade-weight-delta", type=float, default=0.0)
    parity.add_argument("--tolerance", type=float, default=1e-6)
    parity.add_argument("--sample-limit", type=int, default=20)
    parity.set_defaults(func=run_parity_report_mode)

    parity_cal = sub.add_parser(
        "parity-calibrate-rsi",
        help="Sweep RSI smoothing spans and rank candidates by Composer parity mismatches",
    )
    parity_cal.add_argument("--symphony-id", required=True)
    parity_cal.add_argument("--prices-csv", required=True)
    parity_cal.add_argument("--composer-api-base-url", default="https://api.composer.trade")
    parity_cal.add_argument("--composer-token", default=None, help="Or set COMPOSER_API_TOKEN")
    parity_cal.add_argument("--start-date", default=None)
    parity_cal.add_argument("--end-date", default=None)
    parity_cal.add_argument("--initial-equity", type=float, default=100000.0)
    parity_cal.add_argument("--warmup-days", type=int, default=260)
    parity_cal.add_argument("--slippage-bps", type=float, default=1.0)
    parity_cal.add_argument("--sell-fee-bps", type=float, default=0.0)
    parity_cal.add_argument("--min-trade-weight-delta", type=float, default=0.0)
    parity_cal.add_argument("--smoothing-spans", default="2,3,5,8,13,21,34,55,89,144,233,377,610,1000")
    parity_cal.add_argument("--threshold-band", type=float, default=0.75)
    parity_cal.add_argument("--tolerance", type=float, default=1e-6)
    parity_cal.add_argument("--sample-limit", type=int, default=20)
    parity_cal.add_argument("--output-json", default="", help="Optional path to write JSON calibration report")
    parity_cal.set_defaults(func=run_parity_calibrate_rsi_mode)

    replay = sub.add_parser("overlay-replay", help="Replay intraday overlay state machine on minute CSV data")
    replay.add_argument("--daily-prices-csv", required=True, help="Daily wide CSV used to build baseline targets")
    replay.add_argument("--minute-prices-csv", required=True, help="Minute wide CSV with timestamp and symbols")
    replay.add_argument("--overlay-eval-minutes", type=int, default=5)
    replay.add_argument("--lookback-minutes", type=int, default=75)
    replay.add_argument("--sample-limit", type=int, default=20)
    replay.set_defaults(func=run_overlay_replay_mode)

    e2e = sub.add_parser(
        "runtime-e2e-paper",
        help="Run bounded end-to-end paper integration checks and emit a JSON report",
    )
    e2e.add_argument("--data-feed", default="sip", help="Alpaca data feed for checks")
    e2e.add_argument("--loops", type=int, default=2, help="Number of bounded runtime loops to execute")
    e2e.add_argument("--sleep-seconds", type=float, default=1.0)
    e2e.add_argument("--minute-lookback-minutes", type=int, default=75)
    e2e.add_argument("--overlay-eval-minutes", type=int, default=5)
    e2e.add_argument("--state-db", default="runtime_e2e_state.db")
    e2e.add_argument("--execute-orders", action="store_true", help="Submit paper orders during E2E run")
    e2e.add_argument("--close-after-order", action="store_true", help="Close positions after E2E order submits")
    e2e.add_argument("--order-mode", choices=["market", "bracket", "fractional"], default="market")
    e2e.add_argument("--max-intents-per-loop", type=int, default=1)
    e2e.add_argument("--take-profit-pct", type=float, default=0.03)
    e2e.add_argument("--stop-loss-pct", type=float, default=0.015)
    e2e.add_argument("--max-order-retries", type=int, default=2)
    e2e.add_argument("--order-stale-seconds", type=float, default=20.0)
    e2e.add_argument("--order-poll-seconds", type=float, default=2.0)
    e2e.add_argument("--report-json", default="", help="Optional path to write JSON report")
    e2e.add_argument("--log-level", default="INFO")
    _add_phased_execution_args(e2e)
    e2e.set_defaults(func=run_runtime_e2e_paper_mode)

    runtime = sub.add_parser("runtime", help="Run paper/live runtime loop with Alpaca")
    runtime.add_argument("--mode", choices=["paper", "live"], default="paper")
    runtime.add_argument("--execute-orders", action="store_true", help="Actually submit orders")
    runtime.add_argument("--order-mode", choices=["market", "bracket", "fractional"], default="market")
    runtime.add_argument("--take-profit-pct", type=float, default=0.03)
    runtime.add_argument("--stop-loss-pct", type=float, default=0.015)
    runtime.add_argument("--max-order-retries", type=int, default=2)
    runtime.add_argument("--order-stale-seconds", type=float, default=20.0)
    runtime.add_argument("--order-poll-seconds", type=float, default=2.0)
    runtime.add_argument("--stale-data-threshold-minutes", type=int, default=3)
    runtime.add_argument("--fallback-data-feed", default="", help="Optional fallback feed for stale-data recovery")
    runtime.add_argument(
        "--cancel-all-on-stale-after",
        type=int,
        default=3,
        help="Cancel all open orders after this many consecutive stale-data recovery failures",
    )
    runtime.add_argument("--state-db", default="runtime_state.db")
    runtime.add_argument("--no-trade-open-minutes", type=int, default=5)
    runtime.add_argument("--no-trade-close-minutes", type=int, default=5)
    runtime.add_argument("--enable-profit-lock", action="store_true")
    runtime.add_argument("--profit-lock-threshold-pct", type=float, default=15.0)
    runtime.add_argument("--profit-lock-reeval-start", default="15:45", help="HH:MM ET")
    runtime.add_argument("--profit-lock-reeval-end", default="16:00", help="HH:MM ET")
    runtime.add_argument("--profit-lock-max-triggers-per-day", type=int, default=1)
    runtime.add_argument(
        "--profit-lock-override-no-trade-window",
        action="store_true",
        help="Allow one re-evaluation pass in the close no-trade window after profit-lock trigger.",
    )
    runtime.add_argument("--enable-overnight-flatten", action="store_true")
    runtime.add_argument("--overnight-flatten-mode", choices=["leveraged-only", "all"], default="leveraged-only")
    runtime.add_argument("--flatten-minutes-before-close", type=int, default=10)
    runtime.add_argument(
        "--leveraged-symbols",
        default="SOXL,SOXS,TQQQ,SQQQ,SPXL,SPXS,TMF,TMV",
        help="CSV list used by leveraged-only flatten mode",
    )
    runtime.add_argument(
        "--allow-overnight-symbols",
        default="",
        help="CSV list of symbols allowed to carry overnight even when flatten policy is enabled",
    )
    runtime.add_argument("--overlay-eval-minutes", type=int, default=5)
    runtime.add_argument("--loop-sleep-seconds", type=int, default=30)
    runtime.add_argument("--data-feed", default="sip", help="Alpaca feed: sip or iex")
    runtime.add_argument("--log-level", default="INFO")
    _add_phased_execution_args(runtime)
    runtime.set_defaults(func=run_runtime_mode)

    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    configure_logging(getattr(args, "log_level", "INFO"))
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception:
        logger.exception("Fatal runtime error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
