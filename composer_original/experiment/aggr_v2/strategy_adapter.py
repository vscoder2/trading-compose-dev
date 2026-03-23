from __future__ import annotations

from dataclasses import dataclass

from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import InsufficientDataError, evaluate_strategy
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.portfolio.target_weights import normalize_weights

from .data import MarketData


@dataclass(frozen=True)
class StrategyDecision:
    day_index: int
    weights: dict[str, float]
    skipped: bool
    reason: str = ""


def evaluate_target_weights_for_day(market_data: MarketData, day_index: int) -> StrategyDecision:
    """Compute normalized target weights for a given day.

    The adapter intentionally calls the existing strategy evaluator directly, but
    keeps all glue logic inside this isolated package.
    """
    closes: dict[str, list[float]] = {}
    for sym, bars in market_data.bars_by_symbol.items():
        # Slice up to and including current day. This keeps causality strict.
        closes[sym] = [float(b.close) for b in bars[: day_index + 1]]

    try:
        weights = normalize_weights(evaluate_strategy(DictContext(closes=closes)))
        return StrategyDecision(day_index=day_index, weights=weights, skipped=False)
    except InsufficientDataError as exc:
        # Warmup-style gaps are surfaced as skipped decisions instead of errors.
        return StrategyDecision(day_index=day_index, weights={}, skipped=True, reason=str(exc))
