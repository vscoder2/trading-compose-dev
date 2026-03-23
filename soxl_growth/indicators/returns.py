from __future__ import annotations

from typing import Iterable


def _to_float_list(close: Iterable[float]) -> list[float]:
    values = [float(x) for x in close]
    if any(v <= 0 for v in values):
        raise ValueError("Close prices must be positive.")
    return values


def simple_returns(close: Iterable[float]) -> list[float]:
    prices = _to_float_list(close)
    if len(prices) < 2:
        return []
    return [(prices[i] / prices[i - 1]) - 1.0 for i in range(1, len(prices))]


def cumulative_return_percent(close: Iterable[float], window: int) -> float | None:
    prices = _to_float_list(close)
    if len(prices) < window + 1:
        return None
    p0 = prices[-(window + 1)]
    p1 = prices[-1]
    return 100.0 * (p1 / p0 - 1.0)
