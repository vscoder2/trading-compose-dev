from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .data import MarketData
from .model_types import WindowSpec


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    window: WindowSpec
    note: str


def _worst_window_by_symbol_return(
    market_data: MarketData,
    *,
    symbol: str,
    span_days: int,
) -> WindowSpec:
    days = market_data.days
    bars = market_data.bars_by_symbol[symbol]
    if len(days) < span_days + 2:
        return WindowSpec(label=f"worst_{span_days}d", start=days[0], end=days[-1])

    worst_ret = float("inf")
    worst_start_idx = 0
    for i in range(0, len(days) - span_days):
        j = i + span_days
        p0 = float(bars[i].close)
        p1 = float(bars[j].close)
        if p0 <= 0:
            continue
        r = p1 / p0 - 1.0
        if r < worst_ret:
            worst_ret = r
            worst_start_idx = i

    s = days[worst_start_idx]
    e = days[min(worst_start_idx + span_days, len(days) - 1)]
    return WindowSpec(label=f"worst_{span_days}d", start=s, end=e)


def build_default_scenarios(market_data: MarketData) -> list[ScenarioSpec]:
    """Construct deterministic scenario windows from available dataset.

    Because datasets vary by environment, scenarios are derived from the loaded
    history rather than hard-coding calendar dates that may be missing.
    """
    days = market_data.days
    if not days:
        raise RuntimeError("No market days available for scenarios")

    end = days[-1]
    out: list[ScenarioSpec] = []

    # Recent horizon scenarios.
    out.append(
        ScenarioSpec(
            name="recent_3m",
            window=WindowSpec(label="recent_3m", start=max(days[0], end - timedelta(days=93)), end=end),
            note="Recent quarter behavior",
        )
    )
    out.append(
        ScenarioSpec(
            name="recent_6m",
            window=WindowSpec(label="recent_6m", start=max(days[0], end - timedelta(days=186)), end=end),
            note="Recent half-year behavior",
        )
    )

    # Earliest available quarter.
    out.append(
        ScenarioSpec(
            name="early_3m",
            window=WindowSpec(label="early_3m", start=days[0], end=days[min(len(days) - 1, 93)]),
            note="Early-sample behavior",
        )
    )

    # Data-driven worst windows on main trend proxies.
    out.append(
        ScenarioSpec(
            name="worst_90d_soxl",
            window=_worst_window_by_symbol_return(market_data, symbol="SOXL", span_days=90),
            note="Worst 90-day SOXL return window",
        )
    )
    out.append(
        ScenarioSpec(
            name="worst_60d_tqqq",
            window=_worst_window_by_symbol_return(market_data, symbol="TQQQ", span_days=60),
            note="Worst 60-day TQQQ return window",
        )
    )

    return out
