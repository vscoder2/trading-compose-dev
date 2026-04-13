# fast_entry_variant_v1

Standalone research variant for one-day-early control-plane promotion.

## Goal
Avoid delayed inverse exposure on days like 2026-04-08 by allowing a fast-entry
promotion from `baseline` to `inverse_ma20` when all of these are true:

1. Hysteresis is still `risk_off`, but `enter_streak == 1` (first confirmation day)
2. Regime signal is high enough (`fast_signal_threshold`)
3. SOXL is strongly above MA20 (`fast_trend_gap_pct`)
4. Base target is inverse-heavy (`fast_inverse_min_weight`)

## Important
- This folder is a new variant path only.
- Existing runtime files are not modified.

## Runner
- Script: `fast_entry_variant_v1/tools/fast_entry_override_grid.py`
- Output: ranked grid results under `fast_entry_variant_v1/reports/`

## Example
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/fast_entry_variant_v1/tools/fast_entry_override_grid.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --windows 10d,1m,2m,3m,4m,5m,6m,1y \
  --end-date 2026-04-10 \
  --initial-equity 10000 \
  --rebalance-time-ny 15:55 \
  --runtime-profit-lock-order-type market_order \
  --fast-signal-thresholds 0.66,0.68,0.70 \
  --fast-trend-gap-pcts 6,8,10 \
  --fast-inverse-min-weights 0.80,0.90
```
