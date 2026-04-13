#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta
from itertools import product
from pathlib import Path
from typing import Any

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from meta_router_v1.tools.historical_meta_router_windows import (
    RouterConfig,
    _build_meta_targets_and_thresholds,
    _parse_hhmm,
)
from m0106_runtime_v1.tools.historical_m0106_windows import _build_m0106_targets_and_thresholds
import m0106_runtime_v1.runtime_m0106_loop as m0106
import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as hv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


# Global read-only bundle for worker processes. This is intentionally populated once in
# parent process and inherited via fork to avoid repeated expensive data loads.
G: dict[str, Any] = {}


def _load_env_file(path: str, *, override: bool) -> int:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {p}")
    loaded = 0
    for raw_line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        if (not override) and (key in os.environ):
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _parse_csv_floats(raw: str) -> list[float]:
    out: list[float] = []
    for x in str(raw).split(","):
        x = x.strip()
        if not x:
            continue
        out.append(float(x))
    if not out:
        raise ValueError("Empty float list")
    return out


def _parse_csv_ints(raw: str) -> list[int]:
    out: list[int] = []
    for x in str(raw).split(","):
        x = x.strip()
        if not x:
            continue
        out.append(int(x))
    if not out:
        raise ValueError("Empty int list")
    return out


def _window_range(end_day: date, label: str) -> tuple[date, date]:
    if label not in hv.WINDOW_TO_DAYS:
        raise ValueError(f"Unsupported window: {label}")
    return end_day - timedelta(days=int(hv.WINDOW_TO_DAYS[label])), end_day


def _simulate_window(
    *,
    target_by_day: dict[date, dict[str, float]],
    threshold_by_day: dict[date, float],
    start_day: date,
    end_day: date,
    bundle: dict[str, Any] | None = None,
) -> hv.SimulationResult:
    # Worker tasks use inherited read-only global bundle. The main process can
    # optionally pass an explicit bundle before G is initialized.
    b = bundle if bundle is not None else G
    return hv._simulate_intraday(
        symbols=b["symbols"],
        aligned_days=b["aligned_days"],
        price_history=b["price_history"],
        close_map_by_symbol=b["close_map_by_symbol"],
        high_map_by_symbol=b["high_map_by_symbol"],
        minute_by_day_symbol=b["minute_by_day_symbol"],
        target_by_day=target_by_day,
        rebalance_threshold_by_day=threshold_by_day,
        profile=b["profile"],
        start_day=start_day,
        end_day=end_day,
        initial_equity=b["initial_equity"],
        slippage_bps=b["slippage_bps"],
        sell_fee_bps=b["sell_fee_bps"],
        runtime_profit_lock_order_type=b["runtime_profit_lock_order_type"],
        runtime_stop_price_offset_bps=b["runtime_stop_price_offset_bps"],
        rebalance_time_ny=b["rebalance_time_ny"],
        split_ratio_by_day_symbol=b["split_ratio_by_day_symbol"],
    )


def _score_candidate_row(candidate: dict[str, Any], windows: list[str]) -> dict[str, Any]:
    wins = 0
    uplifts: list[float] = []
    dd_penalty: list[float] = []

    for w in windows:
        m_final = float(candidate[f"{w}_meta_final"])
        b_final = float(candidate[f"{w}_best_baseline_final"])
        if m_final > b_final:
            wins += 1
        uplift_pct = 100.0 * ((m_final / b_final) - 1.0) if b_final > 0 else 0.0
        uplifts.append(uplift_pct)

        # Penalize drawdown only if meta dd is worse than best baseline dd for that window.
        m_dd = float(candidate[f"{w}_meta_dd"])
        b_dd = float(candidate[f"{w}_best_baseline_dd"])
        dd_penalty.append(max(0.0, m_dd - b_dd))

    avg_uplift = sum(uplifts) / len(uplifts) if uplifts else 0.0
    avg_dd_over = sum(dd_penalty) / len(dd_penalty) if dd_penalty else 0.0

    # Composite ranking score: prioritize win count, then uplift, then lower dd-overage.
    candidate["wins_vs_best_baseline"] = int(wins)
    candidate["avg_uplift_pct_vs_best_baseline"] = float(avg_uplift)
    candidate["avg_dd_over_best_baseline_pct"] = float(avg_dd_over)
    candidate["ranking_score"] = float((wins * 1000.0) + (avg_uplift * 10.0) - (avg_dd_over * 3.0))
    return candidate


