#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from statistics import mean, median, pstdev

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import BacktestConfig


ORIGINAL_SYMBOLS = ["SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "SPXS", "TMF", "TMV"]
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/?s={ticker}&i=d"
ALPACA_DATA_URL_DEFAULT = "https://data.alpaca.markets"
POLYGON_BASE_URL_DEFAULT = "https://api.polygon.io"
STRATEGY_PRESETS: dict[str, dict[str, object]] = {
    "original_composer": {
        "locks": {
            "enable_profit_lock": False,
            "profit_lock_mode": "fixed",
            "profit_lock_threshold_pct": 15.0,
            "profit_lock_trail_pct": 5.0,
            "profit_lock_partial_sell_pct": 50.0,
            "profit_lock_adaptive_threshold": False,
            "profit_lock_adaptive_symbol": "TQQQ",
            "profit_lock_adaptive_rv_window": 14,
            "profit_lock_adaptive_rv_baseline_pct": 85.0,
            "profit_lock_adaptive_min_threshold_pct": 8.0,
            "profit_lock_adaptive_max_threshold_pct": 30.0,
            "profit_lock_trend_filter": False,
            "profit_lock_regime_gated": False,
        },
        "cli_map": {
            "--enable-profit-lock": "enable_profit_lock",
            "--profit-lock-mode": "profit_lock_mode",
            "--profit-lock-threshold-pct": "profit_lock_threshold_pct",
            "--profit-lock-trail-pct": "profit_lock_trail_pct",
            "--profit-lock-partial-sell-pct": "profit_lock_partial_sell_pct",
            "--profit-lock-adaptive-threshold": "profit_lock_adaptive_threshold",
            "--profit-lock-adaptive-symbol": "profit_lock_adaptive_symbol",
            "--profit-lock-adaptive-rv-window": "profit_lock_adaptive_rv_window",
            "--profit-lock-adaptive-rv-baseline-pct": "profit_lock_adaptive_rv_baseline_pct",
            "--profit-lock-adaptive-min-threshold-pct": "profit_lock_adaptive_min_threshold_pct",
            "--profit-lock-adaptive-max-threshold-pct": "profit_lock_adaptive_max_threshold_pct",
            "--profit-lock-trend-filter": "profit_lock_trend_filter",
            "--profit-lock-regime-gated": "profit_lock_regime_gated",
        },
    },
    "trailing12_4_adapt": {
        "locks": {
            "enable_profit_lock": True,
            "profit_lock_mode": "trailing",
            "profit_lock_threshold_pct": 12.0,
            "profit_lock_trail_pct": 4.0,
            "profit_lock_partial_sell_pct": 50.0,
            "profit_lock_adaptive_threshold": True,
            "profit_lock_adaptive_symbol": "TQQQ",
            "profit_lock_adaptive_rv_window": 14,
            "profit_lock_adaptive_rv_baseline_pct": 85.0,
            "profit_lock_adaptive_min_threshold_pct": 8.0,
            "profit_lock_adaptive_max_threshold_pct": 30.0,
            "profit_lock_trend_filter": False,
            "profit_lock_regime_gated": False,
        },
        "cli_map": {
            "--enable-profit-lock": "enable_profit_lock",
            "--profit-lock-mode": "profit_lock_mode",
            "--profit-lock-threshold-pct": "profit_lock_threshold_pct",
            "--profit-lock-trail-pct": "profit_lock_trail_pct",
            "--profit-lock-partial-sell-pct": "profit_lock_partial_sell_pct",
            "--profit-lock-adaptive-threshold": "profit_lock_adaptive_threshold",
            "--profit-lock-adaptive-symbol": "profit_lock_adaptive_symbol",
            "--profit-lock-adaptive-rv-window": "profit_lock_adaptive_rv_window",
            "--profit-lock-adaptive-rv-baseline-pct": "profit_lock_adaptive_rv_baseline_pct",
            "--profit-lock-adaptive-min-threshold-pct": "profit_lock_adaptive_min_threshold_pct",
            "--profit-lock-adaptive-max-threshold-pct": "profit_lock_adaptive_max_threshold_pct",
            "--profit-lock-trend-filter": "profit_lock_trend_filter",
            "--profit-lock-regime-gated": "profit_lock_regime_gated",
        },
    },
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30": {
        "locks": {
            "enable_profit_lock": True,
            "profit_lock_mode": "trailing",
            "profit_lock_threshold_pct": 10.0,
            "profit_lock_trail_pct": 2.0,
            "profit_lock_partial_sell_pct": 50.0,
            "profit_lock_adaptive_threshold": True,
            "profit_lock_adaptive_symbol": "TQQQ",
            "profit_lock_adaptive_rv_window": 14,
            "profit_lock_adaptive_rv_baseline_pct": 85.0,
            "profit_lock_adaptive_min_threshold_pct": 8.0,
            "profit_lock_adaptive_max_threshold_pct": 30.0,
            "profit_lock_trend_filter": False,
            "profit_lock_regime_gated": False,
        },
        "cli_map": {
            "--enable-profit-lock": "enable_profit_lock",
            "--profit-lock-mode": "profit_lock_mode",
            "--profit-lock-threshold-pct": "profit_lock_threshold_pct",
            "--profit-lock-trail-pct": "profit_lock_trail_pct",
            "--profit-lock-partial-sell-pct": "profit_lock_partial_sell_pct",
            "--profit-lock-adaptive-threshold": "profit_lock_adaptive_threshold",
            "--profit-lock-adaptive-symbol": "profit_lock_adaptive_symbol",
            "--profit-lock-adaptive-rv-window": "profit_lock_adaptive_rv_window",
            "--profit-lock-adaptive-rv-baseline-pct": "profit_lock_adaptive_rv_baseline_pct",
            "--profit-lock-adaptive-min-threshold-pct": "profit_lock_adaptive_min_threshold_pct",
            "--profit-lock-adaptive-max-threshold-pct": "profit_lock_adaptive_max_threshold_pct",
            "--profit-lock-trend-filter": "profit_lock_trend_filter",
            "--profit-lock-regime-gated": "profit_lock_regime_gated",
        },
    },
}


@dataclass
class GpuReplayResult:
    equity_curve: list[tuple[date, float]]
    trade_count_total: int
    trade_count_by_day: dict[date, int]


@dataclass
class CpuReplayResult:
    equity_curve: list[tuple[date, float]]
    trade_count_total: int
    trade_count_by_day: dict[date, int]


def _date_from_iso(value: str) -> date:
    return date.fromisoformat(value)


