# Switch Runtime V1 Paper/Live Guide

## 1) Scope and Intent
This guide documents the standalone runtime implementation in:
- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py`

Design goals:
- Keep implementation separate from `composer_original` runtime files.
- Preserve base strategy behavior for `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`.
- Add deterministic switch-state behavior (`baseline`, `inverse_ma20`, `inverse_ma60`) for paper/live execution.
- Support real order submission in Alpaca paper/live mode.


Companion sequence diagrams:
- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/docs/SWITCH_RUNTIME_V1_SEQUENCE_DIAGRAMS.md`
## 2) What Is Locked
Current locked artifact hash is recorded in:
- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/docs/SWITCH_RUNTIME_V1_LOCK_MANIFEST.md`

## 3) High-Level Architecture
Core runtime components:
- Strategy evaluator:
  - `soxl_growth/composer_port/symphony_soxl_growth_v245_rl.py` via `evaluate_strategy(...)`
- Market data:
  - `soxl_growth/data/alpaca_data.py` (`AlpacaBarLoader`)
- Broker execution:
  - `soxl_growth/execution/broker.py` (`AlpacaBroker`)
- Rebalance intents:
  - `soxl_growth/execution/orders.py` (`build_rebalance_order_intents`)
- Runtime persistence:
  - `soxl_growth/db.py` (`StateStore`)
- Switch overlay:
  - `composer_original/experiment/aggr_v2/overlays.py` (`apply_inverse_blocker`)

## 4) Runtime Loop Lifecycle
Loop steps per cycle:
1. Load env vars (`--env-file`, `--env-override`).
2. Create Alpaca config from environment.
3. Open broker + data clients and state DB.
4. Read market clock.
5. If market closed:
   - log next open
   - sleep (or exit with `--run-once`).
6. If market open and before eval-time:
   - run intraday profit-lock checks on cadence (5 minutes for current profile).
7. At/after eval-time:
   - fetch daily history
   - compute baseline target weights
   - compute regime metrics and choose variant
   - apply variant overlay to target
   - run profit-lock close logic
   - build and submit rebalance intents
   - persist state/events
   - sleep next loop (or exit with `--run-once`).

## 5) Strategy Profile Used
Supported profile key in runtime:
- `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`

Profile parameters:
- `enable_profit_lock = true`
- `profit_lock_mode = trailing`
- `profit_lock_threshold_pct = 10.0`
- `profit_lock_trail_pct = 2.0`
- `profit_lock_adaptive_threshold = true`
- `profit_lock_adaptive_symbol = TQQQ`
- `profit_lock_adaptive_rv_window = 14`
- `profit_lock_adaptive_rv_baseline_pct = 85.0`
- `profit_lock_adaptive_min_threshold_pct = 8.0`
- `profit_lock_adaptive_max_threshold_pct = 30.0`
- `intraday_profit_lock_check_minutes = 5`

## 6) Profit-Lock Logic (Detailed)
For each open long position:
- `prev_close = previous daily close`
- `threshold_ratio = 1 + threshold_pct/100`
- `trigger_price = prev_close * threshold_ratio`
- `day_high = max intraday high since session open`
- `last_price = latest intraday close`

Trigger condition:
- Trigger active if `day_high >= trigger_price`.

Trailing exit condition (`profit_lock_mode=trailing`):
- `trail_stop_price = day_high * (1 - trail_pct/100)`
- Exit if `last_price <= trail_stop_price`.

Adaptive threshold calculation:
- Compute realized volatility from `profit_lock_adaptive_symbol` closes over RV window.
- `ratio = rv / baseline`
- `raw_threshold = base_threshold * ratio`
- Clamp to `[min_threshold, max_threshold]`.

## 7) Switch Strategy Logic (Detailed)
### 7.1 Variants
- `baseline`: inverse blocker OFF
- `inverse_ma20`: inverse blocker ON with trend MA 20
- `inverse_ma60`: inverse blocker ON with trend MA 60

### 7.2 Regime Metrics (SOXL-based)
- `close`
- `ma20`, `ma60`, `ma200`
- `slope20_pct`: MA20 change vs 5 days ago
- `slope60_pct`: MA60 change vs 20 days ago
- `rv20_ann`: annualized realized vol from last 20 daily returns
- `crossovers20`: sign flips of `(close - MA20)` in last 20 days
- `dd20_pct`: max drawdown over last 20 closes

### 7.3 Rule Priority
Hard overrides first:
1. If `dd20_pct >= 12`, force `baseline` for next 5 days.
2. If `rv20_ann >= 1.35`, lock `baseline` until `rv20_ann < 1.20`.

Then primary gate:
3. If any of below true, select `baseline` immediately:
   - `close < ma60`
   - `rv20_ann >= 1.30`
   - `crossovers20 >= 4`

Then transition logic with persistence streaks:
4. From `baseline`:
   - if rule2 true for 3 consecutive days -> `inverse_ma20`
   - else if rule3 true for 3 consecutive days -> `inverse_ma60`
5. From `inverse_ma20`:
   - if rule2 false for 3 days and rule3 true for 3 days -> `inverse_ma60`
6. From `inverse_ma60`:
   - if rule2 true for 3 days -> `inverse_ma20`

Where:
- `rule2` (fast trend):
  - `close > ma20 > ma60`
  - `slope20_pct >= 1.0`
  - `rv20_ann <= 0.95`
  - `crossovers20 <= 2`
- `rule3` (slow trend):
  - `close > ma60 > ma200`
  - `slope60_pct >= 0.5`
  - `rv20_ann <= 1.20`

## 8) Rebalance Logic
1. Compute baseline target from strategy evaluator.
2. Apply selected variant overlay via inverse blocker function.
3. Read current positions and intraday prices.
4. Build intents with `build_rebalance_order_intents(...)` using:
   - account equity
   - final target weights
   - current qty
   - last prices
   - `--rebalance-threshold`
5. Submit intents:
   - `--rebalance-order-type market`: market orders
   - `--rebalance-order-type bracket`: buy uses bracket with configured TP/SL

Special blocking behavior:
- If profit-lock exits were submitted as `stop_order` or `trailing_stop`, symbols closed by profit-lock are excluded from same-cycle rebalance intents.

## 9) Order Types and Their Runtime Meaning
### 9.1 Profit lock (`--profit-lock-order-type`)
- `close_position`: broker-level close for full position
- `market_order`: explicit sell market order
- `stop_order`: sell stop near trigger/trail (capped by `--stop-price-offset-bps`)
- `trailing_stop`: broker trailing-stop order with trail percent from profile

### 9.2 Rebalance (`--rebalance-order-type`)
- `market`: direct buy/sell market orders
- `bracket`: buy order with attached take-profit and stop-loss

## 10) State and Event Persistence
Default DB:
- `switch_runtime_v1_runtime.db`

Persistent keys:
- `switch_executed_day`
- `switch_last_profile`
- `switch_last_variant`
- `switch_last_baseline_target`
- `switch_last_final_target`
- `switch_regime_state`
- `switch_intraday_profit_lock_last_slot`

Event types written:
- `switch_variant_changed`
- `switch_profit_lock_intraday_close`
- `switch_profit_lock_close`
- `switch_rebalance_order`
- `switch_cycle_complete`

## 11) CLI Parameters (Operationally Important)
Execution controls:
- `--mode {paper,live}`
- `--execute-orders`
- `--run-once`

Timing/data:
- `--eval-time HH:MM` (NY)
- `--data-feed sip|iex`
- `--loop-sleep-seconds`
- `--daily-lookback-days`
- `--stale-data-threshold-minutes`

Profit lock:
- `--profit-lock-order-type`
- `--cancel-existing-exit-orders`
- `--stop-price-offset-bps`

Rebalance:
- `--rebalance-order-type market|bracket`
- `--bracket-take-profit-pct`
- `--bracket-stop-loss-pct`
- `--rebalance-threshold`
- `--max-intents-per-cycle`

## 12) Production Commands
### 12.1 Safe smoke test
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --run-once
```

