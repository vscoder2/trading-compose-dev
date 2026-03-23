from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_module():
    path = ROOT / "composer_original" / "tools" / "intraday_profit_lock_verification.py"
    spec = importlib.util.spec_from_file_location("intraday_profit_lock_verification", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Phase5IntradayProfitLockVerificationTest(unittest.TestCase):
    def test_trailing_profit_lock_records_intraday_sell_timestamp(self) -> None:
        m = _load_module()
        d1 = date(2026, 1, 2)
        d2 = date(2026, 1, 5)

        symbols = ["SOXL", "SOXS"]
        aligned_days = [d1, d2]
        price_history = {
            "SOXL": [(d1, 100.0), (d2, 109.0)],
            "SOXS": [(d1, 10.0), (d2, 10.0)],
        }
        close_map = {
            "SOXL": {d1: 100.0, d2: 109.0},
            "SOXS": {d1: 10.0, d2: 10.0},
        }
        minute = {
            d1: {
                "SOXL": [(datetime(2026, 1, 2, 15, 59), 100.0, 100.0, 100.0, 100.0)],
                "SOXS": [(datetime(2026, 1, 2, 15, 59), 10.0, 10.0, 10.0, 10.0)],
            },
            d2: {
                "SOXL": [
                    (datetime(2026, 1, 5, 10, 0), 100.0, 111.0, 110.0, 110.0),
                    (datetime(2026, 1, 5, 10, 1), 110.0, 112.0, 108.0, 109.0),
                    (datetime(2026, 1, 5, 15, 59), 109.0, 109.5, 108.8, 109.0),
                ],
                "SOXS": [(datetime(2026, 1, 5, 15, 59), 10.0, 10.0, 10.0, 10.0)],
            },
        }
        baseline = {
            d1: {"SOXL": 1.0},
            d2: {"SOXL": 1.0},
        }
        profile = m.LockedProfile(
            name="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
            enable_profit_lock=True,
            profit_lock_mode="trailing",
            profit_lock_threshold_pct=10.0,
            profit_lock_trail_pct=2.0,
            profit_lock_adaptive_threshold=False,
            profit_lock_adaptive_symbol="TQQQ",
            profit_lock_adaptive_rv_window=14,
            profit_lock_adaptive_rv_baseline_pct=85.0,
            profit_lock_adaptive_min_threshold_pct=8.0,
            profit_lock_adaptive_max_threshold_pct=30.0,
        )

        result = m._simulate_intraday_verification(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map,
            minute_by_day_symbol=minute,
            baseline_target_by_day=baseline,
            profile=profile,
            start_day=d1,
            end_day=d2,
            initial_equity=10_000.0,
            slippage_bps=0.0,
            sell_fee_bps=0.0,
            profit_lock_exec_model="synthetic",
        )
        sell_events = [e for e in result.events if e.event_type == "profit_lock_sell"]
        self.assertEqual(len(sell_events), 1)
        self.assertEqual(sell_events[0].symbol, "SOXL")
        self.assertEqual(sell_events[0].ts.hour, 10)
        self.assertEqual(sell_events[0].ts.minute, 1)

        day2_row = [r for r in result.daily if r.day == d2][0]
        self.assertIn("10:01 | SOXL", day2_row.sale_time_stock)
        self.assertIn("15:59 | SOXL", day2_row.new_purchase_time_stock)

    def test_market_close_exec_model_uses_close_price(self) -> None:
        m = _load_module()
        d1 = date(2026, 1, 2)
        d2 = date(2026, 1, 5)

        symbols = ["SOXL", "SOXS"]
        aligned_days = [d1, d2]
        price_history = {
            "SOXL": [(d1, 100.0), (d2, 109.0)],
            "SOXS": [(d1, 10.0), (d2, 10.0)],
        }
        close_map = {
            "SOXL": {d1: 100.0, d2: 109.0},
            "SOXS": {d1: 10.0, d2: 10.0},
        }
        minute = {
            d1: {
                "SOXL": [(datetime(2026, 1, 2, 15, 59), 100.0, 100.0, 100.0, 100.0)],
                "SOXS": [(datetime(2026, 1, 2, 15, 59), 10.0, 10.0, 10.0, 10.0)],
            },
            d2: {
                "SOXL": [
                    (datetime(2026, 1, 5, 10, 0), 100.0, 111.0, 110.0, 110.0),
                    (datetime(2026, 1, 5, 10, 1), 110.0, 112.0, 108.0, 109.0),
                ],
                "SOXS": [(datetime(2026, 1, 5, 15, 59), 10.0, 10.0, 10.0, 10.0)],
            },
        }
        baseline = {
            d1: {"SOXL": 1.0},
            d2: {"SOXL": 1.0},
        }
        profile = m.LockedProfile(
            name="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
            enable_profit_lock=True,
            profit_lock_mode="trailing",
            profit_lock_threshold_pct=10.0,
            profit_lock_trail_pct=2.0,
            profit_lock_adaptive_threshold=False,
            profit_lock_adaptive_symbol="TQQQ",
            profit_lock_adaptive_rv_window=14,
            profit_lock_adaptive_rv_baseline_pct=85.0,
            profit_lock_adaptive_min_threshold_pct=8.0,
            profit_lock_adaptive_max_threshold_pct=30.0,
        )

        result = m._simulate_intraday_verification(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map,
            minute_by_day_symbol=minute,
            baseline_target_by_day=baseline,
            profile=profile,
            start_day=d1,
            end_day=d2,
            initial_equity=10_000.0,
            slippage_bps=0.0,
            sell_fee_bps=0.0,
            profit_lock_exec_model="market_close",
        )
        sell_events = [e for e in result.events if e.event_type == "profit_lock_sell"]
        self.assertEqual(len(sell_events), 1)
        self.assertAlmostEqual(sell_events[0].price, 109.0, places=9)

    def test_split_adjustment_scales_holdings_before_day_logic(self) -> None:
        m = _load_module()
        d1 = date(2026, 3, 4)
        d2 = date(2026, 3, 5)

        symbols = ["SOXL", "SOXS"]
        aligned_days = [d1, d2]
        price_history = {
            "SOXL": [(d1, 10.0), (d2, 10.0)],
            "SOXS": [(d1, 2.0), (d2, 40.0)],
        }
        close_map = {
            "SOXL": {d1: 10.0, d2: 10.0},
            "SOXS": {d1: 2.0, d2: 40.0},
        }
        minute = {
            d1: {
                "SOXL": [(datetime(2026, 3, 4, 15, 59), 10.0, 10.0, 10.0, 10.0)],
                "SOXS": [(datetime(2026, 3, 4, 15, 59), 2.0, 2.0, 2.0, 2.0)],
            },
            d2: {
                "SOXL": [(datetime(2026, 3, 5, 15, 59), 10.0, 10.0, 10.0, 10.0)],
                "SOXS": [(datetime(2026, 3, 5, 15, 59), 40.0, 40.0, 40.0, 40.0)],
            },
        }
        baseline = {
            d1: {"SOXS": 1.0},
            d2: {"SOXS": 1.0},
        }
        profile = m.LockedProfile(
            name="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
            enable_profit_lock=False,
            profit_lock_mode="trailing",
            profit_lock_threshold_pct=10.0,
            profit_lock_trail_pct=2.0,
            profit_lock_adaptive_threshold=False,
            profit_lock_adaptive_symbol="TQQQ",
            profit_lock_adaptive_rv_window=14,
            profit_lock_adaptive_rv_baseline_pct=85.0,
            profit_lock_adaptive_min_threshold_pct=8.0,
            profit_lock_adaptive_max_threshold_pct=30.0,
        )

        # Overnight reverse split: shares should scale down 20x at day2 start.
        split_ratio = {
            d1: {"SOXL": 1.0, "SOXS": 1.0},
            d2: {"SOXL": 1.0, "SOXS": 0.05},
        }

        result = m._simulate_intraday_verification(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map,
            minute_by_day_symbol=minute,
            baseline_target_by_day=baseline,
            profile=profile,
            start_day=d1,
            end_day=d2,
            initial_equity=10_000.0,
            slippage_bps=0.0,
            sell_fee_bps=0.0,
            profit_lock_exec_model="synthetic",
            split_ratio_by_day_symbol=split_ratio,
        )
        # With split-adjustment, equity should stay stable (ignoring tiny rounding), not 20x jump.
        self.assertAlmostEqual(result.daily[0].equity, 10_000.0, places=9)
        self.assertAlmostEqual(result.daily[1].equity, 10_000.0, places=9)
        split_events = [e for e in result.events if e.event_type == "split_adjustment" and e.symbol == "SOXS"]
        self.assertEqual(len(split_events), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
