#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from dateutil.relativedelta import relativedelta
import pandas as pd

# Keep this script self-contained but reuse stable simulation functions.
ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from protective_stop_variant_v2.tools.export_last30_daybyday import (
    _build_targets_for_engine,
    _parse_hhmm,
    _simulate_with_table,
)
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1


@dataclass(frozen=True)
class Candidate:
    stop_pct: float
    rv_gate: float
    trail_scale: float
    threshold_scale: float

    @property
    def cid(self) -> str:
        return (
            f"C_sp{self.stop_pct:.1f}_rv{self.rv_gate:.0f}_"
            f"tr{self.trail_scale:.2f}_th{self.threshold_scale:.2f}"
        )


def _window_map() -> dict[str, relativedelta]:
    return {
        "1m": relativedelta(months=1),
        "2m": relativedelta(months=2),
        "3m": relativedelta(months=3),
        "4m": relativedelta(months=4),
        "5m": relativedelta(months=5),
        "6m": relativedelta(months=6),
        "1y": relativedelta(years=1),
        "2y": relativedelta(years=2),
        "3y": relativedelta(years=3),
        "5y": relativedelta(years=5),
    }


def _align_start(aligned_days: list[date], cal_start: date, end_day: date) -> date:
    for d in aligned_days:
        if cal_start <= d <= end_day:
            return d
    return aligned_days[0]


def _build_profile_variant(base: iv.LockedProfile, *, trail_scale: float, threshold_scale: float) -> iv.LockedProfile:
    # Scale trail and threshold coherently while respecting sane bounds.
    new_trail = max(1.0, float(base.profit_lock_trail_pct) * float(trail_scale))

    new_thresh = max(2.0, float(base.profit_lock_threshold_pct) * float(threshold_scale))
    new_min = max(2.0, float(base.profit_lock_adaptive_min_threshold_pct) * float(threshold_scale))
    new_max = max(new_min + 0.1, float(base.profit_lock_adaptive_max_threshold_pct) * float(threshold_scale))

    return iv.LockedProfile(
        name=f"{base.name}_tr{trail_scale:.2f}_th{threshold_scale:.2f}",
        enable_profit_lock=base.enable_profit_lock,
        profit_lock_mode=base.profit_lock_mode,
        profit_lock_threshold_pct=new_thresh,
        profit_lock_trail_pct=new_trail,
        profit_lock_adaptive_threshold=base.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=base.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=base.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=base.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=new_min,
        profit_lock_adaptive_max_threshold_pct=new_max,
    )


def _run_candidate_windows(
    *,
    candidate: Candidate,
    windows: list[tuple[str, date, date]],
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    split_ratio: dict[date, dict[str, float]],
    target_by_day: dict[date, dict[str, float]],
    threshold_by_day: dict[date, float],
    base_profile: iv.LockedProfile,
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    rebalance_time: str,
) -> pd.DataFrame:
    profile_variant = _build_profile_variant(
        base_profile,
        trail_scale=candidate.trail_scale,
        threshold_scale=candidate.threshold_scale,
    )

    rows: list[dict[str, Any]] = []
    for wname, sday, eday in windows:
        day_rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=threshold_by_day,
            profile=profile_variant,
            start_day=sday,
            end_day=eday,
            initial_equity=float(initial_equity),
            slippage_bps=float(slippage_bps),
            sell_fee_bps=float(sell_fee_bps),
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=_parse_hhmm(rebalance_time),
            split_ratio_by_day_symbol=split_ratio,
            enable_protective_stop=True,
            protective_stop_pct=float(candidate.stop_pct),
            stop_scope="inverse_only",
            rv_gate_min_pct=float(candidate.rv_gate),
            rv_gate_window=20,
        )
        df = pd.DataFrame(day_rows)
        if df.empty:
            final = float(initial_equity)
            maxdd = 0.0
            days = 0
        else:
            final = float(df.iloc[-1]["Day End Equity"])
            maxdd = float(df["Drawdown %"].max())
            days = int(len(df))
        pnl = final - float(initial_equity)
        ret = (final / float(initial_equity) - 1.0) * 100.0
        rows.append(
            {
                "candidate_id": candidate.cid,
                "window": wname,
                "period": f"{sday.isoformat()} to {eday.isoformat()}",
                "final_equity": round(final, 2),
                "pnl": round(pnl, 2),
                "return_pct": round(ret, 4),
                "max_dd_pct": round(maxdd, 4),
                "days": days,
            }
        )
    return pd.DataFrame(rows)


