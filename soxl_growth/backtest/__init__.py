"""Backtest engine, cost model, and parity helpers."""

from .cost_model import CostModel
from .engine import BacktestResult, run_backtest
from .intraday_replay import ReplayConfig, ReplayResult, run_intraday_overlay_replay
from .parity_calibration import RsiParityCandidateResult, run_rsi_parity_calibration
from .parity import AllocationMismatch, ComposerParityClient, compare_allocations

__all__ = [
    "AllocationMismatch",
    "BacktestResult",
    "ComposerParityClient",
    "CostModel",
    "ReplayConfig",
    "ReplayResult",
    "RsiParityCandidateResult",
    "compare_allocations",
    "run_intraday_overlay_replay",
    "run_backtest",
    "run_rsi_parity_calibration",
]
