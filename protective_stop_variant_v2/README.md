# Protective Stop Variant V2

Second-generation standalone research path (no edits to existing runtime code).

## Added policy controls
- `inverse_only` stop scope (default)
- volatility gate (`rv_gate_min_pct`, annualized %)
- no same-day re-entry after protective exit

## Engines
- `v1` (`runtime_switch_loop.py` target logic)
- `v2` (`runtime_switch_loop_v2_controlplane.py` target logic)
- `fev1` (`FEV1-0001` target logic)

## Run

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/protective_stop_variant_v2/tools/protective_stop_v2_ab.py \
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
  --stop-scope inverse_only \
  --protective-stop-pcts 0,3,4,5,6,7 \
  --rv-gate-min-pcts 0,40,60,80 \
  --rv-gate-window 20 \
  --output-prefix protective_stop_v2_run1
```

## Outputs
- `protective_stop_variant_v2/reports/*_details.csv`
- `protective_stop_variant_v2/reports/*_ranked.csv`
- `protective_stop_variant_v2/reports/*_summary.json`

`summary.json` includes `strict_acceptance_candidates`:
- average return delta > 0
- average maxDD delta < 0
