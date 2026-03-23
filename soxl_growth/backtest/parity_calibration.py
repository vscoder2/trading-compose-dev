from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Callable
from typing import Sequence

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.backtest.parity import AllocationMismatch, compare_allocations
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import (
    build_smoothed_rsi_tree,
    evaluate_strategy,
)
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import BacktestConfig
from soxl_growth.indicators.rsi import rsi_base, rsi_smoothed


RsiFn = Callable[[Sequence[float], int], float | None]


@dataclass(frozen=True)
class RsiParityCandidateResult:
    mode: str
    smoothing_span: int
    mismatch_count: int
    mismatch_days: int
    avg_abs_weight_error: float
    max_abs_weight_error: float
    near_threshold_days: int
    near_threshold_mismatch_days: int


@dataclass(frozen=True)
class NearThresholdPoint:
    trading_day: str
    rsi_soxl_32: float | None
    rsi_soxl_30: float | None
    rsi_tqqq_30: float | None
    min_distance: float


def parse_rsi_span_csv(text: str) -> list[int]:
    out: list[int] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise ValueError(f"RSI smoothing span must be positive: {value}")
        out.append(value)
    if not out:
        raise ValueError("At least one RSI smoothing span must be provided.")
    return sorted(set(out))


def _build_rsi_fn(mode: str, smoothing_span: int) -> RsiFn:
    if mode == "base":
        return rsi_base
    if mode == "smoothed":
        return lambda close, window: rsi_smoothed(close, window, smoothing_span=smoothing_span)
    raise ValueError(f"Unsupported RSI mode: {mode}")


def _build_evaluate_fn(mode: str, smoothing_span: int):
    if mode == "base":
        return evaluate_strategy
    if mode == "smoothed":
        tree = build_smoothed_rsi_tree(smoothing_span=smoothing_span)
        return lambda ctx: evaluate_strategy(ctx, tree=tree)
    raise ValueError(f"Unsupported RSI mode: {mode}")


def _calc_weight_errors(mismatches: list[AllocationMismatch]) -> tuple[float, float]:
    if not mismatches:
        return 0.0, 0.0
    diffs = [abs(m.expected_weight - m.actual_weight) for m in mismatches]
    return mean(diffs), max(diffs)


def _near_threshold_days(
    *,
    price_history: dict[str, list[tuple[date, float]]],
    warmup_days: int,
    rsi_fn: RsiFn,
    band: float,
) -> tuple[set[str], list[NearThresholdPoint]]:
    symbols = sorted(price_history.keys())
    dates = [d for d, _ in price_history[symbols[0]]]
    series = {symbol: [price for _, price in rows] for symbol, rows in price_history.items()}

    near_days: set[str] = set()
    points: list[NearThresholdPoint] = []

    for i, day in enumerate(dates):
        if i < warmup_days:
            continue

        soxl = series.get("SOXL", [])[: i + 1]
        tqqq = series.get("TQQQ", [])[: i + 1]
        rsi_soxl_32 = rsi_fn(soxl, 32) if soxl else None
        rsi_soxl_30 = rsi_fn(soxl, 30) if soxl else None
        rsi_tqqq_30 = rsi_fn(tqqq, 30) if tqqq else None

        candidates: list[float] = []
        if rsi_soxl_32 is not None:
            candidates.append(abs(rsi_soxl_32 - 62.1995))
            candidates.append(abs(rsi_soxl_32 - 50.0))
        if rsi_soxl_30 is not None:
            candidates.append(abs(rsi_soxl_30 - 57.49))
        if rsi_tqqq_30 is not None:
            candidates.append(abs(rsi_tqqq_30 - 50.0))

        if not candidates:
            continue
        min_distance = min(candidates)
        if min_distance <= band:
            key = day.isoformat()
            near_days.add(key)
            points.append(
                NearThresholdPoint(
                    trading_day=key,
                    rsi_soxl_32=rsi_soxl_32,
                    rsi_soxl_30=rsi_soxl_30,
                    rsi_tqqq_30=rsi_tqqq_30,
                    min_distance=min_distance,
                )
            )
    return near_days, points


def run_rsi_parity_calibration(
    *,
    oracle_daily_allocations: dict[str, dict[str, float]],
    price_history: dict[str, list[tuple[date, float]]],
    backtest_config: BacktestConfig,
    smoothing_spans: list[int],
    tolerance: float,
    threshold_band: float,
) -> tuple[list[RsiParityCandidateResult], list[NearThresholdPoint]]:
    """Sweep RSI smoothing candidates and return mismatch diagnostics."""
    results: list[RsiParityCandidateResult] = []
    best_points: list[NearThresholdPoint] = []

    modes: list[tuple[str, int]] = [("base", 1)] + [("smoothed", span) for span in smoothing_spans]
    for mode, span in modes:
        evaluate_fn = _build_evaluate_fn(mode=mode, smoothing_span=span)
        bt = run_backtest(
            price_history=price_history,
            config=backtest_config,
            evaluate_fn=evaluate_fn,
        )
        local_daily_alloc = {d.isoformat(): w for d, w in bt.allocations}
        mismatches = compare_allocations(
            oracle_daily_allocations=oracle_daily_allocations,
            local_daily_allocations=local_daily_alloc,
            tolerance=tolerance,
        )
        mismatch_days = {m.trading_day for m in mismatches}
        avg_abs_error, max_abs_error = _calc_weight_errors(mismatches)

        rsi_fn = _build_rsi_fn(mode=mode, smoothing_span=span)
        near_days, near_points = _near_threshold_days(
            price_history=price_history,
            warmup_days=backtest_config.warmup_days,
            rsi_fn=rsi_fn,
            band=threshold_band,
        )
        near_mismatch_days = mismatch_days & near_days

        row = RsiParityCandidateResult(
            mode=mode,
            smoothing_span=span,
            mismatch_count=len(mismatches),
            mismatch_days=len(mismatch_days),
            avg_abs_weight_error=avg_abs_error,
            max_abs_weight_error=max_abs_error,
            near_threshold_days=len(near_days),
            near_threshold_mismatch_days=len(near_mismatch_days),
        )
        results.append(row)

    if results:
        best = min(
            results,
            key=lambda x: (
                x.mismatch_count,
                x.near_threshold_mismatch_days,
                x.avg_abs_weight_error,
                x.max_abs_weight_error,
            ),
        )
        best_rsi_fn = _build_rsi_fn(mode=best.mode, smoothing_span=best.smoothing_span)
        _, best_points = _near_threshold_days(
            price_history=price_history,
            warmup_days=backtest_config.warmup_days,
            rsi_fn=best_rsi_fn,
            band=threshold_band,
        )

    results.sort(
        key=lambda x: (
            x.mismatch_count,
            x.near_threshold_mismatch_days,
            x.avg_abs_weight_error,
            x.max_abs_weight_error,
        )
    )
    return results, best_points
