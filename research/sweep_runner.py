#!/usr/bin/env python3
"""Parallel research sweep for intraday_profit_lock_verification.

This script intentionally writes only inside the `research/` folder.
It does not modify any existing project code.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gpu_native_daily_batch import run_gpu_native_daily_batch


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "composer_original" / "tools" / "intraday_profit_lock_verification.py"
PYTHON_BIN = ROOT / "composer_original" / ".venv" / "bin" / "python"
DEFAULT_ENV_FILE = ROOT / ".env.dev"
DEFAULT_REPORT_ROOT = ROOT / "research" / "reports"


def _load_grid(path: Path) -> dict[str, list[Any]]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Grid config must be a JSON object of key -> list.")
    grid: dict[str, list[Any]] = {}
    for key, value in data.items():
        if isinstance(value, list):
            grid[key] = value
        else:
            grid[key] = [value]
    return grid


def _expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    combos: list[dict[str, Any]] = []
    for row in itertools.product(*values):
        combos.append(dict(zip(keys, row)))
    return combos


def _bool_flag(value: bool, true_flag: str, false_flag: str) -> list[str]:
    return [true_flag if bool(value) else false_flag]


def _build_cmd(
    *,
    combo: dict[str, Any],
    idx: int,
    args: argparse.Namespace,
    run_dir: Path,
) -> tuple[list[str], str]:
    prefix = f"sweep_{idx:05d}"

    cmd = [
        str(PYTHON_BIN),
        str(VERIFIER),
        "--env-file",
        str(args.env_file),
        "--mode",
        args.mode,
        "--strategy-profile",
        str(combo.get("strategy_profile", args.strategy_profile_default)),
        "--profit-lock-exec-model",
        str(combo.get("profit_lock_exec_model", args.profit_lock_exec_model_default)),
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--data-feed",
        args.data_feed,
        "--initial-equity",
        str(args.initial_equity),
        "--initial-principal",
        str(args.initial_principal),
        "--warmup-days",
        str(combo.get("warmup_days", args.warmup_days_default)),
        "--daily-lookback-days",
        str(combo.get("daily_lookback_days", args.daily_lookback_days_default)),
        "--slippage-bps",
        str(combo.get("slippage_bps", args.slippage_bps_default)),
        "--sell-fee-bps",
        str(combo.get("sell_fee_bps", args.sell_fee_bps_default)),
        "--rebalance-time-ny",
        str(combo.get("rebalance_time_ny", args.rebalance_time_ny_default)),
        "--runtime-profit-lock-order-type",
        str(combo.get("runtime_profit_lock_order_type", args.runtime_profit_lock_order_type_default)),
        "--runtime-stop-price-offset-bps",
        str(combo.get("runtime_stop_price_offset_bps", args.runtime_stop_price_offset_bps_default)),
        "--reports-dir",
        str(run_dir),
        "--output-prefix",
        prefix,
    ]

    if args.env_override:
        cmd.append("--env-override")

    cmd.extend(
        _bool_flag(
            bool(combo.get("anchor_window_start_equity", args.anchor_window_start_equity_default)),
            "--anchor-window-start-equity",
            "--no-anchor-window-start-equity",
        )
    )
    cmd.extend(
        _bool_flag(
            bool(combo.get("daily_synthetic_parity", args.daily_synthetic_parity_default)),
            "--daily-synthetic-parity",
            "--no-daily-synthetic-parity",
        )
    )
    cmd.extend(
        _bool_flag(
            bool(
                combo.get(
                    "daily_synthetic_parity_split_adjustment",
                    args.daily_synthetic_parity_split_adjustment_default,
                )
            ),
            "--daily-synthetic-parity-split-adjustment",
            "--no-daily-synthetic-parity-split-adjustment",
        )
    )
    cmd.extend(
        _bool_flag(
            bool(combo.get("paper_live_style_daily_synth_profile", args.paper_live_style_daily_synth_profile_default)),
            "--paper-live-style-daily-synth-profile",
            "--no-paper-live-style-daily-synth-profile",
        )
    )

    return cmd, prefix


def _find_summary_json(run_dir: Path, prefix: str) -> Path | None:
    candidates = sorted(run_dir.glob(f"{prefix}_*.json"))
    return candidates[-1] if candidates else None


def _run_one(
    *,
    combo: dict[str, Any],
    idx: int,
    args: argparse.Namespace,
    run_dir: Path,
) -> dict[str, Any]:
    cmd, prefix = _build_cmd(combo=combo, idx=idx, args=args, run_dir=run_dir)

    attempts = max(1, int(args.retries))
    last_error = ""

    for attempt in range(1, attempts + 1):
        start = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start

        if proc.returncode == 0:
            summary_path = _find_summary_json(run_dir, prefix)
            if not summary_path:
                last_error = "summary json not found"
            else:
                summary = json.loads(summary_path.read_text())
                cpu = summary["cpu"]
                gpu = summary["gpu"]
                init_eq = float(summary["initial_equity"])
                return {
                    "ok": True,
                    "idx": idx,
                    "attempt": attempt,
                    "elapsed_sec": round(elapsed, 3),
                    "window": f"{summary['window']['start_date']} to {summary['window']['end_date']}",
                    "cpu_final_equity": float(cpu["final_equity"]),
                    "cpu_return_pct": float(cpu["total_return_pct"]),
                    "cpu_pnl": float(cpu["final_equity"]) - init_eq,
                    "cpu_maxdd_pct": float(cpu["max_drawdown_pct"]),
                    "gpu_final_equity": float(gpu["final_equity"]),
                    "gpu_return_pct": float(gpu["total_return_pct"]),
                    "gpu_pnl": float(gpu["final_equity"]) - init_eq,
                    "gpu_maxdd_pct": float(gpu["max_drawdown_pct"]),
                    "cpu_gpu_diff_bps": float(summary["parity"]["final_equity_diff_bps_vs_cpu"]),
                    "gpu_backend": str(gpu.get("backend", "")),
                    "summary_json": str(summary_path),
                    **combo,
                }
        else:
            tail = (proc.stderr or proc.stdout or "").strip()
            last_error = tail[-1500:]

        if attempt < attempts:
            time.sleep(float(args.retry_backoff_sec) * attempt)

    return {
        "ok": False,
        "idx": idx,
        "error": last_error,
        **combo,
    }


def _score(df: pd.DataFrame, dd_weight: float) -> pd.DataFrame:
    out = df.copy()
    out["cpu_score"] = out["cpu_return_pct"] - dd_weight * out["cpu_maxdd_pct"]
    out["gpu_score"] = out["gpu_return_pct"] - dd_weight * out["gpu_maxdd_pct"]
    out["combined_score"] = (out["cpu_score"] + out["gpu_score"]) / 2.0
    return out.sort_values("combined_score", ascending=False).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parallel parameter sweep research runner")
    p.add_argument("--grid-config", type=Path, required=True, help="JSON grid config")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--initial-equity", type=float, default=10000.0)
    p.add_argument("--initial-principal", type=float, default=None)
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    p.add_argument("--env-override", action="store_true", default=True)
    p.add_argument("--no-env-override", dest="env_override", action="store_false")

    p.add_argument("--max-workers", type=int, default=max(1, os.cpu_count() or 1))
    p.add_argument(
        "--engine",
        choices=["verifier_subprocess", "gpu_native_daily_batch"],
        default="verifier_subprocess",
        help=(
            "Execution engine. verifier_subprocess runs one verifier process per combo. "
            "gpu_native_daily_batch runs vectorized daily-synthetic GPU/CPU batch simulation in one process."
        ),
    )
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--retry-backoff-sec", type=float, default=2.0)
    p.add_argument("--dd-weight", type=float, default=0.5)
    p.add_argument("--top-n", type=int, default=20)

    p.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    p.add_argument("--run-name", default="")

    # Defaults used when key is omitted from grid
    p.add_argument("--strategy-profile-default", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--profit-lock-exec-model-default", default="paper_live_style_optimistic")
    p.add_argument("--runtime-profit-lock-order-type-default", default="market_order")
    p.add_argument("--rebalance-time-ny-default", default="15:55")
    p.add_argument("--warmup-days-default", type=int, default=260)
    p.add_argument("--daily-lookback-days-default", type=int, default=800)
    p.add_argument("--slippage-bps-default", type=float, default=1.0)
    p.add_argument("--sell-fee-bps-default", type=float, default=1.0)
    p.add_argument("--runtime-stop-price-offset-bps-default", type=float, default=2.0)
    p.add_argument("--rebalance-min-trade-weight-delta-default", type=float, default=0.0)
    p.add_argument("--anchor-window-start-equity-default", action="store_true", default=False)
    p.add_argument("--daily-synthetic-parity-default", action="store_true", default=False)
    p.add_argument("--daily-synthetic-parity-split-adjustment-default", action="store_true", default=False)
    p.add_argument("--paper-live-style-daily-synth-profile-default", action="store_true", default=False)

    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.initial_principal is None:
        args.initial_principal = args.initial_equity

    grid = _load_grid(args.grid_config)
    combos = _expand_grid(grid)
    if not combos:
        raise RuntimeError("No parameter combinations found.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name.strip() or f"sweep_{stamp}"
    run_dir = args.report_root / run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "run_name": run_name,
        "created_at": datetime.now().isoformat(),
        "grid_config": str(args.grid_config),
        "engine": str(args.engine),
        "combo_count": len(combos),
        "max_workers": int(args.max_workers),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_equity": float(args.initial_equity),
        "initial_principal": float(args.initial_principal),
    }
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))

    print(f"[research] run_dir={run_dir}")
    print(f"[research] combinations={len(combos)} max_workers={args.max_workers}")

    if args.dry_run:
        preview = pd.DataFrame(combos).head(10)
        preview.to_csv(run_dir / "dry_run_preview.csv", index=False)
        print("[research] dry-run complete (preview written)")
        return 0

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    if args.engine == "gpu_native_daily_batch":
        results, failures, engine_meta = run_gpu_native_daily_batch(combos=combos, args=args, run_dir=run_dir)
        meta["engine_meta"] = engine_meta
        (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2))
        print(
            "[research] gpu-native batch complete "
            f"ok={len(results)} fail={len(failures)} backend={engine_meta.get('backend')}"
        )
    else:
        max_workers = max(1, int(args.max_workers))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_run_one, combo=c, idx=i, args=args, run_dir=run_dir) for i, c in enumerate(combos)]
            for fut in as_completed(futs):
                row = fut.result()
                if row.get("ok"):
                    results.append(row)
                else:
                    failures.append(row)
                done = len(results) + len(failures)
                if done % 10 == 0 or done == len(combos):
                    print(f"[research] progress={done}/{len(combos)} ok={len(results)} fail={len(failures)}")

    if failures:
        pd.DataFrame(failures).to_csv(run_dir / "failures.csv", index=False)

    if not results:
        print("[research] no successful runs")
        return 2

    df = pd.DataFrame(results)
    ranked = _score(df, dd_weight=float(args.dd_weight))

    ranked.to_csv(run_dir / "results_ranked.csv", index=False)
    ranked.head(int(args.top_n)).to_csv(run_dir / "results_top.csv", index=False)
    (run_dir / "results_ranked.json").write_text(ranked.to_json(orient="records", indent=2))

    cols = [
        "idx",
        "cpu_return_pct",
        "cpu_maxdd_pct",
        "gpu_return_pct",
        "gpu_maxdd_pct",
        "combined_score",
        "cpu_final_equity",
        "gpu_final_equity",
    ]
    print("\n[research] top results")
    print(ranked[cols].head(int(args.top_n)).to_string(index=False))
    print(f"\n[research] outputs: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