### 12.2 Paper mode with order execution
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

### 12.3 Live mode with order execution
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

## 13) Backtest-to-Production Expectation Guidance
Reference outputs:
- Optimistic switch backtests:
  - `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/reports/switch_profile_multiwindow_alpaca_sip_10k_paper_live_style_optimistic/`
- Realistic switch backtests:
  - `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/reports/switch_profile_multiwindow_alpaca_sip_10k_realistic_close/`
- Side-by-side gap table:
  - `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/reports/switch_profile_similarity_check.csv`

Operational interpretation:
- `paper_live_style_optimistic` is upper-bound style (optimistic fill semantics).
- `realistic_close` is closer to practical execution expectation.
- Real broker fills can still differ from both due to order queueing, spread, latency, partials, and broker microstructure.

## 14) Failure Modes and Recovery
Common runtime failures and responses:
- Missing Alpaca env vars:
  - Ensure `.env.dev` contains `ALPACA_API_KEY`, `ALPACA_API_SECRET`.
- Missing `alpaca-py`:
  - Install in venv used for runtime.
- Market closed behavior:
  - Expected; script logs next open and waits.
- Stale intraday bars:
  - Cycle skipped by stale-data guard; inspect feed and timestamps.
- Order rejection:
  - Check broker logs, symbol tradability, quantity precision, and buying power.

