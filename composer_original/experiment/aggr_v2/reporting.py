from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .gpu_replay import GpuReplaySummary
from .model_types import BacktestResultV2
from .validation import ValidationSummary


def _round(x: float, n: int = 4) -> float:
    return float(round(float(x), n))


def build_summary_row(
    *,
    result: BacktestResultV2,
    gpu: GpuReplaySummary,
    validation: ValidationSummary,
) -> dict[str, Any]:
    pnl = result.final_equity - result.initial_equity
    return {
        "Profile": result.profile_name,
        "Window": result.window_label,
        "Mode": result.mode,
        "Start Equity": _round(result.initial_equity, 2),
        "CPU Final Equity": _round(result.final_equity, 2),
        "CPU Return %": _round(result.total_return_pct, 4),
        "CPU PnL": _round(pnl, 2),
        "CPU MaxDD %": _round(result.max_drawdown_pct, 4),
        "CPU Trades": int(result.trade_count),
        "GPU Backend": gpu.backend,
        "GPU Final Equity": _round(gpu.final_equity, 2),
        "GPU Return %": _round(gpu.total_return_pct, 4),
        "CPU-GPU Diff (bps)": _round(gpu.diff_bps_vs_cpu, 4),
        "Sharpe": _round(validation.sharpe, 4),
        "Sortino": _round(validation.sortino, 4),
        "Calmar": _round(validation.calmar, 4),
        "Worst Day %": _round(validation.worst_day_pct, 4),
        "Best Day %": _round(validation.best_day_pct, 4),
        "Bootstrap P10 %": _round(validation.bootstrap_cagr_p10, 4),
        "Bootstrap P50 %": _round(validation.bootstrap_cagr_p50, 4),
        "Bootstrap P90 %": _round(validation.bootstrap_cagr_p90, 4),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def daily_table_rows(result: BacktestResultV2) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in result.daily:
        rows.append(
            {
                "Date": d.day.isoformat(),
                "Start Equity": _round(d.start_equity, 2),
                "End Equity": _round(d.end_equity, 2),
                "PnL": _round(d.pnl, 2),
                "Return %": _round(d.return_pct, 4),
                "Drawdown %": _round(d.drawdown_pct, 4),
                "Holdings": json.dumps(d.holdings, sort_keys=True),
                "Notes": d.notes,
            }
        )
    return rows


def serialize_result(result: BacktestResultV2, gpu: GpuReplaySummary, validation: ValidationSummary) -> dict[str, Any]:
    return {
        "summary": build_summary_row(result=result, gpu=gpu, validation=validation),
        "result": {
            "profile_name": result.profile_name,
            "window_label": result.window_label,
            "mode": result.mode,
            "initial_equity": result.initial_equity,
            "final_equity": result.final_equity,
            "total_return_pct": result.total_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "cagr_pct": result.cagr_pct,
            "trade_count": result.trade_count,
            "meta": result.meta,
        },
        "gpu": asdict(gpu),
        "validation": asdict(validation),
        "daily": daily_table_rows(result),
    }
