#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
import argparse
import math
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.execution.orders import build_rebalance_order_intents

from composer_original.experiment.aggr_v2.data import MarketData, load_market_data
from composer_original.experiment.aggr_v2.execution_models import (
    execution_price,
    profit_lock_decision,
    sell_fee,
    threshold_pct_for_day,
)
from composer_original.experiment.aggr_v2.gpu_replay import replay_with_gpu
from composer_original.experiment.aggr_v2.metrics import cagr_pct, max_drawdown_pct
from composer_original.experiment.aggr_v2.model_types import (
    BacktestConfigV2,
    BacktestResultV2,
    DailySnapshot,
    OverlayConfig,
    PositionState,
    StrategyProfile,
    TradeRecord,
    WindowSpec,
)
from composer_original.experiment.aggr_v2.overlays import (
    PersistenceState,
    apply_inverse_blocker,
    apply_persistence_hysteresis,
    apply_vol_target,
    loss_limiter_triggered,
    realized_vol_ann,
)
from composer_original.experiment.aggr_v2.profiles import get_profile
from composer_original.experiment.aggr_v2.reporting import (
    build_summary_row,
    daily_table_rows,
    serialize_result,
    write_csv,
    write_json,
)
from composer_original.experiment.aggr_v2.runner_utils import slice_market_data, trim_result_to_window
from composer_original.experiment.aggr_v2.strategy_adapter import evaluate_target_weights_for_day
from composer_original.experiment.aggr_v2.validation import summarize_validation
from composer_original.experiment.aggr_v2.windows import WINDOW_TO_DAYS, resolve_windows


SWITCH_PROFILE_NAME = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1"


@dataclass
class RegimeState:
    """State machine for deterministic variant switching.

    Variants:
    - baseline: no inverse blocker
    - inverse_ma20: inverse blocker enabled, trend MA=20
    - inverse_ma60: inverse blocker enabled, trend MA=60
    """

    current_variant: str = "baseline"
    cond2_streak: int = 0
    cond3_streak: int = 0
    cond2_false_streak: int = 0
    forced_baseline_days: int = 0
    high_vol_lock: bool = False


@dataclass(frozen=True)
class RegimeMetrics:
    close: float
    ma20: float | None
    ma60: float | None
    ma200: float | None
    slope20_pct: float | None
    slope60_pct: float | None
    rv20_ann: float
    crossovers20: int
    dd20_pct: float


def _parse_day(value: str) -> date:
    return date.fromisoformat(str(value))


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    sample = values[-window:]
    return float(sum(sample) / float(window))


def _rolling_ma(values: list[float], end_idx: int, window: int) -> float | None:
    if end_idx < window - 1:
        return None
    start = end_idx - window + 1
    sample = values[start : end_idx + 1]
    if len(sample) != window:
        return None
    return float(sum(sample) / float(window))


def _slope_pct(values: list[float], ma_window: int, lookback_days: int) -> float | None:
    """Slope as percent change in MA from lookback_days ago to today."""
    end_idx = len(values) - 1
    ma_now = _rolling_ma(values, end_idx, ma_window)
    ma_prev = _rolling_ma(values, end_idx - lookback_days, ma_window)
    if ma_now is None or ma_prev is None or ma_prev <= 0:
        return None
    return (ma_now / ma_prev - 1.0) * 100.0


def _rv20_ann(values: list[float]) -> float:
    """Annualized realized volatility from the last 20 daily returns."""
    if len(values) < 21:
        return 0.0
    sample = values[-21:]
    rets: list[float] = []
    for i in range(1, len(sample)):
        prev = sample[i - 1]
        cur = sample[i]
        if prev > 0:
            rets.append(cur / prev - 1.0)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / len(rets)
    return float(math.sqrt(var) * math.sqrt(252.0))


def _crossovers20(values: list[float]) -> int:
    """Count sign flips of (close - MA20) over the last 20 trading days."""
    n_days = 20
    ma_w = 20
    if len(values) < (ma_w + n_days - 1):
        return 0

    signs: list[int] = []
    for end_idx in range(len(values) - n_days, len(values)):
        ma = _rolling_ma(values, end_idx, ma_w)
        if ma is None:
            continue
        diff = values[end_idx] - ma
        if diff > 0:
            signs.append(1)
        elif diff < 0:
            signs.append(-1)
        else:
            signs.append(0)

    flips = 0
    prev_non_zero: int | None = None
    for s in signs:
        if s == 0:
            continue
        if prev_non_zero is not None and s != prev_non_zero:
            flips += 1
        prev_non_zero = s
    return flips


