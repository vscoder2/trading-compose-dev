from __future__ import annotations

from dataclasses import dataclass

from soxl_growth.execution.orders import OrderIntent


@dataclass(frozen=True)
class PhasedExecutionConfig:
    """Configuration for staged entries during stressed intraday conditions."""

    enable: bool = False
    rv_trigger: float = 120.0
    spread_trigger_bps: float = 12.0
    extreme_rv_trigger: float = 180.0
    extreme_spread_trigger_bps: float = 25.0
    stage_fraction: float = 0.5
    extreme_stage_fraction: float = 0.25
    min_notional: float = 50.0


@dataclass(frozen=True)
class PhasedExecutionResult:
    intents: list[OrderIntent]
    staging_fraction: float
    staged_buy_count: int
    skipped_buy_count: int


def _clamp_fraction(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def compute_staging_fraction(
    *,
    rv_annualized_pct: float,
    spread_bps: float,
    config: PhasedExecutionConfig,
) -> float:
    """Compute fraction of buy quantity to submit under current stress metrics."""
    if not config.enable:
        return 1.0

    if rv_annualized_pct >= config.extreme_rv_trigger or spread_bps >= config.extreme_spread_trigger_bps:
        return _clamp_fraction(config.extreme_stage_fraction)
    if rv_annualized_pct >= config.rv_trigger or spread_bps >= config.spread_trigger_bps:
        return _clamp_fraction(config.stage_fraction)
    return 1.0


def apply_phased_execution(
    *,
    intents: list[OrderIntent],
    last_prices: dict[str, float],
    staging_fraction: float,
    min_notional: float,
) -> PhasedExecutionResult:
    """Scale buy intents for staged-entry execution while preserving full exits."""
    fraction = _clamp_fraction(staging_fraction)
    if fraction >= 0.999:
        return PhasedExecutionResult(
            intents=list(intents),
            staging_fraction=1.0,
            staged_buy_count=0,
            skipped_buy_count=0,
        )

    staged: list[OrderIntent] = []
    staged_buy_count = 0
    skipped_buy_count = 0

    for intent in intents:
        if intent.side != "buy":
            staged.append(intent)
            continue

        staged_qty = intent.qty * fraction
        price = float(last_prices.get(intent.symbol, 0.0))
        staged_notional = staged_qty * price
        if staged_qty <= 0 or staged_notional < max(0.0, min_notional):
            skipped_buy_count += 1
            continue

        if staged_qty < intent.qty:
            staged_buy_count += 1

        staged.append(
            OrderIntent(
                symbol=intent.symbol,
                side=intent.side,
                qty=staged_qty,
                target_weight=intent.target_weight,
            )
        )

    return PhasedExecutionResult(
        intents=staged,
        staging_fraction=fraction,
        staged_buy_count=staged_buy_count,
        skipped_buy_count=skipped_buy_count,
    )
