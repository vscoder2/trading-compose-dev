from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from improvements2_impl.src.audit_export import (  # noqa: E402
    build_eod_row,
    list_eod_reports,
    row_to_dict,
    upsert_eod_report,
)
from improvements2_impl.src.decision_ledger import compute_decision_hash  # noqa: E402
from improvements2_impl.src.execution_policy import (  # noqa: E402
    estimate_turnover_notional,
    resolve_order_conflicts,
)
from improvements2_impl.src.models import ActionIntent, OpenOrder  # noqa: E402


class TestPhase4ExecutionObservability(unittest.TestCase):
    def test_t024_conflict_resolver_emits_single_net_action(self) -> None:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10.0,
                priority_class="rebalance_add",
                source="strategy",
                reason_code="add",
            ),
            ActionIntent(
                symbol="SOXL",
                side="sell",
                qty=8.0,
                priority_class="profit_lock_exit",
                source="risk",
                reason_code="pl_exit",
            ),
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=5.0,
                priority_class="maintenance",
                source="maintenance",
                reason_code="noise",
            ),
        ]
        open_orders = [
            OpenOrder(
                order_id="OID-1",
                symbol="SOXL",
                side="buy",
                qty=2.0,
                status="open",
                created_ts=datetime.now(timezone.utc),
            )
        ]

        kept, blocked, _diag = resolve_order_conflicts(intents, open_orders)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].symbol, "SOXL")
        self.assertEqual(kept[0].side, "sell")
        self.assertGreaterEqual(len(blocked), 2)

    def test_t025_decision_hash_is_deterministic_for_same_input(self) -> None:
        snap = {
            "cycle_id": "c-123",
            "profile": "aggr_profile",
            "target": {"SOXL": 1.0},
            "reasons": ["profit_lock_exit", "pending_buy_exists"],
        }
        h1 = compute_decision_hash(snap)
        h2 = compute_decision_hash(snap)
        self.assertEqual(h1, h2)

    def test_t026_eod_report_single_row_per_day_with_turnover_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "phase4_eod.db"
            row = build_eod_row(
                report_date="2026-03-28",
                profile="aggr_intraday_v1",
                start_equity=10000.0,
                end_equity=10750.0,
                max_drawdown_pct=4.25,
                trade_count=6,
                turnover_buy_notional=12000.0,
                turnover_sell_notional=11850.0,
                metadata={"window": "1m"},
            )
            upsert_eod_report(db, row)

            # Upsert same day/profile should replace, not duplicate.
            row2 = build_eod_row(
                report_date="2026-03-28",
                profile="aggr_intraday_v1",
                start_equity=10000.0,
                end_equity=10800.0,
                max_drawdown_pct=4.10,
                trade_count=7,
                turnover_buy_notional=12100.0,
                turnover_sell_notional=12000.0,
                metadata={"window": "1m", "rerun": True},
            )
            upsert_eod_report(db, row2)

            rows = list_eod_reports(db, profile="aggr_intraday_v1")
            self.assertEqual(len(rows), 1)
            only = rows[0]
            self.assertEqual(only["report_date"], "2026-03-28")
            self.assertEqual(only["trade_count"], 7)
            self.assertIn("turnover_buy_notional", only)
            self.assertIn("turnover_sell_notional", only)
            self.assertIn("turnover_total_notional", only)
            self.assertIn("turnover_ratio", only)
            self.assertGreater(float(only["turnover_total_notional"]), 0.0)
            self.assertGreater(float(only["turnover_ratio"]), 0.0)

    def test_turnover_estimator(self) -> None:
        intents = [
            ActionIntent(
                symbol="SOXL",
                side="buy",
                qty=10,
                priority_class="rebalance_add",
                source="s",
                reason_code="r",
            ),
            ActionIntent(
                symbol="SOXS",
                side="sell",
                qty=4,
                priority_class="rebalance_reduction",
                source="s",
                reason_code="r",
            ),
        ]
        prices = {"SOXL": 50.0, "SOXS": 30.0}
        out = estimate_turnover_notional(intents, prices)
        self.assertAlmostEqual(out["buy_notional"], 500.0, places=6)
        self.assertAlmostEqual(out["sell_notional"], 120.0, places=6)
        self.assertAlmostEqual(out["total_notional"], 620.0, places=6)
        self.assertEqual(int(out["trade_count"]), 2)

        row = build_eod_row(
            report_date="2026-03-28",
            profile="demo",
            start_equity=10000.0,
            end_equity=10100.0,
            max_drawdown_pct=1.5,
            trade_count=int(out["trade_count"]),
            turnover_buy_notional=out["buy_notional"],
            turnover_sell_notional=out["sell_notional"],
        )
        d = row_to_dict(row)
        self.assertIn("pnl", d)
        self.assertIn("return_pct", d)
        self.assertIn("turnover_total_notional", d)
        self.assertIn("turnover_ratio", d)


if __name__ == "__main__":
    unittest.main()
