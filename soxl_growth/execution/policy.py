from __future__ import annotations

from dataclasses import dataclass
import math

from soxl_growth.execution.orders import OrderIntent


@dataclass(frozen=True)
class ExecutionPolicy:
    order_mode: str = "market"  # market | bracket | fractional
    take_profit_pct: float = 0.03
    stop_loss_pct: float = 0.015


def to_whole_share_qty(qty: float) -> float:
    if qty <= 0:
        return 0.0
    return float(math.floor(qty))


def build_stop_levels(last_price: float, side: str, take_profit_pct: float, stop_loss_pct: float) -> tuple[float, float]:
    if side == "buy":
        take_profit = last_price * (1.0 + take_profit_pct)
        stop_loss = last_price * (1.0 - stop_loss_pct)
    else:
        # For sell-to-open paths this would invert; this strategy is long-only,
        # but we keep side-aware behavior for future use.
        take_profit = last_price * (1.0 - take_profit_pct)
        stop_loss = last_price * (1.0 + stop_loss_pct)
    return take_profit, stop_loss


def intents_to_notional(intents: list[OrderIntent], last_prices: dict[str, float]) -> list[tuple[OrderIntent, float]]:
    out: list[tuple[OrderIntent, float]] = []
    for intent in intents:
        px = float(last_prices.get(intent.symbol, 0.0))
        if px <= 0:
            continue
        out.append((intent, abs(intent.qty) * px))
    return out