def _evaluate_candidate(task: tuple[int, RouterConfig, list[str], date]) -> dict[str, Any]:
    idx, cfg, windows, end_day = task

    meta_targets, meta_thresholds, selected_engine, _selected_reason = _build_meta_targets_and_thresholds(
        aligned_days=G["aligned_days"],
        symbols=G["symbols"],
        close_series=G["close_series"],
        v1_targets=G["v1_targets"],
        v1_thresholds=G["v1_thresholds"],
        v2_targets=G["v2_targets"],
        v2_thresholds=G["v2_thresholds"],
        m0106_targets=G["m0106_targets"],
        m0106_thresholds=G["m0106_thresholds"],
        router_cfg=cfg,
    )

    row: dict[str, Any] = {
        "candidate_id": f"MRV1-{idx:04d}",
        **asdict(cfg),
    }

    # Count which engine the router used over the full aligned day set for observability.
    eng_counts = {"v1": 0, "v2": 0, "m0106": 0}
    for _d, eng in selected_engine.items():
        if eng in eng_counts:
            eng_counts[eng] += 1
    row["router_days_v1_all"] = int(eng_counts["v1"])
    row["router_days_v2_all"] = int(eng_counts["v2"])
    row["router_days_m0106_all"] = int(eng_counts["m0106"])

    for w in windows:
        start_day, end = _window_range(end_day, w)
        sim = _simulate_window(
            target_by_day=meta_targets,
            threshold_by_day=meta_thresholds,
            start_day=start_day,
            end_day=end,
        )
        m_final = float(sim.final_equity)
        m_ret = float(sim.total_return_pct)
        m_dd = float(sim.max_drawdown_pct)

        b_v1 = float(G["baseline"][w]["v1_final"])
        b_v2 = float(G["baseline"][w]["v2_final"])
        b_m0 = float(G["baseline"][w]["m0106_final"])
        b_best = max(b_v1, b_v2, b_m0)

        b_v1_dd = float(G["baseline"][w]["v1_dd"])
        b_v2_dd = float(G["baseline"][w]["v2_dd"])
        b_m0_dd = float(G["baseline"][w]["m0106_dd"])
        b_best_dd = min(b_v1_dd, b_v2_dd, b_m0_dd)

        row[f"{w}_meta_final"] = m_final
        row[f"{w}_meta_return"] = m_ret
        row[f"{w}_meta_dd"] = m_dd
        row[f"{w}_best_baseline_final"] = b_best
        row[f"{w}_best_baseline_dd"] = b_best_dd

    return _score_candidate_row(row, windows)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Parallel parameter sweep for meta_router_v1")
    p.add_argument("--env-file", default="")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    p.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    p.add_argument("--initial-equity", type=float, default=10_000.0)
    p.add_argument("--warmup-days", type=int, default=260)
    p.add_argument("--daily-lookback-days", type=int, default=800)
    p.add_argument("--slippage-bps", type=float, default=1.0)
    p.add_argument("--sell-fee-bps", type=float, default=0.0)
    p.add_argument("--rebalance-time-ny", default="15:55")
    p.add_argument(
        "--runtime-profit-lock-order-type",
        choices=["close_position", "market_order", "stop_order", "trailing_stop"],
        default="market_order",
    )
    p.add_argument("--runtime-stop-price-offset-bps", type=float, default=2.0)

    # v2 base controls.
    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--controlplane-threshold-cap", type=float, default=0.50)
    p.add_argument("--controlplane-hysteresis-enter", type=float, default=0.62)
    p.add_argument("--controlplane-hysteresis-exit", type=float, default=0.58)
    p.add_argument("--controlplane-hysteresis-enter-days", type=int, default=2)
    p.add_argument("--controlplane-hysteresis-exit-days", type=int, default=2)

    # Coarse and full windows.
    p.add_argument("--coarse-windows", default="2m,6m,1y")
    p.add_argument("--full-windows", default="1m,2m,3m,4m,5m,6m,1y,2y,3y,4y,5y,7y")

    # Sweep grid lists.
    p.add_argument("--grid-burst-ret3d-min", default="0.10,0.12")
    p.add_argument("--grid-burst-rv20-max", default="120,140")
    p.add_argument("--grid-burst-dd20-max", default="12,18")
    p.add_argument("--grid-riskoff-rv20-min", default="90,100")
    p.add_argument("--grid-riskoff-crossovers20-min", default="7,9")
    p.add_argument("--grid-riskoff-dd20-min", default="18")

    p.add_argument("--coarse-workers", type=int, default=8)
    p.add_argument("--full-workers", type=int, default=4)
    p.add_argument("--topk-to-full", type=int, default=8)

    p.add_argument("--reports-dir", default=str(ROOT / "meta_router_v1" / "reports"))
    p.add_argument("--output-prefix", default="sweep_meta_router")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    coarse_windows = [x.strip() for x in str(args.coarse_windows).split(",") if x.strip()]
    full_windows = [x.strip() for x in str(args.full_windows).split(",") if x.strip()]
    for w in coarse_windows + full_windows:
        if w not in hv.WINDOW_TO_DAYS:
            raise ValueError(f"Unsupported window: {w}")

    # Build candidate grid.
    grid = []
    for values in product(
        _parse_csv_floats(args.grid_burst_ret3d_min),
        _parse_csv_floats(args.grid_burst_rv20_max),
        _parse_csv_floats(args.grid_burst_dd20_max),
        _parse_csv_floats(args.grid_riskoff_rv20_min),
        _parse_csv_ints(args.grid_riskoff_crossovers20_min),
        _parse_csv_floats(args.grid_riskoff_dd20_min),
    ):
        cfg = RouterConfig(
            burst_ret3d_min=float(values[0]),
            burst_rv20_max=float(values[1]),
            burst_dd20_max=float(values[2]),
            riskoff_rv20_min=float(values[3]),
            riskoff_crossovers20_min=int(values[4]),
            riskoff_dd20_min=float(values[5]),
        )
        grid.append(cfg)

    if args.strategy_profile not in rt_v1.PROFILES:
        raise ValueError(f"Unknown strategy profile: {args.strategy_profile}")

    # Load data once.
    max_days = max(hv.WINDOW_TO_DAYS[w] for w in full_windows)
    earliest_start = args.end_date - timedelta(days=max_days)

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        earliest_start - timedelta(days=max(int(args.daily_lookback_days), int(args.warmup_days) + 20)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_ohlc_adjusted = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_ohlc_raw = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_adjusted, symbols=symbols)

    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _c, h in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(h)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    _, _, raw_close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)
    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(args.warmup_days),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    (
        v1_targets,
        v1_thresholds,
        _v1_variants,
        v2_targets,
        v2_thresholds,
        _v2_variants,
    ) = hv._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=float(args.rebalance_threshold),
        controlplane_threshold_cap=float(args.controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(args.controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(args.controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(args.controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(args.controlplane_hysteresis_exit_days),
    )

    m0106_targets, m0106_thresholds, _m0106_variants = _build_m0106_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=float(args.rebalance_threshold),
        cfg=m0106.M0106_CFG,
    )

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    profile_rt = rt_v1.PROFILES[args.strategy_profile]
    profile = iv.LockedProfile(
        name=profile_rt.name,
        enable_profit_lock=profile_rt.enable_profit_lock,
        profit_lock_mode=profile_rt.profit_lock_mode,
        profit_lock_threshold_pct=profile_rt.profit_lock_threshold_pct,
        profit_lock_trail_pct=profile_rt.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=profile_rt.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=profile_rt.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=profile_rt.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=profile_rt.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=profile_rt.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=profile_rt.profit_lock_adaptive_max_threshold_pct,
    )

    # Baseline table per window for scoring.
    local_bundle = {
        "symbols": symbols,
        "aligned_days": aligned_days,
        "price_history": price_history,
        "close_map_by_symbol": close_map_by_symbol,
        "high_map_by_symbol": high_map_by_symbol,
        "minute_by_day_symbol": minute_by_day_symbol,
        "profile": profile,
        "initial_equity": float(args.initial_equity),
        "slippage_bps": float(args.slippage_bps),
        "sell_fee_bps": float(args.sell_fee_bps),
        "runtime_profit_lock_order_type": str(args.runtime_profit_lock_order_type),
        "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
        "rebalance_time_ny": rebalance_time_ny,
        "split_ratio_by_day_symbol": split_ratio_by_day_symbol,
    }

    baseline: dict[str, dict[str, float]] = {}
    for w in full_windows:
        start_day, end_day = _window_range(args.end_date, w)
        s_v1 = _simulate_window(
            target_by_day=v1_targets,
            threshold_by_day=v1_thresholds,
            start_day=start_day,
            end_day=end_day,
            bundle=local_bundle,
        )
        s_v2 = _simulate_window(
            target_by_day=v2_targets,
            threshold_by_day=v2_thresholds,
            start_day=start_day,
            end_day=end_day,
            bundle=local_bundle,
        )
        s_m0 = _simulate_window(
            target_by_day=m0106_targets,
            threshold_by_day=m0106_thresholds,
            start_day=start_day,
            end_day=end_day,
            bundle=local_bundle,
        )
        baseline[w] = {
            "v1_final": float(s_v1.final_equity),
            "v2_final": float(s_v2.final_equity),
            "m0106_final": float(s_m0.final_equity),
            "v1_dd": float(s_v1.max_drawdown_pct),
            "v2_dd": float(s_v2.max_drawdown_pct),
            "m0106_dd": float(s_m0.max_drawdown_pct),
        }

    # Fill global bundle for worker processes.
    G.clear()
    G.update(
        {
            "symbols": symbols,
            "aligned_days": aligned_days,
            "price_history": price_history,
            "close_map_by_symbol": close_map_by_symbol,
            "high_map_by_symbol": high_map_by_symbol,
            "minute_by_day_symbol": minute_by_day_symbol,
            "profile": profile,
            "initial_equity": float(args.initial_equity),
            "slippage_bps": float(args.slippage_bps),
            "sell_fee_bps": float(args.sell_fee_bps),
            "runtime_profit_lock_order_type": str(args.runtime_profit_lock_order_type),
            "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
            "rebalance_time_ny": rebalance_time_ny,
            "split_ratio_by_day_symbol": split_ratio_by_day_symbol,
            "close_series": close_series,
            "v1_targets": v1_targets,
            "v1_thresholds": v1_thresholds,
            "v2_targets": v2_targets,
            "v2_thresholds": v2_thresholds,
            "m0106_targets": m0106_targets,
            "m0106_thresholds": m0106_thresholds,
            "baseline": baseline,
        }
    )

    coarse_tasks = [(i + 1, cfg, coarse_windows, args.end_date) for i, cfg in enumerate(grid)]

    # Use fork context to inherit large preloaded data without reloading in each worker.
    ctx = mp.get_context("fork")

    coarse_results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.coarse_workers)), mp_context=ctx) as ex:
        futs = [ex.submit(_evaluate_candidate, t) for t in coarse_tasks]
        for i, fut in enumerate(as_completed(futs), start=1):
            coarse_results.append(fut.result())
            if i % 5 == 0:
                print(json.dumps({"stage": "coarse", "completed": i, "total": len(futs)}))

    coarse_results.sort(key=lambda r: (r["ranking_score"], r["wins_vs_best_baseline"], r["avg_uplift_pct_vs_best_baseline"]), reverse=True)

    topk = max(1, int(args.topk_to_full))
    full_candidates = coarse_results[:topk]

    full_tasks = []
    for idx, row in enumerate(full_candidates, start=1):
        cfg = RouterConfig(
            burst_ret3d_min=float(row["burst_ret3d_min"]),
            burst_rv20_max=float(row["burst_rv20_max"]),
            burst_dd20_max=float(row["burst_dd20_max"]),
            riskoff_rv20_min=float(row["riskoff_rv20_min"]),
            riskoff_crossovers20_min=int(row["riskoff_crossovers20_min"]),
            riskoff_dd20_min=float(row["riskoff_dd20_min"]),
        )
        full_tasks.append((idx, cfg, full_windows, args.end_date))

    full_results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.full_workers)), mp_context=ctx) as ex:
        futs = [ex.submit(_evaluate_candidate, t) for t in full_tasks]
        for i, fut in enumerate(as_completed(futs), start=1):
            full_results.append(fut.result())
            print(json.dumps({"stage": "full", "completed": i, "total": len(futs)}))

    full_results.sort(key=lambda r: (r["ranking_score"], r["wins_vs_best_baseline"], r["avg_uplift_pct_vs_best_baseline"]), reverse=True)

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.end_date.strftime("%Y%m%d")
    base = reports_dir / f"{args.output_prefix}_{stamp}"
    coarse_csv = base.with_name(base.name + "_coarse.csv")
    full_csv = base.with_name(base.name + "_full.csv")
    out_json = base.with_suffix(".json")

    # Write coarse and full tables.
    if coarse_results:
        with coarse_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(coarse_results[0].keys()))
            w.writeheader()
            w.writerows(coarse_results)

    if full_results:
        with full_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(full_results[0].keys()))
            w.writeheader()
            w.writerows(full_results)

    payload = {
        "run": {
            "mode": args.mode,
            "data_feed": args.data_feed,
            "strategy_profile": args.strategy_profile,
            "end_date": args.end_date.isoformat(),
            "initial_equity": float(args.initial_equity),
            "coarse_windows": coarse_windows,
            "full_windows": full_windows,
            "grid_size": len(grid),
            "coarse_workers": int(args.coarse_workers),
            "full_workers": int(args.full_workers),
            "topk_to_full": int(args.topk_to_full),
        },
        "baseline": baseline,
        "best_full_candidate": (full_results[0] if full_results else None),
        "outputs": {
            "coarse_csv": str(coarse_csv),
            "full_csv": str(full_csv),
            "json": str(out_json),
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
