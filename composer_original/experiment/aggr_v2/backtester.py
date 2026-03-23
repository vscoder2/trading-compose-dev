from __future__ import annotations

from dataclasses import asdict
from datetime import date

from soxl_growth.execution.orders import build_rebalance_order_intents

from .data import MarketData
from .execution_models import execution_price, profit_lock_decision, sell_fee, threshold_pct_for_day
from .metrics import cagr_pct, max_drawdown_pct
from .overlays import (
    PersistenceState,
    apply_inverse_blocker,
    apply_persistence_hysteresis,
    apply_vol_target,
    loss_limiter_triggered,
    realized_vol_ann,
)
from .strategy_adapter import evaluate_target_weights_for_day
from .model_types import BacktestConfigV2, BacktestResultV2, DailySnapshot, OverlayConfig, PositionState, StrategyProfile, TradeRecord


def _current_equity(cash: float, positions: dict[str, PositionState], close_prices: dict[str, float]) -> float:
    return cash + sum(pos.qty * close_prices[sym] for sym, pos in positions.items())


def _current_holdings(positions: dict[str, PositionState]) -> dict[str, float]:
    return {sym: float(pos.qty) for sym, pos in positions.items() if pos.qty > 0}


def run_backtest_v2(
    *,
    market_data: MarketData,
    profile: StrategyProfile,
    config: BacktestConfigV2,
    overlay: OverlayConfig,
    window_label: str,
) -> BacktestResultV2:
    """Run isolated research backtest for one window/profile/mode.

    Important design choices:
    - No existing project code is modified.
    - Existing strategy evaluator is invoked through adapter only.
    - All execution and overlay logic lives inside this isolated package.
    """
    symbols = sorted(market_data.bars_by_symbol.keys())
    positions = {sym: PositionState() for sym in symbols}
    persistence = PersistenceState()

    cash = float(config.initial_equity)
    equity_curve: list[tuple[date, float]] = []
    trades: list[TradeRecord] = []
    daily_rows: list[DailySnapshot] = []

    peak_equity = float(config.initial_equity)

    for i, day in enumerate(market_data.days):
        bars = {sym: market_data.bars_by_symbol[sym][i] for sym in symbols}
        closes = {sym: float(bar.close) for sym, bar in bars.items()}

        start_equity = _current_equity(cash, positions, closes)
        notes: list[str] = []

        # Warmup section: we mark-to-market but intentionally skip strategy decisions.
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

        # Build close history snapshot once per day for all indicator-dependent logic.
        close_hist = {
            sym: [float(b.close) for b in market_data.bars_by_symbol[sym][: i + 1]]
            for sym in symbols
        }

        # 1) Optional downside loss limiter before regular rebalance intent.
        for sym, pos in positions.items():
            if pos.qty <= 0:
                continue
            hit, reason = loss_limiter_triggered(pos=pos, bar=bars[sym], today=day, overlay=overlay)
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

        # 2) Profit-lock overlay exit checks.
        if profile.enable_profit_lock and i > 0:
            threshold = threshold_pct_for_day(profile, close_hist, day_index=i)
            for sym, pos in positions.items():
                if pos.qty <= 0:
                    continue
                prev_close = float(market_data.bars_by_symbol[sym][i - 1].close)
                decision = profit_lock_decision(
                    prev_close=prev_close,
                    bar=bars[sym],
                    threshold_pct=threshold,
                    trail_pct=float(profile.profit_lock_trail_pct),
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

        # 3) Baseline strategy decision.
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

        # 4) Optional vol targeting using adaptive symbol close history.
        rv_series = close_hist.get(profile.profit_lock_adaptive_symbol, [])
        rv_ann = realized_vol_ann(rv_series, overlay.vol_lookback_days)
        target, vol_note = apply_vol_target(target_weights=target, rv_ann=rv_ann, overlay=overlay)
        notes.append(vol_note)

        # 5) Optional persistence/hysteresis to reduce churn.
        target, persistence, pers_note = apply_persistence_hysteresis(
            proposed_weights=target,
            state=persistence,
            overlay=overlay,
        )
        notes.append(pers_note)

        # 6) Optional trend-based inverse blocker.
        target, inv_note = apply_inverse_blocker(
            proposed_weights=target,
            close_history=close_hist,
            overlay=overlay,
        )
        notes.append(inv_note)

        # Recompute equity before rebalance after any overlay exits.
        equity_before_rebalance = _current_equity(cash, positions, closes)
        if equity_before_rebalance <= 0:
            raise RuntimeError(f"Non-positive equity on {day}")

        # Convert current positions into qty map for order intent builder.
        current_qty = {sym: float(pos.qty) for sym, pos in positions.items()}
        intents = build_rebalance_order_intents(
            equity=equity_before_rebalance,
            target_weights=target,
            current_qty=current_qty,
            last_prices=closes,
            min_trade_weight_delta=float(max(config.min_trade_weight_delta, config.rebalance_threshold)),
        )

        # Execute sells first to free up cash and minimize artificial buy failures.
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

            # Weighted average entry for additive buys.
            old_qty = float(positions[sym].qty)
            old_entry = float(positions[sym].entry_price)
            new_qty = old_qty + qty
            if new_qty > 0:
                new_entry = ((old_qty * old_entry) + (qty * px)) / new_qty
            else:
                new_entry = px

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
        "profile": profile.name,
        "mode": config.profit_lock_exec_model,
        "overlay": asdict(overlay),
        "config": asdict(config),
    }
    return BacktestResultV2(
        profile_name=profile.name,
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
