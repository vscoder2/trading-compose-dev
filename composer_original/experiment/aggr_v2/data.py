from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from .profiles import UNIVERSE
from .model_types import OhlcBar


@dataclass(frozen=True)
class MarketData:
    """Aligned daily OHLC data for all strategy symbols.

    The backtester expects every symbol to have a bar on every trading day in
    `days`. This class enforces that invariant after loading.
    """

    days: list[date]
    bars_by_symbol: dict[str, list[OhlcBar]]


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date


def _intersect_days(series_by_symbol: dict[str, list[OhlcBar]]) -> list[date]:
    day_sets: list[set[date]] = []
    for bars in series_by_symbol.values():
        day_sets.append({b.day for b in bars})
    if not day_sets:
        return []
    return sorted(set.intersection(*day_sets))


def _align_and_trim(series_by_symbol: dict[str, list[OhlcBar]]) -> MarketData:
    """Align all symbols to strict common-day intersection.

    This avoids accidental lookahead or mismatch bugs from unequal calendars.
    """
    days = _intersect_days(series_by_symbol)
    if not days:
        raise RuntimeError("No common dates across symbols")
    keep = set(days)
    aligned: dict[str, list[OhlcBar]] = {}
    for sym, bars in series_by_symbol.items():
        trimmed = [b for b in bars if b.day in keep]
        trimmed.sort(key=lambda b: b.day)
        if len(trimmed) != len(days):
            raise RuntimeError(f"Alignment mismatch for {sym}: expected {len(days)} bars, got {len(trimmed)}")
        aligned[sym] = trimmed
    return MarketData(days=days, bars_by_symbol=aligned)


def _slice_range(market_data: MarketData, start: date, end: date) -> MarketData:
    keep = [d for d in market_data.days if start <= d <= end]
    if not keep:
        raise RuntimeError(f"No rows in requested range {start}..{end}")
    keep_set = set(keep)
    out: dict[str, list[OhlcBar]] = {}
    for sym, bars in market_data.bars_by_symbol.items():
        out[sym] = [b for b in bars if b.day in keep_set]
    return MarketData(days=keep, bars_by_symbol=out)


def load_close_only_csv(path: Path, symbols: Iterable[str] | None = None) -> MarketData:
    """Load wide close-only CSV and synthesize OHLC = close.

    Expected format:
    date,SOXL,SOXS,...
    """
    symbols_set = set(symbols) if symbols is not None else set(UNIVERSE)
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "date" not in [h.lower() for h in reader.fieldnames]:
            raise ValueError("CSV must include 'date' column")
        # Keep only symbols that actually exist in file.
        csv_symbols = [h for h in reader.fieldnames if h != "date" and h in symbols_set]
        rows = list(reader)
    if not rows:
        raise RuntimeError(f"No rows in CSV: {path}")

    data: dict[str, list[OhlcBar]] = {s: [] for s in csv_symbols}
    for row in rows:
        d = date.fromisoformat(str(row["date"]))
        for sym in csv_symbols:
            px = float(row[sym])
            data[sym].append(OhlcBar(day=d, open=px, high=px, low=px, close=px))
    return _align_and_trim(data)


def load_ohlc_long_csv(path: Path, symbols: Iterable[str] | None = None) -> MarketData:
    """Load long-format OHLC CSV.

    Expected columns: date,symbol,open,high,low,close
    """
    frame = pd.read_csv(path)
    required = {"date", "symbol", "open", "high", "low", "close"}
    missing = required - set(c.lower() for c in frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    # Normalize column casing.
    rename_map = {c: c.lower() for c in frame.columns}
    frame = frame.rename(columns=rename_map)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    symbols_set = set(symbols) if symbols is not None else set(UNIVERSE)
    frame = frame[frame["symbol"].isin(symbols_set)].copy()

    data: dict[str, list[OhlcBar]] = {}
    for sym, sub in frame.groupby("symbol"):
        sub = sub.sort_values("date")
        bars = [
            OhlcBar(
                day=row.date,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
            )
            for row in sub.itertuples(index=False)
        ]
        data[str(sym)] = bars
    return _align_and_trim(data)


def fetch_yfinance_daily(start: date, end: date, symbols: Iterable[str] | None = None) -> MarketData:
    """Fetch adjusted daily OHLC from yfinance for research runs.

    This helper is optional and used only when online access is available.
    """
    import yfinance as yf

    symbols_list = list(symbols) if symbols is not None else list(UNIVERSE)
    end_exclusive = end + timedelta(days=1)
    out: dict[str, list[OhlcBar]] = {}

    for sym in symbols_list:
        raw = yf.download(
            tickers=sym,
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if raw is None or raw.empty:
            raise RuntimeError(f"No yfinance rows for {sym}")

        # yfinance may return either flat columns or MultiIndex columns.
        def _series(col: str) -> pd.Series:
            if isinstance(raw.columns, pd.MultiIndex):
                if (col, sym) in raw.columns:
                    return raw[(col, sym)]
                candidates = raw.xs(col, axis=1, level=0)
                return candidates.iloc[:, 0]
            return raw[col]

        open_s = _series("Open")
        high_s = _series("High")
        low_s = _series("Low")
        close_s = _series("Close")

        bars: list[OhlcBar] = []
        for ts, opx, hpx, lpx, cpx in zip(raw.index, open_s, high_s, low_s, close_s, strict=False):
            d = pd.Timestamp(ts).date()
            if d < start or d > end:
                continue
            bars.append(
                OhlcBar(
                    day=d,
                    open=float(opx),
                    high=float(hpx),
                    low=float(lpx),
                    close=float(cpx),
                )
            )
        out[sym] = bars

    return _align_and_trim(out)


def load_market_data(
    *,
    prices_csv: Path | None,
    ohlc_csv: Path | None,
    source: str,
    start: date,
    end: date,
    symbols: Iterable[str] | None = None,
) -> MarketData:
    """Unified loader entrypoint used by CLI and tests."""
    src = source.lower().strip()
    if src == "fixture_close":
        if prices_csv is None:
            raise ValueError("--prices-csv is required for fixture_close source")
        data = load_close_only_csv(prices_csv, symbols=symbols)
    elif src == "ohlc_csv":
        if ohlc_csv is None:
            raise ValueError("--ohlc-csv is required for ohlc_csv source")
        data = load_ohlc_long_csv(ohlc_csv, symbols=symbols)
    elif src == "yfinance":
        data = fetch_yfinance_daily(start=start, end=end, symbols=symbols)
    else:
        raise ValueError(f"Unsupported source: {source}")
    return _slice_range(data, start, end)
