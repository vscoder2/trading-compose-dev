from __future__ import annotations

from datetime import date, timedelta

from .backtester import run_backtest_v2
from .data import MarketData
from .model_types import BacktestConfigV2, BacktestResultV2, OverlayConfig, WindowSpec
from .profiles import get_profile


def slice_market_data(market_data: MarketData, start: date, end: date) -> MarketData:
    """Slice aligned market data to a strict date window."""
    keep_days = [d for d in market_data.days if start <= d <= end]
    if not keep_days:
        raise RuntimeError(f"No market data between {start} and {end}")
    keep_set = set(keep_days)
    bars = {
        sym: [b for b in sym_bars if b.day in keep_set]
        for sym, sym_bars in market_data.bars_by_symbol.items()
    }
    return MarketData(days=keep_days, bars_by_symbol=bars)


def trim_result_to_window(result: BacktestResultV2, window: WindowSpec) -> BacktestResultV2:
    """Trim warmup-extended run output to the report window."""
    keep_curve = [(d, eq) for d, eq in result.equity_curve if window.start <= d <= window.end]
    keep_daily = [r for r in result.daily if window.start <= r.day <= window.end]
    keep_trades = [t for t in result.trades if window.start <= t.day <= window.end]

    if not keep_curve:
        raise RuntimeError(f"No equity rows in output window {window.label} {window.start}..{window.end}")

    final_eq = keep_curve[-1][1]
    init = result.initial_equity
    total_ret = 100.0 * (final_eq / init - 1.0) if init > 0 else 0.0

    return BacktestResultV2(
        profile_name=result.profile_name,
        window_label=window.label,
        mode=result.mode,
        initial_equity=result.initial_equity,
        final_equity=final_eq,
        total_return_pct=total_ret,
        max_drawdown_pct=result.max_drawdown_pct,
        cagr_pct=result.cagr_pct,
        trade_count=len(keep_trades),
        equity_curve=keep_curve,
        trades=keep_trades,
        daily=keep_daily,
        meta=dict(result.meta),
    )


def run_window_backtest(
    *,
    market_data: MarketData,
    profile_name: str,
    config: BacktestConfigV2,
    overlay: OverlayConfig,
    window: WindowSpec,
) -> BacktestResultV2:
    """Run one window with warmup extension and trim to final window.

    We extend by warmup+buffer days before each window so indicators and branch
    decisions have sufficient history.
    """
    profile = get_profile(profile_name)
    run_start = window.start - timedelta(days=int(config.warmup_days) + 30)
    run_data = slice_market_data(market_data, run_start, window.end)
    run = run_backtest_v2(
        market_data=run_data,
        profile=profile,
        config=config,
        overlay=overlay,
        window_label=window.label,
    )
    return trim_result_to_window(run, window)
