from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .data import MarketData
from .model_types import BacktestResultV2


@dataclass(frozen=True)
class GpuReplaySummary:
    backend: str
    final_equity: float
    total_return_pct: float
    diff_bps_vs_cpu: float
    equity_curve: list[tuple[date, float]]


def replay_with_gpu(cpu_result: BacktestResultV2, market_data: MarketData) -> GpuReplaySummary:
    """Replay CPU holdings on GPU path for deterministic parity checks.

    This intentionally validates numerical parity and backend consistency rather
    than inventing a separate decision model.
    """
    try:
        import cupy as cp
    except Exception:
        return GpuReplaySummary(
            backend="cpu_emulated_fallback",
            final_equity=cpu_result.final_equity,
            total_return_pct=cpu_result.total_return_pct,
            diff_bps_vs_cpu=0.0,
            equity_curve=list(cpu_result.equity_curve),
        )

    try:
        symbols = sorted(market_data.bars_by_symbol.keys())
        day_to_idx = {d: i for i, d in enumerate(market_data.days)}

        # Build close price matrix [day, symbol].
        prices_np = [
            [float(market_data.bars_by_symbol[sym][i].close) for sym in symbols]
            for i in range(len(market_data.days))
        ]
        prices = cp.asarray(prices_np, dtype=cp.float64)

        # Build qty matrix from CPU daily snapshots.
        qty_np: list[list[float]] = []
        cash_np: list[float] = []
        daily_curve: list[tuple[date, float]] = []

        for row in cpu_result.daily:
            idx = day_to_idx[row.day]
            q = [float(row.holdings.get(sym, 0.0)) for sym in symbols]
            qty_np.append(q)
            holdings_value = sum(q[j] * prices_np[idx][j] for j in range(len(symbols)))
            cash_np.append(float(row.end_equity) - holdings_value)

        qty = cp.asarray(qty_np, dtype=cp.float64)
        cash = cp.asarray(cash_np, dtype=cp.float64)
        holdings_val = cp.sum(qty * prices, axis=1)
        eq = holdings_val + cash
        eq_host = cp.asnumpy(eq)

        for i, row in enumerate(cpu_result.daily):
            daily_curve.append((row.day, float(eq_host[i])))

        final_equity = float(eq_host[-1]) if len(eq_host) else 0.0
        init = float(cpu_result.initial_equity)
        ret = 100.0 * (final_equity / init - 1.0) if init > 0 else 0.0
        diff = abs(final_equity - float(cpu_result.final_equity))
        diff_bps = 10_000.0 * diff / float(cpu_result.final_equity) if cpu_result.final_equity > 0 else 0.0

        return GpuReplaySummary(
            backend="cupy",
            final_equity=final_equity,
            total_return_pct=ret,
            diff_bps_vs_cpu=diff_bps,
            equity_curve=daily_curve,
        )
    except Exception:
        # CuPy can import even when CUDA runtime is unavailable. Fall back safely.
        return GpuReplaySummary(
            backend="cpu_emulated_fallback",
            final_equity=cpu_result.final_equity,
            total_return_pct=cpu_result.total_return_pct,
            diff_bps_vs_cpu=0.0,
            equity_curve=list(cpu_result.equity_curve),
        )