def _max_drawdown_pct_last20(values: list[float]) -> float:
    """Path-aware max drawdown over the last 20 closes."""
    if len(values) < 2:
        return 0.0
    window = values[-20:] if len(values) >= 20 else values
    peak = float(window[0])
    max_dd = 0.0
    for px in window:
        peak = max(peak, float(px))
        if peak > 0:
            dd = 100.0 * (peak - float(px)) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _compute_regime_metrics(close_hist_soxl: list[float]) -> RegimeMetrics:
    close = float(close_hist_soxl[-1])
    ma20 = _sma(close_hist_soxl, 20)
    ma60 = _sma(close_hist_soxl, 60)
    ma200 = _sma(close_hist_soxl, 200)
    slope20 = _slope_pct(close_hist_soxl, ma_window=20, lookback_days=5)
    slope60 = _slope_pct(close_hist_soxl, ma_window=60, lookback_days=20)
    rv20 = _rv20_ann(close_hist_soxl)
    x20 = _crossovers20(close_hist_soxl)
    dd20 = _max_drawdown_pct_last20(close_hist_soxl)
    return RegimeMetrics(
        close=close,
        ma20=ma20,
        ma60=ma60,
        ma200=ma200,
        slope20_pct=slope20,
        slope60_pct=slope60,
        rv20_ann=rv20,
        crossovers20=x20,
        dd20_pct=dd20,
    )


def _choose_variant(metrics: RegimeMetrics, state: RegimeState) -> tuple[str, str]:
    """Apply strict rule priority and hysteresis transitions.

    Rule priority from previous specification:
    1) Close<MA60 OR RV20>=1.30 OR Crossovers20>=4 -> baseline
    2) Strong fast bull trend -> inverse_ma20
    3) Slower bull trend confirmation -> inverse_ma60
    4) Fallback baseline

    Hard overrides:
    - dd20 >= 12% => baseline for next 5 days
    - rv20 >= 1.35 => baseline lock until rv20 < 1.20
    """

    ma20 = metrics.ma20
    ma60 = metrics.ma60
    ma200 = metrics.ma200
    slope20 = metrics.slope20_pct
    slope60 = metrics.slope60_pct

    # Boolean conditions with explicit None checks.
    rule1 = bool((ma60 is not None and metrics.close < ma60) or (metrics.rv20_ann >= 1.30) or (metrics.crossovers20 >= 4))
    rule2 = bool(
        (ma20 is not None and ma60 is not None and metrics.close > ma20 and ma20 > ma60)
        and (slope20 is not None and slope20 >= 1.0)
        and (metrics.rv20_ann <= 0.95)
        and (metrics.crossovers20 <= 2)
    )
    rule3 = bool(
        (ma60 is not None and ma200 is not None and metrics.close > ma60 and ma60 > ma200)
        and (slope60 is not None and slope60 >= 0.5)
        and (metrics.rv20_ann <= 1.20)
    )

    # Update streak counters first so transitions are deterministic.
    state.cond2_streak = state.cond2_streak + 1 if rule2 else 0
    state.cond3_streak = state.cond3_streak + 1 if rule3 else 0
    state.cond2_false_streak = state.cond2_false_streak + 1 if not rule2 else 0

    # Hard override 1: drawdown circuit breaker.
    if metrics.dd20_pct >= 12.0:
        state.forced_baseline_days = 5

    # Hard override 2: high-vol baseline lock with hysteresis unlock.
    if metrics.rv20_ann >= 1.35:
        state.high_vol_lock = True
    elif state.high_vol_lock and metrics.rv20_ann < 1.20:
        state.high_vol_lock = False

    # Enforce hard overrides before regular rule path.
    if state.forced_baseline_days > 0:
        state.current_variant = "baseline"
        state.forced_baseline_days -= 1
        return state.current_variant, "override_dd20_ge_12"

    if state.high_vol_lock:
        state.current_variant = "baseline"
        return state.current_variant, "override_high_vol_lock"

    # Immediate baseline drop if risk/chop gate triggers.
    if rule1:
        state.current_variant = "baseline"
        return state.current_variant, "rule1_risk_or_chop"

    # Transition matrix with persistence constraints.
    current = state.current_variant
    if current == "baseline":
        if state.cond2_streak >= 3:
            state.current_variant = "inverse_ma20"
            return state.current_variant, "baseline_to_inv20_rule2_3d"
        if state.cond3_streak >= 3:
            state.current_variant = "inverse_ma60"
            return state.current_variant, "baseline_to_inv60_rule3_3d"
        return state.current_variant, "baseline_hold"

    if current == "inverse_ma20":
        # shift to slower regime if fast condition weakens for 3d but slow condition confirms for 3d
        if state.cond2_false_streak >= 3 and state.cond3_streak >= 3:
            state.current_variant = "inverse_ma60"
            return state.current_variant, "inv20_to_inv60_transition"
        return state.current_variant, "inv20_hold"

    if current == "inverse_ma60":
        # shift up to faster regime when fast condition confirms 3d
        if state.cond2_streak >= 3:
            state.current_variant = "inverse_ma20"
            return state.current_variant, "inv60_to_inv20_transition"
        return state.current_variant, "inv60_hold"

    # Safety fallback.
    state.current_variant = "baseline"
    return state.current_variant, "fallback_unknown_variant"


