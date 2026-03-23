from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from soxl_growth.logging_setup import get_logger
from soxl_growth.types import Bar

logger = get_logger(__name__)

@dataclass(frozen=True)
class BarRequestSpec:
    symbols: list[str]
    start: datetime
    end: datetime
    timeframe: str  # examples: 1Min, 5Min, 15Min, 1Hour, 1Day
    adjustment: str = "all"
    feed: str = "sip"


class AlpacaBarLoader:
    """Thin wrapper around alpaca-py StockHistoricalDataClient.

    This class keeps Alpaca imports lazy so local development and backtests can
    run without requiring the alpaca-py package.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self._client: Any = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "alpaca-py is required for AlpacaBarLoader. Install alpaca-py to enable live/paper data."
            ) from exc
        self._client = StockHistoricalDataClient(self.api_key, self.api_secret)
        return self._client

    @staticmethod
    def _parse_timeframe(timeframe: str):
        try:
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("alpaca-py not installed.") from exc

        text = timeframe.strip().lower()
        mapping = {
            "1min": (1, TimeFrameUnit.Minute),
            "5min": (5, TimeFrameUnit.Minute),
            "15min": (15, TimeFrameUnit.Minute),
            "60min": (60, TimeFrameUnit.Minute),
            "1hour": (1, TimeFrameUnit.Hour),
            "1day": (1, TimeFrameUnit.Day),
        }
        if text not in mapping:
            raise ValueError(f"Unsupported timeframe '{timeframe}'.")
        amount, unit = mapping[text]
        return TimeFrame(amount, unit)

    @staticmethod
    def _parse_adjustment(adjustment: str):
        from alpaca.data.enums import Adjustment

        key = adjustment.strip().upper()
        if not hasattr(Adjustment, key):
            raise ValueError(f"Unsupported adjustment '{adjustment}'.")
        return getattr(Adjustment, key)

    @staticmethod
    def _parse_feed(feed: str):
        from alpaca.data.enums import DataFeed

        key = feed.strip().upper()
        if not hasattr(DataFeed, key):
            raise ValueError(f"Unsupported feed '{feed}'.")
        return getattr(DataFeed, key)

    def get_bars(self, spec: BarRequestSpec) -> list[Bar]:
        """Return normalized bars from Alpaca historical data."""
        client = self._ensure_client()
        from alpaca.data.requests import StockBarsRequest

        req = StockBarsRequest(
            symbol_or_symbols=spec.symbols,
            timeframe=self._parse_timeframe(spec.timeframe),
            start=spec.start,
            end=spec.end,
            adjustment=self._parse_adjustment(spec.adjustment),
            feed=self._parse_feed(spec.feed),
        )
        logger.info(
            "Requesting Alpaca bars symbols=%s timeframe=%s adjustment=%s feed=%s",
            spec.symbols,
            spec.timeframe,
            spec.adjustment,
            spec.feed,
        )
        bars = client.get_stock_bars(req)
        return self._normalize_to_bars(bars.df)

    def get_bars_df(self, spec: BarRequestSpec):
        """Return the raw DataFrame from alpaca-py for advanced pipelines."""
        client = self._ensure_client()
        from alpaca.data.requests import StockBarsRequest

        req = StockBarsRequest(
            symbol_or_symbols=spec.symbols,
            timeframe=self._parse_timeframe(spec.timeframe),
            start=spec.start,
            end=spec.end,
            adjustment=self._parse_adjustment(spec.adjustment),
            feed=self._parse_feed(spec.feed),
        )
        bars = client.get_stock_bars(req)
        return bars.df

    @staticmethod
    def _normalize_to_bars(df) -> list[Bar]:
        """Normalize Alpaca DataFrame rows into local Bar records."""
        records: list[Bar] = []
        if df is None or df.empty:
            return records
        for idx, row in df.iterrows():
            # alpaca-py commonly uses MultiIndex (symbol, timestamp).
            if isinstance(idx, tuple):
                symbol = str(idx[0])
                timestamp = idx[1]
            else:
                symbol = str(getattr(row, "symbol", ""))
                timestamp = idx
            records.append(
                Bar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return records
