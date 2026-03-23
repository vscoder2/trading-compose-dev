from __future__ import annotations

from typing import Iterable


def max_drawdown_percent(close: Iterable[float], window: int) -> float | None:
    prices = [float(x) for x in close]
    if len(prices) < window:
        return None
    values = prices[-window:]
    peak = values[0]
    max_dd = 0.0
    for price in values:
        if price <= 0:
            raise ValueError("Close prices must be positive.")
        if price > peak:
            peak = price
        dd = (peak - price) / peak
        if dd > max_dd:
            max_dd = dd
    return 100.0 * max_dd
