from __future__ import annotations

import math
from typing import Iterable

from .returns import simple_returns

TRADING_DAYS_PER_YEAR = 252


def stdev_return_annualized_percent(close: Iterable[float], window: int) -> float | None:
    rets = simple_returns(close)
    if len(rets) < window:
        return None
    sample = rets[-window:]
    mean = sum(sample) / window
    var = sum((x - mean) ** 2 for x in sample) / window
    sigma = math.sqrt(var)
    return 100.0 * sigma * math.sqrt(TRADING_DAYS_PER_YEAR)
