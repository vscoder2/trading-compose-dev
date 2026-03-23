from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from soxl_growth.config import OverlayConfig
from soxl_growth.logging_setup import get_logger
from soxl_growth.types import Weights

logger = get_logger(__name__)


class OverlayState(str, Enum):
    BASELINE_TRACKING = "baseline_tracking"
    OVERLAY_HEDGE = "overlay_hedge"
    OVERLAY_REENTRY_PENDING = "overlay_reentry_pending"
    OVERBOUGHT_FADE = "overbought_fade"
    COOLDOWN = "cooldown"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class OverlayMetrics:
    dd_intra_soxl: float
    rv_15m_tqqq: float
    price_soxl: float = 0.0
    vwap_60m_soxl: float = 0.0
    rsi_15m_soxl: float = 0.0
    realized_pnl_pct: float = 0.0
    data_failure_minutes: int = 0
    margin_usage: float = 0.0
    overbought_fade_regime: bool = False
    fade_confirmed: bool = False


@dataclass(frozen=True)
class OverlaySnapshot:
    flip_count_today: int
    minutes_since_last_trade: int


@dataclass(frozen=True)
class StepResult:
    state: OverlayState
    target: Weights
    reason: str


class OverlayStateMachine:
    def __init__(self, config: OverlayConfig | None = None) -> None:
        self.config = config or OverlayConfig()
        self.state = OverlayState.BASELINE_TRACKING
        self._reentry_confirmation_count = 0

    def on_trade_executed(self) -> None:
        if self.state != OverlayState.KILL_SWITCH:
            self.state = OverlayState.COOLDOWN
            logger.info("Overlay state transition -> %s (trade executed)", self.state)

    def _kill_switch(self, metrics: OverlayMetrics) -> bool:
        return (
            metrics.realized_pnl_pct <= -abs(self.config.daily_loss_limit_pct)
            or metrics.data_failure_minutes > self.config.data_failure_minutes_limit
            or metrics.margin_usage > self.config.margin_safety_threshold
        )

    def _can_flip(self, snapshot: OverlaySnapshot) -> bool:
        return (
            snapshot.flip_count_today < self.config.max_flips_per_day
            and snapshot.minutes_since_last_trade >= self.config.min_hold_minutes
        )

    def _hedge_triggered(self, metrics: OverlayMetrics) -> bool:
        return (
            metrics.dd_intra_soxl >= self.config.hedge_trigger_dd
            or metrics.rv_15m_tqqq >= self.config.hedge_trigger_rv
        )

    def _risk_normalized(self, metrics: OverlayMetrics) -> bool:
        return (
            metrics.dd_intra_soxl < self.config.hedge_trigger_dd * self.config.reentry_dd_ratio
            and metrics.rv_15m_tqqq < self.config.hedge_trigger_rv * self.config.reentry_rv_ratio
        )

    def _reentry_confirmed(self, metrics: OverlayMetrics) -> bool:
        return (
            metrics.price_soxl > metrics.vwap_60m_soxl
            or metrics.rsi_15m_soxl >= 50.0
        )

    def step(
        self,
        metrics: OverlayMetrics,
        snapshot: OverlaySnapshot,
        baseline_target: Weights,
        overlay_target: Weights,
    ) -> StepResult:
        if self._kill_switch(metrics):
            self.state = OverlayState.KILL_SWITCH
            logger.error("Overlay kill-switch triggered")
            return StepResult(self.state, {}, "kill_switch_triggered")

        if self.state == OverlayState.KILL_SWITCH:
            return StepResult(self.state, {}, "kill_switch_latched")

        if self.state == OverlayState.COOLDOWN:
            if snapshot.minutes_since_last_trade < self.config.cooldown_minutes:
                return StepResult(self.state, baseline_target, "cooldown_active")
            self.state = OverlayState.BASELINE_TRACKING

        # Hidden fourth regime from the tree: overbought fade mode.
        # When active, we gate execution into SOXS and can park in CASH until
        # confirmation appears.
        if metrics.overbought_fade_regime:
            if self.state != OverlayState.OVERBOUGHT_FADE:
                self.state = OverlayState.OVERBOUGHT_FADE
                logger.warning("Overlay state transition -> %s", self.state)
            if (not self.config.overbought_fade_require_confirmation) or metrics.fade_confirmed:
                return StepResult(
                    self.state,
                    {self.config.overbought_fade_symbol: 1.0},
                    "overbought_fade_confirmed",
                )
            return StepResult(
                self.state,
                {self.config.overbought_fade_cash_symbol: 1.0},
                "overbought_fade_wait_confirmation",
            )
        elif self.state == OverlayState.OVERBOUGHT_FADE:
            self.state = OverlayState.BASELINE_TRACKING
            logger.info("Overlay state transition -> %s", self.state)

        if self.state == OverlayState.BASELINE_TRACKING:
            if self._hedge_triggered(metrics) and self._can_flip(snapshot):
                self.state = OverlayState.OVERLAY_HEDGE
                self._reentry_confirmation_count = 0
                logger.warning("Overlay state transition -> %s", self.state)
                return StepResult(self.state, overlay_target, "hedge_triggered")
            return StepResult(self.state, baseline_target, "baseline_tracking")

        if self.state == OverlayState.OVERLAY_HEDGE:
            if self._risk_normalized(metrics):
                self.state = OverlayState.OVERLAY_REENTRY_PENDING
                self._reentry_confirmation_count = 0
                logger.info("Overlay state transition -> %s", self.state)
                return StepResult(self.state, overlay_target, "risk_normalized_wait_confirmation")
            return StepResult(self.state, overlay_target, "stay_hedged")

        if self.state == OverlayState.OVERLAY_REENTRY_PENDING:
            if self._reentry_confirmed(metrics):
                self._reentry_confirmation_count += 1
            else:
                self._reentry_confirmation_count = 0

            if (
                self._reentry_confirmation_count >= self.config.reentry_confirmations
                and self._can_flip(snapshot)
            ):
                self.state = OverlayState.BASELINE_TRACKING
                self._reentry_confirmation_count = 0
                logger.info("Overlay state transition -> %s", self.state)
                return StepResult(self.state, baseline_target, "reentry_confirmed")
            return StepResult(self.state, overlay_target, "reentry_pending")

        return StepResult(self.state, baseline_target, "default")
