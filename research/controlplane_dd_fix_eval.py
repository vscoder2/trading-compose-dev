#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1
from composer_original.tools import intraday_profit_lock_verification as iv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader

REPORT_DIR = ROOT / "research" / "reports" / f"controlplane_dd_fix_eval_{date.today().strftime('%Y%m%d')}"

WINDOW_DAYS: dict[str, int] = {
    "10d": 14,  # approx last 10 trading days
    "1m": 30,
    "2m": 61,
    "3m": 92,
    "4m": 122,
    "5m": 153,
    "6m": 183,
    "1y": 365,
}


@dataclass(frozen=True)
class FixedCfg:
    cap: float = 0.10
    enter: float = 0.72
    exit: float = 0.64
    enter_days: int = 3
    exit_days: int = 2


def _window_range(end_day: date, label: str) -> tuple[date, date]:
    if label not in WINDOW_DAYS:
        raise ValueError(f"Unsupported window: {label}")
    return end_day - timedelta(days=int(WINDOW_DAYS[label])), end_day


def _load_profile(name: str = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m") -> iv.LockedProfile:
    p = rt_v1.PROFILES[name]
    return iv.LockedProfile(
        name=p.name,
        enable_profit_lock=p.enable_profit_lock,
        profit_lock_mode=p.profit_lock_mode,
        profit_lock_threshold_pct=p.profit_lock_threshold_pct,
        profit_lock_trail_pct=p.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=p.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=p.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=p.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=p.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=p.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=p.profit_lock_adaptive_max_threshold_pct,
    )


def _simulate(
    *,
    windows: list[str],
    end_date: date,
    initial_equity: float,
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    high_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    split_ratio_by_day_symbol: dict[date, dict[str, float]],
    profile: iv.LockedProfile,
    target_by_day: dict[date, dict[str, float]],
    threshold_by_day: dict[date, float],
    rebalance_time_ny: dt_time,
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for w in windows:
        start_day, _ = _window_range(end_date, w)
        r = ab._simulate_intraday(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            high_map_by_symbol=high_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=threshold_by_day,
            profile=profile,
            start_day=start_day,
            end_day=end_date,
            initial_equity=initial_equity,
            slippage_bps=1.0,
            sell_fee_bps=0.0,
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        )
        out[w] = {
            "final_equity": float(r.final_equity),
            "return_pct": float(r.total_return_pct),
            "maxdd_pct": float(r.max_drawdown_pct),
            "events": float(len(r.events)),
        }
    return out


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    end_date = date.fromisoformat("2026-03-27")
    windows = ["10d", "1m", "2m", "3m", "4m", "5m", "6m", "1y"]
    initial_equity = 10_000.0
    rebalance_time_ny = ab._parse_hhmm("15:55")
    cfg = FixedCfg()

    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)
    profile = _load_profile()

    max_days = max(WINDOW_DAYS[w] for w in windows)
    earliest_start = end_date - timedelta(days=max_days)
    lookback_start = datetime.combine(earliest_start - timedelta(days=max(800, 260 + 20)), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

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
    _, _, raw_close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)

    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )

    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _c, h in daily_ohlc_adjusted.get(sym, []):
            hmap[d] = float(h)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=initial_equity,
        warmup_days=260,
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=earliest_start,
        end_day=end_date,
        feed=alpaca.data_feed,
    )

    v1_targets, v1_thresholds, _x1, _x2, _x3, _x4 = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=0.50,
        controlplane_hysteresis_enter=0.62,
        controlplane_hysteresis_exit=0.58,
        controlplane_hysteresis_enter_days=2,
        controlplane_hysteresis_exit_days=2,
    )
    _a, _b, _c, v2_targets, v2_thresholds, _d = ab._build_switch_targets_and_thresholds(
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        base_rebalance_threshold=0.05,
        controlplane_threshold_cap=float(cfg.cap),
        controlplane_hysteresis_enter=float(cfg.enter),
        controlplane_hysteresis_exit=float(cfg.exit),
        controlplane_hysteresis_enter_days=int(cfg.enter_days),
        controlplane_hysteresis_exit_days=int(cfg.exit_days),
    )

    v1 = _simulate(
        windows=windows,
        end_date=end_date,
        initial_equity=initial_equity,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        profile=profile,
        target_by_day=v1_targets,
        threshold_by_day=v1_thresholds,
        rebalance_time_ny=rebalance_time_ny,
    )
    v2 = _simulate(
        windows=windows,
        end_date=end_date,
        initial_equity=initial_equity,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        profile=profile,
        target_by_day=v2_targets,
        threshold_by_day=v2_thresholds,
        rebalance_time_ny=rebalance_time_ny,
    )

    rows: list[dict[str, Any]] = []
    for w in windows:
        rows.append(
            {
                "window": w,
                "period": f"{_window_range(end_date, w)[0].isoformat()} to {end_date.isoformat()}",
                "start_equity": initial_equity,
                "baseline_final_equity": v1[w]["final_equity"],
                "baseline_return_pct": v1[w]["return_pct"],
                "baseline_maxdd_pct": v1[w]["maxdd_pct"],
                "fixed_final_equity": v2[w]["final_equity"],
                "fixed_return_pct": v2[w]["return_pct"],
                "fixed_maxdd_pct": v2[w]["maxdd_pct"],
                "fixed_minus_baseline_equity": v2[w]["final_equity"] - v1[w]["final_equity"],
                "fixed_minus_baseline_return_pct": v2[w]["return_pct"] - v1[w]["return_pct"],
            }
        )

    csv_path = REPORT_DIR / "controlplane_dd_fix_eval_windows.csv"
    json_path = REPORT_DIR / "controlplane_dd_fix_eval_windows.json"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    payload = {
        "config": {
            "cap": cfg.cap,
            "enter": cfg.enter,
            "exit": cfg.exit,
            "enter_days": cfg.enter_days,
            "exit_days": cfg.exit_days,
        },
        "end_date": end_date.isoformat(),
        "windows": windows,
        "csv": str(csv_path),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
