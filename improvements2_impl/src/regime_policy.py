"""Decision-quality controls for pending Phase 3 items (I2-021..I2-023).

This module is intentionally pure-function + dataclass based:
1. Hysteresis regime state machine to reduce threshold oscillation flips.
2. Bounded confidence score with structured logging payload.
3. Adaptive rebalance threshold that widens in noisy regimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class HysteresisConfig:
    """Config for less-binary regime switching.

    Interpretation:
    - signal >= enter_threshold contributes to entering `risk_on`.
    - signal <= exit_threshold contributes to exiting `risk_on`.
    - enter_threshold should be strictly higher than exit_threshold.
    """

    enter_threshold: float = 0.62
    exit_threshold: float = 0.58
    min_enter_days: int = 2
    min_exit_days: int = 2


@dataclass(frozen=True)
class HysteresisState:
    """Hysteresis state tracked across cycles."""

    regime: str  # "risk_off" | "risk_on"
    enter_streak: int
    exit_streak: int


@dataclass(frozen=True)
class ConfidenceInputs:
    """Inputs for bounded confidence scoring."""

    trend_strength: float  # expected roughly in [-1, 1]
    realized_vol_ann: float  # annualized realized volatility
    chop_score: float  # larger => noisier regime
    data_fresh: bool = True


def step_hysteresis_state(
    *,
    prior: HysteresisState,
    signal: float,
    cfg: HysteresisConfig | None = None,
) -> HysteresisState:
    """Advance hysteresis state one cycle.

    This intentionally avoids instant flips on boundary noise by requiring
    consecutive confirmations before switching.
    """

    config = cfg or HysteresisConfig()
    regime = str(prior.regime or "risk_off").strip().lower()
    if regime not in {"risk_off", "risk_on"}:
        regime = "risk_off"

    enter = max(1, int(config.min_enter_days))
    exit_ = max(1, int(config.min_exit_days))
    sig = float(signal)

    if regime == "risk_off":
        enter_streak = int(prior.enter_streak) + 1 if sig >= float(config.enter_threshold) else 0
        if enter_streak >= enter:
            return HysteresisState(regime="risk_on", enter_streak=0, exit_streak=0)
        return HysteresisState(regime="risk_off", enter_streak=enter_streak, exit_streak=0)

    # regime == risk_on
    exit_streak = int(prior.exit_streak) + 1 if sig <= float(config.exit_threshold) else 0
    if exit_streak >= exit_:
        return HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)
    return HysteresisState(regime="risk_on", enter_streak=0, exit_streak=exit_streak)


def compute_regime_confidence(inputs: ConfidenceInputs) -> tuple[float, dict[str, float]]:
    """Compute confidence score in [0,1] and return explainable components."""

    # Trend maps from [-1, 1] => [0, 1], rewarding stronger directional signal.
    trend_component = _clamp((float(inputs.trend_strength) + 1.0) / 2.0, 0.0, 1.0)

    # Vol/chop are penalties normalized to [0,1].
    vol_penalty = _clamp((float(inputs.realized_vol_ann) - 0.60) / 1.40, 0.0, 1.0)
    chop_penalty = _clamp(float(inputs.chop_score) / 8.0, 0.0, 1.0)

    # Weighted blend: trend is strongest, then vol/chop penalties.
    score = (
        (0.65 * trend_component)
        + (0.20 * (1.0 - vol_penalty))
        + (0.15 * (1.0 - chop_penalty))
    )

    # Stale data hard-caps confidence so downstream logic cannot over-trust it.
    if not bool(inputs.data_fresh):
        score = min(score, 0.35)

    bounded = _clamp(score, 0.0, 1.0)
    components = {
        "trend_component": float(trend_component),
        "vol_penalty": float(vol_penalty),
        "chop_penalty": float(chop_penalty),
    }
    return bounded, components


def build_confidence_log_payload(
    *,
    cycle_id: str,
    profile: str,
    confidence_score: float,
    components: dict[str, float],
) -> dict[str, Any]:
    """Build structured payload for persistence/event logs."""

    return {
        "cycle_id": str(cycle_id),
        "profile": str(profile),
        "confidence_score": _clamp(float(confidence_score), 0.0, 1.0),
        "components": {
            "trend_component": _clamp(float(components.get("trend_component", 0.0)), 0.0, 1.0),
            "vol_penalty": _clamp(float(components.get("vol_penalty", 0.0)), 0.0, 1.0),
            "chop_penalty": _clamp(float(components.get("chop_penalty", 0.0)), 0.0, 1.0),
        },
    }


def compute_adaptive_rebalance_threshold(
    *,
    base_threshold_pct: float,
    realized_vol_ann: float,
    chop_score: float,
    confidence_score: float,
    min_threshold_pct: float = 0.0,
    max_threshold_pct: float = 1.0,
) -> float:
    """Compute adaptive threshold that widens under noise/uncertainty."""

    base = max(0.0, float(base_threshold_pct))
    vol_norm = _clamp((float(realized_vol_ann) - 0.60) / 1.40, 0.0, 1.0)
    chop_norm = _clamp(float(chop_score) / 8.0, 0.0, 1.0)
    conf = _clamp(float(confidence_score), 0.0, 1.0)

    # Expansion multiplier:
    # - wider with high vol/chop
    # - wider with lower confidence
    multiplier = 1.0 + (1.20 * vol_norm) + (0.80 * chop_norm) + (0.60 * (1.0 - conf))
    out = base * multiplier
    return _clamp(out, float(min_threshold_pct), float(max_threshold_pct))
