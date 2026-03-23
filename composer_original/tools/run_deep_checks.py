#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
COMPOSER_ORIGINAL_DIR = ROOT / "composer_original"
ORIGINAL_CLJ = COMPOSER_ORIGINAL_DIR / "files" / "composer_original_file.txt"
PY_TREE = ROOT / "soxl_growth" / "composer_port" / "symphony_soxl_growth_v245_rl.py"
REPORTS_DIR = COMPOSER_ORIGINAL_DIR / "reports"
FIXTURES_DIR = COMPOSER_ORIGINAL_DIR / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import soxl_growth.composer_port.symphony_soxl_growth_v245_rl as symphony
from soxl_growth.backtest.engine import run_backtest
from soxl_growth.composer_port.tree import DictContext
from soxl_growth.config import BacktestConfig


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict[str, Any]


def _extract_clj_conditions(source: str) -> set[tuple[str, str, int, str, float]]:
    pattern = re.compile(
        r"\((<=|>=)\s+\((max-drawdown|stdev-return|cumulative-return|rsi)\s+\"([A-Z]+)\"\s+\{:window\s+(\d+)\}\)\s+(-?\d+(?:\.\d+)?)\)"
    )
    out: set[tuple[str, str, int, str, float]] = set()
    for op, metric, symbol, window, value in pattern.findall(source):
        out.add((metric, symbol, int(window), op, float(value)))
    return out


def _extract_python_conditions(source: str) -> set[tuple[str, str, int, str, float]]:
    patterns = [
        (re.compile(r"_mdd\(ctx,\s*\"([A-Z]+)\",\s*(\d+)\)\s*(<=|>=)\s*(-?\d+(?:\.\d+)?)"), "max-drawdown"),
        (re.compile(r"_stdev\(ctx,\s*\"([A-Z]+)\",\s*(\d+)\)\s*(<=|>=)\s*(-?\d+(?:\.\d+)?)"), "stdev-return"),
        (re.compile(r"_cumret\(ctx,\s*\"([A-Z]+)\",\s*(\d+)\)\s*(<=|>=)\s*(-?\d+(?:\.\d+)?)"), "cumulative-return"),
        (re.compile(r"rsi_metric\(ctx,\s*\"([A-Z]+)\",\s*(\d+)\)\s*(<=|>=)\s*(-?\d+(?:\.\d+)?)"), "rsi"),
    ]
    out: set[tuple[str, str, int, str, float]] = set()
    for pattern, metric in patterns:
        for symbol, window, op, value in pattern.findall(source):
            out.add((metric, symbol, int(window), op, float(value)))
    return out


def check_1_threshold_parity() -> CheckResult:
    clj_source = ORIGINAL_CLJ.read_text(encoding="utf-8")
    py_source = PY_TREE.read_text(encoding="utf-8")
    clj_conditions = _extract_clj_conditions(clj_source)
    py_conditions = _extract_python_conditions(py_source)

    # Compare as rounded tuples to avoid floating literal formatting noise.
    clj_rounded = {(m, s, w, op, round(v, 6)) for (m, s, w, op, v) in clj_conditions}
    py_rounded = {(m, s, w, op, round(v, 6)) for (m, s, w, op, v) in py_conditions}

    missing_in_python = sorted(clj_rounded - py_rounded)
    extra_in_python = sorted(py_rounded - clj_rounded)

    passed = (len(missing_in_python) == 0) and (len(extra_in_python) == 0)
    return CheckResult(
        name="check_1_threshold_parity",
        passed=passed,
        details={
            "clj_condition_count": len(clj_rounded),
            "python_condition_count": len(py_rounded),
            "missing_in_python": missing_in_python,
            "extra_in_python": extra_in_python,
        },
    )


class MetricContext:
    def __init__(self, values: dict[tuple[str, str, int], float]) -> None:
        self.values = values

    def close_series(self, symbol: str):
        # Not used because metrics are patched, but keeps type contract.
        return [100.0, 101.0, 102.0]


