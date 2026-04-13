#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
import m0106_runtime_v1.runtime_m0106_loop as m0106
import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as hv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


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


def _parse_hhmm(raw: str) -> dt_time:
    s = str(raw or "").strip()
    hh, mm = s.split(":", 1)
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        raise ValueError(f"Invalid HH:MM: {raw!r}")
    return dt_time(h, m)


def _build_m0106_targets_and_thresholds(
    *,
    aligned_days: list[date],
    symbols: list[str],
    close_series: dict[str, list[float]],
    baseline_target_by_day: dict[date, dict[str, float]],
    base_rebalance_threshold: float,
    cfg: m0106.M0106Config,
) -> tuple[dict[date, dict[str, float]], dict[date, float], dict[date, str]]:
    """Build day-wise M0106 targets and rebalance thresholds.

    This mirrors runtime_m0106_loop daily decision flow:
    1) baseline target
    2) v2-style variant + adaptive threshold
    3) M0106 overlays (including shock-hold threshold override)
    """

    targets: dict[date, dict[str, float]] = {}
    thresholds: dict[date, float] = {}
    variants: dict[date, str] = {}

    reg_state = rt_v1.RegimeState()
    h_state = m0106.HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)
    shock_left = 0

    for idx, d in enumerate(aligned_days):
        baseline_target = dict(baseline_target_by_day.get(d, {}))
        if not baseline_target:
            targets[d] = {}
            thresholds[d] = float(base_rebalance_threshold)
            variants[d] = "baseline"
            continue

        hist_by_symbol = {s: list(close_series.get(s, [])[: idx + 1]) for s in symbols}
        soxl_hist = hist_by_symbol.get("SOXL", [])

        variant = "baseline"
        rebalance_threshold = float(base_rebalance_threshold)

        if len(soxl_hist) >= 20:
            metrics = rt_v1._compute_regime_metrics(soxl_hist)
            (
                variant,
                _reason,
                rebalance_threshold,
                reg_state,
                h_state,
                _confidence,
            ) = m0106._compute_v2_variant_and_threshold(
                metrics=metrics,
                reg_state=reg_state,
                h_state=h_state,
                base_threshold=float(base_rebalance_threshold),
                cfg=cfg,
            )

        switched_target, _inv_note = rt_v1._apply_variant_to_target(baseline_target, hist_by_symbol, variant)
        final_target, threshold_override, shock_left, _debug = m0106._apply_m0106_overlay(
            baseline_target=baseline_target,
            switched_target=switched_target,
            variant=variant,
            daily_closes=hist_by_symbol,
            shock_left=shock_left,
            cfg=cfg,
        )

        if threshold_override is not None:
            rebalance_threshold = float(threshold_override)

        targets[d] = dict(final_target)
        thresholds[d] = float(rebalance_threshold)
        variants[d] = str(variant)

    return targets, thresholds, variants