def _summarize_against_baseline(
    candidate_windows: pd.DataFrame,
    baseline_windows: pd.DataFrame,
    *,
    dd_penalty: float,
) -> dict[str, Any]:
    merged = candidate_windows.merge(
        baseline_windows[["window", "return_pct", "max_dd_pct"]],
        on="window",
        suffixes=("", "_baseline"),
    )

    avg_ret = float(merged["return_pct"].mean())
    avg_dd = float(merged["max_dd_pct"].mean())
    score = float(avg_ret - float(dd_penalty) * avg_dd)

    ret_wins = int((merged["return_pct"] > merged["return_pct_baseline"]).sum())
    dd_wins = int((merged["max_dd_pct"] < merged["max_dd_pct_baseline"]).sum())
    both_wins = int(
        ((merged["return_pct"] > merged["return_pct_baseline"]) &
         (merged["max_dd_pct"] < merged["max_dd_pct_baseline"]))
        .sum()
    )

    avg_ret_baseline = float(merged["return_pct_baseline"].mean())
    avg_dd_baseline = float(merged["max_dd_pct_baseline"].mean())

    return {
        "candidate_id": str(merged.iloc[0]["candidate_id"]),
        "avg_return_pct": round(avg_ret, 4),
        "avg_max_dd_pct": round(avg_dd, 4),
        "score": round(score, 4),
        "ret_wins": ret_wins,
        "dd_wins": dd_wins,
        "both_wins": both_wins,
        "delta_avg_return_pct": round(avg_ret - avg_ret_baseline, 4),
        "delta_avg_max_dd_pct": round(avg_dd - avg_dd_baseline, 4),
        "strict_accept": bool((avg_ret > avg_ret_baseline) and (avg_dd < avg_dd_baseline)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Standalone fev1_best tuning research (no existing code edits)")
    ap.add_argument("--env-file", default=str(ROOT / ".env.dev"))
    ap.add_argument("--env-override", action="store_true")
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--data-feed", choices=["sip", "iex"], default="sip")
    ap.add_argument("--strategy-profile", default="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m")
    ap.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 4, 10))
    ap.add_argument("--initial-equity", type=float, default=10000.0)
    ap.add_argument("--rebalance-time", default="15:55")
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--sell-fee-bps", type=float, default=0.0)
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "configs" / "fev1_upgrade_grid_v1.json"))
    ap.add_argument("--max-candidates", type=int, default=0, help="Optional cap for fast exploratory runs.")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    with Path(args.config).open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Build candidate set from the standalone research config.
    candidates = [
        Candidate(stop_pct=float(sp), rv_gate=float(rv), trail_scale=float(ts), threshold_scale=float(ths))
        for sp, rv, ts, ths in itertools.product(
            cfg["stop_pct"],
            cfg["rv_gate"],
            cfg["trail_scale"],
            cfg["threshold_scale"],
        )
    ]

    # Also include baseline fev1_best explicitly, so ranking always has a known anchor row.
    baseline_candidate = Candidate(stop_pct=5.0, rv_gate=80.0, trail_scale=1.0, threshold_scale=1.0)
    if baseline_candidate not in candidates:
        candidates.append(baseline_candidate)

    if int(args.max_candidates) > 0:
        # Keep baseline always present.
        selected = candidates[: int(args.max_candidates)]
        if baseline_candidate not in selected:
            selected.append(baseline_candidate)
        candidates = selected

    print(f"[INFO] Candidate count: {len(candidates)}")

    ab._load_env_file(args.env_file, override=bool(args.env_override))

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    window_defs = _window_map()
    quick_names = [str(x) for x in cfg["quick_windows"]]
    full_names = [str(x) for x in cfg["full_windows"]]

    max_delta = max([window_defs[w] for w in full_names], key=lambda x: (x.years, x.months))
    earliest_start = args.end_date - max_delta

    # Pull enough history once and reuse for all candidates/windows.
    lookback_start = datetime.combine(earliest_start - timedelta(days=1200), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    print("[INFO] Fetching daily bars (adjusted/raw)...")
    daily_ohlc_adj = iv._fetch_daily_ohlc(
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

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_adj, symbols=symbols)
    _, _, raw_close_map = iv._align_daily_close_history(daily_ohlc_raw, symbols=symbols)

    split_ratio = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map,
    )

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=260,
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    quick_windows = [
        (
            w,
            _align_start(sorted(aligned_days), args.end_date - window_defs[w], args.end_date),
            args.end_date,
        )
        for w in quick_names
    ]
    full_windows = [
        (
            w,
            _align_start(sorted(aligned_days), args.end_date - window_defs[w], args.end_date),
            args.end_date,
        )
        for w in full_names
    ]

    minute_start = min([s for _, s, _ in (quick_windows + full_windows)])
    print(f"[INFO] Fetching minute bars from {minute_start} to {args.end_date}...")
    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=minute_start,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    fev1_targets, fev1_thresholds = _build_targets_for_engine(
        engine="fev1",
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=0.05,
        controlplane_threshold_cap=0.50,
        controlplane_hysteresis_enter=0.62,
        controlplane_hysteresis_exit=0.58,
        controlplane_hysteresis_enter_days=2,
        controlplane_hysteresis_exit_days=2,
    )

    base_rt_profile = rt_v1.PROFILES[args.strategy_profile]
    base_profile = iv.LockedProfile(
        name=base_rt_profile.name,
        enable_profit_lock=base_rt_profile.enable_profit_lock,
        profit_lock_mode=base_rt_profile.profit_lock_mode,
        profit_lock_threshold_pct=base_rt_profile.profit_lock_threshold_pct,
        profit_lock_trail_pct=base_rt_profile.profit_lock_trail_pct,
        profit_lock_adaptive_threshold=base_rt_profile.profit_lock_adaptive_threshold,
        profit_lock_adaptive_symbol=base_rt_profile.profit_lock_adaptive_symbol,
        profit_lock_adaptive_rv_window=base_rt_profile.profit_lock_adaptive_rv_window,
        profit_lock_adaptive_rv_baseline_pct=base_rt_profile.profit_lock_adaptive_rv_baseline_pct,
        profit_lock_adaptive_min_threshold_pct=base_rt_profile.profit_lock_adaptive_min_threshold_pct,
        profit_lock_adaptive_max_threshold_pct=base_rt_profile.profit_lock_adaptive_max_threshold_pct,
    )

    stamp = datetime.now(tz=NY).strftime("%Y%m%d_%H%M%S")
    tag = args.tag.strip()
    run_id = f"fev1_upgrade_{stamp}" if not tag else f"fev1_upgrade_{tag}_{stamp}"
    run_dir = Path(__file__).resolve().parents[1] / "reports" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Quick phase across all candidates.
    quick_rows = []
    baseline_quick = None
    print(f"[INFO] Quick phase: {len(candidates)} candidates x {len(quick_windows)} windows")
    for i, c in enumerate(candidates, start=1):
        print(f"[QUICK] {i}/{len(candidates)} {c.cid}")
        c_quick = _run_candidate_windows(
            candidate=c,
            windows=quick_windows,
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            split_ratio=split_ratio,
            target_by_day=fev1_targets,
            threshold_by_day=fev1_thresholds,
            base_profile=base_profile,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            rebalance_time=str(args.rebalance_time),
        )
        if c == baseline_candidate:
            baseline_quick = c_quick.copy()
        quick_rows.append(c_quick)

    quick_df = pd.concat(quick_rows, ignore_index=True)
    quick_df.to_csv(run_dir / "quick_window_metrics.csv", index=False)

    if baseline_quick is None:
        raise RuntimeError("Baseline candidate was not evaluated in quick phase")

    quick_rank = []
    for cid, cdf in quick_df.groupby("candidate_id"):
        quick_rank.append(_summarize_against_baseline(cdf, baseline_quick, dd_penalty=float(cfg["dd_penalty"])))
    quick_rank_df = pd.DataFrame(quick_rank).sort_values(["strict_accept", "score", "avg_return_pct"], ascending=[False, False, False])
    quick_rank_df.to_csv(run_dir / "quick_ranked.csv", index=False)

    # Full phase only for top-K from quick.
    top_k = int(cfg["top_k_from_quick"])
    top_ids = quick_rank_df.head(top_k)["candidate_id"].tolist()
    top_candidates = [c for c in candidates if c.cid in set(top_ids)]
    if baseline_candidate.cid not in top_ids:
        top_candidates.append(baseline_candidate)

    full_rows = []
    baseline_full = None
    print(f"[INFO] Full phase: {len(top_candidates)} candidates x {len(full_windows)} windows")
    for i, c in enumerate(top_candidates, start=1):
        print(f"[FULL] {i}/{len(top_candidates)} {c.cid}")
        c_full = _run_candidate_windows(
            candidate=c,
            windows=full_windows,
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            split_ratio=split_ratio,
            target_by_day=fev1_targets,
            threshold_by_day=fev1_thresholds,
            base_profile=base_profile,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            rebalance_time=str(args.rebalance_time),
        )
        if c == baseline_candidate:
            baseline_full = c_full.copy()
        full_rows.append(c_full)

    full_df = pd.concat(full_rows, ignore_index=True)
    full_df.to_csv(run_dir / "full_window_metrics.csv", index=False)

    if baseline_full is None:
        raise RuntimeError("Baseline candidate was not evaluated in full phase")

    full_rank = []
    for cid, cdf in full_df.groupby("candidate_id"):
        full_rank.append(_summarize_against_baseline(cdf, baseline_full, dd_penalty=float(cfg["dd_penalty"])))
    full_rank_df = pd.DataFrame(full_rank).sort_values(["strict_accept", "score", "avg_return_pct"], ascending=[False, False, False])

    # Attach candidate params for readability.
    param_map = {c.cid: asdict(c) for c in candidates}
    for k in ["stop_pct", "rv_gate", "trail_scale", "threshold_scale"]:
        full_rank_df[k] = full_rank_df["candidate_id"].map(lambda cid: param_map[cid][k])

    full_rank_df.to_csv(run_dir / "full_ranked.csv", index=False)

    # A compact leaderboard artifact for quick user readout.
    leaderboard_cols = [
        "candidate_id",
        "stop_pct",
        "rv_gate",
        "trail_scale",
        "threshold_scale",
        "strict_accept",
        "score",
        "avg_return_pct",
        "avg_max_dd_pct",
        "delta_avg_return_pct",
        "delta_avg_max_dd_pct",
        "ret_wins",
        "dd_wins",
        "both_wins",
    ]
    full_rank_df[leaderboard_cols].head(30).to_csv(run_dir / "leaderboard_top30.csv", index=False)

    run_meta = {
        "run_id": run_id,
        "reports_dir": str(run_dir),
        "baseline_candidate": baseline_candidate.cid,
        "candidate_count": len(candidates),
        "quick_windows": quick_names,
        "full_windows": full_names,
        "top_k_from_quick": top_k,
        "strict_accept_count": int(full_rank_df["strict_accept"].sum()),
    }
    with (run_dir / "run_meta.json").open("w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2)

    print(json.dumps(run_meta, indent=2))
    print("TOP10")
    print(full_rank_df[leaderboard_cols].head(10).to_csv(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
