from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .model_types import OhlcBar, OverlayConfig, PositionState


def realized_vol_ann(close_history: list[float], window: int) -> float:
    """Annualized realized volatility from close-to-close returns."""
    w = max(2, int(window))
    if len(close_history) < w + 1:
        return 0.0
    sample = close_history[-(w + 1) :]
    rets: list[float] = []
    for i in range(1, len(sample)):
        prev = sample[i - 1]
        cur = sample[i]
        if prev > 0:
            rets.append(cur / prev - 1.0)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / len(rets)
    return (var**0.5) * (252.0**0.5)


def apply_vol_target(
    *,
    target_weights: dict[str, float],
    rv_ann: float,
    overlay: OverlayConfig,
) -> tuple[dict[str, float], str]:
    """Scale gross exposure based on realized volatility.

    This uses cash as an implicit sink by scaling all risky weights down when
    estimated volatility exceeds target.
    """
    if not overlay.enable_vol_target:
        return target_weights, "vol_target_off"
    if rv_ann <= 0:
        return target_weights, "vol_target_no_rv"

    # If RV is above target, exposure factor < 1; otherwise capped at max gross.
    factor = min(float(overlay.max_gross_exposure), float(overlay.target_vol_ann) / max(rv_ann, 1e-9))
    factor = max(0.0, min(float(overlay.max_gross_exposure), factor))

    scaled = {sym: w * factor for sym, w in target_weights.items()}
    return scaled, f"vol_target_scale={factor:.4f}"


@dataclass
class PersistenceState:
    """State holder for persistence/hysteresis overlay."""

    active_symbol: str | None = None
    pending_symbol: str | None = None
    pending_count: int = 0


def apply_persistence_hysteresis(
    *,
    proposed_weights: dict[str, float],
    state: PersistenceState,
    overlay: OverlayConfig,
) -> tuple[dict[str, float], PersistenceState, str]:
    """Reduce rapid symbol flip churn using persistence and hysteresis logic.

    For single-dominant allocations this behavior is often the largest source of
    cost reduction.
    """
    if not overlay.enable_persistence:
        # Keep state aligned with latest dominant symbol for future toggles.
        dominant = max(proposed_weights, key=proposed_weights.get) if proposed_weights else None
        state.active_symbol = dominant
        state.pending_symbol = None
        state.pending_count = 0
        return proposed_weights, state, "persistence_off"

    if not proposed_weights:
        return proposed_weights, state, "persistence_no_proposal"

    dominant = max(proposed_weights, key=proposed_weights.get)
    dominant_w = float(proposed_weights[dominant])

    if state.active_symbol is None:
        state.active_symbol = dominant
        state.pending_symbol = None
        state.pending_count = 0
        return proposed_weights, state, "persistence_seeded"

    if dominant == state.active_symbol:
        state.pending_symbol = None
        state.pending_count = 0
        return proposed_weights, state, "persistence_same_symbol"

    # Hysteresis: ignore weak challengers near current symbol weight.
    active_w = float(proposed_weights.get(state.active_symbol, 0.0))
    if dominant_w <= active_w + float(overlay.hysteresis_band_pct):
        hold = {state.active_symbol: 1.0} if state.active_symbol else proposed_weights
        return hold, state, "hysteresis_blocked_switch"

    # Persistence: require a challenger to persist for N days before switching.
    if state.pending_symbol != dominant:
        state.pending_symbol = dominant
        state.pending_count = 1
        hold = {state.active_symbol: 1.0} if state.active_symbol else proposed_weights
        return hold, state, "persistence_waiting_new_candidate"

    state.pending_count += 1
    required = max(1, int(overlay.persistence_days))
    if state.pending_count < required:
        hold = {state.active_symbol: 1.0} if state.active_symbol else proposed_weights
        return hold, state, f"persistence_waiting_{state.pending_count}/{required}"

    # Switch confirmed.
    state.active_symbol = dominant
    state.pending_symbol = None
    state.pending_count = 0
    return proposed_weights, state, "persistence_switch_confirmed"


def loss_limiter_triggered(
    *,
    pos: PositionState,
    bar: OhlcBar,
    today: date,
    overlay: OverlayConfig,
) -> tuple[bool, str]:
    """Optional downside limiter: fixed stop + max holding days."""
    if not overlay.enable_loss_limiter:
        return False, "loss_limiter_off"
    if pos.qty <= 0 or pos.entry_price <= 0:
        return False, "loss_limiter_no_position"

    stop_price = pos.entry_price * (1.0 - float(overlay.stop_loss_pct))
    if bar.low <= stop_price:
        return True, "loss_limiter_stop_hit"

    if pos.entry_day is not None:
        holding_days = (today - pos.entry_day).days
        if holding_days >= int(overlay.max_holding_days):
            return True, "loss_limiter_time_stop"

    return False, "loss_limiter_hold"


def apply_inverse_blocker(
    *,
    proposed_weights: dict[str, float],
    close_history: dict[str, list[float]],
    overlay: OverlayConfig,
) -> tuple[dict[str, float], str]:
    """Block inverse allocations when trend symbol is above its MA.

    This is an isolated research overlay meant to avoid getting stuck in
    persistent inverse holdings during sustained bull trends.
    """
    if not overlay.enable_inverse_blocker:
        return proposed_weights, "inverse_blocker_off"
    if not proposed_weights:
        return proposed_weights, "inverse_blocker_no_proposal"

    trend_sym = str(overlay.trend_symbol)
    series = close_history.get(trend_sym, [])
    w = max(2, int(overlay.trend_ma_days))
    if len(series) < w:
        return proposed_weights, "inverse_blocker_no_ma_history"

    ma = sum(series[-w:]) / float(w)
    px = float(series[-1])
    if px <= ma:
        return proposed_weights, "inverse_blocker_trend_not_bull"

    inverse_set = {"SOXS", "SQQQ", "SPXS", "TMV"}
    kept = {sym: wt for sym, wt in proposed_weights.items() if sym not in inverse_set}
    removed = [sym for sym in proposed_weights if sym in inverse_set]
    if not removed:
        return proposed_weights, "inverse_blocker_no_inverse_exposure"
    if not kept:
        # Fallback to SOXL when all proposed weights are inverse in strong bull trend.
        kept = {"SOXL": 1.0}
        return kept, "inverse_blocker_fallback_soxl"

    total = sum(kept.values())
    if total <= 0:
        return {"SOXL": 1.0}, "inverse_blocker_fallback_soxl_zero_sum"
    renorm = {sym: wt / total for sym, wt in kept.items()}
    return renorm, "inverse_blocker_removed_inverse"
