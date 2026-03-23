# AGGR v2 Isolated Research Stack (No Existing Code Modified)

This folder contains a **completely separate implementation path** for strategy research and backtesting.
It is designed so you can iterate aggressively without touching:

- `composer_original/tools/*`
- `soxl_growth/*`
- locked production/runtime profile definitions

## What is implemented here

1. `run_isolated_backtests.py`
- Batch window backtester.
- Uses existing strategy evaluator through adapter only.
- Supports execution modes:
  - `synthetic`
  - `paper_live_style_optimistic`
  - `realistic_close`
- Produces CPU and GPU parity outputs.
- Can run 4-pass deep review checks per window.

2. `run_phase45_pipeline.py`
- Implements the remaining Phase 4/5 workflow:
  - multi-window evaluation
  - data-driven scenario stress windows
  - walk-forward folds
  - strict hard acceptance rule versus baseline
  - 3-review audit passes (baseline presence, determinism, acceptance-gate integrity)
- Produces leaderboard CSV/JSON with full audit details.

3. Core modules
- `data.py`: loaders for fixture close CSV, OHLC CSV, optional yfinance.
- `strategy_adapter.py`: bridges to `evaluate_strategy` safely.
- `execution_models.py`: profit-lock trigger/fill models.
- `overlays.py`: vol targeting, persistence/hysteresis, loss limiter.
- `backtester.py`: isolated accounting + order execution simulation.
- `gpu_replay.py`: parity replay (CuPy if present, deterministic fallback otherwise).
- `validation.py`: bootstrap + validation metrics.
- `review.py`: 4 deep review passes.
- `reporting.py`: CSV/JSON serialization.
- `candidate_grid.py`: phase-4/5 candidate set generator.
- `runner_utils.py`: shared run/slice/trim helpers.
- `scenarios.py`: deterministic scenario window builder.
- `wf_search.py`: walk-forward + strict ranking engine.

## Example command (fixture source)

```bash
python3 composer_original/experiment/aggr_v2/run_isolated_backtests.py \
  --source fixture_close \
  --prices-csv /home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv \
  --profile aggr_adapt_t10_tr2_rv14_b85_m8_M30 \
  --mode paper_live_style_optimistic \
  --windows 1m,2m,3m,6m,1y \
  --initial-equity 10000 \
  --warmup-days 260 \
  --run-four-reviews
```

## Phase 4/5 pipeline command

```bash
python3 composer_original/experiment/aggr_v2/run_phase45_pipeline.py \
  --source fixture_close \
  --prices-csv /home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv \
  --profile aggr_adapt_t10_tr2_rv14_b85_m8_M30 \
  --mode paper_live_style_optimistic \
  --windows 1m,2m,3m,6m,1y \
  --initial-equity 10000 \
  --warmup-days 260
```

## Output files

Default output directory:

`/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/reports`

Generated artifacts:
- `summary_<profile>_<mode>.csv`
- `summary_<profile>_<mode>.json`
- `daily_<profile>_<window>_<mode>.csv`

## 4-pass review checks

1. Profile hash lock check.
2. Determinism check (same input -> same result).
3. CPU/GPU parity check.
4. Validation sanity check (risk/stat metrics).

## Notes

- This path is intentionally isolated for experimentation.
- It does not submit broker orders and is not a runtime replacement.
