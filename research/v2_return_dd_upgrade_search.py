#!/usr/bin/env python3
"""Research-only search for v2 controlplane params.

Goal:
- Beat baseline v2 terminal equity over 2024-04-10..2026-04-10.
- Reduce drawdown/loss severity versus baseline.

Safety:
- Writes only under research/reports/.
- Does NOT modify existing runtime code.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, asdict
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from statistics import mean

import pandas as pd

import switch_runtime_v1.runtime_switch_loop as rt_v1
import switch_runtime_v1.runtime_switch_loop_v2_controlplane as rt_v2
import switch_runtime_v1.tools.historical_runtime_v1_v2_ab as hv
from composer_original.tools import intraday_profit_lock_verification as iv
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "research" / "reports" / f"v2_upgrade_search_{date.today().strftime('%Y%m%d')}"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Candidate:
    cid: str
    base_rebalance_threshold: float
    controlplane_threshold_cap: float
    controlplane_hysteresis_enter: float
    controlplane_hysteresis_exit: float
    controlplane_hysteresis_enter_days: int
    controlplane_hysteresis_exit_days: int
    eval_hhmm: str


@dataclass
class Metrics:
    cid: str
    terminal_equity: float
    total_return_pct: float
    avg_monthly_return_pct: float
    median_monthly_return_pct: float
    win_months: int
    loss_months: int
    avg_monthly_maxdd_pct: float
    worst_month_return_pct: float
    worst_month_maxdd_pct: float


# ---------- fixed experimental config (same execution model) ----------
START = date(2024, 4, 10)
END = date(2026, 4, 10)
INITIAL = 10_000.0
PROFILE_NAME = "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"
MODE = "paper"
FEED = "sip"
SLIPPAGE_BPS = 1.0
SELL_FEE_BPS = 0.0
RUNTIME_PROFIT_LOCK_ORDER_TYPE = "market_order"
RUNTIME_STOP_OFFSET_BPS = 2.0
WARMUP_DAYS = 260
DAILY_LOOKBACK_DAYS = 800


def _monthly_windows_day10(start_day: date, end_day: date) -> list[tuple[date, date]]:
    """Generate chained windows anchored on day=10 for monthly compounding."""

    def add_month(d: date) -> date:
        y = d.year
        m = d.month + 1
        if m == 13:
            y += 1
            m = 1
        return date(y, m, 10)

    out: list[tuple[date, date]] = []
    cur = start_day
    while cur < end_day:
        nxt = add_month(cur)
        if nxt > end_day:
            nxt = end_day
        out.append((cur, nxt))
        if nxt == end_day:
            break
        cur = nxt
    return out


def _make_profile() -> iv.LockedProfile:
    p = rt_v1.PROFILES[PROFILE_NAME]
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


def _prepare_data():
    hv._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=(MODE == "paper"), data_feed=FEED)
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        START - timedelta(days=max(DAILY_LOOKBACK_DAYS, WARMUP_DAYS + 20)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(END + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_adj = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="all",
    )
    daily_raw = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment="raw",
    )

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_adj, symbols=symbols)

    high_map_by_symbol: dict[str, dict[date, float]] = {}
    for sym in symbols:
        hmap: dict[date, float] = {}
        for d, _close_px, high_px in daily_adj.get(sym, []):
            hmap[d] = float(high_px)
        high_map_by_symbol[sym] = {d: float(hmap[d]) for d in aligned_days if d in hmap}

    _, _, raw_close_map_by_symbol = iv._align_daily_close_history(daily_raw, symbols=symbols)
    split_ratio_by_day_symbol = iv._build_split_ratio_by_day_symbol(
        aligned_days=aligned_days,
        symbols=symbols,
        adjusted_close_map=close_map_by_symbol,
        raw_close_map=raw_close_map_by_symbol,
    )

    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=float(INITIAL),
        warmup_days=int(WARMUP_DAYS),
    )

    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=START,
        end_day=END,
        feed=alpaca.data_feed,
    )

    return {
        "symbols": symbols,
        "aligned_days": aligned_days,
        "price_history": price_history,
        "close_map_by_symbol": close_map_by_symbol,
        "high_map_by_symbol": high_map_by_symbol,
        "split_ratio_by_day_symbol": split_ratio_by_day_symbol,
        "baseline_target_by_day": baseline_target_by_day,
        "close_series": close_series,
        "minute_by_day_symbol": minute_by_day_symbol,
    }


def _simulate_candidate(c: Candidate, data: dict) -> tuple[Metrics, list[dict]]:
    # Build day-wise v2 targets/thresholds from candidate control-plane params.
    (
        _v1_targets,
        _v1_thresholds,
        _v1_variants,
        v2_targets,
        v2_thresholds,
        _v2_variants,
    ) = hv._build_switch_targets_and_thresholds(
        aligned_days=data["aligned_days"],
        symbols=data["symbols"],
        close_series=data["close_series"],
        baseline_target_by_day=data["baseline_target_by_day"],
        base_rebalance_threshold=float(c.base_rebalance_threshold),
        controlplane_threshold_cap=float(c.controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(c.controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(c.controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(c.controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(c.controlplane_hysteresis_exit_days),
    )

    hh, mm = [int(x) for x in c.eval_hhmm.split(":")]
    eval_time = dt_time(hh, mm)

    profile = _make_profile()
    windows = _monthly_windows_day10(START, END)

    principal = float(INITIAL)
    rows: list[dict] = []
    for idx, (ws, we) in enumerate(windows, start=1):
        r = hv._simulate_intraday(
            symbols=data["symbols"],
            aligned_days=data["aligned_days"],
            price_history=data["price_history"],
            close_map_by_symbol=data["close_map_by_symbol"],
            high_map_by_symbol=data["high_map_by_symbol"],
            minute_by_day_symbol=data["minute_by_day_symbol"],
            target_by_day=v2_targets,
            rebalance_threshold_by_day=v2_thresholds,
            profile=profile,
            start_day=ws,
            end_day=we,
            initial_equity=float(principal),
            slippage_bps=float(SLIPPAGE_BPS),
            sell_fee_bps=float(SELL_FEE_BPS),
            runtime_profit_lock_order_type=str(RUNTIME_PROFIT_LOCK_ORDER_TYPE),
            runtime_stop_price_offset_bps=float(RUNTIME_STOP_OFFSET_BPS),
            rebalance_time_ny=eval_time,
            split_ratio_by_day_symbol=data["split_ratio_by_day_symbol"],
        )

        final = float(r.final_equity)
        ret = (final / principal - 1.0) * 100.0 if principal > 0 else 0.0
        rows.append(
            {
                "cid": c.cid,
                "window_idx": idx,
                "period": f"{ws.isoformat()} to {we.isoformat()}",
                "start_principal": principal,
                "final_equity": final,
                "pnl_usd": final - principal,
                "return_pct": ret,
                "maxdd_pct": float(r.max_drawdown_pct),
                "maxdd_usd": float(r.max_drawdown_usd),
                "eval_hhmm": c.eval_hhmm,
            }
        )
        principal = final

    m = Metrics(
        cid=c.cid,
        terminal_equity=float(rows[-1]["final_equity"]),
        total_return_pct=float((rows[-1]["final_equity"] / INITIAL - 1.0) * 100.0),
        avg_monthly_return_pct=float(mean(x["return_pct"] for x in rows)),
        median_monthly_return_pct=float(pd.Series([x["return_pct"] for x in rows]).median()),
        win_months=int(sum(1 for x in rows if x["return_pct"] > 0)),
        loss_months=int(sum(1 for x in rows if x["return_pct"] < 0)),
        avg_monthly_maxdd_pct=float(mean(x["maxdd_pct"] for x in rows)),
        worst_month_return_pct=float(min(x["return_pct"] for x in rows)),
        worst_month_maxdd_pct=float(max(x["maxdd_pct"] for x in rows)),
    )
    return m, rows


def _build_candidates() -> list[Candidate]:
    """Targeted but broad grid; keeps runtime manageable."""
    # Centered around known-stable v2 defaults, with bounded exploration.
    caps = [0.40, 0.50, 0.60]
    enters = [0.60, 0.62, 0.64]
    exits = [0.54, 0.56]
    enter_days = [2, 3]
    exit_days = [1, 2]
    base_thresholds = [0.04, 0.05, 0.06]
    eval_times = ["15:55", "15:56"]

    # Keep only coherent hysteresis pairs.
    combos = []
    for bt, cap, en, ex, ed, xd, et in itertools.product(
        base_thresholds, caps, enters, exits, enter_days, exit_days, eval_times
    ):
        if en <= ex:
            continue
        # modest guard to avoid extremely jumpy combos
        if cap < bt:
            continue
        combos.append((bt, cap, en, ex, ed, xd, et))

    # deterministic thinning to keep run stable on workstation
    # (evaluate every 6th combo + always include baseline).
    sampled = [x for i, x in enumerate(combos) if i % 6 == 0]

    cands: list[Candidate] = []
    # baseline v2 as explicit candidate
    cands.append(
        Candidate(
            cid="BASE_V2",
            base_rebalance_threshold=0.05,
            controlplane_threshold_cap=0.50,
            controlplane_hysteresis_enter=0.62,
            controlplane_hysteresis_exit=0.58,
            controlplane_hysteresis_enter_days=2,
            controlplane_hysteresis_exit_days=2,
            eval_hhmm="15:55",
        )
    )
    for i, (bt, cap, en, ex, ed, xd, et) in enumerate(sampled, start=1):
        cid = f"UPG2Y-{i:04d}"
        cands.append(
            Candidate(
                cid=cid,
                base_rebalance_threshold=float(bt),
                controlplane_threshold_cap=float(cap),
                controlplane_hysteresis_enter=float(en),
                controlplane_hysteresis_exit=float(ex),
                controlplane_hysteresis_enter_days=int(ed),
                controlplane_hysteresis_exit_days=int(xd),
                eval_hhmm=str(et),
            )
        )
    return cands


def main() -> int:
    data = _prepare_data()
    candidates = _build_candidates()

    metrics_rows: list[dict] = []
    monthly_rows: list[dict] = []

    for i, c in enumerate(candidates, start=1):
        m, month_rows = _simulate_candidate(c, data)
        r = asdict(c)
        r.update(asdict(m))
        metrics_rows.append(r)
        monthly_rows.extend([{**asdict(c), **x} for x in month_rows])
        if i % 10 == 0:
            print(f"progress {i}/{len(candidates)}", flush=True)

    mdf = pd.DataFrame(metrics_rows)
    wdf = pd.DataFrame(monthly_rows)

    base = mdf.loc[mdf["cid"] == "BASE_V2"].iloc[0]

    # strict dominance: higher terminal + better avg DD + better worst loss + better worst DD
    strict = mdf[
        (mdf["terminal_equity"] > float(base["terminal_equity"]))
        & (mdf["avg_monthly_maxdd_pct"] < float(base["avg_monthly_maxdd_pct"]))
        & (mdf["worst_month_return_pct"] > float(base["worst_month_return_pct"]))
        & (mdf["worst_month_maxdd_pct"] < float(base["worst_month_maxdd_pct"]))
    ].copy()

    # soft score for ranking if strict set is sparse.
    mdf["score"] = (
        (mdf["terminal_equity"] - float(base["terminal_equity"])) / max(1.0, float(base["terminal_equity"]))
        - 0.7 * (mdf["avg_monthly_maxdd_pct"] - float(base["avg_monthly_maxdd_pct"])) / 100.0
        - 0.6 * (float(base["worst_month_maxdd_pct"]) - mdf["worst_month_maxdd_pct"]) * (-1.0) / 100.0
        + 0.4 * (mdf["worst_month_return_pct"] - float(base["worst_month_return_pct"])) / 100.0
    )

    ranked = mdf.sort_values(["score", "terminal_equity"], ascending=[False, False]).reset_index(drop=True)

    out_metrics = REPORT_DIR / "candidate_metrics.csv"
    out_monthly = REPORT_DIR / "candidate_monthly_windows.csv"
    out_ranked = REPORT_DIR / "ranked_top20.csv"
    out_strict = REPORT_DIR / "strict_dominance.csv"
    out_summary = REPORT_DIR / "summary.json"

    mdf.to_csv(out_metrics, index=False)
    wdf.to_csv(out_monthly, index=False)
    ranked.head(20).to_csv(out_ranked, index=False)
    strict.sort_values("terminal_equity", ascending=False).to_csv(out_strict, index=False)

    summary = {
        "range": [START.isoformat(), END.isoformat()],
        "initial_equity": INITIAL,
        "candidate_count": int(len(mdf)),
        "baseline": {
            "cid": "BASE_V2",
            "terminal_equity": float(base["terminal_equity"]),
            "avg_monthly_maxdd_pct": float(base["avg_monthly_maxdd_pct"]),
            "worst_month_return_pct": float(base["worst_month_return_pct"]),
            "worst_month_maxdd_pct": float(base["worst_month_maxdd_pct"]),
        },
        "strict_dominance_count": int(len(strict)),
        "top_ranked": ranked.head(5).to_dict(orient="records"),
        "outputs": {
            "candidate_metrics": str(out_metrics),
            "candidate_monthly_windows": str(out_monthly),
            "ranked_top20": str(out_ranked),
            "strict_dominance": str(out_strict),
        },
    }
    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
