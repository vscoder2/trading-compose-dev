from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.models import DriftRecord, OpenOrder  # noqa: E402
from improvements2_impl.src.state_adapter import ControlPlaneStore  # noqa: E402


class TestPhase2StateAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "phase2_test.db"
        self.store = ControlPlaneStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_is_idempotent_and_tables_exist(self) -> None:
        first = self.store.apply_migration()
        second = self.store.apply_migration()
        self.assertTrue(first)
        self.assertFalse(second)

        names = set(self.store.list_tables())
        required = {
            "schema_migrations",
            "locks",
            "decision_cycles",
            "decision_reasons",
            "drift_snapshots",
            "open_order_state",
            "risk_state",
            "session_state",
            "shadow_cycles",
            "state_kv",
            "events",
        }
        self.assertTrue(required.issubset(names))

    def test_apply_migration_accepts_path_argument(self) -> None:
        migration_path = REPO_ROOT / "improvements2_impl" / "migrations" / "001_control_plane.sql"
        first = self.store.apply_migration(migration_path)
        second = self.store.apply_migration(migration_path)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_lock_lifecycle_persists_across_reopen(self) -> None:
        self.store.apply_migration()
        lock_id = self.store.put_lock(
            lock_type="reentry_block",
            scope="symbol",
            subject="SOXL",
            reason="profit_lock_exit",
            metadata={"cycle_id": "c1"},
        )
        self.assertGreater(lock_id, 0)
        rows = self.store.get_active_locks(lock_type="reentry_block", subject="SOXL")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].active)

        # Reopen and verify persistence.
        reopened = ControlPlaneStore(self.db_path)
        rows2 = reopened.get_active_locks(lock_type="reentry_block", subject="SOXL")
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0].reason, "profit_lock_exit")

        cleared = reopened.clear_lock(lock_id)
        self.assertTrue(cleared)
        rows3 = reopened.get_active_locks(lock_type="reentry_block", subject="SOXL")
        self.assertEqual(rows3, [])

    def test_expire_due_locks(self) -> None:
        self.store.apply_migration()
        expiry = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.store.put_lock(
            lock_type="cooldown",
            scope="symbol",
            subject="SOXS",
            reason="switch_cooldown",
            expiry_ts=expiry,
        )
        count = self.store.expire_due_locks()
        self.assertEqual(count, 1)
        self.assertEqual(self.store.get_active_locks(lock_type="cooldown", subject="SOXS"), [])

    def test_decision_cycle_and_reason_write(self) -> None:
        self.store.apply_migration()
        self.store.put_decision_cycle(
            cycle_id="cycle-1",
            cycle_type="daily_eval",
            profile="aggr_test",
            target={"SOXL": 1.0},
            effective_target={"SOXL": 0.5},
            decision_hash="abc123",
            severity="warn",
            details={"variant": "baseline"},
        )
        reason_id = self.store.append_decision_reason(
            cycle_id="cycle-1",
            reason_code="hard_brake_blocks_add",
            priority_class="hard_brake_exit",
            symbol="SOXL",
            detail={"blocked_qty": 10},
        )
        self.assertGreater(reason_id, 0)
        row = self.store.dump_decision_cycle("cycle-1")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["cycle_id"], "cycle-1")
        self.assertEqual(row["severity"], "warn")
        self.assertEqual(row["effective_target_json"]["SOXL"], 0.5)

    def test_open_order_upsert_list_remove(self) -> None:
        self.store.apply_migration()
        now = datetime.now(timezone.utc)
        order = OpenOrder(
            order_id="OID-1",
            symbol="SOXL",
            side="buy",
            qty=12.0,
            status="open",
            created_ts=now,
        )
        self.store.upsert_open_order(order, state="open", metadata={"source": "unit"})
        rows = self.store.list_open_orders(symbol="SOXL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].order_id, "OID-1")
        self.assertEqual(rows[0].qty, 12.0)

        # Update same order.
        order2 = OpenOrder(
            order_id="OID-1",
            symbol="SOXL",
            side="buy",
            qty=7.0,
            status="partial",
            created_ts=now,
        )
        self.store.upsert_open_order(order2, state="partial", metadata={"source": "unit-update"})
        rows2 = self.store.list_open_orders(symbol="SOXL")
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0].qty, 7.0)
        self.assertEqual(rows2[0].status, "partial")

        removed = self.store.remove_open_order("OID-1")
        self.assertTrue(removed)
        self.assertEqual(self.store.list_open_orders(symbol="SOXL"), [])

    def test_drift_risk_session_shadow_writes(self) -> None:
        self.store.apply_migration()
        drift_count = self.store.append_drift_records(
            cycle_id="cycle-2",
            rows=[
                DriftRecord(
                    symbol="SOXL",
                    expected_qty=10.0,
                    broker_qty=8.0,
                    qty_drift=-2.0,
                    unexpected_open_orders=1,
                    severity="warn",
                )
            ],
        )
        self.assertEqual(drift_count, 1)

        ts = self.store.put_risk_state(
            equity=10000,
            peak_equity=12000,
            drawdown_pct=16.6667,
            exposure_scalar=0.5,
            dd_brake_state="soft_brake",
            recovery_phase="none",
        )
        self.assertTrue(ts)

        self.store.put_session_state(
            session_date="2026-03-28",
            session_pnl=-125.5,
            breaker_state="adds_blocked",
            reentry_blocked_symbols=["SOXL", "soxs"],
        )

        shadow_id = self.store.append_shadow_cycle(
            cycle_id="cycle-2",
            variant_name="no_profit_lock",
            effective_target={"SOXL": 1.0},
            hypothetical_actions=[{"symbol": "SOXL", "side": "buy", "qty": 1}],
            diff={"equity_delta": 5.0},
        )
        self.assertGreater(shadow_id, 0)
        self.assertEqual(self.store.count_rows("drift_snapshots"), 1)
        self.assertEqual(self.store.count_rows("risk_state"), 1)
        self.assertEqual(self.store.count_rows("session_state"), 1)
        self.assertEqual(self.store.count_rows("shadow_cycles"), 1)


if __name__ == "__main__":
    unittest.main()