def _window_range(end_day: date, label: str) -> tuple[date, date]:
    if label not in hv.WINDOW_TO_DAYS:
        raise ValueError(f"Unsupported window: {label}")
    return end_day - timedelta(days=int(hv.WINDOW_TO_DAYS[label])), end_day


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Historical multi-window runner for m0106_runtime_v1/runtime_m0106_loop.py"
    )
    p.add_argument("--env-file", default="")
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    p.add_argument(
        "--strategy-profile",
        choices=sorted(m0106.PROFILES.keys()),
        default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_m0106",
    )
    p.add_argument("--windows", default="1m,2m,3m,4m,5m,6m,1y,2y,3y,4y,5y,7y")
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
    p.add_argument("--rebalance-threshold", type=float, default=0.05)
    p.add_argument("--reports-dir", default=str(ROOT / "m0106_runtime_v1" / "reports"))
    p.add_argument("--output-prefix", default="compare_m0106_true_historical")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rebalance_time_ny = _parse_hhmm(args.rebalance_time_ny)

    if args.env_file:
        loaded = _load_env_file(args.env_file, override=bool(args.env_override))
        print(json.dumps({"loaded_env_vars": loaded, "env_file": args.env_file, "env_override": bool(args.env_override)}))

    profile_rt = m0106.PROFILES[args.strategy_profile]
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

    windows = [w.strip() for w in str(args.windows).split(",") if w.strip()]
    if not windows:
        raise ValueError("No windows provided")
    for w in windows:
        if w not in hv.WINDOW_TO_DAYS:
            raise ValueError(f"Unsupported window: {w}")

    max_days = max(int(hv.WINDOW_TO_DAYS[w]) for w in windows)
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
        for d, _close_px, high_px in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(high_px)
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

    m0106_targets, m0106_thresholds, m0106_variants = _build_m0106_targets_and_thresholds(
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

    gpu_backend = "cpu_emulated_fallback"
    try:
        import cupy  # type: ignore # noqa: F401

        gpu_backend = "cupy_available_cpu_logic"
    except Exception:
        gpu_backend = "cpu_emulated_fallback"

    rows: list[dict[str, Any]] = []
    for window in windows:
        start_day, end_day = _window_range(args.end_date, window)
        cpu_res = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=m0106_targets,
            rebalance_threshold_by_day=m0106_thresholds,
            profile=profile,
            start_day=start_day,
            end_day=end_day,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
            runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        )
        gpu_res = hv._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=m0106_targets,
            rebalance_threshold_by_day=m0106_thresholds,
            profile=profile,
            start_day=start_day,
            end_day=end_day,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            runtime_profit_lock_order_type=str(args.runtime_profit_lock_order_type),
            runtime_stop_price_offset_bps=float(args.runtime_stop_price_offset_bps),
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        )

        var_count = len({m0106_variants[d] for d in aligned_days if start_day <= d <= end_day and d in m0106_variants})

        rows.append(
            {
                "window": window,
                "period": f"{start_day.isoformat()} to {end_day.isoformat()}",
                "start_equity": float(args.initial_equity),
                "m0106_cpu_final_equity": float(cpu_res.final_equity),
                "m0106_cpu_pnl": float(cpu_res.final_equity - float(args.initial_equity)),
                "m0106_cpu_return_pct": float(cpu_res.total_return_pct),
                "m0106_cpu_maxdd_pct": float(cpu_res.max_drawdown_pct),
                "m0106_cpu_maxdd_usd": float(cpu_res.max_drawdown_usd),
                "m0106_cpu_events": len(cpu_res.events),
                "m0106_variants_seen": int(var_count),
                "m0106_gpu_final_equity": float(gpu_res.final_equity),
                "m0106_gpu_pnl": float(gpu_res.final_equity - float(args.initial_equity)),
                "m0106_gpu_return_pct": float(gpu_res.total_return_pct),
                "m0106_gpu_maxdd_pct": float(gpu_res.max_drawdown_pct),
                "m0106_gpu_maxdd_usd": float(gpu_res.max_drawdown_usd),
                "m0106_gpu_events": len(gpu_res.events),
                "m0106_cpu_gpu_diff_bps": float(iv._safe_bps_diff(float(cpu_res.final_equity), float(gpu_res.final_equity))),
            }
        )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.end_date.strftime("%Y%m%d")
    base = reports_dir / f"{args.output_prefix}_{stamp}"
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")

    headers = [
        "window",
        "period",
        "start_equity",
        "m0106_cpu_final_equity",
        "m0106_cpu_pnl",
        "m0106_cpu_return_pct",
        "m0106_cpu_maxdd_pct",
        "m0106_cpu_maxdd_usd",
        "m0106_cpu_events",
        "m0106_variants_seen",
        "m0106_gpu_final_equity",
        "m0106_gpu_pnl",
        "m0106_gpu_return_pct",
        "m0106_gpu_maxdd_pct",
        "m0106_gpu_maxdd_usd",
        "m0106_gpu_events",
        "m0106_cpu_gpu_diff_bps",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    payload = {
        "run": {
            "mode": args.mode,
            "data_feed": args.data_feed,
            "strategy_profile": args.strategy_profile,
            "windows": windows,
            "end_date": args.end_date.isoformat(),
            "initial_equity": float(args.initial_equity),
            "rebalance_time_ny": args.rebalance_time_ny,
            "runtime_profit_lock_order_type": args.runtime_profit_lock_order_type,
            "runtime_stop_price_offset_bps": float(args.runtime_stop_price_offset_bps),
            "slippage_bps": float(args.slippage_bps),
            "sell_fee_bps": float(args.sell_fee_bps),
            "gpu_backend": gpu_backend,
            "m0106_config": asdict(m0106.M0106_CFG),
        },
        "rows": rows,
        "outputs": {
            "csv": str(csv_path),
            "json": str(json_path),
        },
    }

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
