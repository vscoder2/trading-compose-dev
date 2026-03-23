# Switch Runtime V1 (Standalone)

This folder contains a standalone paper/live runtime loop that mirrors the `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1` behavior without changing existing project code.

## File
- `runtime_switch_loop.py`

## What It Does
- Runs in `paper` or `live` mode using Alpaca credentials from env.
- Uses the locked profile:
  - `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`
- Chooses dynamic variant each cycle:
  - `baseline`
  - `inverse_ma20`
  - `inverse_ma60`
- Runs intraday profit-lock checks every 5 minutes (profile-driven).
- Runs main rebalance cycle at `--eval-time` (default `15:55` NY).
- Supports profit-lock execution styles:
  - `close_position`, `market_order`, `stop_order`, `trailing_stop`
- Supports rebalance styles:
  - `market`, `bracket`
- Persists state/events into its own DB (default `switch_runtime_v1_runtime.db`).

## Prerequisites
- Use the project venv that already has runtime deps:
  - `/home/chewy/projects/trading-compose-dev/composer_original/.venv`
- Ensure Alpaca env vars are present in:
  - `/home/chewy/projects/trading-compose-dev/.env.dev`

## Safe Smoke Test
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --run-once
```

## Paper Trading (Execute Orders)
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## Live Trading (Execute Orders)
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode live \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## Notes
- This runtime is fully separate from `composer_original` runtime files.
- It imports shared libraries from the existing repo, but does not modify them.
- If market is closed, the loop logs next open and waits (or exits with `--run-once`).
