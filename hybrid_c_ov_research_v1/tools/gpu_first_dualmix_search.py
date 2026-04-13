#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import cupy as cp
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
from csp47_overlay_research_v1.tools.sweep_csp47_overlays import _build_scaled_profile
from protective_stop_variant_v2.tools.export_last30_daybyday import _build_targets_for_engine, _parse_hhmm, _simulate_with_table
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as ab
import switch_runtime_v1.runtime_switch_loop as rt_v1

WINDOWS = {
    "1m": relativedelta(months=1),
    "2m": relativedelta(months=2),
    "3m": relativedelta(months=3),
    "4m": relativedelta(months=4),
    "5m": relativedelta(months=5),
    "6m": relativedelta(months=6),
    "1y": relativedelta(years=1),
    "2y": relativedelta(years=2),
}
SHORT_WINDOWS = ["1m", "2m", "3m", "4m", "5m", "6m", "1y"]


@dataclass(frozen=True)
class ParamSet:
    bias: float
    a20: float
    a60: float
    b_rv: float
    b_dd: float
    temp: float
    floor: float
    ceil: float


def _rolling_features(close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    # Features computed at day t from data up to t-1 to avoid lookahead.
    n = close.shape[0]
    ret = np.zeros(n, dtype=np.float64)
    ret[1:] = np.where(close[:-1] > 0, close[1:] / close[:-1] - 1.0, 0.0)
    mom20 = np.full(n, np.nan, dtype=np.float64)
    mom60 = np.full(n, np.nan, dtype=np.float64)
    rv20 = np.full(n, np.nan, dtype=np.float64)
    dd60 = np.full(n, np.nan, dtype=np.float64)
    for i in range(n):
        p = i - 1
        if p >= 20 and close[p - 20] > 0:
            mom20[i] = (close[p] / close[p - 20] - 1.0) * 100.0
        if p >= 60 and close[p - 60] > 0:
            mom60[i] = (close[p] / close[p - 60] - 1.0) * 100.0
        if p >= 20:
            win = ret[p - 19 : p + 1]
            rv20[i] = float(np.std(win, ddof=0) * 100.0)
        if p >= 60:
            w = close[p - 59 : p + 1]
            peak = float(np.max(w))
            dd60[i] = ((peak - float(w[-1])) / peak * 100.0) if peak > 0 else 0.0
    # Fill early NaN with neutral zeros.
    for arr in (mom20, mom60, rv20, dd60):
        arr[np.isnan(arr)] = 0.0
    return mom20, mom60, rv20, dd60


def _window_indices(days: list[date], end_day: date) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for w, rel in WINDOWS.items():
        cstart = end_day - rel
        s = 0
        for i, d in enumerate(days):
            if d >= cstart:
                s = i
                break
        out[w] = (s, len(days) - 1)
    return out


def _window_return_from_cum(cum: cp.ndarray, s: int, e: int) -> cp.ndarray:
    if s <= 0:
        return (cum[:, e] - 1.0) * 100.0
    return ((cum[:, e] / cum[:, s - 1]) - 1.0) * 100.0


def _make_param_grid() -> list[ParamSet]:
    # Large but tractable grid for GPU proxy.
    biases = np.arange(-2.5, 2.6, 0.5)          # 11
    a20s = np.arange(0.03, 0.21, 0.03)          # 6
    a60s = np.arange(0.00, 0.13, 0.02)          # 7
    brvs = np.arange(0.12, 0.49, 0.06)          # 7
    bdds = np.arange(0.02, 0.11, 0.02)          # 5
    temps = np.array([0.7, 1.0, 1.3, 1.6])      # 4
    floors = np.array([0.0, 0.1, 0.2])          # 3
    ceils = np.array([0.8, 0.9, 1.0])           # 3
    out: list[ParamSet] = []
    for bias in biases:
        for a20 in a20s:
            for a60 in a60s:
                for brv in brvs:
                    for bdd in bdds:
                        for temp in temps:
                            for fl in floors:
                                for ce in ceils:
                                    if fl >= ce:
                                        continue
                                    out.append(
                                        ParamSet(
                                            bias=float(bias),
                                            a20=float(a20),
                                            a60=float(a60),
                                            b_rv=float(brv),
                                            b_dd=float(bdd),
                                            temp=float(temp),
                                            floor=float(fl),
                                            ceil=float(ce),
                                        )
                                    )
    return out


def _make_random_params(n: int, seed: int) -> list[ParamSet]:
    rng = np.random.default_rng(seed)
    bias = rng.uniform(-8.0, 8.0, n)
    a20 = rng.uniform(0.0, 0.8, n)
    a60 = rng.uniform(-0.2, 0.6, n)
    b_rv = rng.uniform(0.0, 1.2, n)
    b_dd = rng.uniform(0.0, 0.8, n)
    temp = rng.uniform(0.05, 1.6, n)
    floor = rng.uniform(0.0, 0.4, n)
    ceil = rng.uniform(0.6, 1.0, n)
    out: list[ParamSet] = []
    for i in range(n):
        lo = float(min(floor[i], ceil[i] - 0.05))
        hi = float(max(ceil[i], lo + 0.05))
        out.append(
            ParamSet(
                bias=float(bias[i]),
                a20=float(a20[i]),
                a60=float(a60[i]),
                b_rv=float(b_rv[i]),
                b_dd=float(b_dd[i]),
                temp=float(temp[i]),
                floor=max(0.0, min(0.95, lo)),
                ceil=max(0.05, min(1.0, hi)),
            )
        )
    return out


def _make_local_params(
    n: int,
    seed: int,
    *,
    center: ParamSet,
    sigma_bias: float,
    sigma_a20: float,
    sigma_a60: float,
    sigma_b_rv: float,
    sigma_b_dd: float,
    sigma_temp: float,
    sigma_floor: float,
    sigma_ceil: float,
) -> list[ParamSet]:
    rng = np.random.default_rng(seed)

    def _clip(v: np.ndarray, lo: float, hi: float) -> np.ndarray:
        return np.clip(v, lo, hi)

    bias = _clip(rng.normal(center.bias, sigma_bias, n), -10.0, 10.0)
    a20 = _clip(rng.normal(center.a20, sigma_a20, n), 0.0, 1.2)
    a60 = _clip(rng.normal(center.a60, sigma_a60, n), -0.5, 1.0)
    b_rv = _clip(rng.normal(center.b_rv, sigma_b_rv, n), 0.0, 2.0)
    b_dd = _clip(rng.normal(center.b_dd, sigma_b_dd, n), 0.0, 1.2)
    temp = _clip(rng.normal(center.temp, sigma_temp, n), 0.03, 3.0)
    floor = _clip(rng.normal(center.floor, sigma_floor, n), 0.0, 0.95)
    ceil = _clip(rng.normal(center.ceil, sigma_ceil, n), 0.05, 1.0)

    out: list[ParamSet] = []
    for i in range(n):
        lo = float(floor[i])
        hi = float(ceil[i])
        if lo >= hi:
            lo = max(0.0, min(0.94, hi - 0.05))
            hi = min(1.0, max(0.06, lo + 0.05))
        out.append(
            ParamSet(
                bias=float(bias[i]),
                a20=float(a20[i]),
                a60=float(a60[i]),
                b_rv=float(b_rv[i]),
                b_dd=float(b_dd[i]),
                temp=float(temp[i]),
                floor=float(lo),
                ceil=float(hi),
            )
        )
    return out


def _load_c_ov_reports(report_dir: Path, start_day: date, end_day: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    c_path = report_dir / "C_sp4.7_rv75_tr1.10_th1.20_2024-04-10_to_2026-04-10_10k.csv"
    ov_path = report_dir / "OV_sh6_h1_dd0w20_re1_SOXS_2024-04-10_to_2026-04-10_10k.csv"
    c = pd.read_csv(c_path)
    ov = pd.read_csv(ov_path)
    for df in (c, ov):
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    c = c[(c["Date"] >= start_day) & (c["Date"] <= end_day)].copy()
    ov = ov[(ov["Date"] >= start_day) & (ov["Date"] <= end_day)].copy()
    merged = c[["Date", "Return %", "End-of-Day Ticker"]].rename(columns={"Return %": "ret_c", "End-of-Day Ticker": "ticker_c"}).merge(
        ov[["Date", "Return %", "End-of-Day Ticker"]].rename(columns={"Return %": "ret_ov", "End-of-Day Ticker": "ticker_ov"}),
        on="Date",
        how="inner",
    )
    merged["ret_c"] = pd.to_numeric(merged["ret_c"], errors="coerce")
    merged["ret_ov"] = pd.to_numeric(merged["ret_ov"], errors="coerce")
    merged = merged.sort_values("Date").reset_index(drop=True)
    return c, ov, merged


def _build_validation_data(start_day: date, end_day: date):
    cfg = {
        "rebalance_threshold": 0.05,
        "controlplane_threshold_cap": 0.50,
        "controlplane_hysteresis_enter": 0.62,
        "controlplane_hysteresis_exit": 0.58,
        "controlplane_hysteresis_enter_days": 2,
        "controlplane_hysteresis_exit_days": 2,
        "warmup_days": 260,
    }
    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(start_day - timedelta(days=1200), dt_time(0, 0), tzinfo=NY)
    lookback_end = datetime.combine(end_day + timedelta(days=1), dt_time(23, 59), tzinfo=NY)
    daily_ohlc_adj = iv._fetch_daily_ohlc(loader, symbols=symbols, start_dt=lookback_start, end_dt=lookback_end, feed=alpaca.data_feed, adjustment="all")
    daily_ohlc_raw = iv._fetch_daily_ohlc(loader, symbols=symbols, start_dt=lookback_start, end_dt=lookback_end, feed=alpaca.data_feed, adjustment="raw")
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
        initial_equity=10000.0,
        warmup_days=int(cfg["warmup_days"]),
    )
    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    _, base_thresholds = _build_targets_for_engine(
        engine="fev1",
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=float(cfg["rebalance_threshold"]),
        controlplane_threshold_cap=float(cfg["controlplane_threshold_cap"]),
        controlplane_hysteresis_enter=float(cfg["controlplane_hysteresis_enter"]),
        controlplane_hysteresis_exit=float(cfg["controlplane_hysteresis_exit"]),
        controlplane_hysteresis_enter_days=int(cfg["controlplane_hysteresis_enter_days"]),
        controlplane_hysteresis_exit_days=int(cfg["controlplane_hysteresis_exit_days"]),
    )
    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader, symbols=symbols, start_day=start_day, end_day=end_day, feed=alpaca.data_feed
    )
    profile_rt = rt_v1.PROFILES["aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m"]
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
    profile = _build_scaled_profile(base_profile, trail_scale=1.10, threshold_scale=1.20)
    return {
        "symbols": symbols,
        "aligned_days": aligned_days,
        "price_history": price_history,
        "close_map_by_symbol": close_map_by_symbol,
        "minute_by_day_symbol": minute_by_day_symbol,
        "base_thresholds": base_thresholds,
        "split_ratio": split_ratio,
        "profile": profile,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GPU-first dualmix research sweep")
    parser.add_argument("--start-day", default="2024-04-10")
    parser.add_argument("--end-day", default="2026-04-10")
    parser.add_argument("--chunk-size", type=int, default=20000)
    parser.add_argument("--top-proxy", type=int, default=60)
    parser.add_argument("--full-validate-top", type=int, default=12)
    parser.add_argument("--param-mode", choices=["grid", "random", "local"], default="grid")
    parser.add_argument("--random-samples", type=int, default=800000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--local-center-bias", type=float, default=5.613500129003333)
    parser.add_argument("--local-center-a20", type=float, default=0.13322290278341634)
    parser.add_argument("--local-center-a60", type=float, default=-0.14978923950980383)
    parser.add_argument("--local-center-b-rv", type=float, default=0.045378776655056054)
    parser.add_argument("--local-center-b-dd", type=float, default=0.011551175233580313)
    parser.add_argument("--local-center-temp", type=float, default=0.28485560299273727)
    parser.add_argument("--local-center-floor", type=float, default=0.01714487698580607)
    parser.add_argument("--local-center-ceil", type=float, default=0.757395951331836)
    parser.add_argument("--local-sigma-bias", type=float, default=1.0)
    parser.add_argument("--local-sigma-a20", type=float, default=0.08)
    parser.add_argument("--local-sigma-a60", type=float, default=0.08)
    parser.add_argument("--local-sigma-b-rv", type=float, default=0.08)
    parser.add_argument("--local-sigma-b-dd", type=float, default=0.08)
    parser.add_argument("--local-sigma-temp", type=float, default=0.12)
    parser.add_argument("--local-sigma-floor", type=float, default=0.05)
    parser.add_argument("--local-sigma-ceil", type=float, default=0.08)
    args = parser.parse_args()

    start_day = date.fromisoformat(args.start_day)
    end_day = date.fromisoformat(args.end_day)
    report_dir = ROOT / "hybrid_c_ov_research_v1" / "reports_gpu1"
    report_dir.mkdir(parents=True, exist_ok=True)

    c_df, ov_df, m = _load_c_ov_reports(ROOT / "csp47_overlay_research_v1" / "reports", start_day, end_day)
    if m.empty:
        raise RuntimeError("No overlap rows between C and OV report data for requested range.")
    days = list(m["Date"])
    days_iso = [d.isoformat() for d in days]
    T = len(days)

    # SOXL daily close for feature generation.
    ab._load_env_file(str(ROOT / ".env.dev"), override=True)
    alpaca = AlpacaConfig.from_env(paper=True, data_feed="sip")
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    soxl_ohlc = iv._fetch_daily_ohlc(
        loader,
        symbols=["SOXL"],
        start_dt=datetime.combine(start_day - timedelta(days=120), dt_time(0, 0), tzinfo=NY),
        end_dt=datetime.combine(end_day + timedelta(days=2), dt_time(0, 0), tzinfo=NY),
        feed=alpaca.data_feed,
        adjustment="all",
    )
    # _fetch_daily_ohlc returns list[(day, close, high)] per symbol.
    soxl_rows = soxl_ohlc.get("SOXL", [])
    soxl_close_by_day = {d: float(close_px) for d, close_px, _ in soxl_rows}
    close_np = np.array([float(soxl_close_by_day.get(d, np.nan)) for d in days], dtype=np.float64)
    if np.isnan(close_np).any():
        # simple forward fill fallback
        s = pd.Series(close_np).ffill().bfill()
        close_np = s.to_numpy(dtype=np.float64)
    mom20, mom60, rv20, dd60 = _rolling_features(close_np)

    # CPU benchmark returns by window from report returns (compounded).
    window_idx = _window_indices(days, end_day=end_day)
    c_ret = m["ret_c"].to_numpy(dtype=np.float64)
    ov_ret = m["ret_ov"].to_numpy(dtype=np.float64)

    def window_ret_from_daily(ret_arr: np.ndarray, s: int, e: int) -> float:
        f = np.cumprod(1.0 + (ret_arr / 100.0))
        if s <= 0:
            return (f[e] - 1.0) * 100.0
        return ((f[e] / f[s - 1]) - 1.0) * 100.0

    c_bench: dict[str, float] = {}
    ov_bench: dict[str, float] = {}
    for w, (s, e) in window_idx.items():
        c_bench[w] = window_ret_from_daily(c_ret, s, e)
        ov_bench[w] = window_ret_from_daily(ov_ret, s, e)

    # GPU arrays.
    c_ret_cp = cp.asarray(c_ret, dtype=cp.float32)
    ov_ret_cp = cp.asarray(ov_ret, dtype=cp.float32)
    mom20_cp = cp.asarray(mom20, dtype=cp.float32)
    mom60_cp = cp.asarray(mom60, dtype=cp.float32)
    rv20_cp = cp.asarray(rv20, dtype=cp.float32)
    dd60_cp = cp.asarray(dd60, dtype=cp.float32)

    if args.param_mode == "random":
        params = _make_random_params(int(args.random_samples), int(args.seed))
    elif args.param_mode == "local":
        center = ParamSet(
            bias=float(args.local_center_bias),
            a20=float(args.local_center_a20),
            a60=float(args.local_center_a60),
            b_rv=float(args.local_center_b_rv),
            b_dd=float(args.local_center_b_dd),
            temp=float(args.local_center_temp),
            floor=float(args.local_center_floor),
            ceil=float(args.local_center_ceil),
        )
        params = _make_local_params(
            int(args.random_samples),
            int(args.seed),
            center=center,
            sigma_bias=float(args.local_sigma_bias),
            sigma_a20=float(args.local_sigma_a20),
            sigma_a60=float(args.local_sigma_a60),
            sigma_b_rv=float(args.local_sigma_b_rv),
            sigma_b_dd=float(args.local_sigma_b_dd),
            sigma_temp=float(args.local_sigma_temp),
            sigma_floor=float(args.local_sigma_floor),
            sigma_ceil=float(args.local_sigma_ceil),
        )
    else:
        params = _make_param_grid()
    n_params = len(params)
    print(f"[gpu-proxy] params={n_params}, days={T}, chunk={args.chunk_size}")

    top_rows: list[dict[str, Any]] = []
    t0 = time.time()
    for start in range(0, n_params, args.chunk_size):
        end = min(start + args.chunk_size, n_params)
        chunk = params[start:end]
        n = len(chunk)
        bias = cp.asarray([p.bias for p in chunk], dtype=cp.float32)[:, None]
        a20 = cp.asarray([p.a20 for p in chunk], dtype=cp.float32)[:, None]
        a60 = cp.asarray([p.a60 for p in chunk], dtype=cp.float32)[:, None]
        brv = cp.asarray([p.b_rv for p in chunk], dtype=cp.float32)[:, None]
        bdd = cp.asarray([p.b_dd for p in chunk], dtype=cp.float32)[:, None]
        temp = cp.asarray([p.temp for p in chunk], dtype=cp.float32)[:, None]
        floor = cp.asarray([p.floor for p in chunk], dtype=cp.float32)[:, None]
        ceil = cp.asarray([p.ceil for p in chunk], dtype=cp.float32)[:, None]

        score_raw = bias + a20 * mom20_cp[None, :] + a60 * mom60_cp[None, :] - brv * rv20_cp[None, :] - bdd * dd60_cp[None, :]
        weight = 1.0 / (1.0 + cp.exp(-score_raw / temp))
        weight = floor + (ceil - floor) * weight

        daily_ret = weight * c_ret_cp[None, :] + (1.0 - weight) * ov_ret_cp[None, :]
        factors = 1.0 + (daily_ret / 100.0)
        cum = cp.cumprod(factors, axis=1)
        eq = 10000.0 * cum

        rets: dict[str, cp.ndarray] = {}
        for w, (s, e) in window_idx.items():
            rets[w] = _window_return_from_cum(cum, s, e)

        short_pen = cp.zeros((n,), dtype=cp.float32)
        short_excess = cp.zeros((n,), dtype=cp.float32)
        for w in SHORT_WINDOWS:
            diff = rets[w] - np.float32(c_bench[w])
            short_pen += cp.maximum(0.0, -diff)
            short_excess += cp.maximum(0.0, diff)
        long_diff = rets["2y"] - np.float32(ov_bench["2y"])
        gate_ok = long_diff >= 0.0
        score = cp.where(gate_ok, -short_pen + 0.1 * short_excess + 0.02 * long_diff, -10000.0 - short_pen + long_diff)

        keep = min(args.top_proxy, n)
        idx = cp.argpartition(score, -keep)[-keep:]
        idx_cpu = cp.asnumpy(idx)
        score_cpu = cp.asnumpy(score[idx])
        gate_cpu = cp.asnumpy(gate_ok[idx])
        long_cpu = cp.asnumpy(long_diff[idx])
        ret_cpu = {w: cp.asnumpy(rets[w][idx]) for w in WINDOWS}
        eq_sel = cp.asnumpy(eq[idx])
        mdd_cpu = []
        for row_eq in eq_sel:
            pk = np.maximum.accumulate(row_eq)
            dd_row = np.where(pk > 0.0, (pk - row_eq) / pk * 100.0, 0.0)
            mdd_cpu.append(float(np.max(dd_row)))

        for k in range(len(idx_cpu)):
            i_local = int(idx_cpu[k])
            p = chunk[i_local]
            row = {
                "candidate_id": f"G1-{start + i_local + 1:06d}",
                "gate_ok_2y_proxy": bool(gate_cpu[k]),
                "score_proxy": float(score_cpu[k]),
                "long_diff_2y_vs_ov_proxy": float(long_cpu[k]),
                "mdd_2y_proxy": float(mdd_cpu[k]),
                "bias": p.bias,
                "a20": p.a20,
                "a60": p.a60,
                "b_rv": p.b_rv,
                "b_dd": p.b_dd,
                "temp": p.temp,
                "floor": p.floor,
                "ceil": p.ceil,
            }
            for w in WINDOWS:
                row[f"{w}_ret_proxy"] = float(ret_cpu[w][k])
            top_rows.append(row)

        if end % (args.chunk_size * 2) == 0 or end == n_params:
            print(f"[gpu-proxy] {end}/{n_params} done")

        # free chunk temporaries promptly
        del bias, a20, a60, brv, bdd, temp, floor, ceil
        del score_raw, weight, daily_ret, factors, cum, eq, rets
        cp.get_default_memory_pool().free_all_blocks()

    proxy_df = pd.DataFrame(top_rows).sort_values(["gate_ok_2y_proxy", "score_proxy"], ascending=[False, False]).reset_index(drop=True)
    # De-duplicate identical param rows retained from multiple chunks.
    proxy_df = proxy_df.drop_duplicates(subset=["bias", "a20", "a60", "b_rv", "b_dd", "temp", "floor", "ceil"]).reset_index(drop=True)
    proxy_csv = report_dir / "gpu_proxy_ranked.csv"
    proxy_df.to_csv(proxy_csv, index=False)
    print(f"[gpu-proxy] done in {time.time() - t0:.1f}s rows={len(proxy_df)}")

    # Full validation for top candidates.
    top_full = proxy_df.head(args.full_validate_top).copy()
    val_data = _build_validation_data(start_day, end_day)
    aligned_val = val_data["aligned_days"]
    idx_by_day = {d: i for i, d in enumerate(days)}

    def weight_series_for_param(p: ParamSet) -> np.ndarray:
        z = p.bias + p.a20 * mom20 + p.a60 * mom60 - p.b_rv * rv20 - p.b_dd * dd60
        w = 1.0 / (1.0 + np.exp(-(z / p.temp)))
        return p.floor + (p.ceil - p.floor) * w

    full_rows: list[dict[str, Any]] = []
    for i, (_, r) in enumerate(top_full.iterrows(), start=1):
        p = ParamSet(
            bias=float(r["bias"]),
            a20=float(r["a20"]),
            a60=float(r["a60"]),
            b_rv=float(r["b_rv"]),
            b_dd=float(r["b_dd"]),
            temp=float(r["temp"]),
            floor=float(r["floor"]),
            ceil=float(r["ceil"]),
        )
        w_arr = weight_series_for_param(p)
        # Build target_by_day by blending C and OV end-of-day ticker targets.
        c_ticker_by_day = {d: t for d, t in zip(c_df["Date"], c_df["End-of-Day Ticker"])}
        ov_ticker_by_day = {d: t for d, t in zip(ov_df["Date"], ov_df["End-of-Day Ticker"])}
        target_by_day: dict[date, dict[str, float]] = {}
        for d in aligned_val:
            if d < start_day or d > end_day:
                continue
            if d not in idx_by_day:
                continue
            w = float(w_arr[idx_by_day[d]])
            c_t = str(c_ticker_by_day.get(d, "CASH"))
            o_t = str(ov_ticker_by_day.get(d, "CASH"))
            # Normalize CASH rows to avoid non-tradable target symbols during simulation.
            if c_t == "CASH" and o_t == "CASH":
                target_by_day[d] = {}
            elif c_t == "CASH":
                target_by_day[d] = {o_t: 1.0}
            elif o_t == "CASH":
                target_by_day[d] = {c_t: 1.0}
            elif c_t == o_t:
                target_by_day[d] = {c_t: 1.0}
            else:
                target_by_day[d] = {c_t: w, o_t: (1.0 - w)}

        sim_rows = _simulate_with_table(
            symbols=val_data["symbols"],
            aligned_days=val_data["aligned_days"],
            price_history=val_data["price_history"],
            close_map_by_symbol=val_data["close_map_by_symbol"],
            minute_by_day_symbol=val_data["minute_by_day_symbol"],
            target_by_day=target_by_day,
            rebalance_threshold_by_day=val_data["base_thresholds"],
            profile=val_data["profile"],
            start_day=start_day,
            end_day=end_day,
            initial_equity=10000.0,
            slippage_bps=1.0,
            sell_fee_bps=1.0,
            runtime_profit_lock_order_type="market_order",
            runtime_stop_price_offset_bps=2.0,
            rebalance_time_ny=_parse_hhmm("15:55"),
            split_ratio_by_day_symbol=val_data["split_ratio"],
            enable_protective_stop=True,
            protective_stop_pct=4.7,
            stop_scope="inverse_only",
            rv_gate_min_pct=75.0,
            rv_gate_window=20,
        )
        wm = {}
        for wname, rel in WINDOWS.items():
            cstart = end_day - rel
            rr = [x for x in sim_rows if cstart <= date.fromisoformat(str(x["Date"])) <= end_day]
            if not rr:
                wm[wname] = {"ret": 0.0, "mdd": 0.0}
                continue
            s = float(rr[0]["Day Start Equity"])
            e = float(rr[-1]["Day End Equity"])
            ret = ((e / s) - 1.0) * 100.0 if s > 0 else 0.0
            mdd = max(float(x["Drawdown %"]) for x in rr)
            wm[wname] = {"ret": ret, "mdd": mdd}

        short_pen = sum(max(0.0, c_bench[w] - wm[w]["ret"]) for w in SHORT_WINDOWS)
        short_excess = sum(max(0.0, wm[w]["ret"] - c_bench[w]) for w in SHORT_WINDOWS)
        long_diff = wm["2y"]["ret"] - ov_bench["2y"]
        gate_ok = long_diff >= 0.0
        score = (-short_pen + 0.1 * short_excess + 0.02 * long_diff) if gate_ok else (-10000.0 - short_pen + long_diff)

        rec = {
            "candidate_id": str(r["candidate_id"]),
            "gate_ok_2y": gate_ok,
            "score": score,
            "short_penalty_sum": short_pen,
            "short_excess_sum": short_excess,
            "long_diff_2y_vs_ov": long_diff,
            "bias": p.bias,
            "a20": p.a20,
            "a60": p.a60,
            "b_rv": p.b_rv,
            "b_dd": p.b_dd,
            "temp": p.temp,
            "floor": p.floor,
            "ceil": p.ceil,
        }
        for w in WINDOWS:
            rec[f"{w}_ret"] = wm[w]["ret"]
            rec[f"{w}_mdd"] = wm[w]["mdd"]
        full_rows.append(rec)
        print(f"[full-validate] {i}/{len(top_full)} done")

    full_df = pd.DataFrame(full_rows).sort_values(["gate_ok_2y", "score"], ascending=[False, False]).reset_index(drop=True)
    full_csv = report_dir / "gpu_full_validated.csv"
    full_df.to_csv(full_csv, index=False)

    gated = full_df[full_df["gate_ok_2y"]].copy()
    strict = None
    for _, r in gated.iterrows():
        if all(float(r[f"{w}_ret"]) >= c_bench[w] for w in SHORT_WINDOWS):
            strict = r.to_dict()
            break
    nearest = gated.sort_values(["short_penalty_sum", "2y_ret"], ascending=[True, False]).head(1)
    nearest_rec = nearest.iloc[0].to_dict() if not nearest.empty else None

    summary = {
        "gpu_device": cp.cuda.runtime.getDeviceProperties(0)["name"].decode(),
        "param_mode": args.param_mode,
        "random_samples": int(args.random_samples) if args.param_mode == "random" else None,
        "local_center": (
            {
                "bias": float(args.local_center_bias),
                "a20": float(args.local_center_a20),
                "a60": float(args.local_center_a60),
                "b_rv": float(args.local_center_b_rv),
                "b_dd": float(args.local_center_b_dd),
                "temp": float(args.local_center_temp),
                "floor": float(args.local_center_floor),
                "ceil": float(args.local_center_ceil),
            }
            if args.param_mode == "local"
            else None
        ),
        "proxy_candidates_evaluated": int(n_params),
        "proxy_candidates_ranked": int(len(proxy_df)),
        "full_candidates_evaluated": int(len(full_df)),
        "gate_ok_count_full": int(len(gated)),
        "strict_match_candidate": strict,
        "nearest_gate_candidate": nearest_rec,
        "reports": {
            "proxy_csv": str(proxy_csv),
            "full_csv": str(full_csv),
        },
    }
    summary_json = report_dir / "gpu_search_summary.json"
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
