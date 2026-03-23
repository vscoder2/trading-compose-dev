from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime


Weights = dict[str, float]
LeafPick = tuple[str, float]


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    imputed_partial: bool = False
    missing_fraction: float = 0.0


@dataclass(frozen=True)
class DailyClose:
    symbol: str
    trading_day: date
    close: float


@dataclass(frozen=True)
class TradeFill:
    trading_day: date
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    fee: float
