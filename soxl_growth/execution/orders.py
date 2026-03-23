from __future__ import annotations

from dataclasses import dataclass

from soxl_growth.logging_setup import get_logger
from soxl_growth.types import Weights

logger = get_logger(__name__)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    qty: float
    target_weight: float


def build_rebalance_order_intents(
    equity: float,
    target_weights: Weights,
    current_qty: dict[str, float],
    last_prices: dict[str, float],
    min_trade_weight_delta: float = 0.0,
) -> list[OrderIntent]:
    """Build net rebalance intents from current holdings to target weights.

    This routine creates only net-delta orders to reduce unnecessary churn.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")

    intents: list[OrderIntent] = []
    symbols = set(target_weights) | set(current_qty)

    for symbol in sorted(symbols):
        price = float(last_prices.get(symbol, 0.0))
        if price <= 0:
            logger.warning("Missing/invalid price for %s; skipping", symbol)
            continue

        current_shares = float(current_qty.get(symbol, 0.0))
        current_notional = current_shares * price
        current_weight = current_notional / equity

        target_weight = float(target_weights.get(symbol, 0.0))
        weight_delta = target_weight - current_weight
        if abs(weight_delta) <= min_trade_weight_delta:
            continue

        target_notional = target_weight * equity
        delta_notional = target_notional - current_notional
        qty = abs(delta_notional / price)
        if qty <= 0:
            continue

        side = "buy" if delta_notional > 0 else "sell"
        intents.append(
            OrderIntent(
                symbol=symbol,
                side=side,
                qty=qty,
                target_weight=target_weight,
            )
        )

    # Execute sells before buys to recycle capital and reduce rejection risk.
    intents.sort(key=lambda x: 0 if x.side == "sell" else 1)
    if intents:
        logger.info("Built %d rebalance intents", len(intents))
    else:
        logger.debug("Built 0 rebalance intents")
    return intents
