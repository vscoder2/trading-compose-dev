#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from dateutil.relativedelta import relativedelta

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


WINDOWS: dict[str, relativedelta] = {
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

INVERSE_DEFENSIVE = {"SOXS", "SQQQ", "SPXS", "TMV"}


@dataclass(frozen=True)
class OverlayCandidate:
    shock_drop_pct: float
    shock_hold_days: int
    dd_trigger_pct: float
    dd_window_days: int
    reentry_pos_days: int
    defensive_symbol: str

    @property
    def cid(self) -> str:
        return (
            f"OV_sh{self.shock_drop_pct:.0f}_h{self.shock_hold_days}_"
            f"dd{self.dd_trigger_pct:.0f}w{self.dd_window_days}_"
            f"re{self.reentry_pos_days}_{self.defensive_symbol}"
        )


def _align_start(aligned_days: list[date], cal_start: date, end_day: date) -> date:
    for d in aligned_days:
        if cal_start <= d <= end_day:
            return d
    return aligned_days[0]


def _build_scaled_profile(base: iv.LockedProfile, trail_scale: float, threshold_scale: float) -> iv.LockedProfile:
    # Keep same locked profile family but allow scaling knobs for dedicated what-if runs.
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


def _is_risk_on_target(target: dict[str, float]) -> bool:
    if not target:
        return False
    sym = max(target.items(), key=lambda kv: float(kv[1]))[0].upper()
    return sym not in INVERSE_DEFENSIVE


def _overlay_targets(
    *,
    aligned_days: list[date],
    close_series: dict[str, list[float]],
    base_target_by_day: dict[date, dict[str, float]],
    candidate: OverlayCandidate,
) -> dict[date, dict[str, float]]:
    """Apply standalone overlay logic to base daily targets.

    Rules:
    - Shock guard: if SOXL daily return <= -shock_drop_pct, force defensive target for shock_hold_days.
    - Drawdown brake: if SOXL drawdown over dd_window >= dd_trigger_pct, force at least one defensive day.
    - Re-entry confirmation: after defensive mode is active, require N positive SOXL closes to release.
    """
    out: dict[date, dict[str, float]] = {}

    soxl = close_series.get("SOXL", [])
    cooldown = 0
    defensive_latch = False
    pos_streak = 0

    for idx, d in enumerate(aligned_days):
        base_t = dict(base_target_by_day.get(d, {}))
        if not base_t:
            out[d] = {}
            continue

        # Use prior day information only (avoid look-ahead).
        prev_ret = 0.0
        if idx >= 2 and soxl[idx - 2] > 0:
            prev_ret = (soxl[idx - 1] / soxl[idx - 2]) - 1.0

        dd_pct = 0.0
        w = int(candidate.dd_window_days)
        if idx >= max(2, w):
            look = soxl[idx - w : idx]
            if look:
                peak = max(look)
                cur = look[-1]
                if peak > 0:
                    dd_pct = 100.0 * (peak - cur) / peak

        # Trigger logic from prior-day data.
        if float(candidate.shock_drop_pct) > 0 and prev_ret <= -(float(candidate.shock_drop_pct) / 100.0):
            cooldown = max(cooldown, int(candidate.shock_hold_days))
            defensive_latch = True
            pos_streak = 0

        if float(candidate.dd_trigger_pct) > 0 and dd_pct >= float(candidate.dd_trigger_pct):
            cooldown = max(cooldown, 1)
            defensive_latch = True
            pos_streak = 0

        # Release logic: need positive closes after cooldown when latch is on.
        if idx >= 2 and prev_ret > 0:
            pos_streak += 1
        elif idx >= 2:
            pos_streak = 0

        enforce_defensive = False
        if cooldown > 0:
            enforce_defensive = True
            cooldown -= 1
        elif defensive_latch:
            needed = max(1, int(candidate.reentry_pos_days))
            enforce_defensive = pos_streak < needed
            if not enforce_defensive:
                defensive_latch = False
                pos_streak = 0

        if enforce_defensive and _is_risk_on_target(base_t):
            out[d] = {candidate.defensive_symbol: 1.0}
        else:
            out[d] = base_t

    return out


def _run_candidate_windows(
    *,
    candidate_id: str,
    windows: list[tuple[str, date, date]],
    symbols: list[str],
    aligned_days: list[date],
    price_history: dict[str, list[tuple[date, float]]],
    close_map_by_symbol: dict[str, dict[date, float]],
    minute_by_day_symbol: dict[date, dict[str, list[tuple[datetime, float, float, float, float]]]],
    split_ratio_by_day_symbol: dict[date, dict[str, float]],
    target_by_day: dict[date, dict[str, float]],
    threshold_by_day: dict[date, float],
    profile: iv.LockedProfile,
    initial_equity: float,
    slippage_bps: float,
    sell_fee_bps: float,
    rebalance_time_ny: dt_time,
    stop_pct: float,
    rv_gate: float,
) -> pd.DataFrame:
    rows = []
    for wname, sday, eday in windows:
        day_rows = _simulate_with_table(
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            target_by_day=target_by_day,
            rebalance_threshold_by_day=threshold_by_day,
            profile=profile,
            start_day=sday,
            end_day=eday,
            initial_equity=float(initial_equity),
            slippage_bps=float(slippage_bps),
            sell_fee_bps=float(sell_fee_bps),
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=rebalance_time_ny,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
            enable_protective_stop=(float(stop_pct) > 0),
            protective_stop_pct=float(stop_pct),
            stop_scope="inverse_only",
            rv_gate_min_pct=float(rv_gate),
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
                "candidate_id": candidate_id,
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


def _summarize(candidate_windows: pd.DataFrame, baseline_windows: pd.DataFrame, dd_penalty: float) -> dict[str, Any]:
    merged = candidate_windows.merge(
        baseline_windows[["window", "return_pct", "max_dd_pct"]],
        on="window",
        suffixes=("", "_baseline"),
    )
    avg_ret = float(merged["return_pct"].mean())
    avg_dd = float(merged["max_dd_pct"].mean())
    score = float(avg_ret - float(dd_penalty) * avg_dd)
    avg_ret_baseline = float(merged["return_pct_baseline"].mean())
    avg_dd_baseline = float(merged["max_dd_pct_baseline"].mean())

    return {
        "candidate_id": str(merged.iloc[0]["candidate_id"]),
        "avg_return_pct": round(avg_ret, 4),
        "avg_max_dd_pct": round(avg_dd, 4),
        "score": round(score, 4),
        "ret_wins": int((merged["return_pct"] > merged["return_pct_baseline"]).sum()),
        "dd_wins": int((merged["max_dd_pct"] < merged["max_dd_pct_baseline"]).sum()),
        "both_wins": int(
            ((merged["return_pct"] > merged["return_pct_baseline"]) &
             (merged["max_dd_pct"] < merged["max_dd_pct_baseline"]))
            .sum()
        ),
        "delta_avg_return_pct": round(avg_ret - avg_ret_baseline, 4),
        "delta_avg_max_dd_pct": round(avg_dd - avg_dd_baseline, 4),
        "strict_accept": bool((avg_ret > avg_ret_baseline) and (avg_dd < avg_dd_baseline)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Overlay research around locked C_sp4.7 profile (no existing code edits)")
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
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "configs" / "overlay_grid_v1.json"))
    ap.add_argument("--max-candidates", type=int, default=0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    # Load environment variables for Alpaca data access.
    ab._load_env_file(args.env_file, override=bool(args.env_override))

    alpaca = AlpacaConfig.from_env(paper=(args.mode == "paper"), data_feed=args.data_feed)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    quick_names = [str(x) for x in cfg["quick_windows"]]
    full_names = [str(x) for x in cfg["full_windows"]]

    max_delta = max([WINDOWS[w] for w in full_names], key=lambda x: (x.years, x.months))
    earliest_start = args.end_date - max_delta

    lookback_start = datetime.combine(earliest_start - timedelta(days=1200), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(args.end_date + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

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

    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map,
    )

    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(args.initial_equity),
        warmup_days=int(cfg.get("warmup_days", 260)),
    )

    base_targets, base_thresholds = _build_targets_for_engine(
        engine="fev1",
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=float(cfg["rebalance_threshold"]),
        controlplane_threshold_cap=float(cfg.get("controlplane_threshold_cap", 0.5)),
        controlplane_hysteresis_enter=float(cfg.get("controlplane_hysteresis_enter", 0.62)),
        controlplane_hysteresis_exit=float(cfg.get("controlplane_hysteresis_exit", 0.58)),
        controlplane_hysteresis_enter_days=int(cfg.get("controlplane_hysteresis_enter_days", 2)),
        controlplane_hysteresis_exit_days=int(cfg.get("controlplane_hysteresis_exit_days", 2)),
    )

    start_day = _align_start(aligned_days, earliest_start, args.end_date)
    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=start_day,
        end_day=args.end_date,
        feed=alpaca.data_feed,
    )

    profile_rt = rt_v1.PROFILES[args.strategy_profile]
    base_profile = iv.LockedProfile(
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

    # Locked profile values: C_sp4.7_rv75_tr1.10_th1.20
    locked_profile = _build_scaled_profile(base_profile, trail_scale=1.10, threshold_scale=1.20)
    locked_stop_pct = 4.7
    locked_rv_gate = 75.0

    quick_windows: list[tuple[str, date, date]] = []
    full_windows: list[tuple[str, date, date]] = []
    for wn in quick_names:
        cal_start = args.end_date - WINDOWS[wn]
        quick_windows.append((wn, _align_start(aligned_days, cal_start, args.end_date), args.end_date))
    for wn in full_names:
        cal_start = args.end_date - WINDOWS[wn]
        full_windows.append((wn, _align_start(aligned_days, cal_start, args.end_date), args.end_date))

    baseline_quick = _run_candidate_windows(
        candidate_id="LOCKED_C_sp4.7_rv75_tr1.10_th1.20",
        windows=quick_windows,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        target_by_day=base_targets,
        threshold_by_day=base_thresholds,
        profile=locked_profile,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        rebalance_time_ny=_parse_hhmm(args.rebalance_time),
        stop_pct=locked_stop_pct,
        rv_gate=locked_rv_gate,
    )

    # Build overlay candidate grid.
    candidates = [
        OverlayCandidate(
            shock_drop_pct=float(sh),
            shock_hold_days=int(hd),
            dd_trigger_pct=float(dd),
            dd_window_days=int(dw),
            reentry_pos_days=int(re),
            defensive_symbol=str(ds),
        )
        for sh, hd, dd, dw, re, ds in itertools.product(
            cfg["shock_drop_pct"],
            cfg["shock_hold_days"],
            cfg["dd_trigger_pct"],
            cfg["dd_window_days"],
            cfg["reentry_pos_days"],
            cfg["defensive_symbol"],
        )
    ]

    if int(args.max_candidates) > 0:
        candidates = candidates[: int(args.max_candidates)]

    quick_metrics = []
    quick_ranked = []

    for c in candidates:
        overlay_targets = _overlay_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            candidate=c,
        )
        cw = _run_candidate_windows(
            candidate_id=c.cid,
            windows=quick_windows,
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
            target_by_day=overlay_targets,
            threshold_by_day=base_thresholds,
            profile=locked_profile,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            rebalance_time_ny=_parse_hhmm(args.rebalance_time),
            stop_pct=locked_stop_pct,
            rv_gate=locked_rv_gate,
        )
        quick_metrics.append(cw)
        s = _summarize(cw, baseline_quick, float(cfg["dd_penalty"]))
        s.update(
            {
                "shock_drop_pct": c.shock_drop_pct,
                "shock_hold_days": c.shock_hold_days,
                "dd_trigger_pct": c.dd_trigger_pct,
                "dd_window_days": c.dd_window_days,
                "reentry_pos_days": c.reentry_pos_days,
                "defensive_symbol": c.defensive_symbol,
            }
        )
        quick_ranked.append(s)

    quick_metrics_df = pd.concat(quick_metrics, ignore_index=True) if quick_metrics else pd.DataFrame()
    quick_ranked_df = pd.DataFrame(quick_ranked).sort_values(["score", "avg_return_pct"], ascending=[False, False])

    top_k = int(cfg["top_k_from_quick"])
    selected = quick_ranked_df.head(top_k)["candidate_id"].tolist() if not quick_ranked_df.empty else []

    full_metrics = []
    full_ranked = []
    for cid in selected:
        row = quick_ranked_df[quick_ranked_df["candidate_id"] == cid].iloc[0]
        c = OverlayCandidate(
            shock_drop_pct=float(row["shock_drop_pct"]),
            shock_hold_days=int(row["shock_hold_days"]),
            dd_trigger_pct=float(row["dd_trigger_pct"]),
            dd_window_days=int(row["dd_window_days"]),
            reentry_pos_days=int(row["reentry_pos_days"]),
            defensive_symbol=str(row["defensive_symbol"]),
        )
        overlay_targets = _overlay_targets(
            aligned_days=aligned_days,
            close_series=close_series,
            base_target_by_day=base_targets,
            candidate=c,
        )
        fw = _run_candidate_windows(
            candidate_id=cid,
            windows=full_windows,
            symbols=symbols,
            aligned_days=aligned_days,
            price_history=price_history,
            close_map_by_symbol=close_map_by_symbol,
            minute_by_day_symbol=minute_by_day_symbol,
            split_ratio_by_day_symbol=split_ratio_by_day_symbol,
            target_by_day=overlay_targets,
            threshold_by_day=base_thresholds,
            profile=locked_profile,
            initial_equity=float(args.initial_equity),
            slippage_bps=float(args.slippage_bps),
            sell_fee_bps=float(args.sell_fee_bps),
            rebalance_time_ny=_parse_hhmm(args.rebalance_time),
            stop_pct=locked_stop_pct,
            rv_gate=locked_rv_gate,
        )
        full_metrics.append(fw)

    baseline_full = _run_candidate_windows(
        candidate_id="LOCKED_C_sp4.7_rv75_tr1.10_th1.20",
        windows=full_windows,
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
        target_by_day=base_targets,
        threshold_by_day=base_thresholds,
        profile=locked_profile,
        initial_equity=float(args.initial_equity),
        slippage_bps=float(args.slippage_bps),
        sell_fee_bps=float(args.sell_fee_bps),
        rebalance_time_ny=_parse_hhmm(args.rebalance_time),
        stop_pct=locked_stop_pct,
        rv_gate=locked_rv_gate,
    )

    if full_metrics:
        full_metrics_df = pd.concat(full_metrics, ignore_index=True)
        for cid in selected:
            fw = full_metrics_df[full_metrics_df["candidate_id"] == cid]
            s = _summarize(fw, baseline_full, float(cfg["dd_penalty"]))
            r = quick_ranked_df[quick_ranked_df["candidate_id"] == cid].iloc[0]
            s.update(
                {
                    "shock_drop_pct": r["shock_drop_pct"],
                    "shock_hold_days": r["shock_hold_days"],
                    "dd_trigger_pct": r["dd_trigger_pct"],
                    "dd_window_days": r["dd_window_days"],
                    "reentry_pos_days": r["reentry_pos_days"],
                    "defensive_symbol": r["defensive_symbol"],
                }
            )
            full_ranked.append(s)
        full_ranked_df = pd.DataFrame(full_ranked).sort_values(["score", "avg_return_pct"], ascending=[False, False])
    else:
        full_metrics_df = pd.DataFrame()
        full_ranked_df = pd.DataFrame()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"csp47_overlay_{stamp}" if not args.tag else f"csp47_overlay_{args.tag}_{stamp}"
    out_dir = ROOT / "csp47_overlay_research_v1" / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if not quick_metrics_df.empty:
        quick_metrics_df.to_csv(out_dir / "quick_window_metrics.csv", index=False)
    if not quick_ranked_df.empty:
        quick_ranked_df.to_csv(out_dir / "quick_ranked.csv", index=False)
    if not full_metrics_df.empty:
        full_metrics_df.to_csv(out_dir / "full_window_metrics.csv", index=False)
    if not full_ranked_df.empty:
        full_ranked_df.to_csv(out_dir / "full_ranked.csv", index=False)
        full_ranked_df.head(30).to_csv(out_dir / "leaderboard_top30.csv", index=False)

    baseline_full.to_csv(out_dir / "baseline_full_windows_locked47.csv", index=False)

    meta = {
        "run_id": run_id,
        "reports_dir": str(out_dir),
        "baseline_candidate": "LOCKED_C_sp4.7_rv75_tr1.10_th1.20",
        "candidate_count": len(candidates),
        "selected_for_full": len(selected),
        "quick_windows": quick_names,
        "full_windows": full_names,
        "top_k_from_quick": top_k,
        "strict_accept_count": int(full_ranked_df["strict_accept"].sum()) if not full_ranked_df.empty else 0,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps(meta, indent=2))
    if not full_ranked_df.empty:
        print("\nTOP10")
        print(full_ranked_df.head(10).to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
