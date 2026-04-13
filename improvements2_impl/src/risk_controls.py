"""Phase 3 safety controls (pure functions, no broker dependencies).

This module adds deterministic risk primitives that can be called by any
runtime/simulator integration layer:

1. Dynamic exposure scaling
2. Hard drawdown brake state machine
3. Session PnL circuit-breaker
4. Recovery probe step-up logic

All functions are side-effect free and intentionally explicit so they can be
unit-tested with fixture-driven scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class ExposureInputs:
    """Stress inputs used to compute an exposure scalar in [0, 1]."""

    drawdown_pct: float
    realized_vol_ann: float
    chop_score: float = 0.0


@dataclass(frozen=True)
class DrawdownBrakeResult:
    """Result of one drawdown-brake state transition step."""

    state: str
    blocks_adds: bool
    reason_code: str


@dataclass(frozen=True)
class SessionBreakerResult:
    """Result of one session PnL circuit-breaker transition step."""

    state: str
    blocks_adds: bool
    reason_code: str


@dataclass(frozen=True)
class RecoveryProbeState:
    """Recovery probe ramp state after stress periods.

    `level_index` maps to capped exposure:
    - 0 => 0.25
    - 1 => 0.50
    - 2 => 0.75
    - 3 => 1.00
    """

    active: bool
    level_index: int
    success_streak: int

    @property
    def exposure_cap(self) -> float:
        levels = (0.25, 0.50, 0.75, 1.00)
        idx = int(_clamp(self.level_index, 0, len(levels) - 1))
        return float(levels[idx])


def compute_exposure_scalar(
    inputs: ExposureInputs,
    *,
    min_scalar: float = 0.20,
    max_scalar: float = 1.00,
) -> float:
    """Compute bounded exposure scalar with monotonic stress response.

    Higher drawdown / volatility / chop => lower scalar.
    """

    dd_norm = _clamp(float(inputs.drawdown_pct) / 40.0, 0.0, 1.0)
    vol_norm = _clamp((float(inputs.realized_vol_ann) - 0.60) / 1.40, 0.0, 1.0)
    chop_norm = _clamp(float(inputs.chop_score) / 8.0, 0.0, 1.0)

    # Drawdown has the highest influence, then volatility, then chop.
    stress = _clamp((0.55 * dd_norm) + (0.30 * vol_norm) + (0.15 * chop_norm), 0.0, 1.0)
    raw = float(max_scalar) * (1.0 - stress)
    return _clamp(raw, float(min_scalar), float(max_scalar))


def next_drawdown_brake_state(
    *,
    prior_state: str,
    drawdown_pct: float,
    soft_enter_pct: float = 12.0,
    soft_exit_pct: float = 8.0,
    hard_enter_pct: float = 20.0,
    hard_exit_pct: float = 14.0,
) -> DrawdownBrakeResult:
    """Transition drawdown brake state with explicit hysteresis bands.

    States:
    - none
    - soft_brake
    - hard_brake
    """

    state = str(prior_state or "none").strip().lower()
    dd = float(drawdown_pct)

    if state not in {"none", "soft_brake", "hard_brake"}:
        state = "none"

    if state == "hard_brake":
        if dd <= hard_exit_pct:
            if dd >= soft_enter_pct:
                return DrawdownBrakeResult("soft_brake", False, "hard_brake_released_to_soft")
            return DrawdownBrakeResult("none", False, "hard_brake_released")
        return DrawdownBrakeResult("hard_brake", True, "hard_brake_persist")

    if state == "soft_brake":
        if dd >= hard_enter_pct:
            return DrawdownBrakeResult("hard_brake", True, "hard_brake_enter")
        if dd <= soft_exit_pct:
            return DrawdownBrakeResult("none", False, "soft_brake_released")
        return DrawdownBrakeResult("soft_brake", False, "soft_brake_persist")

    # state == none
    if dd >= hard_enter_pct:
        return DrawdownBrakeResult("hard_brake", True, "hard_brake_enter")
    if dd >= soft_enter_pct:
        return DrawdownBrakeResult("soft_brake", False, "soft_brake_enter")
    return DrawdownBrakeResult("none", False, "no_brake")


def next_session_breaker_state(
    *,
    prior_state: str,
    session_pnl_pct: float,
    adds_block_threshold_pct: float = -2.5,
    full_stop_threshold_pct: float = -4.0,
    recover_threshold_pct: float = -1.0,
) -> SessionBreakerResult:
    """Transition session breaker state based on same-day PnL."""

    state = str(prior_state or "open").strip().lower()
    pnl = float(session_pnl_pct)
    if state not in {"open", "adds_blocked", "full_stop"}:
        state = "open"

    if pnl <= full_stop_threshold_pct:
        return SessionBreakerResult("full_stop", True, "session_full_stop_trigger")

    if state == "full_stop":
        if pnl >= recover_threshold_pct:
            return SessionBreakerResult("open", False, "session_full_stop_release")
        return SessionBreakerResult("full_stop", True, "session_full_stop_persist")

    if pnl <= adds_block_threshold_pct:
        return SessionBreakerResult("adds_blocked", True, "session_adds_block_trigger")

    if state == "adds_blocked":
        if pnl >= recover_threshold_pct:
            return SessionBreakerResult("open", False, "session_adds_block_release")
        return SessionBreakerResult("adds_blocked", True, "session_adds_block_persist")

    return SessionBreakerResult("open", False, "session_open")


def start_recovery_probe() -> RecoveryProbeState:
    """Start recovery probe at lowest exposure cap after stress."""

    return RecoveryProbeState(active=True, level_index=0, success_streak=0)


def step_recovery_probe(
    prior: RecoveryProbeState,
    *,
    hard_brake_active: bool,
    success_signal: bool,
    success_days_to_step: int = 2,
) -> RecoveryProbeState:
    """Advance recovery probe state.

    Rules:
    - If hard brake is active, reset probe to level 0 and keep it active.
    - If probe inactive, stay inactive.
    - On success, increase streak; step up one level after N successes.
    - On failure, reset streak and step down one level (if possible).
    """

    if hard_brake_active:
        return RecoveryProbeState(active=True, level_index=0, success_streak=0)

    if not prior.active:
        return prior

    level = int(_clamp(prior.level_index, 0, 3))
    streak = max(0, int(prior.success_streak))
    threshold = max(1, int(success_days_to_step))

    if success_signal:
        streak += 1
        if streak >= threshold and level < 3:
            level += 1
            streak = 0
        return RecoveryProbeState(active=True, level_index=level, success_streak=streak)

    # Failed day: reduce risk one notch and reset success streak.
    if level > 0:
        level -= 1
    return RecoveryProbeState(active=True, level_index=level, success_streak=0)
