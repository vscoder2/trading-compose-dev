from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.regime_policy import (  # noqa: E402
    ConfidenceInputs,
    HysteresisConfig,
    HysteresisState,
    build_confidence_log_payload,
    compute_adaptive_rebalance_threshold,
    compute_regime_confidence,
    step_hysteresis_state,
)


class TestPhase3DecisionQuality(unittest.TestCase):
    def test_t021_hysteresis_avoids_oscillation_flips(self) -> None:
        cfg = HysteresisConfig(
            enter_threshold=0.62,
            exit_threshold=0.58,
            min_enter_days=2,
            min_exit_days=2,
        )
        st = HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)

        # Alternating around middle band should not trigger flips.
        for sig in [0.61, 0.59] * 6:
            st = step_hysteresis_state(prior=st, signal=sig, cfg=cfg)
            self.assertEqual(st.regime, "risk_off")

        # Two consecutive strong signals should flip to risk_on.
        st = step_hysteresis_state(prior=st, signal=0.70, cfg=cfg)
        self.assertEqual(st.regime, "risk_off")
        st = step_hysteresis_state(prior=st, signal=0.72, cfg=cfg)
        self.assertEqual(st.regime, "risk_on")

        # One weak signal is insufficient to flip back due to hysteresis.
        st = step_hysteresis_state(prior=st, signal=0.50, cfg=cfg)
        self.assertEqual(st.regime, "risk_on")
        st = step_hysteresis_state(prior=st, signal=0.50, cfg=cfg)
        self.assertEqual(st.regime, "risk_off")

    def test_t022_confidence_bounded_and_logged(self) -> None:
        calm = ConfidenceInputs(trend_strength=0.8, realized_vol_ann=0.75, chop_score=1.0, data_fresh=True)
        stress = ConfidenceInputs(trend_strength=-0.4, realized_vol_ann=1.9, chop_score=8.0, data_fresh=False)

        c1, comp1 = compute_regime_confidence(calm)
        c2, comp2 = compute_regime_confidence(stress)

        self.assertGreaterEqual(c1, 0.0)
        self.assertLessEqual(c1, 1.0)
        self.assertGreaterEqual(c2, 0.0)
        self.assertLessEqual(c2, 1.0)
        self.assertGreater(c1, c2)

        payload = build_confidence_log_payload(
            cycle_id="cycle-021",
            profile="aggr_profile",
            confidence_score=c1,
            components=comp1,
        )
        self.assertEqual(payload["cycle_id"], "cycle-021")
        self.assertEqual(payload["profile"], "aggr_profile")
        self.assertIn("confidence_score", payload)
        self.assertIn("components", payload)
        self.assertGreaterEqual(payload["confidence_score"], 0.0)
        self.assertLessEqual(payload["confidence_score"], 1.0)
        self.assertIn("trend_component", comp2)
        self.assertIn("vol_penalty", comp2)
        self.assertIn("chop_penalty", comp2)

    def test_t023_adaptive_threshold_widens_in_noisy_regime(self) -> None:
        base = 0.05
        calm = compute_adaptive_rebalance_threshold(
            base_threshold_pct=base,
            realized_vol_ann=0.75,
            chop_score=1.0,
            confidence_score=0.85,
        )
        noisy = compute_adaptive_rebalance_threshold(
            base_threshold_pct=base,
            realized_vol_ann=1.80,
            chop_score=7.0,
            confidence_score=0.30,
        )
        self.assertGreaterEqual(calm, base)
        self.assertGreater(noisy, calm)
        self.assertGreater(noisy, base)


if __name__ == "__main__":
    unittest.main()