## 15) Operating Checklist
Pre-open:
- Verify env variables and API permissions.
- Confirm `--mode` (paper/live) and `--execute-orders` intent.
- Confirm feed (`sip` preferred if available).

Intraday:
- Monitor logs for profit-lock intraday events and stale-data warnings.
- Verify state DB event growth.

After eval window:
- Confirm one cycle per day (`switch_executed_day`).
- Confirm rebalance orders submitted as expected.
- Reconcile broker fills vs intended intents.

## 16) Notes on Scope Boundaries
- This runtime is standalone in `switch_runtime_v1` and does not modify `composer_original` runtime files.
- It reuses shared library modules from the repository for data, strategy evaluation, and broker execution.
- Profile selection exposed via CLI is base profile name; switch behavior is applied internally by the runtime state machine.

## 17) Recommended Next Controls (Optional)
- Add a daily audit exporter that writes executed fills + intents to CSV.
- Add hard notional cap per symbol per cycle.
- Add explicit broker account-mode assertion (paper/live account id match).
- Add alert hooks for `switch_variant_changed` and cycle completion.

## 18) Realistic Backtest Reference (10k Fresh Start Per Window)
Source: `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/reports/switch_profile_multiwindow_alpaca_sip_10k_realistic_close/summary_aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1_realistic_close.csv`

| Window | Start Equity | Final Equity | Return % | PnL | MaxDD % | Trades |
|---|---:|---:|---:|---:|---:|---:|
| 1m | 10,000.00 | 25,588.37 | 155.8837 | 15,588.37 | 46.0221 | 22 |
| 2m | 10,000.00 | 16,210.74 | 62.1074 | 6,210.74 | 46.0221 | 33 |
| 3m | 10,000.00 | 22,333.11 | 123.3311 | 12,333.11 | 46.0221 | 38 |
| 4m | 10,000.00 | 20,653.93 | 106.5393 | 10,653.93 | 46.0221 | 45 |
| 5m | 10,000.00 | 29,537.97 | 195.3797 | 19,537.97 | 46.0221 | 52 |
| 6m | 10,000.00 | 27,396.43 | 173.9643 | 17,396.43 | 46.0221 | 63 |
| 9m | 10,000.00 | 17,040.69 | 70.4069 | 7,040.69 | 53.3291 | 105 |
| 1y | 10,000.00 | 19,806.13 | 98.0613 | 9,806.13 | 56.5545 | 283 |
| 2y | 10,000.00 | 25,156.90 | 151.5690 | 15,156.90 | 65.0152 | 504 |
| 3y | 10,000.00 | 23,137.52 | 131.3752 | 13,137.52 | 65.0152 | 566 |
| 5y | 10,000.00 | 29,011.18 | 190.1118 | 19,011.18 | 84.5905 | 1178 |
| 7y | 10,000.00 | 11,515.29 | 15.1529 | 1,515.29 | 84.5905 | 1495 |