def _fetch_symbol_daily(symbol: str, start_day: date, end_day: date) -> pd.DataFrame:
    ticker = f"{symbol.lower()}.us"
    resp = requests.get(STOOQ_DAILY_URL.format(ticker=ticker), timeout=30)
    resp.raise_for_status()
    frame = pd.read_csv(StringIO(resp.text))
    if frame.empty or "Date" not in frame.columns or "Close" not in frame.columns:
        raise RuntimeError(f"Unexpected Stooq CSV response for {symbol}")
    frame = frame.rename(columns={"Date": "date", "Close": "close", "High": "high"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if "high" in frame.columns:
        frame["high"] = pd.to_numeric(frame["high"], errors="coerce")
    else:
        frame["high"] = frame["close"]
    frame = frame.dropna(subset=["date", "close"])
    frame["high"] = frame["high"].fillna(frame["close"])
    frame = frame[(frame["date"] >= start_day) & (frame["date"] <= end_day)]
    frame = frame[["date", "close", "high"]].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"No rows after date filter for {symbol}")
    return frame


def _fetch_symbol_daily_yfinance(symbol: str, start_day: date, end_day: date) -> pd.DataFrame:
    import yfinance as yf

    # end is exclusive for yfinance download window
    end_exclusive = end_day + timedelta(days=1)
    data = yf.download(
        tickers=symbol,
        start=start_day.isoformat(),
        end=end_exclusive.isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if data is None or data.empty:
        raise RuntimeError(f"No yfinance rows for {symbol}")

    close_series = None
    high_series = None
    if isinstance(data.columns, pd.MultiIndex):
        # yfinance >=1.2 can return MultiIndex columns even for single ticker downloads.
        if ("Close", symbol) in data.columns:
            close_series = data[("Close", symbol)]
            high_series = data.get(("High", symbol))
        elif "Close" in data.columns.get_level_values(0):
            close_series = data.xs("Close", axis=1, level=0).iloc[:, 0]
            if "High" in data.columns.get_level_values(0):
                high_series = data.xs("High", axis=1, level=0).iloc[:, 0]
    elif "Close" in data.columns:
        close_series = data["Close"]
        if "High" in data.columns:
            high_series = data["High"]
    if close_series is None:
        raise RuntimeError(f"Missing Close series from yfinance for {symbol}")
    if high_series is None:
        high_series = close_series

    frame = pd.DataFrame({"date": data.index, "close": close_series.values, "high": high_series.values})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["high"] = pd.to_numeric(frame["high"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    frame["high"] = frame["high"].fillna(frame["close"])
    frame = frame[(frame["date"] >= start_day) & (frame["date"] <= end_day)]
    frame = frame[["date", "close", "high"]].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"No rows after yfinance date filter for {symbol}")
    return frame


def _fetch_symbol_daily_alpaca(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    api_key: str,
    secret_key: str,
    data_url: str,
    data_feed: str,
) -> pd.DataFrame:
    if not api_key or not secret_key:
        raise RuntimeError(
            "Alpaca credentials are required for --data-source alpaca. "
            "Set ALPACA_API_KEY/ALPACA_SECRET_KEY or pass --alpaca-api-key/--alpaca-secret-key."
        )
    base = (data_url or ALPACA_DATA_URL_DEFAULT).rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"https://{base}"
    endpoint = f"{base}/v2/stocks/{symbol}/bars"
    feed = (data_feed or "sip").lower()
    page_token = ""
    records: list[dict[str, object]] = []
    # Alpaca end is effectively exclusive for timestamp filters; include one extra day.
    end_exclusive = end_day + timedelta(days=1)
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    while True:
        params: dict[str, object] = {
            "timeframe": "1Day",
            "start": start_day.isoformat(),
            "end": end_exclusive.isoformat(),
            "adjustment": "all",
            "feed": feed,
            "sort": "asc",
            "limit": 10_000,
        }
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(endpoint, params=params, headers=headers, timeout=30)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Alpaca authorization failed for {symbol} (HTTP {resp.status_code}). "
                f"Check credentials and feed entitlement for '{feed}'."
            )
        resp.raise_for_status()
        payload = resp.json()
        bars = payload.get("bars", [])
        if not isinstance(bars, list):
            raise RuntimeError(f"Unexpected Alpaca bars response for {symbol}")
        records.extend(bars)
        page_token = str(payload.get("next_page_token") or "")
        if not page_token:
            break

    if not records:
        raise RuntimeError(f"No Alpaca bars for {symbol} in requested window")
    frame = pd.DataFrame.from_records(records)
    if frame.empty or "t" not in frame.columns or "c" not in frame.columns:
        raise RuntimeError(f"Unexpected Alpaca bars schema for {symbol}")
    frame["date"] = pd.to_datetime(frame["t"], errors="coerce", utc=True).dt.date
    frame["close"] = pd.to_numeric(frame["c"], errors="coerce")
    if "h" in frame.columns:
        frame["high"] = pd.to_numeric(frame["h"], errors="coerce")
    else:
        frame["high"] = frame["close"]
    frame = frame.dropna(subset=["date", "close"])
    frame["high"] = frame["high"].fillna(frame["close"])
    frame = frame[(frame["date"] >= start_day) & (frame["date"] <= end_day)]
    frame = frame[["date", "close", "high"]].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"No rows after Alpaca date filter for {symbol}")
    return frame


def _fetch_symbol_daily_polygon(
    *,
    symbol: str,
    start_day: date,
    end_day: date,
    api_key: str,
    base_url: str,
) -> pd.DataFrame:
    if not api_key:
        raise RuntimeError(
            "Polygon API key is required for --data-source polygon. "
            "Set POLYGON_API_KEY or pass --polygon-api-key."
        )
    base = (base_url or POLYGON_BASE_URL_DEFAULT).rstrip("/")
    if not base.startswith("http://") and not base.startswith("https://"):
        base = f"https://{base}"
    endpoint = f"{base}/v2/aggs/ticker/{symbol}/range/1/day/{start_day.isoformat()}/{end_day.isoformat()}"
    params: dict[str, object] = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50_000,
        "apiKey": api_key,
    }
    results: list[dict[str, object]] = []
    next_url = endpoint
    max_retries = 8

    def _request_json(url: str, *, params_local: dict[str, object] | None) -> dict[str, object]:
        for attempt in range(max_retries):
            resp = requests.get(url, params=params_local, timeout=30)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait_s = 2.0 ** attempt
                if retry_after:
                    try:
                        wait_s = max(wait_s, float(retry_after))
                    except ValueError:
                        pass
                time.sleep(min(wait_s, 60.0))
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"Polygon authorization failed for {symbol} (HTTP {resp.status_code}). "
                    "Check POLYGON_API_KEY entitlement."
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(
            f"Polygon rate limit persisted for {symbol} after {max_retries} retries. "
            "Retry later or reduce request rate."
        )

    while next_url:
        payload = _request_json(next_url, params_local=params if next_url == endpoint else None)
        chunk = payload.get("results", [])
        if isinstance(chunk, list):
            results.extend(chunk)
        next_url = str(payload.get("next_url") or "")
        if next_url and "apiKey=" not in next_url:
            sep = "&" if "?" in next_url else "?"
            next_url = f"{next_url}{sep}apiKey={api_key}"
    if not results:
        raise RuntimeError(f"No Polygon aggregates for {symbol} in requested window")
    frame = pd.DataFrame.from_records(results)
    if frame.empty or "t" not in frame.columns or "c" not in frame.columns:
        raise RuntimeError(f"Unexpected Polygon aggregates schema for {symbol}")
    frame["date"] = pd.to_datetime(frame["t"], unit="ms", errors="coerce", utc=True).dt.date
    frame["close"] = pd.to_numeric(frame["c"], errors="coerce")
    if "h" in frame.columns:
        frame["high"] = pd.to_numeric(frame["h"], errors="coerce")
    else:
        frame["high"] = frame["close"]
    frame = frame.dropna(subset=["date", "close"])
    frame["high"] = frame["high"].fillna(frame["close"])
    frame = frame[(frame["date"] >= start_day) & (frame["date"] <= end_day)]
    frame = frame[["date", "close", "high"]].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"No rows after Polygon date filter for {symbol}")
    return frame


