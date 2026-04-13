# M0106 Standalone Runtime

This folder is a separate runtime implementation for the M0106 profile.
No existing runtime files were modified.

## Entry point

- `runtime_m0106_loop.py`

## Default profile

- `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_m0106`

## Example command (paper)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/m0106_runtime_v1/runtime_m0106_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## Example command (live)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/m0106_runtime_v1/runtime_m0106_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode live \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## State keys

This runtime uses isolated state keys prefixed with `m0106_` and default DB:
- `m0106_runtime_v1_runtime.db`