def _variant_overlay(base: OverlayConfig, variant: str) -> OverlayConfig:
    """Build day-level overlay for selected variant without mutating base settings."""
    if variant == "inverse_ma20":
        return replace(base, enable_inverse_blocker=True, trend_symbol="SOXL", trend_ma_days=20)
    if variant == "inverse_ma60":
        return replace(base, enable_inverse_blocker=True, trend_symbol="SOXL", trend_ma_days=60)
    return replace(base, enable_inverse_blocker=False)


def _current_equity(cash: float, positions: dict[str, PositionState], close_prices: dict[str, float]) -> float:
    return cash + sum(pos.qty * close_prices[sym] for sym, pos in positions.items())


def _current_holdings(positions: dict[str, PositionState]) -> dict[str, float]:
    return {sym: float(pos.qty) for sym, pos in positions.items() if pos.qty > 0}


def run_backtest_switch_profile(
    *,
    market_data: MarketData,
    base_profile: StrategyProfile,
    config: BacktestConfigV2,
    base_overlay: OverlayConfig,
    window_label: str,
) -> BacktestResultV2:
    """Dynamic variant backtest using a separate switch-profile path.

    This function is intentionally standalone and does not modify existing locked
    profile code paths.
    """
    symbols = sorted(market_data.bars_by_symbol.keys())
    positions = {sym: PositionState() for sym in symbols}
    persistence = PersistenceState()
    regime_state = RegimeState(current_variant="baseline")

    cash = float(config.initial_equity)
    equity_curve: list[tuple[date, float]] = []
    trades: list[TradeRecord] = []
    daily_rows: list[DailySnapshot] = []
    variant_counts = {"baseline": 0, "inverse_ma20": 0, "inverse_ma60": 0}
    variant_switches = 0
    prev_variant = regime_state.current_variant

    peak_equity = float(config.initial_equity)

    for i, day in enumerate(market_data.days):
        bars = {sym: market_data.bars_by_symbol[sym][i] for sym in symbols}
        closes = {sym: float(bar.close) for sym, bar in bars.items()}

        start_equity = _current_equity(cash, positions, closes)
        notes: list[str] = []

        # Warmup: mark-to-market only.
        if i < int(config.warmup_days):
            end_equity = start_equity
            peak_equity = max(peak_equity, end_equity)
            dd = 100.0 * ((peak_equity - end_equity) / peak_equity) if peak_equity > 0 else 0.0
            equity_curve.append((day, end_equity))
            daily_rows.append(
                DailySnapshot(
                    day=day,
                    start_equity=start_equity,
                    end_equity=end_equity,
                    pnl=0.0,
                    return_pct=0.0,
                    drawdown_pct=dd,
                    holdings=_current_holdings(positions),
                    notes="warmup",
                )
            )
            continue

        # Build close history once per day.
        close_hist = {
            sym: [float(b.close) for b in market_data.bars_by_symbol[sym][: i + 1]]
            for sym in symbols
        }

        # Select variant for current day.
        soxl_hist = close_hist.get("SOXL", [])
        metrics = _compute_regime_metrics(soxl_hist)
        chosen_variant, variant_reason = _choose_variant(metrics, regime_state)
        variant_counts[chosen_variant] = variant_counts.get(chosen_variant, 0) + 1
        if chosen_variant != prev_variant:
            variant_switches += 1
            prev_variant = chosen_variant
        notes.append(f"variant={chosen_variant}")
        notes.append(f"variant_reason={variant_reason}")

        # Optional downside limiter before normal rebalance.
        for sym, pos in positions.items():
            if pos.qty <= 0:
                continue
            hit, reason = loss_limiter_triggered(pos=pos, bar=bars[sym], today=day, overlay=base_overlay)
            if not hit:
                continue
            px = execution_price(bars[sym].close, "sell", config.slippage_bps)
            qty = float(pos.qty)
            notional = qty * px
            fee = sell_fee(notional, config.sell_fee_bps, "sell")
            cash += notional - fee
            positions[sym] = PositionState(qty=0.0, entry_price=0.0, entry_day=None)
            trades.append(
                TradeRecord(
                    day=day,
                    symbol=sym,
                    side="sell",
                    qty=qty,
                    price=px,
                    notional=notional,
                    fee=fee,
                    reason=reason,
                )
            )
            notes.append(reason)

        # Profit lock exits.
        if base_profile.enable_profit_lock and i > 0:
            threshold = threshold_pct_for_day(base_profile, close_hist, day_index=i)
            for sym, pos in positions.items():
                if pos.qty <= 0:
                    continue
                prev_close = float(market_data.bars_by_symbol[sym][i - 1].close)
                decision = profit_lock_decision(
                    prev_close=prev_close,
                    bar=bars[sym],
                    threshold_pct=threshold,
                    trail_pct=float(base_profile.profit_lock_trail_pct),
                    mode=config.profit_lock_exec_model,
                )
                if not decision.should_exit:
                    continue
                px = execution_price(decision.exit_price, "sell", config.slippage_bps)
                qty = float(pos.qty)
                notional = qty * px
                fee = sell_fee(notional, config.sell_fee_bps, "sell")
                cash += notional - fee
                positions[sym] = PositionState(qty=0.0, entry_price=0.0, entry_day=None)
                trades.append(
                    TradeRecord(
                        day=day,
                        symbol=sym,
                        side="sell",
                        qty=qty,
                        price=px,
                        notional=notional,
                        fee=fee,
                        reason=decision.reason,
                    )
                )
                notes.append(decision.reason)

        # Base strategy decision (unchanged evaluator).
        strat = evaluate_target_weights_for_day(market_data, i)
        if strat.skipped:
            end_equity = _current_equity(cash, positions, closes)
            peak_equity = max(peak_equity, end_equity)
            dd = 100.0 * ((peak_equity - end_equity) / peak_equity) if peak_equity > 0 else 0.0
            equity_curve.append((day, end_equity))
            daily_rows.append(
                DailySnapshot(
                    day=day,
                    start_equity=start_equity,
                    end_equity=end_equity,
                    pnl=end_equity - start_equity,
                    return_pct=(100.0 * (end_equity / start_equity - 1.0)) if start_equity > 0 else 0.0,
                    drawdown_pct=dd,
                    holdings=_current_holdings(positions),
                    notes=";".join(notes + ["strategy_skipped", strat.reason]),
                )
            )
            continue

        target = dict(strat.weights)

        # Vol target + persistence use base overlay controls.
        rv_series = close_hist.get(base_profile.profit_lock_adaptive_symbol, [])
        rv_ann = realized_vol_ann(rv_series, base_overlay.vol_lookback_days)
        target, vol_note = apply_vol_target(target_weights=target, rv_ann=rv_ann, overlay=base_overlay)
        notes.append(vol_note)

        target, persistence, pers_note = apply_persistence_hysteresis(
            proposed_weights=target,
            state=persistence,
            overlay=base_overlay,
        )
        notes.append(pers_note)

        # Variant-specific inverse blocker settings.
        variant_overlay = _variant_overlay(base_overlay, chosen_variant)
        target, inv_note = apply_inverse_blocker(
            proposed_weights=target,
            close_history=close_hist,
            overlay=variant_overlay,
        )
        notes.append(inv_note)

        # Rebalance intents.
        equity_before_rebalance = _current_equity(cash, positions, closes)
        if equity_before_rebalance <= 0:
            raise RuntimeError(f"Non-positive equity on {day}")

        current_qty = {sym: float(pos.qty) for sym, pos in positions.items()}
        intents = build_rebalance_order_intents(
            equity=equity_before_rebalance,
            target_weights=target,
            current_qty=current_qty,
            last_prices=closes,
            min_trade_weight_delta=float(max(config.min_trade_weight_delta, config.rebalance_threshold)),
        )

        sells = [it for it in intents if it.side == "sell"]
        buys = [it for it in intents if it.side == "buy"]

        for it in sells:
            sym = str(it.symbol)
            qty = min(float(it.qty), max(0.0, positions[sym].qty))
            if qty <= 0:
                continue
            px = execution_price(closes[sym], "sell", config.slippage_bps)
            notional = qty * px
            fee = sell_fee(notional, config.sell_fee_bps, "sell")
            cash += notional - fee
            positions[sym].qty -= qty
            if positions[sym].qty <= 1e-12:
                positions[sym] = PositionState(qty=0.0, entry_price=0.0, entry_day=None)
            trades.append(
                TradeRecord(
                    day=day,
                    symbol=sym,
                    side="sell",
                    qty=qty,
                    price=px,
                    notional=notional,
                    fee=fee,
                    reason="rebalance_sell",
                )
            )

        for it in buys:
            sym = str(it.symbol)
            desired = float(it.qty)
            if desired <= 0:
                continue
            px = execution_price(closes[sym], "buy", config.slippage_bps)
            if px <= 0:
                continue
            max_affordable = cash / px
            qty = min(desired, max_affordable)
            if qty <= 0:
                continue

            old_qty = float(positions[sym].qty)
            old_entry = float(positions[sym].entry_price)
            new_qty = old_qty + qty
            new_entry = ((old_qty * old_entry) + (qty * px)) / new_qty if new_qty > 0 else px

            notional = qty * px
            cash -= notional
            positions[sym] = PositionState(
                qty=new_qty,
                entry_price=new_entry,
                entry_day=positions[sym].entry_day or day,
            )
            trades.append(
                TradeRecord(
                    day=day,
                    symbol=sym,
                    side="buy",
                    qty=qty,
                    price=px,
                    notional=notional,
                    fee=0.0,
                    reason="rebalance_buy",
                )
            )

        end_equity = _current_equity(cash, positions, closes)
        peak_equity = max(peak_equity, end_equity)
        dd = 100.0 * ((peak_equity - end_equity) / peak_equity) if peak_equity > 0 else 0.0

        equity_curve.append((day, end_equity))
        daily_rows.append(
            DailySnapshot(
                day=day,
                start_equity=start_equity,
                end_equity=end_equity,
                pnl=end_equity - start_equity,
                return_pct=(100.0 * (end_equity / start_equity - 1.0)) if start_equity > 0 else 0.0,
                drawdown_pct=dd,
                holdings=_current_holdings(positions),
                notes=";".join(notes),
            )
        )

    final_equity = equity_curve[-1][1] if equity_curve else float(config.initial_equity)
    total_return = 100.0 * (final_equity / float(config.initial_equity) - 1.0) if config.initial_equity > 0 else 0.0

    meta = {
        "switch_profile": SWITCH_PROFILE_NAME,
        "base_profile": base_profile.name,
        "mode": config.profit_lock_exec_model,
        "overlay_base": asdict(base_overlay),
        "config": asdict(config),
        "variant_counts": variant_counts,
        "variant_switches": variant_switches,
    }
    return BacktestResultV2(
        profile_name=SWITCH_PROFILE_NAME,
        window_label=window_label,
        mode=config.profit_lock_exec_model,
        initial_equity=float(config.initial_equity),
        final_equity=float(final_equity),
        total_return_pct=float(total_return),
        max_drawdown_pct=float(max_drawdown_pct(equity_curve)),
        cagr_pct=float(cagr_pct(equity_curve)),
        trade_count=len(trades),
        equity_curve=equity_curve,
        trades=trades,
        daily=daily_rows,
        meta=meta,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run isolated switch-profile backtests (new code path)")
    p.add_argument("--source", choices=["ohlc_csv", "yfinance", "fixture_close"], default="ohlc_csv")
    p.add_argument(
        "--ohlc-csv",
        default="/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/data/alpaca_sip_daily_2020-01-01_2026-03-20.csv",
    )
    p.add_argument("--prices-csv", default="/home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv")
    p.add_argument("--base-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument(
        "--mode",
        default="paper_live_style_optimistic",
        choices=["synthetic", "paper_live_style_optimistic", "realistic_close"],
    )
    p.add_argument("--windows", default="5y")
    p.add_argument("--end-day", default="")

    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--min-trade-weight-delta", type=float, default=0.0)
    p.add_argument("--rebalance-threshold", type=float, default=0.0)

    # Keep base overlay controls configurable but default-off.
    p.add_argument("--enable-vol-target", action="store_true")
    p.add_argument("--target-vol-ann", type=float, default=0.35)
    p.add_argument("--vol-lookback-days", type=int, default=20)
    p.add_argument("--max-gross-exposure", type=float, default=1.0)
    p.add_argument("--enable-loss-limiter", action="store_true")
    p.add_argument("--stop-loss-pct", type=float, default=0.12)
    p.add_argument("--max-holding-days", type=int, default=30)
    p.add_argument("--enable-persistence", action="store_true")
    p.add_argument("--persistence-days", type=int, default=1)
    p.add_argument("--hysteresis-band-pct", type=float, default=0.0)

    p.add_argument(
        "--output-dir",
        default="/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/reports/switch_profile",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_profile = get_profile(args.base_profile)
    cfg = BacktestConfigV2(
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        min_trade_weight_delta=float(args.min_trade_weight_delta),
        rebalance_threshold=float(args.rebalance_threshold),
        profit_lock_exec_model=str(args.mode),
    )
    overlay = OverlayConfig(
        enable_vol_target=bool(args.enable_vol_target),
        target_vol_ann=float(args.target_vol_ann),
        vol_lookback_days=int(args.vol_lookback_days),
        max_gross_exposure=float(args.max_gross_exposure),
        enable_loss_limiter=bool(args.enable_loss_limiter),
        stop_loss_pct=float(args.stop_loss_pct),
        max_holding_days=int(args.max_holding_days),
        enable_persistence=bool(args.enable_persistence),
        persistence_days=int(args.persistence_days),
        hysteresis_band_pct=float(args.hysteresis_band_pct),
        enable_inverse_blocker=False,
        trend_symbol="SOXL",
        trend_ma_days=60,
    )

    labels = [w.strip().lower() for w in str(args.windows).split(",") if w.strip()]
    for label in labels:
        if label not in WINDOW_TO_DAYS:
            valid = ", ".join(sorted(WINDOW_TO_DAYS))
            raise ValueError(f"Unsupported window '{label}'. Valid windows: {valid}")

    # Determine end day.
    if args.end_day:
        end_day = _parse_day(args.end_day)
    else:
        # Seed load to identify last available date.
        seed = load_market_data(
            prices_csv=Path(args.prices_csv) if args.prices_csv else None,
            ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
            source=args.source,
            start=date.today() - timedelta(days=400),
            end=date.today(),
        )
        end_day = seed.days[-1]

    max_window_days = max(WINDOW_TO_DAYS[w] for w in labels)
    # Extra history for warmup + MA200/slope/crossover metrics.
    start_for_load = end_day - timedelta(days=max_window_days + int(args.warmup_days) + 450)
    market_data = load_market_data(
        prices_csv=Path(args.prices_csv) if args.prices_csv else None,
        ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
        source=args.source,
        start=start_for_load,
        end=end_day,
    )

    windows = resolve_windows(end_day, labels)
    summary_rows: list[dict[str, object]] = []
    json_rows: list[dict[str, object]] = []

    for window in windows:
        run_start = window.start - timedelta(days=int(cfg.warmup_days) + 450)
        run_data = slice_market_data(market_data, run_start, window.end)

        run = run_backtest_switch_profile(
            market_data=run_data,
            base_profile=base_profile,
            config=cfg,
            base_overlay=overlay,
            window_label=window.label,
        )
        trimmed = trim_result_to_window(run, window)

        gpu = replay_with_gpu(trimmed, slice_market_data(market_data, window.start, window.end))
        validation = summarize_validation(trimmed)
        summary = build_summary_row(result=trimmed, gpu=gpu, validation=validation)
        summary_rows.append(summary)

        payload = serialize_result(trimmed, gpu, validation)
        json_rows.append(payload)

        daily_path = out_dir / f"daily_{SWITCH_PROFILE_NAME}_{window.label}_{args.mode}.csv"
        write_csv(daily_path, daily_table_rows(trimmed))

    csv_path = out_dir / f"summary_{SWITCH_PROFILE_NAME}_{args.mode}.csv"
    json_path = out_dir / f"summary_{SWITCH_PROFILE_NAME}_{args.mode}.json"

    write_csv(csv_path, summary_rows)
    write_json(
        json_path,
        {
            "switch_profile": SWITCH_PROFILE_NAME,
            "base_profile": base_profile.name,
            "source": args.source,
            "mode": args.mode,
            "windows": [asdict(w) for w in windows],
            "config": asdict(cfg),
            "overlay_base": asdict(overlay),
            "results": json_rows,
        },
    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    for row in summary_rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
