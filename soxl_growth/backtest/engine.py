from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from statistics import mean
from typing import Callable

from soxl_growth.backtest.cost_model import CostModel
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import InsufficientDataError
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import evaluate_strategy
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import BacktestConfig
from soxl_growth.execution.phased import (
    PhasedExecutionConfig,
    apply_phased_execution,
    compute_staging_fraction,
)
from soxl_growth.execution.orders import build_rebalance_order_intents
from soxl_growth.indicators.volatility import stdev_return_annualized_percent
from soxl_growth.logging_setup import get_logger
from soxl_growth.portfolio.target_weights import normalize_weights
from soxl_growth.types import TradeFill, Weights

logger = get_logger(__name__)


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: list[tuple[date, float]]
    allocations: list[tuple[date, Weights]]
    trades: list[TradeFill]

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1][1] if self.equity_curve else 0.0

    @property
    def total_return_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        start = self.equity_curve[0][1]
        end = self.equity_curve[-1][1]
        if start <= 0:
            return 0.0
        return 100.0 * (end / start - 1.0)

    @property
    def max_drawdown_pct(self) -> float:
        peak = -math.inf
        max_dd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                dd = (peak - eq) / peak
                max_dd = max(max_dd, dd)
        return 100.0 * max_dd

    @property
    def cagr_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        start_day = self.equity_curve[0][0]
        end_day = self.equity_curve[-1][0]
        years = max((end_day - start_day).days / 365.25, 1e-9)
        start = self.equity_curve[0][1]
        end = self.equity_curve[-1][1]
        if start <= 0 or end <= 0:
            return 0.0
        return 100.0 * ((end / start) ** (1.0 / years) - 1.0)

    @property
    def avg_daily_return_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        daily = []
        for i in range(1, len(self.equity_curve)):
            prev = self.equity_curve[i - 1][1]
            cur = self.equity_curve[i][1]
            if prev > 0:
                daily.append(100.0 * (cur / prev - 1.0))
        return mean(daily) if daily else 0.0


def _validate_histories(price_history: dict[str, list[tuple[date, float]]]) -> list[date]:
    if not price_history:
        raise ValueError("price_history is empty")
    symbols = sorted(price_history.keys())
    base_dates = [d for d, _ in price_history[symbols[0]]]
    for symbol in symbols[1:]:
        dates = [d for d, _ in price_history[symbol]]
        if dates != base_dates:
            raise ValueError(f"Date alignment mismatch for symbol {symbol}")
    return base_dates