def _build_wide_table(frames: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DataFrame:
    wide: pd.DataFrame | None = None
    for symbol in symbols:
        f = frames[symbol][["date", "close"]].rename(columns={"close": symbol})
        if wide is None:
            wide = f
        else:
            wide = wide.merge(f, on="date", how="inner")
    if wide is None or wide.empty:
        raise RuntimeError("Failed to construct non-empty wide table")
    wide = wide.sort_values("date").reset_index(drop=True)
    return wide


def _build_wide_high_table(frames: dict[str, pd.DataFrame], symbols: list[str]) -> pd.DataFrame:
    wide: pd.DataFrame | None = None
    for symbol in symbols:
        f = frames[symbol][["date", "high"]].rename(columns={"high": symbol})
        if wide is None:
            wide = f
        else:
            wide = wide.merge(f, on="date", how="inner")
    if wide is None or wide.empty:
        raise RuntimeError("Failed to construct non-empty high table")
    wide = wide.sort_values("date").reset_index(drop=True)
    return wide


def _wide_to_history(wide: pd.DataFrame, symbols: list[str]) -> dict[str, list[tuple[date, float]]]:
    history: dict[str, list[tuple[date, float]]] = {}
    for symbol in symbols:
        history[symbol] = [(d, float(px)) for d, px in zip(wide["date"], wide[symbol], strict=True)]
    return history


def _annualized_rv_pct(closes: list[float]) -> float:
    if len(closes) < 2:
        return 0.0
    rets: list[float] = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        cur = float(closes[i])
        if prev > 0.0:
            rets.append(cur / prev - 1.0)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / len(rets)
    return 100.0 * (var ** 0.5) * (252.0 ** 0.5)


def _build_profit_lock_threshold_series(
    price_history: dict[str, list[tuple[date, float]]],
    *,
    base_threshold_pct: float,
    adaptive_enabled: bool,
    adaptive_symbol: str,
    adaptive_rv_window: int,
    adaptive_rv_baseline_pct: float,
    adaptive_min_threshold_pct: float,
    adaptive_max_threshold_pct: float,
) -> list[float]:
    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    if not adaptive_enabled:
        return [float(base_threshold_pct)] * n
    if adaptive_symbol not in price_history:
        adaptive_symbol = symbols[0] if symbols else ""
    if not adaptive_symbol:
        return [float(base_threshold_pct)] * n

    closes = [float(px) for _, px in price_history[adaptive_symbol]]
    out = [float(base_threshold_pct)] * n
    w = max(2, int(adaptive_rv_window))
    base = float(base_threshold_pct)
    baseline = max(1e-9, float(adaptive_rv_baseline_pct))
    tmin = min(float(adaptive_min_threshold_pct), float(adaptive_max_threshold_pct))
    tmax = max(float(adaptive_min_threshold_pct), float(adaptive_max_threshold_pct))
    for i in range(n):
        if i < w:
            out[i] = base
            continue
        rv = _annualized_rv_pct(closes[i - w : i])
        ratio = rv / baseline if baseline > 0 else 1.0
        thr = base * ratio
        thr = min(tmax, max(tmin, thr))
        out[i] = float(thr)
    return out


def _build_trend_filter_flags(
    price_history: dict[str, list[tuple[date, float]]],
    *,
    trend_filter_enabled: bool,
    trend_symbol: str,
    trend_ma_window: int,
) -> list[bool]:
    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    if not trend_filter_enabled:
        return [True] * n
    if trend_symbol not in price_history:
        trend_symbol = symbols[0] if symbols else ""
    if not trend_symbol:
        return [True] * n

    closes = [float(px) for _, px in price_history[trend_symbol]]
    w = max(2, int(trend_ma_window))
    out = [False] * n
    for i in range(n):
        # Use only prior closes for today's gate to avoid lookahead.
        if i < w:
            out[i] = False
            continue
        lookback = closes[i - w : i]
        ma = sum(lookback) / len(lookback)
        prev_close = closes[i - 1]
        out[i] = prev_close >= ma
    return out


def _build_profit_lock_gate_flags(
    price_history: dict[str, list[tuple[date, float]]],
    *,
    regime_gated: bool,
    regime_symbol: str,
    regime_rv_window: int,
    regime_rv_threshold_pct: float,
) -> list[bool]:
    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    if not regime_gated:
        return [True] * n
    if regime_symbol not in price_history:
        if symbols:
            regime_symbol = symbols[0]
        else:
            return [False] * n
    closes = [float(px) for _, px in price_history[regime_symbol]]
    out = [False] * n
    w = max(2, int(regime_rv_window))
    for i in range(n):
        if i < w:
            out[i] = False
            continue
        rv = _annualized_rv_pct(closes[i - w : i])
        out[i] = rv >= float(regime_rv_threshold_pct)
    return out


def _effective_profit_lock_exec_model(requested: str) -> str:
    req = str(requested).strip().lower()
    if req == "market_close":
        return "market_close"
    # broker_*_sim and paper_live_style_optimistic map to synthetic trigger/trailing fill emulation.
    return "synthetic"


def _cpu_replay_from_allocations(
    price_history: dict[str, list[tuple[date, float]]],
    high_history: dict[str, list[tuple[date, float]]],
    cpu_allocations: list[tuple[date, dict[str, float]]],
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    *,
    enable_profit_lock: bool,
    profit_lock_mode: str,
    profit_lock_threshold_series_pct: list[float] | None,
    profit_lock_threshold_pct: float,
    profit_lock_partial_sell_pct: float,
    profit_lock_trail_pct: float,
    profit_lock_exec_model: str = "synthetic",
    profit_lock_gate_flags: list[bool] | None = None,
) -> CpuReplayResult:
    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    m = len(symbols)
    dates = [price_history[symbols[0]][i][0] for i in range(n)]

    prices = [[float(price_history[s][i][1]) for s in symbols] for i in range(n)]
    highs = [[float(high_history[s][i][1]) for s in symbols] for i in range(n)]
    alloc_by_day = {d.isoformat(): w for d, w in cpu_allocations}
    symbol_to_idx = {s: i for i, s in enumerate(symbols)}
    targets = [[0.0 for _ in range(m)] for _ in range(n)]
    for i, d in enumerate(dates):
        weights = alloc_by_day.get(d.isoformat(), {})
        for sym, w in weights.items():
            if sym in symbol_to_idx:
                targets[i][symbol_to_idx[sym]] = float(w)

    cash = float(initial_equity)
    holdings = [0.0 for _ in range(m)]
    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trail_ratio = max(0.0, float(profit_lock_trail_pct) / 100.0)
    partial_ratio = min(1.0, max(0.0, float(profit_lock_partial_sell_pct) / 100.0))
    if profit_lock_mode in {"partial", "trailing_partial"} and partial_ratio <= 0.0:
        partial_ratio = 0.5
    total_trade_count = 0
    trade_count_by_day: dict[date, int] = {}
    equity_curve: list[tuple[date, float]] = []

    for i, d in enumerate(dates):
        px = prices[i]
        hi = highs[i]
        tgt = targets[i]
        day_trade_count = 0

        gate_on = True
        if profit_lock_gate_flags is not None and i < len(profit_lock_gate_flags):
            gate_on = bool(profit_lock_gate_flags[i])
        if enable_profit_lock and i > 0 and gate_on:
            prev_px = prices[i - 1]
            thr_pct = float(profit_lock_threshold_pct)
            if profit_lock_threshold_series_pct is not None and i < len(profit_lock_threshold_series_pct):
                thr_pct = float(profit_lock_threshold_series_pct[i])
            threshold_ratio = 1.0 + (thr_pct / 100.0)
            for sym_idx in range(m):
                held_qty = float(holdings[sym_idx])
                if held_qty <= 0.0:
                    continue
                prev_close = float(prev_px[sym_idx])
                day_high = float(hi[sym_idx])
                day_close = float(px[sym_idx])
                if prev_close <= 0.0:
                    continue
                trigger_price = prev_close * threshold_ratio
                sell_ratio = 0.0
                exec_sell_price = 0.0

                if profit_lock_mode in {"fixed", "partial"}:
                    if day_high >= trigger_price:
                        sell_ratio = 1.0 if profit_lock_mode == "fixed" else partial_ratio
                        if profit_lock_exec_model == "market_close":
                            exec_sell_price = day_close * (1.0 - slip)
                        else:
                            exec_sell_price = trigger_price * (1.0 - slip)
                elif profit_lock_mode in {"trailing", "trailing_partial"}:
                    if day_high >= trigger_price:
                        trail_stop = day_high * (1.0 - trail_ratio)
                        if day_close <= trail_stop:
                            sell_ratio = 1.0 if profit_lock_mode == "trailing" else partial_ratio
                            if profit_lock_exec_model == "market_close":
                                exec_sell_price = day_close * (1.0 - slip)
                            else:
                                exec_sell_price = trail_stop * (1.0 - slip)

                if sell_ratio <= 0.0 or exec_sell_price <= 0.0:
                    continue
                sell_qty = held_qty * sell_ratio
                notional = sell_qty * exec_sell_price
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym_idx] -= sell_qty
                day_trade_count += 1

        equity_before = cash + sum(holdings[j] * px[j] for j in range(m))

        if sum(tgt) > 0.0:
            for sym_idx in range(m):
                price = float(px[sym_idx])
                if price <= 0.0:
                    continue
                current_notional = holdings[sym_idx] * price
                target_notional = float(tgt[sym_idx]) * equity_before
                delta = target_notional - current_notional
                if delta >= 0.0:
                    continue
                desired_qty = abs(delta) / price
                sell_qty = min(desired_qty, max(holdings[sym_idx], 0.0))
                if sell_qty <= 0.0:
                    continue
                exec_sell_price = price * (1.0 - slip)
                notional = sell_qty * exec_sell_price
                fee = abs(notional) * sell_fee
                cash += notional - fee
                holdings[sym_idx] -= sell_qty
                day_trade_count += 1

            for sym_idx in range(m):
                price = float(px[sym_idx])
                if price <= 0.0:
                    continue
                current_notional = holdings[sym_idx] * price
                target_notional = float(tgt[sym_idx]) * equity_before
                delta = target_notional - current_notional
                if delta <= 0.0:
                    continue
                desired_qty = delta / price
                exec_buy_price = price * (1.0 + slip)
                if exec_buy_price <= 0.0:
                    continue
                max_affordable = cash / exec_buy_price
                buy_qty = min(desired_qty, max_affordable)
                if buy_qty <= 0.0:
                    continue
                cash -= buy_qty * exec_buy_price
                holdings[sym_idx] += buy_qty
                day_trade_count += 1

        total_trade_count += day_trade_count
        trade_count_by_day[d] = day_trade_count
        equity_after = cash + sum(holdings[j] * px[j] for j in range(m))
        equity_curve.append((d, equity_after))

    return CpuReplayResult(
        equity_curve=equity_curve,
        trade_count_total=total_trade_count,
        trade_count_by_day=trade_count_by_day,
    )


def _ensure_cuda_env_for_cupy() -> None:
    if os.getenv("CUDA_PATH"):
        return
    venv_nvrtc = ROOT / "composer_original" / ".venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cuda_nvrtc"
    lib_dir = venv_nvrtc / "lib"
    if venv_nvrtc.exists() and lib_dir.exists():
        os.environ["CUDA_PATH"] = str(venv_nvrtc)
        current = os.getenv("LD_LIBRARY_PATH", "")
        prefix = str(lib_dir)
        os.environ["LD_LIBRARY_PATH"] = f"{prefix}:{current}" if current else prefix


