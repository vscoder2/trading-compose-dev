from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Simple configurable trading-cost model.

    - `slippage_bps`: applied symmetrically to trade price.
    - `sell_fee_bps`: applied to sell notional.
    """

    slippage_bps: float = 1.0
    sell_fee_bps: float = 0.0

    def execution_price(self, mid_price: float, side: str) -> float:
        if mid_price <= 0:
            raise ValueError("mid_price must be positive")
        slip = self.slippage_bps / 10_000.0
        if side == "buy":
            return mid_price * (1.0 + slip)
        if side == "sell":
            return mid_price * (1.0 - slip)
        raise ValueError("side must be 'buy' or 'sell'")

    def fee(self, notional: float, side: str) -> float:
        if side == "sell":
            return abs(notional) * (self.sell_fee_bps / 10_000.0)
        return 0.0
