#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.experiment.aggr_v2.candidate_grid import build_phase45_candidates
from composer_original.experiment.aggr_v2.data import load_market_data
from composer_original.experiment.aggr_v2.model_types import BacktestConfigV2, OverlayConfig
from composer_original.experiment.aggr_v2.profiles import get_profile
from composer_original.experiment.aggr_v2.reporting import write_csv, write_json
from composer_original.experiment.aggr_v2.runner_utils import run_window_backtest
from composer_original.experiment.aggr_v2.scenarios import build_default_scenarios
from composer_original.experiment.aggr_v2.validation import summarize_validation
from composer_original.experiment.aggr_v2.wf_search import score_candidates_strict
from composer_original.experiment.aggr_v2.windows import WINDOW_TO_DAYS, resolve_windows


def _parse_day(value: str) -> date:
    return date.fromisoformat(str(value))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 4/5 isolated pipeline: WFO + scenarios + strict acceptance")
    p.add_argument("--source", choices=["fixture_close", "ohlc_csv", "yfinance"], default="fixture_close")
    p.add_argument(
        "--prices-csv",
        default="/home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv",
    )
    p.add_argument("--ohlc-csv", default="")
    p.add_argument("--profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30")
    p.add_argument("--mode", default="paper_live_style_optimistic", choices=["synthetic", "paper_live_style_optimistic", "realistic_close"])
    p.add_argument("--windows", default="1m,2m,3m,6m,1y")
    p.add_argument("--end-day", default="")
    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--rebalance-threshold", type=float, default=0.0)

    p.add_argument("--wf-train-days", type=int, default=252)
    p.add_argument("--wf-test-days", type=int, default=63)
    p.add_argument("--wf-step-days", type=int, default=21)

    p.add_argument(
        "--output-dir",
        default="/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/reports/phase45_pipeline",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Confirm profile exists; this also protects accidental typo runs.
    profile = get_profile(args.profile)

    # Validate windows early.
    window_labels = [w.strip().lower() for w in str(args.windows).split(",") if w.strip()]
    for label in window_labels:
        if label not in WINDOW_TO_DAYS:
            valid = ", ".join(sorted(WINDOW_TO_DAYS))
            raise ValueError(f"Unsupported window '{label}'. Valid windows: {valid}")

    # Resolve end-day and load sufficient history for warmup + WFO horizon.
    max_window_days = max(WINDOW_TO_DAYS[w] for w in window_labels)
    if args.end_day:
        end_day = _parse_day(args.end_day)
    else:
        seed_start = date.today() - timedelta(days=max_window_days + int(args.warmup_days) + 365)
        seed_data = load_market_data(
            prices_csv=Path(args.prices_csv) if args.prices_csv else None,
            ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
            source=args.source,
            start=seed_start,
            end=date.today(),
        )
        end_day = seed_data.days[-1]

    start_for_load = end_day - timedelta(days=max_window_days + int(args.warmup_days) + 365)
    market_data = load_market_data(
        prices_csv=Path(args.prices_csv) if args.prices_csv else None,
        ohlc_csv=Path(args.ohlc_csv) if args.ohlc_csv else None,
        source=args.source,
        start=start_for_load,
        end=end_day,
    )

    windows = resolve_windows(end_day, window_labels)
    scenarios = [s.window for s in build_default_scenarios(market_data)]

    base_cfg = BacktestConfigV2(
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        min_trade_weight_delta=float(args.rebalance_threshold),
        rebalance_threshold=float(args.rebalance_threshold),
        profit_lock_exec_model=str(args.mode),
    )
    base_overlay = OverlayConfig()

    candidates = build_phase45_candidates(base_config=base_cfg, base_overlay=base_overlay)
    scores = score_candidates_strict(
        market_data=market_data,
        profile_name=profile.name,
        candidates=candidates,
        windows=windows,
        scenarios=scenarios,
        wf_train_days=int(args.wf_train_days),
        wf_test_days=int(args.wf_test_days),
        wf_step_days=int(args.wf_step_days),
    )

    # ---- Review pass 1: strict acceptance integrity ----
    review_passes: list[dict[str, object]] = []
    any_baseline = any(s.candidate_name == "baseline" for s in scores)
    review_passes.append(
        {
            "name": "review1_baseline_presence",
            "ok": bool(any_baseline),
            "details": {"candidate_count": len(scores)},
        }
    )

    # ---- Review pass 2: deterministic top candidate rerun ----
    top = scores[0] if scores else None
    if top is not None:
        c_lookup = {c.name: c for c in candidates}
        top_c = c_lookup[top.candidate_name]
        probe_w = windows[-1]
        r1 = run_window_backtest(
            market_data=market_data,
            profile_name=profile.name,
            config=top_c.config,
            overlay=top_c.overlay,
            window=probe_w,
        )
        r2 = run_window_backtest(
            market_data=market_data,
            profile_name=profile.name,
            config=top_c.config,
            overlay=top_c.overlay,
            window=probe_w,
        )
        det_ok = abs(r1.final_equity - r2.final_equity) < 1e-9 and abs(r1.total_return_pct - r2.total_return_pct) < 1e-9
        review_passes.append(
            {
                "name": "review2_top_candidate_determinism",
                "ok": bool(det_ok),
                "details": {
                    "candidate": top_c.name,
                    "window": probe_w.label,
                    "final_equity_a": r1.final_equity,
                    "final_equity_b": r2.final_equity,
                },
            }
        )
    else:
        review_passes.append(
            {
                "name": "review2_top_candidate_determinism",
                "ok": False,
                "details": {"reason": "no_scores"},
            }
        )

    # ---- Review pass 3: accepted candidates truly beat baseline on both gates ----
    baseline = next((s for s in scores if s.candidate_name == "baseline"), None)
    gate_ok = True
    bad: list[str] = []
    if baseline is not None:
        for s in scores:
            if s.candidate_name == "baseline" or not s.accepted:
                continue
            ret_ok = (
                s.window_avg_return_pct > baseline.window_avg_return_pct
                and s.scenario_avg_return_pct > baseline.scenario_avg_return_pct
                and s.wf_avg_return_pct > baseline.wf_avg_return_pct
            )
            risk_ok = (
                s.window_avg_risk_score > baseline.window_avg_risk_score
                and s.scenario_avg_risk_score > baseline.scenario_avg_risk_score
                and s.wf_avg_risk_score > baseline.wf_avg_risk_score
            )
            if not (ret_ok and risk_ok):
                gate_ok = False
                bad.append(s.candidate_name)
    else:
        gate_ok = False
        bad.append("missing_baseline")

    review_passes.append(
        {
            "name": "review3_acceptance_gate_audit",
            "ok": bool(gate_ok),
            "details": {"violations": bad},
        }
    )

    leaderboard_rows = []
    for s in scores:
        leaderboard_rows.append(
            {
                "candidate": s.candidate_name,
                "accepted": s.accepted,
                "acceptance_reason": s.acceptance_reason,
                "overall_score": round(s.overall_score, 6),
                "window_avg_return_pct": round(s.window_avg_return_pct, 6),
                "window_avg_risk_score": round(s.window_avg_risk_score, 6),
                "scenario_avg_return_pct": round(s.scenario_avg_return_pct, 6),
                "scenario_avg_risk_score": round(s.scenario_avg_risk_score, 6),
                "wf_avg_return_pct": round(s.wf_avg_return_pct, 6),
                "wf_avg_risk_score": round(s.wf_avg_risk_score, 6),
            }
        )

    csv_path = out_dir / f"leaderboard_{profile.name}_{args.mode}.csv"
    json_path = out_dir / f"leaderboard_{profile.name}_{args.mode}.json"
    write_csv(csv_path, leaderboard_rows)
    write_json(
        json_path,
        {
            "profile": profile.name,
            "mode": args.mode,
            "source": args.source,
            "config": asdict(base_cfg),
            "windows": [asdict(w) for w in windows],
            "scenarios": [asdict(s) for s in build_default_scenarios(market_data)],
            "review_passes": review_passes,
            "leaderboard": [
                {
                    **asdict(s),
                    # details can be large but are required for audit traceability.
                    "details": s.details,
                }
                for s in scores
            ],
        },
    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    for r in review_passes:
        print(r)
    if leaderboard_rows:
        print("Top candidate:", leaderboard_rows[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