def _gpu_replay_from_cpu_allocations(
    price_history: dict[str, list[tuple[date, float]]],
    high_history: dict[str, list[tuple[date, float]]],
    cpu_allocations: list[tuple[date, dict[str, float]]],
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    *,
    enable_profit_lock: bool,
    profit_lock_mode: str,
    profit_lock_threshold_series_pct: list[float] | None,
    profit_lock_threshold_pct: float,
    profit_lock_partial_sell_pct: float,
    profit_lock_trail_pct: float,
    profit_lock_exec_model: str = "synthetic",
    profit_lock_gate_flags: list[bool] | None = None,
) -> GpuReplayResult:
    _ensure_cuda_env_for_cupy()
    import cupy as cp

    symbols = sorted(price_history.keys())
    n = len(next(iter(price_history.values())))
    dates = [price_history[symbols[0]][i][0] for i in range(n)]
    prices_np = [[price_history[s][i][1] for s in symbols] for i in range(n)]
    prices = cp.asarray(prices_np, dtype=cp.float64)
    highs_np = [[high_history[s][i][1] for s in symbols] for i in range(n)]
    highs = cp.asarray(highs_np, dtype=cp.float64)

    alloc_by_day = {d.isoformat(): w for d, w in cpu_allocations}
    symbol_to_idx = {s: i for i, s in enumerate(symbols)}
    targets = cp.zeros((n, len(symbols)), dtype=cp.float64)
    for i, d in enumerate(dates):
        weights = alloc_by_day.get(d.isoformat(), {})
        if not weights:
            continue
        row = cp.zeros((len(symbols),), dtype=cp.float64)
        for sym, w in weights.items():
            if sym in symbol_to_idx:
                row[symbol_to_idx[sym]] = float(w)
        targets[i] = row

    cash = float(initial_equity)
    holdings = cp.zeros((len(symbols),), dtype=cp.float64)
    slip = float(slippage_bps) / 10_000.0
    sell_fee = float(sell_fee_bps) / 10_000.0
    trail_ratio = max(0.0, float(profit_lock_trail_pct) / 100.0)
    partial_ratio = min(1.0, max(0.0, float(profit_lock_partial_sell_pct) / 100.0))
    if profit_lock_mode in {"partial", "trailing_partial"} and partial_ratio <= 0.0:
        partial_ratio = 0.5
    total_trade_count = 0
    trade_count_by_day: dict[date, int] = {}
    equity_curve: list[tuple[date, float]] = []

    for i, d in enumerate(dates):
        px = prices[i]
        hi = highs[i]
        tgt = targets[i]
        day_trade_count = 0

        gate_on = True
        if profit_lock_gate_flags is not None and i < len(profit_lock_gate_flags):
            gate_on = bool(profit_lock_gate_flags[i])
        if enable_profit_lock and i > 0 and gate_on:
            thr_pct = float(profit_lock_threshold_pct)
            if profit_lock_threshold_series_pct is not None and i < len(profit_lock_threshold_series_pct):
                thr_pct = float(profit_lock_threshold_series_pct[i])
            threshold_ratio = 1.0 + (thr_pct / 100.0)
            prev_px = prices[i - 1]
            trigger_px = prev_px * threshold_ratio
            base_mask = (holdings > 0.0) & (prev_px > 0.0) & (hi >= trigger_px)
            sell_qty = cp.zeros_like(holdings)
            exec_sell_price = cp.zeros_like(px)
            if profit_lock_mode in {"fixed", "partial"}:
                ratio = 1.0 if profit_lock_mode == "fixed" else partial_ratio
                sell_qty = holdings * ratio * base_mask
                if profit_lock_exec_model == "market_close":
                    exec_sell_price = px * (1.0 - slip)
                else:
                    exec_sell_price = trigger_px * (1.0 - slip)
            elif profit_lock_mode in {"trailing", "trailing_partial"}:
                ratio = 1.0 if profit_lock_mode == "trailing" else partial_ratio
                trail_stop = hi * (1.0 - trail_ratio)
                trail_mask = base_mask & (px <= trail_stop)
                sell_qty = holdings * ratio * trail_mask
                if profit_lock_exec_model == "market_close":
                    exec_sell_price = px * (1.0 - slip)
                else:
                    exec_sell_price = trail_stop * (1.0 - slip)

            if bool(cp.any(sell_qty > 0.0).item()):
                notional = sell_qty * exec_sell_price
                fee = cp.abs(notional) * sell_fee
                cash += float(cp.sum(notional - fee).item())
                holdings = cp.maximum(holdings - sell_qty, 0.0)
                day_trade_count += int(cp.sum(sell_qty > 0.0).item())

        equity_before = cash + float(cp.sum(holdings * px).item())
        if float(cp.sum(tgt).item()) <= 0.0:
            total_trade_count += day_trade_count
            trade_count_by_day[d] = day_trade_count
            equity_curve.append((d, equity_before))
            continue

        current_notional = holdings * px
        target_notional = tgt * equity_before
        delta_notional = target_notional - current_notional
        qty = cp.abs(delta_notional / px)
        sell_mask = delta_notional < 0
        buy_mask = delta_notional > 0

        if bool(cp.any(sell_mask).item()):
            sell_qty = cp.minimum(qty, cp.maximum(holdings, 0.0))
            exec_sell_price = px * (1.0 - slip)
            notional = sell_qty * exec_sell_price * sell_mask
            fee = cp.abs(notional) * sell_fee
            cash += float(cp.sum(notional - fee).item())
            holdings = holdings - (sell_qty * sell_mask)
            day_trade_count += int(cp.sum((sell_qty * sell_mask) > 0).item())

        exec_buy_price = px * (1.0 + slip)
        qty_host = cp.asnumpy(qty)
        buy_mask_host = cp.asnumpy(buy_mask)
        exec_buy_price_host = cp.asnumpy(exec_buy_price)
        for sym_idx in range(len(symbols)):
            if not bool(buy_mask_host[sym_idx]):
                continue
            desired_qty = float(qty_host[sym_idx])
            px_exec = float(exec_buy_price_host[sym_idx])
            if px_exec <= 0:
                continue
            max_affordable = cash / px_exec
            actual_qty = min(desired_qty, max_affordable)
            if actual_qty <= 0:
                continue
            cash -= actual_qty * px_exec
            holdings[sym_idx] = holdings[sym_idx] + actual_qty
            day_trade_count += 1

        total_trade_count += day_trade_count
        trade_count_by_day[d] = day_trade_count
        equity_after = cash + float(cp.sum(holdings * px).item())
        equity_curve.append((d, equity_after))

    return GpuReplayResult(
        equity_curve=equity_curve,
        trade_count_total=total_trade_count,
        trade_count_by_day=trade_count_by_day,
    )


def _slice_curve(curve: list[tuple[date, float]], start_day: date, end_day: date) -> list[tuple[date, float]]:
    return [(d, eq) for d, eq in curve if start_day <= d <= end_day]


def _curve_summary(curve: list[tuple[date, float]]) -> dict[str, float | int | str]:
    if not curve:
        return {
            "equity_points": 0,
            "start_date": "",
            "end_date": "",
            "start_equity": 0.0,
            "final_equity": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "cagr_pct": 0.0,
            "avg_daily_return_pct": 0.0,
        }

    start_day, start_eq = curve[0]
    end_day, end_eq = curve[-1]
    total_return = 100.0 * (end_eq / start_eq - 1.0) if start_eq > 0 else 0.0

    peak = -math.inf
    max_dd = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    years = max((end_day - start_day).days / 365.25, 1e-9)
    cagr = 100.0 * ((end_eq / start_eq) ** (1.0 / years) - 1.0) if start_eq > 0 and end_eq > 0 else 0.0

    daily_returns: list[float] = []
    for i in range(1, len(curve)):
        prev = curve[i - 1][1]
        cur = curve[i][1]
        if prev > 0:
            daily_returns.append(100.0 * (cur / prev - 1.0))
    avg_daily_return = mean(daily_returns) if daily_returns else 0.0

    return {
        "equity_points": len(curve),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "start_equity": float(start_eq),
        "final_equity": float(end_eq),
        "total_return_pct": float(total_return),
        "max_drawdown_pct": float(100.0 * max_dd),
        "cagr_pct": float(cagr),
        "avg_daily_return_pct": float(avg_daily_return),
    }


def _parse_cli_overrides(argv_tokens: list[str]) -> dict[str, str | bool]:
    overrides: dict[str, str | bool] = {}
    i = 0
    while i < len(argv_tokens):
        token = argv_tokens[i]
        if not token.startswith("--"):
            i += 1
            continue
        if "=" in token:
            k, v = token.split("=", 1)
            overrides[k] = v
            i += 1
            continue
        if i + 1 < len(argv_tokens) and not argv_tokens[i + 1].startswith("--"):
            overrides[token] = argv_tokens[i + 1]
            i += 2
            continue
        overrides[token] = True
        i += 1
    return overrides


