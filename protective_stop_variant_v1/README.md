# Protective Stop Variant V1

Standalone research path for adding a **hard protective stop-loss** on top of existing runtime target engines, without changing any existing runtime code.

## What It Tests

For each engine:
- `v1` (runtime_switch_loop logic)
- `v2` (runtime_switch_loop_v2_controlplane logic)
- `fev1` (FEV1-0001 fast-entry override logic)

It compares multiple `protective_stop_pct` values (including `0` as baseline) using the same:
- Alpaca SIP minute+daily data
- profile lock (`aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m` by default)
- slippage/fees settings
- eval/rebalance time and profit-lock order mode assumptions

## Protective Stop Rule

For each held symbol:
1. Track split-adjusted average entry price.
2. Compute stop level: `entry_price * (1 - protective_stop_pct/100)`.
3. If intraday low reaches stop level, flatten position.
4. Block same-day rebalance re-entry for that symbol.

Profit-lock logic still runs (same behavior as existing simulator), but protective-stop exits happen first.

## Run

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/protective_stop_variant_v1/tools/protective_stop_ab.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --windows 10d,1m,2m,3m,4m,5m,6m,1y \
  --initial-equity 10000 \
  --rebalance-time-ny 15:55 \
  --runtime-profit-lock-order-type market_order \
  --engines v1,v2,fev1 \
  --protective-stop-pcts 0,4,5,6,7,8 \
  --output-prefix protective_stop_ab
```

## Outputs

Under `protective_stop_variant_v1/reports/`:
- `<prefix>_<YYYYMMDD>_details.csv`
- `<prefix>_<YYYYMMDD>_ranked.csv`
- `<prefix>_<YYYYMMDD>_summary.json`

`details.csv` includes per window:
- final equity, return %, max drawdown %/$
- deltas vs same-engine baseline (`protective_stop_pct=0`)

`ranked.csv` aggregates each stop level across windows per engine.
