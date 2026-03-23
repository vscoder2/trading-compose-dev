from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class StrategyProfile:
    """Immutable strategy profile used by this isolated runner.

    These fields intentionally mirror the locked runtime profile knobs so
    comparisons stay interpretable, while remaining completely separate from
    runtime code paths.
    """

    name: str
    enable_profit_lock: bool
    profit_lock_mode: str
    profit_lock_threshold_pct: float
    profit_lock_trail_pct: float
    profit_lock_adaptive_threshold: bool
    profit_lock_adaptive_symbol: str
    profit_lock_adaptive_rv_window: int
    profit_lock_adaptive_rv_baseline_pct: float
    profit_lock_adaptive_min_threshold_pct: float
    profit_lock_adaptive_max_threshold_pct: float
    intraday_profit_lock_check_minutes: int = 0


@dataclass(frozen=True)
class OverlayConfig:
    """Optional risk/robustness overlays for research runs.

    The defaults are conservative no-op values so baseline behavior remains
    close to the locked profile unless explicitly enabled.
    """

    # Volatility targeting knobs.
    enable_vol_target: bool = False
    target_vol_ann: float = 0.35
    vol_lookback_days: int = 20
    max_gross_exposure: float = 1.0

    # Downside limiter knobs.
    enable_loss_limiter: bool = False
    stop_loss_pct: float = 0.12
    max_holding_days: int = 30

    # Persistence / hysteresis knobs to reduce churn.
    enable_persistence: bool = False
    persistence_days: int = 1
    hysteresis_band_pct: float = 0.0

    # Trend guard to reduce inverse exposure in strong risk-on regimes.
    enable_inverse_blocker: bool = False
    trend_symbol: str = "SOXL"
    trend_ma_days: int = 50


@dataclass(frozen=True)
class BacktestConfigV2:
    """Execution and simulation settings for the isolated backtest."""

    initial_equity: float = 10_000.0
    warmup_days: int = 260
    slippage_bps: float = 1.0
    sell_fee_bps: float = 0.0
    min_trade_weight_delta: float = 0.0
    rebalance_threshold: float = 0.0

    # Execution modeling mode.
    # - synthetic: daily-high synthetic trailing model.
    # - paper_live_style_optimistic: intraday-like optimistic trigger model.
    # - realistic_close: conservative daily-close-only behavior.
    profit_lock_exec_model: str = "synthetic"


@dataclass(frozen=True)
class OhlcBar:
    """Single daily bar used by this engine."""

    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class PositionState:
    """Mutable position state used by the simulator."""

    qty: float = 0.0
    entry_price: float = 0.0
    entry_day: date | None = None


@dataclass(frozen=True)
class TradeRecord:
    """Normalized trade event for reporting and verification."""

    day: date
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    fee: float
    reason: str


@dataclass(frozen=True)
class DailySnapshot:
    """End-of-day accounting and risk snapshot."""

    day: date
    start_equity: float
    end_equity: float
    pnl: float
    return_pct: float
    drawdown_pct: float
    holdings: dict[str, float]
    notes: str = ""


@dataclass(frozen=True)
class BacktestResultV2:
    """Container for full backtest output."""

    profile_name: str
    window_label: str
    mode: str
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    cagr_pct: float
    trade_count: int
    equity_curve: list[tuple[date, float]]
    trades: list[TradeRecord]
    daily: list[DailySnapshot]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WindowSpec:
    """Date window abstraction used by batch runners."""

    label: str
    start: date
    end: date