def _coerce_expected_type(value: str | bool, expected: object) -> object:
    if isinstance(expected, bool):
        if isinstance(value, bool):
            return value
        txt = str(value).strip().lower()
        if txt in {"1", "true", "yes", "on"}:
            return True
        if txt in {"0", "false", "no", "off"}:
            return False
        return True
    if isinstance(expected, int) and not isinstance(expected, bool):
        return int(float(str(value)))
    if isinstance(expected, float):
        return float(str(value))
    return str(value)


def _apply_strategy_preset(args: argparse.Namespace, argv_tokens: list[str]) -> None:
    if not args.strategy_preset:
        return
    if args.walk_forward_grid:
        raise ValueError("--strategy-preset cannot be combined with --walk-forward-grid")
    if args.strategy_preset not in STRATEGY_PRESETS:
        raise ValueError(f"Unknown strategy preset: {args.strategy_preset}")

    preset = STRATEGY_PRESETS[args.strategy_preset]
    locks: dict[str, object] = dict(preset["locks"])
    cli_map: dict[str, str] = dict(preset["cli_map"])
    cli_overrides = _parse_cli_overrides(argv_tokens)

    conflicts: list[str] = []
    for cli_flag, attr_name in cli_map.items():
        if cli_flag not in cli_overrides:
            continue
        expected = locks[attr_name]
        provided = _coerce_expected_type(cli_overrides[cli_flag], expected)
        if provided != expected:
            conflicts.append(f"{cli_flag}={provided} (expected {expected})")

    if conflicts:
        details = "; ".join(conflicts)
        raise ValueError(
            f"Preset '{args.strategy_preset}' is locked. Remove conflicting flags: {details}"
        )

    for attr_name, expected in locks.items():
        setattr(args, attr_name, expected)


def _shift_months(day: date, months: int) -> date:
    return (pd.Timestamp(day) + pd.DateOffset(months=months)).date()


def _build_walk_forward_folds(*, end_day: date, train_months: int, test_months: int, fold_count: int) -> list[dict[str, object]]:
    folds_newest_first: list[dict[str, object]] = []
    for i in range(fold_count):
        test_end = _shift_months(end_day, -test_months * i)
        test_start = _shift_months(test_end, -test_months) + timedelta(days=1)
        train_end = test_start - timedelta(days=1)
        train_start = _shift_months(train_end, -train_months) + timedelta(days=1)
        folds_newest_first.append(
            {
                "name": f"fold{i + 1}",
                "train": (train_start, train_end),
                "test": (test_start, test_end),
            }
        )
    return list(reversed(folds_newest_first))


def _profit_lock_grid_configs() -> list[dict[str, object]]:
    return [
        {"name": "original_composer", "flags": ["--strategy-preset", "original_composer"]},
        {"name": "trailing12_4_adapt", "flags": ["--strategy-preset", "trailing12_4_adapt"]},
        {
            "name": "aggr_adapt_t10_tr2_rv14_b85_m8_M30",
            "flags": ["--strategy-preset", "aggr_adapt_t10_tr2_rv14_b85_m8_M30"],
        },
    ]


