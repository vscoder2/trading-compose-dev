from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import time
import os
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str
    api_secret: str
    paper: bool = True
    data_feed: str = "sip"
    trading_url: str | None = None

    @classmethod
    def from_env(cls, paper: bool = True, data_feed: str = "sip") -> "AlpacaConfig":
        api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        api_secret = (
            os.getenv("ALPACA_API_SECRET")
            or os.getenv("ALPACA_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
        )
        if not api_key or not api_secret:
            raise ValueError(
                "Missing Alpaca credentials. Set ALPACA_API_KEY (or APCA_API_KEY_ID) and "
                "ALPACA_API_SECRET (or ALPACA_SECRET_KEY / APCA_API_SECRET_KEY)."
            )
        return cls(api_key=api_key, api_secret=api_secret, paper=paper, data_feed=data_feed)


@dataclass(frozen=True)
class OverlayConfig:
    max_flips_per_day: int = 3
    min_hold_minutes: int = 30
    cooldown_minutes: int = 20
    hedge_trigger_dd: float = 6.0
    hedge_trigger_rv: float = 120.0
    reentry_dd_ratio: float = 0.6
    reentry_rv_ratio: float = 0.7
    reentry_confirmations: int = 2
    daily_loss_limit_pct: float = 0.05
    data_failure_minutes_limit: int = 5
    margin_safety_threshold: float = 0.80
    overbought_fade_symbol: str = "SOXS"
    overbought_fade_cash_symbol: str = "CASH"
    overbought_fade_require_confirmation: bool = True


@dataclass(frozen=True)
class StrategyConfig:
    timezone: ZoneInfo = NY
    baseline_eval_time: time = time(15, 55)
    overlay_eval_minutes: int = 5
    rebalance_threshold: float = 0.05
    symbols: tuple[str, ...] = (
        "SOXL",
        "SOXS",
        "TQQQ",
        "SQQQ",
        "SPXL",
        "SPXS",
        "TMF",
        "TMV",
    )


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "paper"  # live | paper | backtest
    state_db_path: str = "runtime_state.db"
    log_level: str = "INFO"
    loop_sleep_seconds: int = 1


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 100_000.0
    warmup_days: int = 260
    slippage_bps: float = 1.0
    sell_fee_bps: float = 0.0
    min_trade_weight_delta: float = 0.0
    phased_execution_enabled: bool = False
    phased_rv_trigger: float = 120.0
    phased_extreme_rv_trigger: float = 180.0
    phased_stage_fraction: float = 0.5
    phased_extreme_stage_fraction: float = 0.25
    phased_min_notional: float = 50.0
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class ComposerConfig:
    api_base_url: str = "https://api.composer.trade"
    api_token: str | None = None
