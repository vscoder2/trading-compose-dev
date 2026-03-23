from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import random

from .metrics import calmar_ratio, daily_returns, sharpe_ratio, sortino_ratio
from .model_types import BacktestResultV2


@dataclass(frozen=True)
class ValidationSummary:
    sharpe: float
    sortino: float
    calmar: float
    worst_day_pct: float
    best_day_pct: float
    bootstrap_cagr_p10: float
    bootstrap_cagr_p50: float
    bootstrap_cagr_p90: float


def walk_forward_splits(
    days: list[date],
    *,
    train_days: int,
    test_days: int,
    step_days: int,
) -> list[tuple[int, int, int, int]]:
    """Create index-based walk-forward split tuples.

    Returns list of (train_start, train_end, test_start, test_end) indexes.
    """
    out: list[tuple[int, int, int, int]] = []
    n = len(days)
    cur = 0
    while True:
        train_start = cur
        train_end = train_start + train_days
        test_start = train_end
        test_end = test_start + test_days
        if test_end > n:
            break
        out.append((train_start, train_end, test_start, test_end))
        cur += step_days
        if cur >= n:
            break
    return out


def _bootstrap_terminal_equity(returns: list[float], initial: float, draws: int, block_len: int, seed: int) -> list[float]:
    """Simple block bootstrap for path-uncertainty stress."""
    if not returns:
        return [initial]
    rng = random.Random(seed)
    n = len(returns)
    out: list[float] = []

    for _ in range(draws):
        idx: list[int] = []
        while len(idx) < n:
            start = rng.randint(0, max(0, n - block_len))
            idx.extend(range(start, min(start + block_len, n)))
        idx = idx[:n]

        eq = float(initial)
        for i in idx:
            eq *= 1.0 + returns[i]
        out.append(eq)
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def summarize_validation(
    result: BacktestResultV2,
    *,
    bootstrap_draws: int = 1000,
    bootstrap_block_len: int = 10,
    bootstrap_seed: int = 42,
) -> ValidationSummary:
    """Compute compact validation summary for review checks."""
    rets = daily_returns(result.equity_curve)
    worst = min(rets) * 100.0 if rets else 0.0
    best = max(rets) * 100.0 if rets else 0.0

    boot_eq = _bootstrap_terminal_equity(
        returns=rets,
        initial=result.initial_equity,
        draws=int(bootstrap_draws),
        block_len=max(1, int(bootstrap_block_len)),
        seed=int(bootstrap_seed),
    )
    boot_ret = [100.0 * (x / result.initial_equity - 1.0) for x in boot_eq]

    return ValidationSummary(
        sharpe=sharpe_ratio(result.equity_curve),
        sortino=sortino_ratio(result.equity_curve),
        calmar=calmar_ratio(result.equity_curve),
        worst_day_pct=worst,
        best_day_pct=best,
        bootstrap_cagr_p10=_percentile(boot_ret, 10.0),
        bootstrap_cagr_p50=_percentile(boot_ret, 50.0),
        bootstrap_cagr_p90=_percentile(boot_ret, 90.0),
    )
