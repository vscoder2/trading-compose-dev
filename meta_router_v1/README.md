# Meta Router V1 (Standalone)

This folder is fully standalone and does not modify existing runtime code.

## What it is

`historical_meta_router_windows.py` builds a day-level meta-router that switches between:
- `runtime_switch_loop.py` (v1)
- `runtime_switch_loop_v2_controlplane.py` (v2)
- `runtime_m0106_loop.py` (M0106)

Then it replays each window with the same minute-level execution model used by existing historical harnesses.

## Router rules

Per day (based on SOXL history):
- **Burst (prefer v1)** when all are true:
  - 3-day return >= `--burst-ret3d-min` (default `0.12`)
  - RV20 <= `--burst-rv20-max` (default `130.0`)
  - DD20 <= `--burst-dd20-max` (default `15.0`)
- **Risk-off (prefer M0106)** when any are true:
  - RV20 >= `--riskoff-rv20-min` (default `95.0`)
  - Crossovers20 >= `--riskoff-crossovers20-min` (default `8`)
  - DD20 >= `--riskoff-dd20-min` (default `20.0`)
- Otherwise **prefer v2**.

## Run command

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/meta_router_v1/tools/historical_meta_router_windows.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --windows 1m,2m,3m,4m,5m,6m,1y,2y,3y,4y,5y,7y \
  --end-date 2026-03-27 \
  --initial-equity 10000 \
  --slippage-bps 1 \
  --sell-fee-bps 0 \
  --rebalance-time-ny 15:55 \
  --runtime-profit-lock-order-type market_order \
  --output-prefix compare_meta_router_1m_7y_20260327
```

## Outputs

- JSON: `meta_router_v1/reports/compare_meta_router_1m_7y_20260327_20260327.json`
- CSV: `meta_router_v1/reports/compare_meta_router_1m_7y_20260327_20260327.csv`

Each row includes baseline (`v1`, `v2`, `m0106`) and `meta_router_v1` metrics.

## Review passes completed

1. Compile check (`py_compile`) for the new script.
2. CLI/arg validation (`--help`).
3. Functional smoke run (`1m`) + full multi-window run (`1m..7y`) with output validation.
