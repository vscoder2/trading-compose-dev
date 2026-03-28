#!/usr/bin/env python3
"""
Strict realism overlay for indicator gating research.

Research-only utility:
- Reads day-level baseline return stream and prior gating rankings.
- Re-evaluates top-N rules with additional execution frictions:
  1) switch cost on every regime on/off transition
  2) missed-fill penalty on positive active days

No existing runtime/backtest code is modified.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse rule construction from research script.
from indicator_gating_sweep import _build_atomic_rules, _iter_rule_combos  # type: ignore


@dataclass
class StrictResult:
    rule: str
    final_equity: float
    pnl: float
    return_pct: float
    maxdd_pct: float
    switches: int
    active_days: int
    exposure_pct: float
    penalty_cost_usd: float
    penalty_days_missed_fill: int


def _maxdd_pct(equity_curve: np.ndarray) -> float:
    if equity_curve.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity_curve)
    dd = np.where(peaks > 0, (peaks - equity_curve) / peaks, 0.0)
    return float(np.max(dd) * 100.0)


def _simulate_strict(
    daily_ret_pct: np.ndarray,
    mask: np.ndarray,
    initial_equity: float,
    switch_cost_bps: float,
    missed_fill_penalty_bps: float,
) -> StrictResult:
    eq = float(initial_equity)
    curve = []
    switches = 0
    penalty_cost = 0.0
    missed_fill_days = 0
    prev = bool(mask[0]) if mask.size else False

    for i in range(mask.size):
        active = bool(mask[i])
        if i > 0 and active != prev:
            switches += 1
            before = eq
            eq *= 1.0 - (switch_cost_bps / 10000.0)
            penalty_cost += max(0.0, before - eq)
            prev = active

        r = (daily_ret_pct[i] / 100.0) if active else 0.0
        # Missed-fill penalty applied only on positive active days.
        if active and r > 0:
            missed_fill_days += 1
            r = max(-0.99, r - (missed_fill_penalty_bps / 10000.0))

        eq *= 1.0 + r
        curve.append(eq)

    curve_np = np.array(curve, dtype=float)
    final_eq = float(curve_np[-1]) if curve_np.size else float(initial_equity)
    pnl = final_eq - float(initial_equity)
    ret = ((final_eq / float(initial_equity)) - 1.0) * 100.0
    active_days = int(np.sum(mask))
    exposure_pct = (active_days / mask.size * 100.0) if mask.size else 0.0

    return StrictResult(
        rule="",
        final_equity=final_eq,
        pnl=pnl,
        return_pct=ret,
        maxdd_pct=_maxdd_pct(curve_np),
        switches=switches,
        active_days=active_days,
        exposure_pct=exposure_pct,
        penalty_cost_usd=penalty_cost,
        penalty_days_missed_fill=missed_fill_days,
    )


def run(args: argparse.Namespace) -> int:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    day = pd.read_csv(args.daylevel_csv)
    day = day.sort_values("date").reset_index(drop=True)
    ret = day["cpu_return_pct"].to_numpy(dtype=float)

    # Build all masks from the same atomic construction logic.
    atoms = _build_atomic_rules(day)
    mask_by_rule: dict[str, np.ndarray] = {}
    for name, mask, _k in _iter_rule_combos(atoms, max_k=args.max_rule_size):
        mask_by_rule[name] = mask

    ranked = pd.read_csv(args.ranked_csv).sort_values(["pnl", "return_pct"], ascending=[False, False])
    top_rules = (
        ranked[ranked["rule"] != "BASELINE_ALWAYS_ON"]
        .head(args.top_n)["rule"]
        .astype(str)
        .tolist()
    )

    eval_rules = ["BASELINE_ALWAYS_ON"] + top_rules
    rows: list[dict[str, object]] = []
    for rule in eval_rules:
        if rule not in mask_by_rule:
            continue
        sim = _simulate_strict(
            daily_ret_pct=ret,
            mask=mask_by_rule[rule],
            initial_equity=float(args.initial_equity),
            switch_cost_bps=float(args.switch_cost_bps),
            missed_fill_penalty_bps=float(args.missed_fill_penalty_bps),
        )
        base_row = ranked[ranked["rule"] == rule]
        orig_final = float(base_row["final_equity"].iloc[0]) if not base_row.empty else np.nan
        orig_pnl = float(base_row["pnl"].iloc[0]) if not base_row.empty else np.nan
        orig_ret = float(base_row["return_pct"].iloc[0]) if not base_row.empty else np.nan
        orig_dd = float(base_row["maxdd_pct"].iloc[0]) if not base_row.empty else np.nan

        rows.append(
            {
                "rule": rule,
                "orig_final_equity": orig_final,
                "orig_pnl": orig_pnl,
                "orig_return_pct": orig_ret,
                "orig_maxdd_pct": orig_dd,
                "strict_final_equity": sim.final_equity,
                "strict_pnl": sim.pnl,
                "strict_return_pct": sim.return_pct,
                "strict_maxdd_pct": sim.maxdd_pct,
                "switches": sim.switches,
                "active_days": sim.active_days,
                "exposure_pct": sim.exposure_pct,
                "penalty_cost_usd": sim.penalty_cost_usd,
                "penalty_days_missed_fill": sim.penalty_days_missed_fill,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError("No strict-eval rows produced.")

    base = out[out["rule"] == "BASELINE_ALWAYS_ON"].iloc[0]
    out["delta_strict_pnl_vs_baseline"] = out["strict_pnl"] - float(base["strict_pnl"])
    out["delta_strict_return_pct_vs_baseline"] = out["strict_return_pct"] - float(base["strict_return_pct"])

    out = out.sort_values(["strict_pnl", "strict_return_pct"], ascending=[False, False]).reset_index(drop=True)
    out_csv = out_dir / "strict_eval_top_rules.csv"
    out.to_csv(out_csv, index=False)

    meta = {
        "daylevel_csv": str(args.daylevel_csv),
        "ranked_csv": str(args.ranked_csv),
        "initial_equity": float(args.initial_equity),
        "top_n": int(args.top_n),
        "max_rule_size": int(args.max_rule_size),
        "switch_cost_bps": float(args.switch_cost_bps),
        "missed_fill_penalty_bps": float(args.missed_fill_penalty_bps),
        "output_csv": str(out_csv),
    }
    (out_dir / "strict_eval_meta.json").write_text(json.dumps(meta, indent=2))

    print(json.dumps(meta, indent=2))
    print("STRICT_TOP")
    print(
        out[
            [
                "rule",
                "orig_pnl",
                "strict_pnl",
                "delta_strict_pnl_vs_baseline",
                "orig_maxdd_pct",
                "strict_maxdd_pct",
                "switches",
                "penalty_cost_usd",
            ]
        ].to_csv(index=False)
    )
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strict realism overlay for top indicator-gating rules.")
    p.add_argument("--daylevel-csv", type=Path, required=True)
    p.add_argument("--ranked-csv", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--max-rule-size", type=int, default=3, choices=[1, 2, 3])
    p.add_argument("--switch-cost-bps", type=float, default=7.0)
    p.add_argument("--missed-fill-penalty-bps", type=float, default=10.0)
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))

