from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import run_backtest
from soxl_growth.config import BacktestConfig
from soxl_growth.main import _build_arg_parser


FIXTURE = ROOT / "composer_original" / "fixtures" / "deep_check_prices.csv"
GOLDEN = ROOT / "composer_original" / "spec" / "backtest_golden_snapshot.json"


def _load_history(path: Path) -> dict[str, list[tuple[date, float]]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        symbols = [c for c in (r.fieldnames or []) if c != "date"]
        hist: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
        for row in r:
            d = date.fromisoformat(row["date"])
            for s in symbols:
                hist[s].append((d, float(row[s])))
    return hist


class Phase3BacktestParityCliTest(unittest.TestCase):
    def test_backtest_matches_golden_snapshot(self) -> None:
        self.assertTrue(GOLDEN.exists(), f"missing golden snapshot: {GOLDEN}")
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cfg = BacktestConfig(
            initial_equity=float(golden["config"]["initial_equity"]),
            warmup_days=int(golden["config"]["warmup_days"]),
            slippage_bps=float(golden["config"]["slippage_bps"]),
            sell_fee_bps=float(golden["config"]["sell_fee_bps"]),
        )
        res = run_backtest(_load_history(FIXTURE), cfg)
        summary = golden["summary"]
        self.assertEqual(len(res.equity_curve), summary["equity_points"])
        self.assertEqual(len(res.allocations), summary["allocation_days"])
        self.assertEqual(len(res.trades), summary["trade_count"])
        self.assertAlmostEqual(res.final_equity, summary["final_equity"], places=9)
        self.assertAlmostEqual(res.total_return_pct, summary["total_return_pct"], places=9)
        self.assertAlmostEqual(res.max_drawdown_pct, summary["max_drawdown_pct"], places=9)
        self.assertAlmostEqual(res.cagr_pct, summary["cagr_pct"], places=9)
        self.assertAlmostEqual(res.avg_daily_return_pct, summary["avg_daily_return_pct"], places=9)

        alloc = res.allocations
        first = golden["allocation_samples"]["first"]
        middle = golden["allocation_samples"]["middle"]
        last = golden["allocation_samples"]["last"]
        self.assertEqual(alloc[0][0].isoformat(), first["date"])
        self.assertEqual(alloc[0][1], first["weights"])
        self.assertEqual(alloc[len(alloc) // 2][0].isoformat(), middle["date"])
        self.assertEqual(alloc[len(alloc) // 2][1], middle["weights"])
        self.assertEqual(alloc[-1][0].isoformat(), last["date"])
        self.assertEqual(alloc[-1][1], last["weights"])

    def test_parity_mismatch_report_schema(self) -> None:
        parser = _build_arg_parser()
        args = parser.parse_args(
            [
                "parity-report",
                "--symphony-id",
                "dummy",
                "--prices-csv",
                str(FIXTURE),
                "--tolerance",
                "1e-9",
                "--sample-limit",
                "5",
            ]
        )

        local = run_backtest(_load_history(FIXTURE), BacktestConfig(initial_equity=100_000.0, warmup_days=260))
        oracle_alloc = {d.isoformat(): w for d, w in local.allocations}
        payload = {
            "allocations": oracle_alloc,
        }

        with patch("soxl_growth.main.ComposerParityClient.fetch_backtest", return_value=payload):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = args.func(args)
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        required = {"oracle_days", "local_days", "mismatch_count", "sample_mismatches"}
        self.assertTrue(required.issubset(set(report)))
        self.assertIsInstance(report["sample_mismatches"], list)
        self.assertEqual(report["mismatch_count"], 0)

    def test_cli_smoke_backtest_and_parity_calibrate_rsi(self) -> None:
        parser = _build_arg_parser()

        # backtest smoke
        bt_args = parser.parse_args(
            [
                "backtest",
                "--prices-csv",
                str(FIXTURE),
                "--initial-equity",
                "100000",
                "--warmup-days",
                "260",
            ]
        )
        bt_out = io.StringIO()
        with contextlib.redirect_stdout(bt_out):
            bt_rc = bt_args.func(bt_args)
        self.assertEqual(bt_rc, 0)
        bt_report = json.loads(bt_out.getvalue())
        for k in ("final_equity", "total_return_pct", "max_drawdown_pct", "cagr_pct", "avg_daily_return_pct", "trade_count"):
            self.assertIn(k, bt_report)
            self.assertTrue(math.isfinite(float(bt_report[k])))

        # parity-calibrate-rsi smoke (mock oracle API)
        cal_args = parser.parse_args(
            [
                "parity-calibrate-rsi",
                "--symphony-id",
                "dummy",
                "--prices-csv",
                str(FIXTURE),
                "--smoothing-spans",
                "5,8",
                "--sample-limit",
                "3",
            ]
        )
        local = run_backtest(_load_history(FIXTURE), BacktestConfig(initial_equity=100_000.0, warmup_days=260))
        oracle_alloc = {d.isoformat(): w for d, w in local.allocations}
        payload = {"allocations": oracle_alloc}
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tf:
            out_json = tf.name
        try:
            cal_args.output_json = out_json
            with patch("soxl_growth.main.ComposerParityClient.fetch_backtest", return_value=payload):
                cal_out = io.StringIO()
                with contextlib.redirect_stdout(cal_out):
                    cal_rc = cal_args.func(cal_args)
            self.assertEqual(cal_rc, 0)
            cal_report = json.loads(cal_out.getvalue())
            self.assertIn("candidate_count", cal_report)
            self.assertGreaterEqual(cal_report["candidate_count"], 1)
            self.assertIn("ranked_candidates", cal_report)
            self.assertTrue(Path(out_json).exists())
        finally:
            if os.path.exists(out_json):
                os.remove(out_json)


if __name__ == "__main__":
    unittest.main(verbosity=2)

