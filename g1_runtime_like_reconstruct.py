from __future__ import annotations
import argparse
import csv
import json
import sys
from pathlib import Path
from datetime import date, datetime, time as dt_time, timedelta

ROOT = Path('/home/chewy/projects/trading-compose-dev')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer_original.tools import intraday_profit_lock_verification as iv
import switch_runtime_v1.runtime_switch_loop as base_rt
from switch_runtime_v1.tools import historical_runtime_v1_v2_ab as hab
from csp47_overlay_research_v1.tools.sweep_csp47_overlays import OverlayCandidate, _build_scaled_profile, _overlay_targets
from protective_stop_variant_v2.tools.export_last30_daybyday import _build_targets_for_engine
from g1_412837_runtime_v1.runtime_g1_412837_loop import G1Params, _rolling_features, _weight_for_day, _pick_top_symbol, _compose_target
from soxl_growth.config import AlpacaConfig, NY, StrategyConfig
from soxl_growth.data.alpaca_data import AlpacaBarLoader


def run(start_day: date, end_day: date, out_csv: Path, env_file: Path) -> dict:
    root = Path('/home/chewy/projects/trading-compose-dev')
    iv._load_env_file(str(env_file), override=True)

    initial_equity = 10000.0
    strategy_profile = 'aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m'

    warmup_days = 260
    daily_lookback_days = 800
    rebalance_threshold = 0.05
    controlplane_threshold_cap = 0.50
    controlplane_hysteresis_enter = 0.62
    controlplane_hysteresis_exit = 0.58
    controlplane_hysteresis_enter_days = 2
    controlplane_hysteresis_exit_days = 2

    rebalance_time_ny = dt_time(15, 56)
    slippage_bps = 1.0
    sell_fee_bps = 0.0
    runtime_profit_lock_order_type = 'market_order'
    runtime_stop_price_offset_bps = 2.0

    trail_scale = 1.10
    threshold_scale = 1.20

    alpaca = AlpacaConfig.from_env(paper=True, data_feed='sip')
    loader = AlpacaBarLoader(alpaca.api_key, alpaca.api_secret)
    symbols = list(StrategyConfig().symbols)

    lookback_start = datetime.combine(
        start_day - timedelta(days=max(int(daily_lookback_days), int(warmup_days) + 20)),
        dt_time(0, 0),
        tzinfo=NY,
    )
    lookback_end = datetime.combine(end_day + timedelta(days=1), dt_time(23, 59), tzinfo=NY)

    daily_ohlc_adjusted = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment='all',
    )
    daily_ohlc_raw = iv._fetch_daily_ohlc(
        loader,
        symbols=symbols,
        start_dt=lookback_start,
        end_dt=lookback_end,
        feed=alpaca.data_feed,
        adjustment='raw',
    )

    aligned_days, price_history, close_map_by_symbol = iv._align_daily_close_history(daily_ohlc_adjusted, symbols=symbols)

    high_map_by_symbol = {}
    for sym in symbols:
        hmap = {}
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

    close_series = {s: [float(px) for _, px in price_history[s]] for s in symbols}
    baseline_target_by_day = iv._build_baseline_target_by_day(
        price_history=price_history,
        initial_equity=10000.0,
        warmup_days=int(warmup_days),
    )

    c_targets, _ = _build_targets_for_engine(
        engine='fev1',
        aligned_days=aligned_days,
        symbols=symbols,
        close_series=close_series,
        baseline_target_by_day=baseline_target_by_day,
        rebalance_threshold=float(rebalance_threshold),
        controlplane_threshold_cap=float(controlplane_threshold_cap),
        controlplane_hysteresis_enter=float(controlplane_hysteresis_enter),
        controlplane_hysteresis_exit=float(controlplane_hysteresis_exit),
        controlplane_hysteresis_enter_days=int(controlplane_hysteresis_enter_days),
        controlplane_hysteresis_exit_days=int(controlplane_hysteresis_exit_days),
    )

    ov_candidate = OverlayCandidate(
        shock_drop_pct=6.0,
        shock_hold_days=1,
        dd_trigger_pct=0.0,
        dd_window_days=20,
        reentry_pos_days=1,
        defensive_symbol='SOXS',
    )
    ov_targets = _overlay_targets(
        aligned_days=aligned_days,
        close_series=close_series,
        base_target_by_day=c_targets,
        candidate=ov_candidate,
    )

    soxl_closes = close_series.get('SOXL', [])
    mom20, mom60, rv20, dd60 = _rolling_features(soxl_closes)
    params = G1Params()

    target_by_day = {}
    rebalance_threshold_by_day = {}
    for idx, d in enumerate(aligned_days):
        w = _weight_for_day(idx, mom20, mom60, rv20, dd60, params)
        c_sym = _pick_top_symbol(dict(c_targets.get(d, {})))
        o_sym = _pick_top_symbol(dict(ov_targets.get(d, {})))
        target_by_day[d] = _compose_target(c_sym, o_sym, w)
        rebalance_threshold_by_day[d] = float(rebalance_threshold)

    minute_by_day_symbol = iv._fetch_minute_bars_by_day_symbol(
        loader,
        symbols=symbols,
        start_day=start_day,
        end_day=end_day,
        feed=alpaca.data_feed,
    )

    profile_rt = base_rt.PROFILES[strategy_profile]
    profile_base = iv.LockedProfile(
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
    profile = _build_scaled_profile(profile_base, trail_scale=trail_scale, threshold_scale=threshold_scale)

    sim = hab._simulate_intraday(
        symbols=symbols,
        aligned_days=aligned_days,
        price_history=price_history,
        close_map_by_symbol=close_map_by_symbol,
        high_map_by_symbol=high_map_by_symbol,
        minute_by_day_symbol=minute_by_day_symbol,
        target_by_day=target_by_day,
        rebalance_threshold_by_day=rebalance_threshold_by_day,
        profile=profile,
        start_day=start_day,
        end_day=end_day,
        initial_equity=float(initial_equity),
        slippage_bps=float(slippage_bps),
        sell_fee_bps=float(sell_fee_bps),
        runtime_profit_lock_order_type=str(runtime_profit_lock_order_type),
        runtime_stop_price_offset_bps=float(runtime_stop_price_offset_bps),
        rebalance_time_ny=rebalance_time_ny,
        split_ratio_by_day_symbol=split_ratio_by_day_symbol,
    )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['date', 'start_principal', 'day_pnl', 'day_return_pct', 'end_equity', 'sale_time_stock', 'new_purchase_time_stock'])
        prev = initial_equity
        for d in sim.daily:
            w.writerow([
                d.day.isoformat(),
                f'{prev:.6f}',
                f'{d.pnl:.6f}',
                f'{d.ret_pct:.6f}',
                f'{d.equity:.6f}',
                d.sale_time_stock,
                d.new_purchase_time_stock,
            ])
            prev = float(d.equity)

    return {
        'days': len(sim.daily),
        'events': len(sim.events),
        'final_equity': sim.final_equity,
        'total_return_pct': sim.total_return_pct,
        'max_drawdown_pct': sim.max_drawdown_pct,
        'output_csv': str(out_csv),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start-date', required=True)
    ap.add_argument('--end-date', required=True)
    ap.add_argument('--out-csv', required=True)
    ap.add_argument('--env-file', default='/home/chewy/projects/trading-compose-dev/.env.dev')
    args = ap.parse_args()

    res = run(
        start_day=date.fromisoformat(args.start_date),
        end_day=date.fromisoformat(args.end_date),
        out_csv=Path(args.out_csv),
        env_file=Path(args.env_file),
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