def _eval_with_values(values: dict[tuple[str, str, int], float]) -> dict[str, float]:
    ctx = MetricContext(values)
    with (
        patch.object(symphony, "_mdd", side_effect=lambda c, s, w: c.values[("mdd", s, w)]),
        patch.object(symphony, "_stdev", side_effect=lambda c, s, w: c.values[("stdev", s, w)]),
        patch.object(symphony, "_cumret", side_effect=lambda c, s, w: c.values[("cumret", s, w)]),
        patch.object(symphony, "_rsi", side_effect=lambda c, s, w: c.values[("rsi", s, w)]),
    ):
        return symphony.evaluate_strategy(ctx, tree=symphony.build_tree())


def check_2_branch_coverage() -> CheckResult:
    cases: list[tuple[str, dict[tuple[str, str, int], float], dict[str, float]]] = [
        (
            "crash_lowvol_top2_growth",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 10,
                ("stdev", "TQQQ", 100): 3.0,
                ("cumret", "SOXL", 21): 20,
                ("cumret", "TQQQ", 21): 10,
                ("cumret", "SPXL", 21): 5,
            },
            {"SOXL": 0.5, "TQQQ": 0.5},
        ),
        (
            "crash_rsi_high_stdev30_high",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 10,
                ("stdev", "TQQQ", 100): 10,
                ("rsi", "TQQQ", 30): 55,
                ("stdev", "TQQQ", 30): 6.0,
            },
            {"SOXS": 1.0},
        ),
        (
            "crash_rsi_high_stdev30_low",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 10,
                ("stdev", "TQQQ", 100): 10,
                ("rsi", "TQQQ", 30): 55,
                ("stdev", "TQQQ", 30): 5.0,
            },
            {"SPXL": 1.0},
        ),
        (
            "crash_rsi_low_cum8_deep_drop",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 10,
                ("stdev", "TQQQ", 100): 10,
                ("rsi", "TQQQ", 30): 40,
                ("cumret", "TQQQ", 8): -21,
            },
            {"SOXL": 1.0},
        ),
        (
            "crash_rsi_low_hedge_duplicate_spxs",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 10,
                ("stdev", "TQQQ", 100): 10,
                ("rsi", "TQQQ", 30): 40,
                ("cumret", "TQQQ", 8): -5,
                ("mdd", "TQQQ", 200): 60,
                ("cumret", "TMV", 3): 2,
                ("cumret", "SQQQ", 3): 1,
                ("cumret", "SPXS", 3): -5,
            },
            {"SPXS": 1.0},
        ),
        (
            "crash_highvol_cum30_down",
            {
                ("mdd", "SOXL", 60): 55,
                ("stdev", "TQQQ", 14): 25,
                ("cumret", "TQQQ", 30): -11,
                ("cumret", "TMV", 3): 2,
                ("cumret", "SQQQ", 3): 1,
                ("cumret", "SPXS", 3): -5,
            },
            {"SPXS": 1.0},
        ),
        (
            "normal_calm_direct_soxl",
            {
                ("mdd", "SOXL", 60): 10,
                ("rsi", "SOXL", 32): 55,
                ("stdev", "SOXL", 105): 4.0,
            },
            {"SOXL": 1.0},
        ),
        (
            "normal_rsi30_hot_stdev30_hot",
            {
                ("mdd", "SOXL", 60): 10,
                ("rsi", "SOXL", 32): 55,
                ("stdev", "SOXL", 105): 6.0,
                ("rsi", "SOXL", 30): 60,
                ("stdev", "SOXL", 30): 6.0,
            },
            {"SOXS": 1.0},
        ),
        (
            "normal_rsi30_hot_stdev30_cool_top2",
            {
                ("mdd", "SOXL", 60): 10,
                ("rsi", "SOXL", 32): 55,
                ("stdev", "SOXL", 105): 6.0,
                ("rsi", "SOXL", 30): 60,
                ("stdev", "SOXL", 30): 5.0,
                ("cumret", "SOXL", 21): 4,
                ("cumret", "SPXL", 21): 3,
                ("cumret", "TQQQ", 21): 2,
            },
            {"SOXL": 0.5, "SPXL": 0.5},
        ),
        (
            "normal_rsi30_cool_mdd250_le71",
            {
                ("mdd", "SOXL", 60): 10,
                ("rsi", "SOXL", 32): 55,
                ("stdev", "SOXL", 105): 6.0,
                ("rsi", "SOXL", 30): 50,
                ("cumret", "SOXL", 32): 0,
                ("mdd", "SOXL", 250): 70,
            },
            {"SOXS": 1.0},
        ),
        (
            "normal_overbought_rsi32_gt622",
            {
                ("mdd", "SOXL", 60): 10,
                ("rsi", "SOXL", 32): 70,
            },
            {"SOXS": 1.0},
        ),
    ]

    failures: list[dict[str, Any]] = []
    for case_name, values, expected in cases:
        got = _eval_with_values(values)
        if got != expected:
            failures.append({"case": case_name, "expected": expected, "actual": got})

    # Deeper check: prove "normal_rsi32_gt else top3_growth" path is unreachable.
    # With top-level entry condition (rsi32 > 62.1995), inner condition (rsi32 >= 50) is always true.
    unreachable_path_confirmed = True
    for rsi32 in [62.2, 63.0, 70.0, 99.0]:
        got = _eval_with_values({("mdd", "SOXL", 60): 10, ("rsi", "SOXL", 32): rsi32})
        if got != {"SOXS": 1.0}:
            unreachable_path_confirmed = False
            failures.append({"case": "unreachable_path_proof", "rsi32": rsi32, "actual": got})

    return CheckResult(
        name="check_2_branch_coverage",
        passed=(len(failures) == 0) and unreachable_path_confirmed,
        details={
            "scenario_count": len(cases),
            "failures": failures,
            "unreachable_path_confirmed": unreachable_path_confirmed,
        },
    )


