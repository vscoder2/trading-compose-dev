from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.action_policy import resolve_symbol_actions  # noqa: E402
from improvements2_impl.src.decision_ledger import compute_decision_hash  # noqa: E402
from improvements2_impl.src.models import ActionIntent, DecisionContext, LockState, OpenOrder  # noqa: E402
from improvements2_impl.src.reconcile import build_pending_order_map, detect_state_drift  # noqa: E402
from improvements2_impl.src.supervisor import evaluate  # noqa: E402


class TestPhase1ControlKernel(unittest.TestCase):
    def test_priority_ladder_protective_beats_add(self) -> None:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10,
                priority_class="rebalance_add",
                source="rebalance",
                reason_code="rebalance_add",
            ),
            ActionIntent(
                symbol="SOXL",
                side="sell",
                qty=10,
                priority_class="profit_lock_exit",
                source="profit_lock",
                reason_code="pl_exit",
            ),
        ]
        kept, blocked = resolve_symbol_actions(intents)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].priority_class, "profit_lock_exit")
        self.assertEqual(len(blocked), 1)

    def test_drift_detector_flags_qty_mismatch(self) -> None:
        rows = detect_state_drift(
            expected_qty={"SOXL": 100.0},
            broker_qty={"SOXL": 90.0},
            open_orders=[],
            qty_threshold=0.01,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].symbol, "SOXL")
        self.assertAlmostEqual(rows[0].qty_drift, -10.0)

    def test_pending_order_map_aggregates(self) -> None:
        now = datetime.now(timezone.utc)
        open_orders = [
            OpenOrder(order_id="1", symbol="SOXL", side="buy", qty=10, status="open", created_ts=now),
            OpenOrder(order_id="2", symbol="SOXL", side="buy", qty=5, status="open", created_ts=now),
            OpenOrder(order_id="3", symbol="SOXL", side="sell", qty=2, status="open", created_ts=now),
        ]
        m = build_pending_order_map(open_orders)
        self.assertIn("SOXL", m)
        self.assertEqual(m["SOXL"]["pending_buy_qty"], 15.0)
        self.assertEqual(m["SOXL"]["pending_sell_qty"], 2.0)
        self.assertEqual(sorted(m["SOXL"]["open_order_ids"]), ["1", "2", "3"])

    def test_supervisor_blocks_reentry_and_pending_duplicates(self) -> None:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10,
                priority_class="rebalance_add",
                source="rebalance",
                reason_code="rebalance_add",
            ),
            ActionIntent(
                symbol="SOXS",
                side="buy",
                qty=5,
                priority_class="rebalance_add",
                source="rebalance",
                reason_code="rebalance_add",
            ),
        ]
        locks = [
            LockState(
                lock_type="reentry_block",
                scope="symbol",
                subject="SOXL",
                active=True,
                reason="intraday_profit_lock_exit",
            )
        ]
        ctx = DecisionContext(
            cycle_id="c1",
            intents=intents,
            positions={"SOXL": 0.0, "SOXS": 0.0},
            open_orders=[
                OpenOrder(
                    order_id="o1",
                    symbol="SOXS",
                    side="buy",
                    qty=1.0,
                    status="open",
                )
            ],
            locks=locks,
            buying_power=1000.0,
            market_open=True,
            data_fresh=True,
        )
        res = evaluate(ctx)
        self.assertEqual(len(res.allowed_actions), 0)
        reasons = [row["reason"] for row in res.blocked_actions]
        self.assertIn("reentry_lock_blocks_add", reasons)
        self.assertIn("pending_buy_exists", reasons)

    def test_supervisor_hard_brake_blocks_buys_but_allows_sells(self) -> None:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=3,
                priority_class="rebalance_add",
                source="rebalance",
                reason_code="buy_add",
            ),
            ActionIntent(
                symbol="TQQQ",
                side="sell",
                qty=2,
                priority_class="rebalance_reduction",
                source="rebalance",
                reason_code="risk_reduce",
            ),
        ]
        locks = [
            LockState(
                lock_type="hard_brake",
                scope="global",
                subject=None,
                active=True,
                reason="drawdown_brake",
            )
        ]
        ctx = DecisionContext(
            cycle_id="c2",
            intents=intents,
            positions={"SOXL": 0.0, "TQQQ": 5.0},
            open_orders=[],
            locks=locks,
            buying_power=1000.0,
            market_open=True,
            data_fresh=True,
        )
        res = evaluate(ctx)
        self.assertEqual(len(res.allowed_actions), 1)
        self.assertEqual(res.allowed_actions[0].symbol, "TQQQ")
        self.assertEqual(res.allowed_actions[0].side, "sell")
        self.assertIn("hard_brake_blocks_add", [x["reason"] for x in res.blocked_actions])

    def test_decision_hash_is_deterministic(self) -> None:
        snap1 = {
            "cycle_id": "c3",
            "variant": "baseline",
            "metrics": {"rv20_ann": 1.2, "dd20_pct": 5.0},
            "targets": {"SOXL": 1.0, "SOXS": 0.0},
        }
        snap2 = {
            "targets": {"SOXS": 0.0, "SOXL": 1.0},
            "metrics": {"dd20_pct": 5.0, "rv20_ann": 1.2},
            "variant": "baseline",
            "cycle_id": "c3",
        }
        h1 = compute_decision_hash(snap1)
        h2 = compute_decision_hash(snap2)
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
