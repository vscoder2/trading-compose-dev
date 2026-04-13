from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.models import (  # noqa: E402
    ActionIntent,
    DecisionContext,
    LockState,
)
from improvements2_impl.src.risk_controls import (  # noqa: E402
    ExposureInputs,
    compute_exposure_scalar,
    next_drawdown_brake_state,
    next_session_breaker_state,
    start_recovery_probe,
    step_recovery_probe,
)
from improvements2_impl.src.supervisor import evaluate  # noqa: E402


class TestPhase3RiskControls(unittest.TestCase):
    def _base_context(self, *, locks: list[LockState] | None = None) -> DecisionContext:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10.0,
                priority_class="rebalance_add",
                source="unit",
                reason_code="add_target",
            ),
            ActionIntent(
                symbol="SOXS",
                side="sell",
                qty=5.0,
                priority_class="rebalance_reduction",
                source="unit",
                reason_code="reduce_target",
            ),
        ]
        return DecisionContext(
            cycle_id="phase3-cycle",
            intents=intents,
            positions={"SOXS": 5.0},
            open_orders=[],
            locks=list(locks or []),
            buying_power=10_000.0,
            market_open=True,
            data_fresh=True,
        )

    def test_t016_exposure_scalar_bounded_and_monotonic(self) -> None:
        calm = compute_exposure_scalar(
            ExposureInputs(drawdown_pct=1.0, realized_vol_ann=0.75, chop_score=1.0)
        )
        stress = compute_exposure_scalar(
            ExposureInputs(drawdown_pct=18.0, realized_vol_ann=1.25, chop_score=4.0)
        )
        crisis = compute_exposure_scalar(
            ExposureInputs(drawdown_pct=32.0, realized_vol_ann=1.90, chop_score=8.0)
        )

        for value in (calm, stress, crisis):
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        self.assertGreater(calm, stress)
        self.assertGreater(stress, crisis)

    def test_t017_hard_drawdown_brake_blocks_net_new_buys(self) -> None:
        dd = next_drawdown_brake_state(prior_state="none", drawdown_pct=24.0)
        self.assertEqual(dd.state, "hard_brake")
        self.assertTrue(dd.blocks_adds)

        ctx = self._base_context(
            locks=[
                LockState(
                    lock_type="hard_brake",
                    scope="global",
                    subject=None,
                    active=True,
                    reason="dd_guard",
                )
            ]
        )
        result = evaluate(ctx)
        allowed_sides = {a.side for a in result.allowed_actions}
        self.assertNotIn("buy", allowed_sides)
        self.assertIn("sell", allowed_sides)
        self.assertIn("hard_brake_blocks_add", result.reason_codes)

    def test_t018_recovery_probe_steps_only_after_success_criteria(self) -> None:
        probe = start_recovery_probe()
        self.assertTrue(probe.active)
        self.assertEqual(probe.level_index, 0)
        self.assertEqual(probe.exposure_cap, 0.25)

        # One success is not enough to step with threshold=2.
        probe = step_recovery_probe(probe, hard_brake_active=False, success_signal=True, success_days_to_step=2)
        self.assertEqual(probe.level_index, 0)

        # Second success steps to next level.
        probe = step_recovery_probe(probe, hard_brake_active=False, success_signal=True, success_days_to_step=2)
        self.assertEqual(probe.level_index, 1)
        self.assertEqual(probe.exposure_cap, 0.50)

        # Failure steps down one notch.
        probe = step_recovery_probe(probe, hard_brake_active=False, success_signal=False, success_days_to_step=2)
        self.assertEqual(probe.level_index, 0)

    def test_t019_session_breaker_blocks_adds_but_keeps_exits(self) -> None:
        sb = next_session_breaker_state(prior_state="open", session_pnl_pct=-3.0)
        self.assertEqual(sb.state, "adds_blocked")
        self.assertTrue(sb.blocks_adds)

        ctx = self._base_context(
            locks=[
                LockState(
                    lock_type="hard_brake",
                    scope="global",
                    subject=None,
                    active=True,
                    reason="session_breaker_adds_block",
                )
            ]
        )
        result = evaluate(ctx)
        self.assertTrue(any(a.side == "sell" for a in result.allowed_actions))
        self.assertFalse(any(a.side == "buy" for a in result.allowed_actions))

    def test_t020_intraday_reentry_lock_blocks_same_session_readd(self) -> None:
        ctx = self._base_context(
            locks=[
                LockState(
                    lock_type="reentry_block",
                    scope="symbol",
                    subject="SOXL",
                    active=True,
                    reason="profit_lock_exit",
                )
            ]
        )
        result = evaluate(ctx)
        self.assertFalse(any(a.side == "buy" and a.symbol == "SOXL" for a in result.allowed_actions))
        self.assertIn("reentry_lock_blocks_add", result.reason_codes)
        self.assertTrue(any(a.side == "sell" for a in result.allowed_actions))


if __name__ == "__main__":
    unittest.main()
