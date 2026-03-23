# Runtime vs Backtest Parity Notes

## What is now locked

For `composer_original` tooling, strategy surface is locked to exactly:

1. `original_composer`
2. `trailing12_4_adapt`
3. `aggr_adapt_t10_tr2_rv14_b85_m8_M30`

`composer_original/tools/run_last_6m_cpu_gpu_backtests.py` now enforces these profiles and defaults to `original_composer`.

## Parity Live/Paper Loop

Use:

`composer_original/tools/runtime_backtest_parity_loop.py`

This loop is intentionally backtest-aligned:

- One cycle per trading day (after `--eval-time`, default `15:55` ET).
- Baseline target computed from daily history with original composer evaluator.
- Profit-lock handled in backtest order (profit-lock exits first, then rebalance).
- Rebalance uses deterministic sell-first intent ordering.
- Strategy profile is locked to the same 3 variants above.

Example (paper, dry-run):

```bash
python3 composer_original/tools/runtime_backtest_parity_loop.py \
  --mode paper \
  --strategy-profile trailing12_4_adapt \
  --data-feed sip \
  --eval-time 15:55
```

Example (paper, submit orders):

```bash
python3 composer_original/tools/runtime_backtest_parity_loop.py \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30 \
  --execute-orders \
  --data-feed sip \
  --eval-time 15:55
```
