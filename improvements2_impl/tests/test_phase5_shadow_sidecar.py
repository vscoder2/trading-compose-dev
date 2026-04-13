from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.models import ActionIntent  # noqa: E402
from improvements2_impl.src.shadow_eval import build_shadow_diff, run_shadow_cycle  # noqa: E402
from improvements2_impl.src.state_adapter import ControlPlaneStore  # noqa: E402


class TestPhase5ShadowSidecar(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "phase5_test.db"
        self.store = ControlPlaneStore(self.db_path)
        self.store.apply_migration(Path("improvements2_impl/migrations/001_control_plane.sql"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_t027_shadow_cycle_updates_state_with_zero_submissions(self) -> None:
        primary = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10.0,
                priority_class="rebalance_add",
                source="primary",
                reason_code="base_add",
            )
        ]
        shadow = [
            ActionIntent(
                symbol="SOXL",
                side="sell",
                qty=10.0,
                priority_class="rebalance_reduction",
                source="shadow",
                reason_code="alt_reduce",
            ),
            ActionIntent(
                symbol="SOXS",
                side="buy",
                qty=4.0,
                priority_class="rebalance_add",
                source="shadow",
                reason_code="alt_add",
            ),
        ]
        result = run_shadow_cycle(
            store=self.store,
            cycle_id="c-shadow-1",
            variant_name="inverse_shadow",
            shadow_effective_target={"SOXL": 0.0, "SOXS": 0.4},
            shadow_actions=shadow,
            primary_actions=primary,
            primary_target={"SOXL": 1.0},
        )

        self.assertGreater(result.shadow_cycle_id, 0)
        self.assertEqual(result.submitted_order_count, 0)
        self.assertEqual(result.diff["submission_mode"], "shadow_no_submit")
        self.assertEqual(result.diff["submitted_order_count"], 0)
        self.assertEqual(self.store.count_rows("shadow_cycles"), 1)

        # Sidecar must not touch open-order submission state.
        self.assertEqual(self.store.count_rows("open_order_state"), 0)

    def test_shadow_cycle_forbids_submit_flag(self) -> None:
        with self.assertRaises(RuntimeError):
            run_shadow_cycle(
                store=self.store,
                cycle_id="c-shadow-2",
                variant_name="baseline_shadow",
                shadow_effective_target={"SOXL": 1.0},
                shadow_actions=[],
                allow_submit=True,
            )

    def test_shadow_diff_deterministic(self) -> None:
        primary = [
            ActionIntent("SOXL", "buy", 2.0, "rebalance_add", "p", "r"),
            ActionIntent("SOXS", "sell", 1.0, "rebalance_reduction", "p", "r"),
        ]
        shadow = [
            ActionIntent("SOXL", "buy", 1.0, "rebalance_add", "s", "r"),
            ActionIntent("SOXS", "sell", 1.0, "rebalance_reduction", "s", "r"),
        ]
        d1 = build_shadow_diff(
            primary_actions=primary,
            shadow_actions=shadow,
            primary_target={"SOXL": 0.6, "SOXS": 0.4},
            shadow_target={"SOXL": 0.5, "SOXS": 0.5},
        )
        d2 = build_shadow_diff(
            primary_actions=primary,
            shadow_actions=shadow,
            primary_target={"SOXL": 0.6, "SOXS": 0.4},
            shadow_target={"SOXL": 0.5, "SOXS": 0.5},
        )
        self.assertEqual(d1, d2)
        self.assertEqual(d1["submitted_order_count"], 0)


if __name__ == "__main__":
    unittest.main()
