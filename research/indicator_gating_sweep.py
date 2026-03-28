#!/usr/bin/env python3
"""
Research-only indicator gating sweep.

This tool does NOT modify existing runtime/backtest code.
It evaluates timing overlays on a baseline day-level return stream.

Assumption:
- Daily returns in input CSV are already net of slippage/fees from the chosen
  baseline run, so costs remain fixed from that baseline.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class RuleMask:
    name: str
    mask: np.ndarray


def _max_drawdown_pct(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    dd = np.where(peaks > 0, (peaks - equity) / peaks, 0.0)
    return float(np.max(dd) * 100.0)


def _build_atomic_rules(df: pd.DataFrame) -> list[RuleMask]:
    q40 = float(df["SOXL_atr_pct_lag1"].quantile(0.40))
    q50 = float(df["SOXL_atr_pct_lag1"].quantile(0.50))
    q60 = float(df["SOXL_atr_pct_lag1"].quantile(0.60))

    atoms: list[RuleMask] = []

    def add(name: str, cond: pd.Series) -> None:
        atoms.append(RuleMask(name=name, mask=cond.fillna(False).to_numpy(dtype=bool)))

    # RSI regimes
    for t in (35, 40, 45):
        add(f"SOXL_RSI14_lag1_lt_{t}", df["SOXL_rsi14_lag1"] < t)
    for t in (55, 60, 65):
        add(f"SOXS_RSI14_lag1_gt_{t}", df["SOXS_rsi14_lag1"] > t)

    # Volume activity regimes
    for z in (0.5, 1.0, 1.5):
        add(f"SOXL_VolZ20_lag1_gt_{z}", df["SOXL_vol_z20_lag1"] > z)
    for z in (0.5, 1.0, 1.5):
        add(f"SOXS_VolZ20_lag1_gt_{z}", df["SOXS_vol_z20_lag1"] > z)

    # Volatility regimes
    add("SOXL_ATRpct_lag1_gt_q40", df["SOXL_atr_pct_lag1"] > q40)
    add("SOXL_ATRpct_lag1_gt_q50", df["SOXL_atr_pct_lag1"] > q50)
    add("SOXL_ATRpct_lag1_gt_q60", df["SOXL_atr_pct_lag1"] > q60)

    # Trend/mean-reversion regimes
    add("SOXL_MACD_hist_lag1_lt_0", df["SOXL_macd_hist_lag1"] < 0.0)
    add("SOXL_MACD_hist_lag1_gt_0", df["SOXL_macd_hist_lag1"] > 0.0)
    add("SOXL_EMA20_lt_EMA50_lag1", df["SOXL_ema20_gt_50_lag1"] == False)  # noqa: E712
    add("SOXL_Px_lt_EMA20_lag1", df["SOXL_px_gt_ema20_lag1"] == False)  # noqa: E712

    # Prior-day move regimes
    for r in (-0.01, -0.02, -0.03):
        add(f"SOXL_ret1_lag1_lt_{r}", df["SOXL_ret1_lag1"] < r)
    for r in (0.01, 0.02):
        add(f"SOXS_ret1_lag1_gt_{r}", df["SOXS_ret1_lag1"] > r)

    return atoms


def _simulate_rule(
    daily_ret_pct: np.ndarray,
    rule_mask: np.ndarray,
    initial_equity: float,
) -> dict[str, float]:
    gross = np.where(rule_mask, 1.0 + (daily_ret_pct / 100.0), 1.0)
    equity = np.empty_like(gross, dtype=float)
    cur = float(initial_equity)
    for i, g in enumerate(gross):
        cur *= float(g)
        equity[i] = cur
    final_equity = float(equity[-1]) if equity.size else float(initial_equity)
    pnl = final_equity - float(initial_equity)
    ret = ((final_equity / float(initial_equity)) - 1.0) * 100.0
    maxdd = _max_drawdown_pct(equity if equity.size else np.array([initial_equity], dtype=float))

    active_days = int(np.sum(rule_mask))
    exposure_pct = (active_days / len(rule_mask) * 100.0) if len(rule_mask) else 0.0
    wins = int(np.sum((daily_ret_pct > 0) & rule_mask))
    win_rate = (wins / active_days * 100.0) if active_days else 0.0
    switches = int(np.sum(np.diff(rule_mask.astype(int)) != 0)) if len(rule_mask) > 1 else 0

    return {
        "final_equity": final_equity,
        "pnl": pnl,
        "return_pct": ret,
        "maxdd_pct": maxdd,
        "active_days": active_days,
        "exposure_pct": exposure_pct,
        "win_rate_when_active_pct": win_rate,
        "switches": switches,
    }


def _iter_rule_combos(atoms: list[RuleMask], max_k: int) -> Iterable[tuple[str, np.ndarray, int]]:
    # Baseline always-on control
    if atoms:
        n = len(atoms[0].mask)
        yield ("BASELINE_ALWAYS_ON", np.ones(n, dtype=bool), 0)

    # Single, pair, triple (up to max_k)
    for k in range(1, max_k + 1):
        for combo in itertools.combinations(atoms, k):
            name = " & ".join(c.name for c in combo)
            mask = combo[0].mask.copy()
            for c in combo[1:]:
                mask &= c.mask
            yield (name, mask, k)


def run(args: argparse.Namespace) -> int:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input_csv)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    if "cpu_return_pct" not in df.columns:
        raise RuntimeError("Input CSV must include 'cpu_return_pct'.")

    daily_ret = df["cpu_return_pct"].to_numpy(dtype=float)
    atoms = _build_atomic_rules(df)

    rows: list[dict[str, object]] = []
    for rule_name, mask, k in _iter_rule_combos(atoms, max_k=args.max_rule_size):
        sim = _simulate_rule(daily_ret, mask, initial_equity=args.initial_equity)
        if sim["active_days"] < args.min_active_days and rule_name != "BASELINE_ALWAYS_ON":
            continue
        rows.append(
            {
                "rule": rule_name,
                "rule_size": k,
                **sim,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("No rules survived filters.")

    base = out[out["rule"] == "BASELINE_ALWAYS_ON"].iloc[0]
    out["delta_pnl_vs_baseline"] = out["pnl"] - float(base["pnl"])
    out["delta_return_pct_vs_baseline"] = out["return_pct"] - float(base["return_pct"])
    out["delta_maxdd_pct_vs_baseline"] = out["maxdd_pct"] - float(base["maxdd_pct"])
    out["pnl_per_maxdd"] = np.where(out["maxdd_pct"] > 0, out["pnl"] / out["maxdd_pct"], np.nan)

    ranked = out.sort_values(["pnl", "return_pct"], ascending=[False, False]).reset_index(drop=True)
    top = ranked.head(args.top_n).copy()

    ranked_csv = out_dir / "indicator_gating_ranked.csv"
    top_csv = out_dir / "indicator_gating_top.csv"
    ranked.to_csv(ranked_csv, index=False)
    top.to_csv(top_csv, index=False)

    meta = {
        "input_csv": str(args.input_csv),
        "rows_in_input": int(len(df)),
        "rules_tested": int(len(ranked)),
        "max_rule_size": int(args.max_rule_size),
        "min_active_days": int(args.min_active_days),
        "initial_equity": float(args.initial_equity),
        "outputs": {
            "ranked_csv": str(ranked_csv),
            "top_csv": str(top_csv),
        },
        "baseline": {
            "final_equity": float(base["final_equity"]),
            "pnl": float(base["pnl"]),
            "return_pct": float(base["return_pct"]),
            "maxdd_pct": float(base["maxdd_pct"]),
        },
    }
    (out_dir / "indicator_gating_meta.json").write_text(json.dumps(meta, indent=2))

    # concise terminal output
    print(json.dumps(meta, indent=2))
    print("TOP_PREVIEW")
    print(
        top[
            [
                "rule",
                "rule_size",
                "active_days",
                "exposure_pct",
                "final_equity",
                "pnl",
                "return_pct",
                "maxdd_pct",
                "delta_pnl_vs_baseline",
            ]
        ].to_csv(index=False)
    )
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Indicator gating sweep on baseline daily returns (research-only).")
    p.add_argument("--input-csv", type=Path, required=True, help="Day-level CSV with cpu_return_pct and lagged indicators")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--max-rule-size", type=int, default=3, choices=[1, 2, 3])
    p.add_argument("--min-active-days", type=int, default=20)
    p.add_argument("--top-n", type=int, default=20)
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))