def run_backtest(
    price_history: dict[str, list[tuple[date, float]]],
    config: BacktestConfig,
    evaluate_fn: Callable[[DictContext], Weights] | None = None,
) -> BacktestResult:
    """Run daily baseline backtest for the Composer-ported strategy.

    Notes:
    - Rebalances are evaluated and executed on each trading day close.
    - Uses fractional shares to avoid unit-size artifacts.
    - Cost model applies configurable slippage and sell-side fees.
    """
    dates = _validate_histories(price_history)
    cost_model = CostModel(slippage_bps=config.slippage_bps, sell_fee_bps=config.sell_fee_bps)

    active_evaluate_fn = evaluate_fn if evaluate_fn is not None else evaluate_strategy

    phased_cfg = PhasedExecutionConfig(
        enable=config.phased_execution_enabled,
        rv_trigger=config.phased_rv_trigger,
        spread_trigger_bps=float("inf"),  # Daily backtest does not model intraday spread windows.
        extreme_rv_trigger=config.phased_extreme_rv_trigger,
        extreme_spread_trigger_bps=float("inf"),
        stage_fraction=config.phased_stage_fraction,
        extreme_stage_fraction=config.phased_extreme_stage_fraction,
        min_notional=config.phased_min_notional,
    )

    cash = float(config.initial_equity)
    holdings: dict[str, float] = {symbol: 0.0 for symbol in price_history}

    equity_curve: list[tuple[date, float]] = []
    allocations: list[tuple[date, Weights]] = []
    trades: list[TradeFill] = []

    logger.info("Starting backtest days=%d symbols=%d", len(dates), len(price_history))

    for i, trading_day in enumerate(dates):
        if i < config.warmup_days:
            prices_now = {s: price_history[s][i][1] for s in price_history}
            equity = cash + sum(holdings[s] * prices_now[s] for s in price_history)
            equity_curve.append((trading_day, equity))
            continue

        prices_now = {s: float(price_history[s][i][1]) for s in price_history}
        for s, px in prices_now.items():
            if px <= 0:
                raise ValueError(f"Non-positive price for {s} on {trading_day}: {px}")

        equity_before = cash + sum(holdings[s] * prices_now[s] for s in price_history)
        closes_for_ctx = {s: [p for _, p in price_history[s][: i + 1]] for s in price_history}

        try:
            target = normalize_weights(active_evaluate_fn(DictContext(closes=closes_for_ctx)))
        except InsufficientDataError as exc:
            logger.warning("Skipping rebalance on %s due to insufficient data: %s", trading_day, exc)
            equity_curve.append((trading_day, equity_before))
            continue
        allocations.append((trading_day, target))

        intents = build_rebalance_order_intents(
            equity=equity_before,
            target_weights=target,
            current_qty=holdings,
            last_prices=prices_now,
            min_trade_weight_delta=config.min_trade_weight_delta,
        )

        effective_intents = intents
        if phased_cfg.enable and intents:
            rv_proxy = stdev_return_annualized_percent(closes_for_ctx.get("TQQQ", []), 14) or 0.0
            stage_fraction = compute_staging_fraction(
                rv_annualized_pct=rv_proxy,
                spread_bps=0.0,
                config=phased_cfg,
            )
            phased = apply_phased_execution(
                intents=intents,
                last_prices=prices_now,
                staging_fraction=stage_fraction,
                min_notional=phased_cfg.min_notional,
            )
            effective_intents = phased.intents
            if phased.staging_fraction < 1.0 and phased.staged_buy_count > 0:
                logger.info(
                    "Backtest staged execution active day=%s rv=%.2f stage_fraction=%.2f staged_buys=%d skipped_buys=%d",
                    trading_day,
                    rv_proxy,
                    phased.staging_fraction,
                    phased.staged_buy_count,
                    phased.skipped_buy_count,
                )

        for intent in effective_intents:
            mid = prices_now[intent.symbol]
            exec_price = cost_model.execution_price(mid, intent.side)
            qty = float(intent.qty)
            notional = qty * exec_price
            fee = cost_model.fee(notional=notional, side=intent.side)

            if intent.side == "buy":
                max_affordable_qty = cash / exec_price if exec_price > 0 else 0.0
                if qty > max_affordable_qty:
                    qty = max_affordable_qty
                    notional = qty * exec_price
                if qty <= 0:
                    continue
                cash -= notional + fee
                holdings[intent.symbol] += qty
            else:
                qty = min(qty, max(holdings[intent.symbol], 0.0))
                if qty <= 0:
                    continue
                notional = qty * exec_price
                cash += notional - fee
                holdings[intent.symbol] -= qty

            trades.append(
                TradeFill(
                    trading_day=trading_day,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=qty,
                    price=exec_price,
                    notional=notional,
                    fee=fee,
                )
            )

        equity_after = cash + sum(holdings[s] * prices_now[s] for s in price_history)
        equity_curve.append((trading_day, equity_after))

    logger.info(
        "Backtest complete final_equity=%.2f total_return_pct=%.2f trades=%d",
        equity_curve[-1][1] if equity_curve else 0.0,
        BacktestResult(equity_curve=equity_curve, allocations=allocations, trades=trades).total_return_pct,
        len(trades),
    )
    return BacktestResult(equity_curve=equity_curve, allocations=allocations, trades=trades)
