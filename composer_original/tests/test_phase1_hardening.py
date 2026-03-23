from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import soxl_growth.composer_port.symphony_soxl_growth_v245_rl as symphony


class MetricContext:
    def __init__(self, values: dict[tuple[str, str, int], float]) -> None:
        self.values = values

    def close_series(self, symbol: str):
        return [100.0, 101.0]


def _eval_with_values(values: dict[tuple[str, str, int], float]) -> dict[str, float]:
    ctx = MetricContext(values)
    with (
        patch.object(symphony, "_mdd", side_effect=lambda c, s, w: c.values[("mdd", s, w)]),
        patch.object(symphony, "_stdev", side_effect=lambda c, s, w: c.values[("stdev", s, w)]),
        patch.object(symphony, "_cumret", side_effect=lambda c, s, w: c.values[("cumret", s, w)]),
        patch.object(symphony, "_rsi", side_effect=lambda c, s, w: c.values[("rsi", s, w)]),
    ):
        return symphony.evaluate_strategy(ctx, tree=symphony.build_tree())


def _crash_base() -> dict[tuple[str, str, int], float]:
    return {
        ("mdd", "SOXL", 60): 55.0,
        ("stdev", "TQQQ", 14): 10.0,
        ("stdev", "TQQQ", 100): 10.0,
        ("rsi", "TQQQ", 30): 40.0,
        ("stdev", "TQQQ", 30): 6.0,
        ("cumret", "TQQQ", 8): -5.0,
        ("mdd", "TQQQ", 200): 60.0,
        ("cumret", "TQQQ", 30): -5.0,
        ("cumret", "SOXL", 21): 20.0,
        ("cumret", "TQQQ", 21): 10.0,
        ("cumret", "SPXL", 21): 5.0,
        ("cumret", "TMF", 21): 0.0,
        ("cumret", "TMV", 3): 2.0,
        ("cumret", "SQQQ", 3): 1.0,
        ("cumret", "SPXS", 3): -5.0,
    }


def _normal_base() -> dict[tuple[str, str, int], float]:
    return {
        ("mdd", "SOXL", 60): 10.0,
        ("rsi", "SOXL", 32): 55.0,
        ("stdev", "SOXL", 105): 6.0,
        ("rsi", "SOXL", 30): 60.0,
        ("stdev", "SOXL", 30): 5.0,
        ("cumret", "SOXL", 32): 0.0,
        ("mdd", "SOXL", 250): 70.0,
        ("cumret", "SOXL", 21): 4.0,
        ("cumret", "SPXL", 21): 3.0,
        ("cumret", "TQQQ", 21): 2.0,
        ("cumret", "TMF", 21): 0.0,
    }