def _build_price_history(days: int = 420) -> dict[str, list[tuple[date, float]]]:
    symbols = ["SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "SPXS", "TMF", "TMV"]
    drift = {
        "SOXL": 0.0018,
        "SOXS": -0.0014,
        "TQQQ": 0.0012,
        "SQQQ": -0.0010,
        "SPXL": 0.0008,
        "SPXS": -0.0006,
        "TMF": 0.0002,
        "TMV": -0.0002,
    }
    start = date(2024, 1, 1)
    out: dict[str, list[tuple[date, float]]] = {s: [] for s in symbols}
    for symbol in symbols:
        px = 100.0
        d = start
        while len(out[symbol]) < days:
            if d.weekday() < 5:
                px *= 1.0 + drift[symbol]
                out[symbol].append((d, round(px, 6)))
            d += timedelta(days=1)
    return out


def _write_wide_csv(path: Path, history: dict[str, list[tuple[date, float]]]) -> None:
    symbols = sorted(history)
    rows = len(next(iter(history.values())))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date"] + symbols)
        for i in range(rows):
            day = history[symbols[0]][i][0].isoformat()
            prices = [history[s][i][1] for s in symbols]
            writer.writerow([day] + prices)


def check_3_backtest_invariants() -> CheckResult:
    history = _build_price_history(days=420)
    cfg = BacktestConfig(initial_equity=100_000.0, warmup_days=260, slippage_bps=1.0, sell_fee_bps=0.0)
    result = run_backtest(history, cfg)

    expected_symbols = {"SOXL", "SOXS", "TQQQ", "SQQQ", "SPXL", "SPXS", "TMF", "TMV"}
    bad_alloc_days: list[dict[str, Any]] = []
    for day, weights in result.allocations:
        weight_sum = sum(weights.values())
        if (not math.isfinite(weight_sum)) or abs(weight_sum - 1.0) > 1e-9:
            bad_alloc_days.append({"day": day.isoformat(), "sum": weight_sum, "weights": weights})
            continue
        if any((w < -1e-12) for w in weights.values()):
            bad_alloc_days.append({"day": day.isoformat(), "negative_weights": weights})
            continue
        unknown = set(weights) - expected_symbols
        if unknown:
            bad_alloc_days.append({"day": day.isoformat(), "unknown_symbols": sorted(unknown), "weights": weights})

    bad_trades = [
        asdict(t)
        for t in result.trades
        if (t.qty <= 0) or (t.price <= 0) or (not math.isfinite(t.notional)) or (not math.isfinite(t.fee))
    ]
    passed = (
        len(result.equity_curve) > 0
        and len(result.allocations) > 0
        and result.final_equity > 0
        and math.isfinite(result.total_return_pct)
        and (len(bad_alloc_days) == 0)
        and (len(bad_trades) == 0)
    )

    return CheckResult(
        name="check_3_backtest_invariants",
        passed=passed,
        details={
            "equity_points": len(result.equity_curve),
            "allocation_days": len(result.allocations),
            "trade_count": len(result.trades),
            "final_equity": result.final_equity,
            "total_return_pct": result.total_return_pct,
            "bad_allocation_days": bad_alloc_days[:10],
            "bad_trade_count": len(bad_trades),
            "bad_trade_sample": bad_trades[:3],
        },
    )


def check_4_cli_roundtrip() -> CheckResult:
    history = _build_price_history(days=420)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = FIXTURES_DIR / "deep_check_prices.csv"
    _write_wide_csv(csv_path, history)

    cmd = [
        "python3",
        "-m",
        "soxl_growth",
        "backtest",
        "--prices-csv",
        str(csv_path),
        "--initial-equity",
        "100000",
        "--warmup-days",
        "260",
        "--slippage-bps",
        "1.0",
        "--sell-fee-bps",
        "0.0",
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    parsed: dict[str, Any] = {}
    parse_error = ""
    if proc.returncode == 0:
        try:
            parsed = json.loads(stdout)
        except Exception as exc:  # pragma: no cover
            parse_error = str(exc)

    required_keys = {
        "avg_daily_return_pct",
        "cagr_pct",
        "final_equity",
        "max_drawdown_pct",
        "total_return_pct",
        "trade_count",
    }
    missing_keys = sorted(required_keys - set(parsed))
    numeric_ok = all(math.isfinite(float(parsed[k])) for k in required_keys if k in parsed)

    passed = (proc.returncode == 0) and (parse_error == "") and (len(missing_keys) == 0) and numeric_ok
    return CheckResult(
        name="check_4_cli_roundtrip",
        passed=passed,
        details={
            "returncode": proc.returncode,
            "missing_keys": missing_keys,
            "numeric_ok": numeric_ok,
            "parse_error": parse_error,
            "stdout": stdout,
            "stderr": stderr,
            "csv_fixture": str(csv_path),
        },
    )


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    checks = [
        check_1_threshold_parity(),
        check_2_branch_coverage(),
        check_3_backtest_invariants(),
        check_4_cli_roundtrip(),
    ]
    report = {
        "root": str(ROOT),
        "composer_original_dir": str(COMPOSER_ORIGINAL_DIR),
        "check_count": len(checks),
        "passed_count": sum(1 for c in checks if c.passed),
        "failed_count": sum(1 for c in checks if not c.passed),
        "checks": [asdict(c) for c in checks],
    }
    out_json = REPORTS_DIR / "deep_checks_report.json"
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Deep Check Report: Composer Original v2.4.5 RL",
        "",
        f"- Check count: {report['check_count']}",
        f"- Passed: {report['passed_count']}",
        f"- Failed: {report['failed_count']}",
        "",
    ]
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"## {c.name} - {status}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(c.details, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    out_md = REPORTS_DIR / "deep_checks_report.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
