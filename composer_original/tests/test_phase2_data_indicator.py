from __future__ import annotations

import csv
import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soxl_growth.backtest.engine import _validate_histories, run_backtest
from soxl_growth.composer_port.symphony_soxl_growth_v245_rl import InsufficientDataError, evaluate_strategy
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import BacktestConfig
from soxl_growth.indicators.drawdown import max_drawdown_percent
from soxl_growth.indicators.returns import cumulative_return_percent
from soxl_growth.indicators.rsi import rsi_base
from soxl_growth.indicators.volatility import stdev_return_annualized_percent
from soxl_growth.portfolio.selector import select_assets


FIXTURE = ROOT / "composer_original" / "fixtures" / "deep_check_prices.csv"


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


class Phase2DataIndicatorTest(unittest.TestCase):
    def test_indicator_insufficient_history_by_required_windows(self) -> None:
        # Required windows in original strategy:
        # MDD: 60, 200, 250; STDEV: 14, 30, 100, 105; CUMRET: 3, 8, 21, 30, 32; RSI: 30, 32
        prices = [100.0] * 20
        self.assertIsNone(max_drawdown_percent(prices, 60))
        self.assertIsNone(max_drawdown_percent(prices, 200))
        self.assertIsNone(max_drawdown_percent(prices, 250))
        self.assertIsNone(stdev_return_annualized_percent(prices, 30))
        self.assertIsNone(stdev_return_annualized_percent(prices, 100))
        self.assertIsNone(stdev_return_annualized_percent(prices, 105))
        self.assertIsNone(cumulative_return_percent(prices, 21))
        self.assertIsNone(cumulative_return_percent(prices, 30))
        self.assertIsNone(cumulative_return_percent(prices, 32))
        self.assertIsNone(rsi_base(prices, 30))
        self.assertIsNone(rsi_base(prices, 32))

    def test_evaluate_strategy_raises_on_insufficient_context(self) -> None:
        # Enough for some short windows, not enough for MDD60.
        closes = {
            "SOXL": [100.0] * 40,
            "SOXS": [100.0] * 40,
            "TQQQ": [100.0] * 40,
            "SQQQ": [100.0] * 40,
            "SPXL": [100.0] * 40,
            "SPXS": [100.0] * 40,
            "TMF": [100.0] * 40,
            "TMV": [100.0] * 40,
        }
        with self.assertRaises(InsufficientDataError):
            evaluate_strategy(DictContext(closes=closes))

    def test_selector_tie_breaking_is_deterministic_and_stable(self) -> None:
        assets = ["A", "B", "C", "D", "E"]
        scores = {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0}
        out1 = select_assets(assets=assets, metric=lambda s: scores[s], k=3, mode="top")
        out2 = select_assets(assets=assets, metric=lambda s: scores[s], k=3, mode="top")
        self.assertEqual(out1, out2)
        self.assertEqual(out1, [("A", 1 / 3), ("B", 1 / 3), ("C", 1 / 3)])

    def test_backtest_history_alignment_assumptions(self) -> None:
        h = _load_history(FIXTURE)
        # Control case: aligned histories must validate.
        dates = _validate_histories(h)
        self.assertGreater(len(dates), 0)

        # Misaligned symbol should raise.
        bad = dict(h)
        bad["SOXL"] = bad["SOXL"][:-1]
        with self.assertRaises(ValueError):
            _validate_histories(bad)

    def test_backtest_runs_with_shared_non_trading_day_gaps(self) -> None:
        h = _load_history(FIXTURE)
        # Remove every 13th row across all symbols to emulate common date gaps.
        keep_idx = [i for i in range(len(next(iter(h.values())))) if (i % 13) != 0]
        gapped: dict[str, list[tuple[date, float]]] = {}
        for s, rows in h.items():
            gapped[s] = [rows[i] for i in keep_idx]
        res = run_backtest(gapped, BacktestConfig(initial_equity=100_000.0, warmup_days=200))
        self.assertGreater(len(res.equity_curve), 0)
        self.assertGreater(len(res.allocations), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