def _run_walk_forward_grid(args: argparse.Namespace, *, end_day: date, reports_dir: Path) -> int:
    fold_count = max(1, int(args.walk_forward_folds))
    train_months = max(1, int(args.walk_forward_train_months))
    test_months = max(1, int(args.walk_forward_test_months))
    folds = _build_walk_forward_folds(
        end_day=end_day,
        train_months=train_months,
        test_months=test_months,
        fold_count=fold_count,
    )

    configs = _profit_lock_grid_configs()
    max_configs = int(args.walk_forward_max_configs)
    if max_configs > 0:
        configs = configs[:max_configs]

    base_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--initial-equity",
        str(float(args.initial_equity)),
        "--warmup-days",
        str(int(args.warmup_days)),
        "--lookback-buffer-days",
        str(int(args.lookback_buffer_days)),
        "--strategy-mode",
        str(args.strategy_mode),
        "--slippage-bps",
        str(float(args.slippage_bps)),
        "--sell-fee-bps",
        str(float(args.sell_fee_bps)),
        "--profit-lock-exec-model",
        str(args.profit_lock_exec_model),
        "--parquet-dir",
        str(args.parquet_dir),
        "--reports-dir",
        str(args.reports_dir),
        "--fixtures-dir",
        str(args.fixtures_dir),
    ]
    if str(args.data_source) == "alpaca":
        base_cmd.extend(["--alpaca-data-feed", str(args.alpaca_data_feed)])
        base_cmd.extend(["--alpaca-data-url", str(args.alpaca_data_url)])
    if str(args.data_source) == "polygon":
        base_cmd.extend(["--polygon-base-url", str(args.polygon_base_url)])
    if args.anchor_window_start_equity:
        base_cmd.append("--anchor-window-start-equity")
    if args.composer_like_mode:
        base_cmd.append("--composer-like-mode")
    else:
        base_cmd.extend(["--data-source", str(args.data_source)])

    reports_path = Path(args.reports_dir)

    def run_window(flags: list[str], start_day_local: date, end_day_local: date) -> dict[str, float]:
        cmd = base_cmd + [
            "--start-date",
            start_day_local.isoformat(),
            "--end-date",
            end_day_local.isoformat(),
        ] + list(flags)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Walk-forward run failed {start_day_local}->{end_day_local} rc={proc.returncode}\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        cpu = json.loads((reports_path / "backtest_cpu_last6m.json").read_text(encoding="utf-8"))
        gpu = json.loads((reports_path / "backtest_gpu_last6m.json").read_text(encoding="utf-8"))
        summary = json.loads((reports_path / "backtest_cpu_gpu_last6m_summary.json").read_text(encoding="utf-8"))
        return {
            "cpu_final_equity": float(cpu["window_metrics"]["final_equity"]),
            "cpu_return_pct": float(cpu["window_metrics"]["total_return_pct"]),
            "cpu_max_drawdown_pct": float(cpu["window_metrics"]["max_drawdown_pct"]),
            "cpu_trades": int(cpu["window_trade_count"]),
            "gpu_final_equity": float(gpu["window_metrics"]["final_equity"]),
            "gpu_return_pct": float(gpu["window_metrics"]["total_return_pct"]),
            "gpu_max_drawdown_pct": float(gpu["window_metrics"]["max_drawdown_pct"]),
            "gpu_trades": int(gpu["window_trade_count"]),
            "cpu_gpu_diff_bps": float(summary["parity_window"]["final_equity_diff_bps_vs_cpu"]),
        }

    results: list[dict[str, object]] = []
    for cfg in configs:
        cfg_name = str(cfg["name"])
        cfg_flags = list(cfg["flags"])
        fold_rows: list[dict[str, object]] = []
        for fold in folds:
            train_start, train_end = fold["train"]
            test_start, test_end = fold["test"]
            train_result = run_window(cfg_flags, train_start, train_end)
            test_result = run_window(cfg_flags, test_start, test_end)
            fold_rows.append(
                {
                    "fold": str(fold["name"]),
                    "train_window": {"start": train_start.isoformat(), "end": train_end.isoformat()},
                    "test_window": {"start": test_start.isoformat(), "end": test_end.isoformat()},
                    "train": train_result,
                    "test": test_result,
                }
            )

        test_returns = [float(r["test"]["cpu_return_pct"]) for r in fold_rows]
        test_equities = [float(r["test"]["cpu_final_equity"]) for r in fold_rows]
        test_drawdowns = [float(r["test"]["cpu_max_drawdown_pct"]) for r in fold_rows]
        test_diff_bps = [abs(float(r["test"]["cpu_gpu_diff_bps"])) for r in fold_rows]
        aggregate = {
            "config": cfg_name,
            "avg_test_return_pct": float(sum(test_returns) / len(test_returns)),
            "median_test_return_pct": float(median(test_returns)),
            "std_test_return_pct": float(pstdev(test_returns) if len(test_returns) > 1 else 0.0),
            "avg_test_final_equity": float(sum(test_equities) / len(test_equities)),
            "avg_test_max_drawdown_pct": float(sum(test_drawdowns) / len(test_drawdowns)),
            "avg_abs_cpu_gpu_diff_bps": float(sum(test_diff_bps) / len(test_diff_bps)),
            "fold_count": len(fold_rows),
        }
        results.append({"config": cfg_name, "flags": cfg_flags, "folds": fold_rows, "aggregate": aggregate})

    baseline = next((r for r in results if str(r["config"]) == "original_composer"), None)
    if baseline is not None:
        baseline_by_fold = {r["fold"]: float(r["test"]["cpu_final_equity"]) for r in baseline["folds"]}
        for r in results:
            wins = 0
            deltas: list[float] = []
            for fr in r["folds"]:
                fold_name = fr["fold"]
                if fold_name not in baseline_by_fold:
                    continue
                base_val = float(baseline_by_fold[fold_name])
                cur_val = float(fr["test"]["cpu_final_equity"])
                if cur_val > base_val:
                    wins += 1
                deltas.append(cur_val - base_val)
            r["aggregate"]["wins_vs_baseline"] = int(wins)
            r["aggregate"]["avg_test_equity_delta_vs_baseline"] = (
                float(sum(deltas) / len(deltas)) if deltas else 0.0
            )

    leaderboard = sorted(
        [r["aggregate"] for r in results],
        key=lambda x: (float(x["avg_test_final_equity"]), float(x["avg_test_return_pct"])),
        reverse=True,
    )

    default_out = reports_dir / f"walk_forward_profit_lock_grid_leaderboard_{int(float(args.initial_equity))}.json"
    out_path = Path(args.walk_forward_output) if args.walk_forward_output else default_out
    payload = {
        "initial_equity": float(args.initial_equity),
        "strategy_mode": str(args.strategy_mode),
        "mode": "composer_like_mode" if bool(args.composer_like_mode) else str(args.data_source),
        "walk_forward": {
            "fold_definition": f"rolling_train_{train_months}m_test_{test_months}m",
            "folds": [
                {
                    "name": str(f["name"]),
                    "train": {"start": f["train"][0].isoformat(), "end": f["train"][1].isoformat()},
                    "test": {"start": f["test"][0].isoformat(), "end": f["test"][1].isoformat()},
                }
                for f in folds
            ],
        },
        "config_count": len(configs),
        "results": results,
        "leaderboard": leaderboard,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    top = leaderboard[:5]
    print(
        json.dumps(
            {
                "walk_forward_output": str(out_path),
                "top": top,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    argv_tokens = list(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Fetch last-6-month parquet data and run CPU/GPU backtests separately")
    parser.add_argument("--start-date", type=_date_from_iso, default=None, help="Backtest window start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=_date_from_iso, default=None, help="Backtest window end date (YYYY-MM-DD)")
    parser.add_argument("--lookback-buffer-days", type=int, default=420, help="Extra calendar days fetched before start date")
    parser.add_argument("--initial-equity", type=float, default=100_000.0)
    parser.add_argument("--warmup-days", type=int, default=260)
    parser.add_argument(
        "--anchor-window-start-equity",
        action="store_true",
        help="Force first in-window equity point to start from initial equity by deferring first rebalance until after window start",
    )
    parser.add_argument(
        "--data-source",
        choices=["stooq", "yfinance", "alpaca", "polygon"],
        default="stooq",
        help="Daily price source used for parquet generation and backtest input",
    )
    parser.add_argument(
        "--alpaca-data-feed",
        default=os.getenv("ALPACA_DATA_FEED", "sip"),
        help="Alpaca market data feed, e.g. sip or iex.",
    )
    parser.add_argument(
        "--alpaca-data-url",
        default=os.getenv("ALPACA_DATA_URL", ALPACA_DATA_URL_DEFAULT),
        help="Alpaca data API base URL.",
    )
    parser.add_argument(
        "--alpaca-api-key",
        default=os.getenv("ALPACA_API_KEY", ""),
        help="Alpaca API key (default from ALPACA_API_KEY env var).",
    )
    parser.add_argument(
        "--alpaca-secret-key",
        default=os.getenv("ALPACA_SECRET_KEY", ""),
        help="Alpaca secret key (default from ALPACA_SECRET_KEY env var).",
    )
    parser.add_argument(
        "--polygon-base-url",
        default=os.getenv("POLYGON_BASE_URL", POLYGON_BASE_URL_DEFAULT),
        help="Polygon API base URL.",
    )
    parser.add_argument(
        "--polygon-api-key",
        default=os.getenv("POLYGON_API_KEY", ""),
        help="Polygon API key (default from POLYGON_API_KEY env var).",
    )
    parser.add_argument(
        "--composer-like-mode",
        action="store_true",
        help="Convenience flag: use yfinance adjusted closes + anchor window start equity",
    )
    parser.add_argument(
        "--strategy-mode",
        choices=["original"],
        default="original",
        help="Strategy evaluator profile (locked to original composer logic).",
    )
    parser.add_argument(
        "--strategy-preset",
        choices=sorted(STRATEGY_PRESETS.keys()),
        default="original_composer",
        help="Locked strategy profile. Only original_composer, trailing12_4_adapt, and aggr_adapt_t10_tr2_rv14_b85_m8_M30 are available.",
    )
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--sell-fee-bps", type=float, default=0.0)
    parser.add_argument("--enable-profit-lock", action="store_true")
    parser.add_argument(
        "--profit-lock-mode",
        choices=["fixed", "trailing", "partial", "trailing_partial"],
        default="fixed",
    )
    parser.add_argument("--profit-lock-threshold-pct", type=float, default=15.0)
    parser.add_argument("--profit-lock-trail-pct", type=float, default=5.0)
    parser.add_argument("--profit-lock-partial-sell-pct", type=float, default=50.0)
    parser.add_argument(
        "--profit-lock-exec-model",
        choices=[
            "synthetic",
            "market_close",
            "paper_live_style_optimistic",
            "broker_stop_sim",
            "broker_trailing_stop_sim",
            "broker_bracket_sim",
        ],
        default="synthetic",
        help=(
            "Profit-lock execution model: synthetic emulates trigger-price fills, "
            "market_close exits lock-triggered sells at close, "
            "paper_live_style_optimistic uses synthetic fills but keeps paper/live-style framing."
        ),
    )
    parser.add_argument("--profit-lock-adaptive-threshold", action="store_true")
    parser.add_argument("--profit-lock-adaptive-symbol", default="TQQQ")
    parser.add_argument("--profit-lock-adaptive-rv-window", type=int, default=14)
    parser.add_argument("--profit-lock-adaptive-rv-baseline-pct", type=float, default=85.0)
    parser.add_argument("--profit-lock-adaptive-min-threshold-pct", type=float, default=8.0)
    parser.add_argument("--profit-lock-adaptive-max-threshold-pct", type=float, default=30.0)
    parser.add_argument("--profit-lock-trend-filter", action="store_true")
    parser.add_argument("--profit-lock-trend-symbol", default="SOXL")
    parser.add_argument("--profit-lock-trend-ma-window", type=int, default=20)
    parser.add_argument("--profit-lock-regime-gated", action="store_true")
    parser.add_argument("--profit-lock-regime-symbol", default="SOXL")
    parser.add_argument("--profit-lock-regime-rv-window", type=int, default=14)
    parser.add_argument("--profit-lock-regime-rv-threshold-pct", type=float, default=85.0)
    parser.add_argument("--walk-forward-grid", action="store_true")
    parser.add_argument("--walk-forward-train-months", type=int, default=12)
    parser.add_argument("--walk-forward-test-months", type=int, default=6)
    parser.add_argument("--walk-forward-folds", type=int, default=4)
    parser.add_argument("--walk-forward-max-configs", type=int, default=0, help="0 means all configs")
    parser.add_argument("--walk-forward-output", default="")
    parser.add_argument("--parquet-dir", default=str(ROOT / "composer_original" / "data" / "parquet"))
    parser.add_argument("--reports-dir", default=str(ROOT / "composer_original" / "reports"))
    parser.add_argument("--fixtures-dir", default=str(ROOT / "composer_original" / "fixtures"))
    parser.add_argument(
        "--include-full-equity-curve",
        action="store_true",
        help="Include full CPU/GPU equity curves in report JSON for downstream analysis.",
    )
    args = parser.parse_args(argv_tokens)
    _apply_strategy_preset(args, argv_tokens)

    end_day = args.end_date or date.today()
    start_day = args.start_date or (end_day - timedelta(days=183))
    if start_day > end_day:
        raise ValueError("start-date must be <= end-date")

    preload_start = start_day - timedelta(days=int(args.lookback_buffer_days))

    effective_data_source = args.data_source
    effective_anchor = bool(args.anchor_window_start_equity)
    strategy_mode = args.strategy_mode

    symbols = list(ORIGINAL_SYMBOLS)
    if args.composer_like_mode:
        effective_data_source = "yfinance"
        effective_anchor = True

    parquet_dir = Path(args.parquet_dir)
    reports_dir = Path(args.reports_dir)
    fixtures_dir = Path(args.fixtures_dir)
    raw_dir = parquet_dir / "raw_daily"
    for d in (parquet_dir, reports_dir, fixtures_dir, raw_dir):
        d.mkdir(parents=True, exist_ok=True)

    if args.walk_forward_grid:
        return _run_walk_forward_grid(args, end_day=end_day, reports_dir=reports_dir)
    effective_profit_lock_exec_model = _effective_profit_lock_exec_model(str(args.profit_lock_exec_model))

    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if effective_data_source == "yfinance":
            frame = _fetch_symbol_daily_yfinance(symbol=symbol, start_day=preload_start, end_day=end_day)
        elif effective_data_source == "alpaca":
            frame = _fetch_symbol_daily_alpaca(
                symbol=symbol,
                start_day=preload_start,
                end_day=end_day,
                api_key=str(args.alpaca_api_key),
                secret_key=str(args.alpaca_secret_key),
                data_url=str(args.alpaca_data_url),
                data_feed=str(args.alpaca_data_feed),
            )
        elif effective_data_source == "polygon":
            frame = _fetch_symbol_daily_polygon(
                symbol=symbol,
                start_day=preload_start,
                end_day=end_day,
                api_key=str(args.polygon_api_key),
                base_url=str(args.polygon_base_url),
            )
        else:
            frame = _fetch_symbol_daily(symbol=symbol, start_day=preload_start, end_day=end_day)
        frame[["date", "close"]].to_parquet(raw_dir / f"{symbol}_daily.parquet", index=False)
        frames[symbol] = frame

    wide = _build_wide_table(frames, symbols=symbols)
    wide_high = _build_wide_high_table(frames, symbols=symbols)
    wide = wide[(wide["date"] >= preload_start) & (wide["date"] <= end_day)].reset_index(drop=True)
    wide_high = wide_high[(wide_high["date"] >= preload_start) & (wide_high["date"] <= end_day)].reset_index(drop=True)
    if wide.empty:
        raise RuntimeError("No rows in merged wide table after date filtering")

    wide_last_6m = wide[(wide["date"] >= start_day) & (wide["date"] <= end_day)].reset_index(drop=True)
    if wide_last_6m.empty:
        raise RuntimeError("No rows in last-6-month window")

    lookback_parquet = parquet_dir / f"soxl_strategy_lookback_{preload_start.isoformat()}_{end_day.isoformat()}.parquet"
    last6m_parquet = parquet_dir / f"soxl_strategy_last6m_{start_day.isoformat()}_{end_day.isoformat()}.parquet"
    lookback_csv = fixtures_dir / f"soxl_strategy_lookback_{preload_start.isoformat()}_{end_day.isoformat()}.csv"
    last6m_csv = fixtures_dir / f"soxl_strategy_last6m_{start_day.isoformat()}_{end_day.isoformat()}.csv"

    wide.to_parquet(lookback_parquet, index=False)
    wide_last_6m.to_parquet(last6m_parquet, index=False)
    wide.to_csv(lookback_csv, index=False)
    wide_last_6m.to_csv(last6m_csv, index=False)

    history = _wide_to_history(wide, symbols=symbols)
    high_history = _wide_to_history(wide_high, symbols=symbols)
    profit_lock_threshold_series = _build_profit_lock_threshold_series(
        history,
        base_threshold_pct=args.profit_lock_threshold_pct,
        adaptive_enabled=bool(args.enable_profit_lock and args.profit_lock_adaptive_threshold),
        adaptive_symbol=args.profit_lock_adaptive_symbol,
        adaptive_rv_window=args.profit_lock_adaptive_rv_window,
        adaptive_rv_baseline_pct=args.profit_lock_adaptive_rv_baseline_pct,
        adaptive_min_threshold_pct=args.profit_lock_adaptive_min_threshold_pct,
        adaptive_max_threshold_pct=args.profit_lock_adaptive_max_threshold_pct,
    )
    trend_gate_flags = _build_trend_filter_flags(
        history,
        trend_filter_enabled=bool(args.enable_profit_lock and args.profit_lock_trend_filter),
        trend_symbol=args.profit_lock_trend_symbol,
        trend_ma_window=args.profit_lock_trend_ma_window,
    )
    profit_lock_gate_flags = _build_profit_lock_gate_flags(
        history,
        regime_gated=bool(args.enable_profit_lock and args.profit_lock_regime_gated),
        regime_symbol=args.profit_lock_regime_symbol,
        regime_rv_window=args.profit_lock_regime_rv_window,
        regime_rv_threshold_pct=args.profit_lock_regime_rv_threshold_pct,
    )
    if len(trend_gate_flags) == len(profit_lock_gate_flags):
        profit_lock_gate_flags = [bool(a and b) for a, b in zip(profit_lock_gate_flags, trend_gate_flags, strict=True)]
    date_series = list(wide["date"])
    if not date_series:
        raise RuntimeError("No trading dates in merged dataset")

    effective_warmup_days = int(args.warmup_days)
    if effective_anchor:
        first_window_idx = next((i for i, d in enumerate(date_series) if d >= start_day), None)
        if first_window_idx is None:
            raise RuntimeError("Unable to locate first in-window trading day")
        # Keep portfolio in cash through the first in-window day so start equity is initial principal.
        effective_warmup_days = max(effective_warmup_days, first_window_idx + 1)

    cfg = BacktestConfig(
        initial_equity=args.initial_equity,
        warmup_days=effective_warmup_days,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
    )
    cpu_full = run_backtest(price_history=history, config=cfg)

    if args.enable_profit_lock:
        cpu_replay = _cpu_replay_from_allocations(
            price_history=history,
            high_history=high_history,
            cpu_allocations=cpu_full.allocations,
            initial_equity=args.initial_equity,
            slippage_bps=args.slippage_bps,
            sell_fee_bps=args.sell_fee_bps,
            enable_profit_lock=True,
            profit_lock_mode=args.profit_lock_mode,
            profit_lock_threshold_series_pct=profit_lock_threshold_series,
            profit_lock_threshold_pct=args.profit_lock_threshold_pct,
            profit_lock_partial_sell_pct=args.profit_lock_partial_sell_pct,
            profit_lock_trail_pct=args.profit_lock_trail_pct,
            profit_lock_exec_model=effective_profit_lock_exec_model,
            profit_lock_gate_flags=profit_lock_gate_flags,
        )
        cpu_curve_full = cpu_replay.equity_curve
        cpu_trade_count_by_day = cpu_replay.trade_count_by_day
        cpu_trade_count_total = cpu_replay.trade_count_total
    else:
        cpu_curve_full = cpu_full.equity_curve
        cpu_trade_count_by_day = {}
        cpu_trade_count_total = len(cpu_full.trades)

    gpu_full = _gpu_replay_from_cpu_allocations(
        price_history=history,
        high_history=high_history,
        cpu_allocations=cpu_full.allocations,
        initial_equity=args.initial_equity,
        slippage_bps=args.slippage_bps,
        sell_fee_bps=args.sell_fee_bps,
        enable_profit_lock=bool(args.enable_profit_lock),
        profit_lock_mode=args.profit_lock_mode,
        profit_lock_threshold_series_pct=profit_lock_threshold_series,
        profit_lock_threshold_pct=args.profit_lock_threshold_pct,
        profit_lock_partial_sell_pct=args.profit_lock_partial_sell_pct,
        profit_lock_trail_pct=args.profit_lock_trail_pct,
        profit_lock_exec_model=effective_profit_lock_exec_model,
        profit_lock_gate_flags=profit_lock_gate_flags,
    )

    cpu_curve_window = _slice_curve(cpu_curve_full, start_day=start_day, end_day=end_day)
    gpu_curve_window = _slice_curve(gpu_full.equity_curve, start_day=start_day, end_day=end_day)
    if not cpu_curve_window or not gpu_curve_window:
        raise RuntimeError("Backtest window has no equity-curve points")

    if args.enable_profit_lock:
        cpu_window_trades = sum(c for d, c in cpu_trade_count_by_day.items() if start_day <= d <= end_day)
    else:
        cpu_window_trades = sum(1 for t in cpu_full.trades if start_day <= t.trading_day <= end_day)
    cpu_window_alloc_days = sum(1 for d, _ in cpu_full.allocations if start_day <= d <= end_day)
    gpu_window_trades = sum(c for d, c in gpu_full.trade_count_by_day.items() if start_day <= d <= end_day)

    cpu_report = {
        "strategy_source": str(ROOT / "composer_original" / "files" / "composer_original_file.txt"),
        "mode": "cpu",
        "window": {"start_date": start_day.isoformat(), "end_date": end_day.isoformat()},
        "config": {
            "initial_equity": args.initial_equity,
            "strategy_preset": args.strategy_preset,
            "warmup_days_requested": args.warmup_days,
            "warmup_days_effective": effective_warmup_days,
            "anchor_window_start_equity": effective_anchor,
            "data_source": effective_data_source,
            "composer_like_mode": bool(args.composer_like_mode),
            "strategy_mode": strategy_mode,
            "symbols": symbols,
            "slippage_bps": args.slippage_bps,
            "sell_fee_bps": args.sell_fee_bps,
            "profit_lock_enabled": bool(args.enable_profit_lock),
            "profit_lock_mode": args.profit_lock_mode,
            "profit_lock_threshold_pct": float(args.profit_lock_threshold_pct),
            "profit_lock_trail_pct": float(args.profit_lock_trail_pct),
            "profit_lock_partial_sell_pct": float(args.profit_lock_partial_sell_pct),
            "profit_lock_exec_model": str(args.profit_lock_exec_model),
            "profit_lock_exec_model_effective": effective_profit_lock_exec_model,
            "profit_lock_adaptive_threshold": bool(args.profit_lock_adaptive_threshold),
            "profit_lock_adaptive_symbol": args.profit_lock_adaptive_symbol,
            "profit_lock_adaptive_rv_window": int(args.profit_lock_adaptive_rv_window),
            "profit_lock_adaptive_rv_baseline_pct": float(args.profit_lock_adaptive_rv_baseline_pct),
            "profit_lock_adaptive_min_threshold_pct": float(args.profit_lock_adaptive_min_threshold_pct),
            "profit_lock_adaptive_max_threshold_pct": float(args.profit_lock_adaptive_max_threshold_pct),
            "profit_lock_trend_filter": bool(args.profit_lock_trend_filter),
            "profit_lock_trend_symbol": args.profit_lock_trend_symbol,
            "profit_lock_trend_ma_window": int(args.profit_lock_trend_ma_window),
            "profit_lock_regime_gated": bool(args.profit_lock_regime_gated),
            "profit_lock_regime_symbol": args.profit_lock_regime_symbol,
            "profit_lock_regime_rv_window": int(args.profit_lock_regime_rv_window),
            "profit_lock_regime_rv_threshold_pct": float(args.profit_lock_regime_rv_threshold_pct),
            "lookback_buffer_days": args.lookback_buffer_days,
        },
        "data_files": {
            "lookback_parquet": str(lookback_parquet),
            "last6m_parquet": str(last6m_parquet),
            "lookback_csv": str(lookback_csv),
            "last6m_csv": str(last6m_csv),
            "raw_daily_dir": str(raw_dir),
        },
        "window_metrics": _curve_summary(cpu_curve_window),
        "window_trade_count": cpu_window_trades,
        "window_allocation_days": cpu_window_alloc_days,
        "full_trade_count": cpu_trade_count_total,
        "full_allocation_days": len(cpu_full.allocations),
    }
    if args.include_full_equity_curve:
        cpu_report["full_equity_curve"] = [(d.isoformat(), float(v)) for d, v in cpu_curve_full]
        cpu_report["window_equity_curve"] = [(d.isoformat(), float(v)) for d, v in cpu_curve_window]

    gpu_report = {
        "strategy_source": str(ROOT / "composer_original" / "files" / "composer_original_file.txt"),
        "mode": "gpu",
        "window": {"start_date": start_day.isoformat(), "end_date": end_day.isoformat()},
        "config": {
            "initial_equity": args.initial_equity,
            "strategy_preset": args.strategy_preset,
            "warmup_days_requested": args.warmup_days,
            "warmup_days_effective": effective_warmup_days,
            "anchor_window_start_equity": effective_anchor,
            "data_source": effective_data_source,
            "composer_like_mode": bool(args.composer_like_mode),
            "strategy_mode": strategy_mode,
            "symbols": symbols,
            "slippage_bps": args.slippage_bps,
            "sell_fee_bps": args.sell_fee_bps,
            "profit_lock_enabled": bool(args.enable_profit_lock),
            "profit_lock_mode": args.profit_lock_mode,
            "profit_lock_threshold_pct": float(args.profit_lock_threshold_pct),
            "profit_lock_trail_pct": float(args.profit_lock_trail_pct),
            "profit_lock_partial_sell_pct": float(args.profit_lock_partial_sell_pct),
            "profit_lock_exec_model": str(args.profit_lock_exec_model),
            "profit_lock_exec_model_effective": effective_profit_lock_exec_model,
            "profit_lock_adaptive_threshold": bool(args.profit_lock_adaptive_threshold),
            "profit_lock_adaptive_symbol": args.profit_lock_adaptive_symbol,
            "profit_lock_adaptive_rv_window": int(args.profit_lock_adaptive_rv_window),
            "profit_lock_adaptive_rv_baseline_pct": float(args.profit_lock_adaptive_rv_baseline_pct),
            "profit_lock_adaptive_min_threshold_pct": float(args.profit_lock_adaptive_min_threshold_pct),
            "profit_lock_adaptive_max_threshold_pct": float(args.profit_lock_adaptive_max_threshold_pct),
            "profit_lock_trend_filter": bool(args.profit_lock_trend_filter),
            "profit_lock_trend_symbol": args.profit_lock_trend_symbol,
            "profit_lock_trend_ma_window": int(args.profit_lock_trend_ma_window),
            "profit_lock_regime_gated": bool(args.profit_lock_regime_gated),
            "profit_lock_regime_symbol": args.profit_lock_regime_symbol,
            "profit_lock_regime_rv_window": int(args.profit_lock_regime_rv_window),
            "profit_lock_regime_rv_threshold_pct": float(args.profit_lock_regime_rv_threshold_pct),
            "lookback_buffer_days": args.lookback_buffer_days,
        },
        "data_files": {
            "lookback_parquet": str(lookback_parquet),
            "last6m_parquet": str(last6m_parquet),
            "lookback_csv": str(lookback_csv),
            "last6m_csv": str(last6m_csv),
            "raw_daily_dir": str(raw_dir),
        },
        "window_metrics": _curve_summary(gpu_curve_window),
        "window_trade_count": gpu_window_trades,
        "full_trade_count": gpu_full.trade_count_total,
    }
    if args.include_full_equity_curve:
        gpu_report["full_equity_curve"] = [(d.isoformat(), float(v)) for d, v in gpu_full.equity_curve]
        gpu_report["window_equity_curve"] = [(d.isoformat(), float(v)) for d, v in gpu_curve_window]

    cpu_final = float(cpu_report["window_metrics"]["final_equity"])
    gpu_final = float(gpu_report["window_metrics"]["final_equity"])
    abs_diff = abs(cpu_final - gpu_final)
    bps_diff = 10_000.0 * abs_diff / cpu_final if cpu_final > 0 else float("inf")
    summary = {
        "strategy_source": str(ROOT / "composer_original" / "files" / "composer_original_file.txt"),
        "strategy_mode": strategy_mode,
        "strategy_preset": args.strategy_preset,
        "window": {"start_date": start_day.isoformat(), "end_date": end_day.isoformat()},
        "cpu_report": str(reports_dir / "backtest_cpu_last6m.json"),
        "gpu_report": str(reports_dir / "backtest_gpu_last6m.json"),
        "parity_window": {
            "final_equity_abs_diff": abs_diff,
            "final_equity_diff_bps_vs_cpu": bps_diff,
            "cpu_final_equity": cpu_final,
            "gpu_final_equity": gpu_final,
        },
        "profit_lock": {
            "enabled": bool(args.enable_profit_lock),
            "mode": args.profit_lock_mode,
            "threshold_pct": float(args.profit_lock_threshold_pct),
            "trail_pct": float(args.profit_lock_trail_pct),
            "partial_sell_pct": float(args.profit_lock_partial_sell_pct),
            "exec_model": str(args.profit_lock_exec_model),
            "exec_model_effective": effective_profit_lock_exec_model,
            "adaptive_threshold": bool(args.profit_lock_adaptive_threshold),
            "adaptive_symbol": args.profit_lock_adaptive_symbol,
            "adaptive_rv_window": int(args.profit_lock_adaptive_rv_window),
            "adaptive_rv_baseline_pct": float(args.profit_lock_adaptive_rv_baseline_pct),
            "adaptive_min_threshold_pct": float(args.profit_lock_adaptive_min_threshold_pct),
            "adaptive_max_threshold_pct": float(args.profit_lock_adaptive_max_threshold_pct),
            "trend_filter": bool(args.profit_lock_trend_filter),
            "trend_symbol": args.profit_lock_trend_symbol,
            "trend_ma_window": int(args.profit_lock_trend_ma_window),
            "regime_gated": bool(args.profit_lock_regime_gated),
            "regime_symbol": args.profit_lock_regime_symbol,
            "regime_rv_window": int(args.profit_lock_regime_rv_window),
            "regime_rv_threshold_pct": float(args.profit_lock_regime_rv_threshold_pct),
            "model": "daily_high_threshold_emulation",
        },
        "data_files": {
            "lookback_parquet": str(lookback_parquet),
            "last6m_parquet": str(last6m_parquet),
            "raw_daily_dir": str(raw_dir),
        },
    }

    cpu_out = reports_dir / "backtest_cpu_last6m.json"
    gpu_out = reports_dir / "backtest_gpu_last6m.json"
    summary_out = reports_dir / "backtest_cpu_gpu_last6m_summary.json"
    cpu_out.write_text(json.dumps(cpu_report, indent=2, sort_keys=True), encoding="utf-8")
    gpu_out.write_text(json.dumps(gpu_report, indent=2, sort_keys=True), encoding="utf-8")
    summary_out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