class Phase1HardeningTest(unittest.TestCase):
    # Threshold edge tests (all pivots in original tree)
    def test_mdd_soxl_60_edge(self) -> None:
        c = _crash_base()
        c[("mdd", "SOXL", 60)] = 50.0
        self.assertEqual(_eval_with_values(c), {"SPXS": 1.0})

        n = _normal_base()
        n[("mdd", "SOXL", 60)] = 49.9
        self.assertEqual(_eval_with_values(n), {"SOXL": 0.5, "SPXL": 0.5})

    def test_stdev_tqqq_14_edge(self) -> None:
        c = _crash_base()
        c[("stdev", "TQQQ", 14)] = 18.0
        self.assertEqual(_eval_with_values(c), {"SPXS": 1.0})

        c2 = _crash_base()
        c2[("stdev", "TQQQ", 14)] = 18.1
        self.assertEqual(_eval_with_values(c2), {"SOXL": 1 / 3, "TQQQ": 1 / 3, "SPXL": 1 / 3})

    def test_stdev_tqqq_100_edge(self) -> None:
        c = _crash_base()
        c[("stdev", "TQQQ", 100)] = 3.8
        self.assertEqual(_eval_with_values(c), {"SOXL": 0.5, "TQQQ": 0.5})

        c2 = _crash_base()
        c2[("stdev", "TQQQ", 100)] = 3.81
        self.assertEqual(_eval_with_values(c2), {"SPXS": 1.0})

    def test_rsi_tqqq_30_edge(self) -> None:
        c = _crash_base()
        c[("rsi", "TQQQ", 30)] = 50.0
        c[("stdev", "TQQQ", 30)] = 6.0
        self.assertEqual(_eval_with_values(c), {"SOXS": 1.0})

        c2 = _crash_base()
        c2[("rsi", "TQQQ", 30)] = 49.9
        self.assertEqual(_eval_with_values(c2), {"SPXS": 1.0})

    def test_stdev_tqqq_30_edge(self) -> None:
        c = _crash_base()
        c[("rsi", "TQQQ", 30)] = 55.0
        c[("stdev", "TQQQ", 30)] = 5.8
        self.assertEqual(_eval_with_values(c), {"SOXS": 1.0})

        c2 = _crash_base()
        c2[("rsi", "TQQQ", 30)] = 55.0
        c2[("stdev", "TQQQ", 30)] = 5.79
        self.assertEqual(_eval_with_values(c2), {"SPXL": 1.0})

    def test_cumret_tqqq_8_edge(self) -> None:
        c = _crash_base()
        c[("cumret", "TQQQ", 8)] = -20.0
        self.assertEqual(_eval_with_values(c), {"SOXL": 1.0})

        c2 = _crash_base()
        c2[("cumret", "TQQQ", 8)] = -19.9
        self.assertEqual(_eval_with_values(c2), {"SPXS": 1.0})

    def test_mdd_tqqq_200_edge(self) -> None:
        c = _crash_base()
        c[("cumret", "TQQQ", 8)] = -10.0
        c[("mdd", "TQQQ", 200)] = 65.0
        self.assertEqual(_eval_with_values(c), {"SPXS": 1.0})

        c2 = _crash_base()
        c2[("cumret", "TQQQ", 8)] = -10.0
        c2[("mdd", "TQQQ", 200)] = 65.1
        self.assertEqual(_eval_with_values(c2), {"SOXL": 1.0})

    def test_cumret_tqqq_30_edge(self) -> None:
        c = _crash_base()
        c[("stdev", "TQQQ", 14)] = 20.0
        c[("cumret", "TQQQ", 30)] = -10.0
        self.assertEqual(_eval_with_values(c), {"SPXS": 1.0})

        c2 = _crash_base()
        c2[("stdev", "TQQQ", 14)] = 20.0
        c2[("cumret", "TQQQ", 30)] = -9.9
        self.assertEqual(_eval_with_values(c2), {"SOXL": 1 / 3, "TQQQ": 1 / 3, "SPXL": 1 / 3})

    def test_rsi_soxl_32_outer_split_edge(self) -> None:
        n = _normal_base()
        n[("rsi", "SOXL", 32)] = 62.1995
        n[("stdev", "SOXL", 105)] = 4.0
        self.assertEqual(_eval_with_values(n), {"SOXL": 1.0})

        n2 = _normal_base()
        n2[("rsi", "SOXL", 32)] = 62.2
        self.assertEqual(_eval_with_values(n2), {"SOXS": 1.0})

    def test_stdev_soxl_105_edge(self) -> None:
        n = _normal_base()
        n[("stdev", "SOXL", 105)] = 4.9226
        self.assertEqual(_eval_with_values(n), {"SOXL": 1.0})

        n2 = _normal_base()
        n2[("stdev", "SOXL", 105)] = 4.93
        n2[("stdev", "SOXL", 30)] = 6.0
        self.assertEqual(_eval_with_values(n2), {"SOXS": 1.0})

    def test_rsi_soxl_30_edge(self) -> None:
        n = _normal_base()
        n[("rsi", "SOXL", 30)] = 57.49
        n[("stdev", "SOXL", 30)] = 5.0
        self.assertEqual(_eval_with_values(n), {"SOXL": 0.5, "SPXL": 0.5})

        n2 = _normal_base()
        n2[("rsi", "SOXL", 30)] = 57.48
        n2[("cumret", "SOXL", 32)] = -1.0
        n2[("mdd", "SOXL", 250)] = 70.0
        self.assertEqual(_eval_with_values(n2), {"SOXS": 1.0})

    def test_stdev_soxl_30_edge(self) -> None:
        n = _normal_base()
        n[("rsi", "SOXL", 30)] = 60.0
        n[("stdev", "SOXL", 30)] = 5.4135
        self.assertEqual(_eval_with_values(n), {"SOXS": 1.0})

        n2 = _normal_base()
        n2[("rsi", "SOXL", 30)] = 60.0
        n2[("stdev", "SOXL", 30)] = 5.41
        self.assertEqual(_eval_with_values(n2), {"SOXL": 0.5, "SPXL": 0.5})

    def test_cumret_soxl_32_edge(self) -> None:
        n = _normal_base()
        n[("rsi", "SOXL", 30)] = 50.0
        n[("cumret", "SOXL", 32)] = -12.0
        self.assertEqual(_eval_with_values(n), {"SOXL": 1.0})

        n2 = _normal_base()
        n2[("rsi", "SOXL", 30)] = 50.0
        n2[("cumret", "SOXL", 32)] = -11.9
        n2[("mdd", "SOXL", 250)] = 70.0
        self.assertEqual(_eval_with_values(n2), {"SOXS": 1.0})

    def test_mdd_soxl_250_edge(self) -> None:
        n = _normal_base()
        n[("rsi", "SOXL", 30)] = 50.0
        n[("cumret", "SOXL", 32)] = -1.0
        n[("mdd", "SOXL", 250)] = 71.0
        self.assertEqual(_eval_with_values(n), {"SOXS": 1.0})

        n2 = _normal_base()
        n2[("rsi", "SOXL", 30)] = 50.0
        n2[("cumret", "SOXL", 32)] = -1.0
        n2[("mdd", "SOXL", 250)] = 71.1
        self.assertEqual(_eval_with_values(n2), {"SOXL": 1.0})

    def test_rsi_soxl_32_inner_condition_is_unreachable_else(self) -> None:
        for value in [62.2, 63.0, 70.0, 99.0]:
            n = _normal_base()
            n[("rsi", "SOXL", 32)] = value
            self.assertEqual(_eval_with_values(n), {"SOXS": 1.0})

    # Invariants
    def test_weights_sum_to_one_and_non_negative_in_scenarios(self) -> None:
        scenarios = [_crash_base(), _normal_base()]
        for s in scenarios:
            out = _eval_with_values(s)
            self.assertAlmostEqual(sum(out.values()), 1.0, places=12)
            self.assertTrue(all(v >= 0.0 for v in out.values()))

    def test_output_symbol_universe_subset(self) -> None:
        allowed = {"SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "SPXS", "TMF", "TMV"}
        out1 = _eval_with_values(_crash_base())
        out2 = _eval_with_values(_normal_base())
        self.assertTrue(set(out1).issubset(allowed))
        self.assertTrue(set(out2).issubset(allowed))

    def test_weight_values_finite(self) -> None:
        out = _eval_with_values(_normal_base())
        self.assertTrue(all(math.isfinite(v) for v in out.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

