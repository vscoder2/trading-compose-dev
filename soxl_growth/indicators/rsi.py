from __future__ import annotations

from typing import Iterable


def _to_prices(close: Iterable[float]) -> list[float]:
    prices = [float(x) for x in close]
    if any(v <= 0 for v in prices):
        raise ValueError("Close prices must be positive.")
    return prices


def _rsi_from_window(prices: list[float], window: int, end: int) -> float | None:
    start = end - window
    if start < 0:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(start + 1, end + 1):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains += delta
        elif delta < 0:
            losses += -delta
    avg_gain = gains / window
    avg_loss = losses / window
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_base(close: Iterable[float], window: int) -> float | None:
    prices = _to_prices(close)
    if len(prices) < window + 1:
        return None
    return _rsi_from_window(prices, window, len(prices) - 1)


def rsi_smoothed(close: Iterable[float], window: int, smoothing_span: int = 1000) -> float | None:
    prices = _to_prices(close)
    if len(prices) < window + 1:
        return None

    span = max(1, min(int(smoothing_span), 1000))
    alpha = 2.0 / (span + 1.0)

    ema: float | None = None
    for end in range(window, len(prices)):
        value = _rsi_from_window(prices, window, end)
        if value is None:
            continue
        if ema is None:
            ema = value
        else:
            ema = alpha * value + (1.0 - alpha) * ema
    return ema
