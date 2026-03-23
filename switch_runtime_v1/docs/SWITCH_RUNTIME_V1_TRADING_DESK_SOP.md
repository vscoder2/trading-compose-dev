# Switch Runtime V1 Trading Desk SOP

## Scope
This SOP is for running and operating:

- `switch_runtime_v1/runtime_switch_loop.py`
- Profile: `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`
- Modes: Alpaca `paper` and `live`
- Feed: Alpaca `sip`

This document is operations-first and focuses on daily execution discipline, expected behavior, and incident response.

## 1) Startup Checklist

Use this before starting the loop each day.

| Check | What to verify | Pass criteria |
|---|---|---|
| Credentials | `.env.dev` has Alpaca key/secret and is readable | `ALPACA_API_KEY` and secret values present |
| Runtime env | Process uses venv Python | `composer_original/.venv/bin/python` |
| Mode intent | You are intentionally running `paper` or `live` | Correct `--mode` in command |
| Execution intent | You intentionally want real order submission | `--execute-orders` present |
| Data feed | SIP feed configured | `--data-feed sip` |
| Decision time | Daily cycle time explicitly set | `--eval-time` set and team-aligned |
| State DB isolation | Correct db path for this runtime instance | `--state-db` points to expected file |
| Process exclusivity | No duplicate runtime for same DB/account | Only one active process per strategy/account |
| Clock sanity | Market clock reachable | `get_clock()` works and returns timestamps |

## 2) Recommended Commands

### Paper mode (recommended baseline)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:56 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

### Live mode (same logic, live account)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode live \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:56 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

### Dry-run safety command (no order placement)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:56 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market
```

## 3) Runtime Behavior Summary

| Stage | Time window | What happens |
|---|---|---|
| Market-closed wait | Outside session | Loop polls clock and waits |
| Intraday profit-lock | Market open to just before eval time | 5-minute slot checks for trailing profit-lock exits |
| Main cycle | At/after `--eval-time` once/day | Evaluate base strategy, choose variant, apply overlay, profit-lock check, rebalance |
| Post-cycle | After execution | Persist state, append events, wait next loop |

## 4) Strategy and Switching Rules

### Base strategy
- Baseline target weights are generated from SOXL Growth v2.4.5 RL evaluator.
- Inputs are daily adjusted closes (split/dividend adjusted).
- Outputs are normalized target weights.

### Variant switch engine
- Runtime computes SOXL regime metrics from daily close history.
- Variant choices are `baseline`, `inverse_ma20`, and `inverse_ma60`.
- Variant transitions use streak-based confirmations and override locks.
- Drawdown and high-volatility overrides can force baseline.

### Inverse blocker overlay
- Applied after baseline target generation.
- In inverse variants, if SOXL trend is bullish above MA window, inverse symbols can be removed.
- If all candidates are inverse and blocked, runtime falls back to `SOXL: 1.0`.

## 5) Profit-Lock Rules (Profile-Specific)

Profile: `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`

| Rule | Value |
|---|---:|
| Profit-lock mode | `trailing` |
| Base threshold | `10%` |
| Adaptive threshold | `ON` |
| Adaptive reference symbol | `TQQQ` |
| Adaptive window | `14` |
| Baseline RV | `85` |
| Min threshold | `8%` |
| Max threshold | `30%` |
| Trailing pullback | `2%` |
| Intraday check interval | `5 minutes` |

Trigger semantics:

- Compute threshold percent adaptively from RV ratio.
- Trigger arm price = `previous_close * (1 + threshold_pct)`.
- A symbol qualifies only if intraday high reaches arm price.
- For trailing mode, exit signal occurs only when latest minute close falls below trailing stop from intraday high.

## 6) Rebalance and Order Flow

Order flow in one daily cycle:

1. Pull current positions and account equity.
2. Build net rebalance intents from target weight deltas.
3. Apply rebalance threshold filter.
4. Submit sells before buys.
5. Record order events in SQLite event log.

For this SOP command:

- Profit-lock exits use `market_order`.
- Rebalance orders use `market`.
- Time in force is `DAY`.

## 7) State, Idempotency, and Persistence

SQLite state store keeps both key/value state and append-only events.

Critical keys:

- `switch_executed_day`: prevents second main-cycle execution on same trading day.
- `switch_intraday_profit_lock_last_slot`: prevents duplicate intraday slot execution.
- `switch_regime_state`: persistent streak counters and active variant.
- `switch_last_baseline_target` and `switch_last_final_target`: latest target snapshots.

Operational rule:

- Reuse the same `--state-db` for continuity.
- Do not run two processes against the same account and state DB for the same strategy.

## 8) Intraday Monitoring Checklist

Monitor these live:

| Item | Why it matters | Expected |
|---|---|---|
| Process heartbeat/log updates | Detect hangs | Log lines every loop interval |
| Clock status | Session gating | Open during session |
| Latest intraday timestamp freshness | Data quality guard | Within stale threshold |
| Variant and reason events | Understand risk regime | Reason string present on changes |
| Profit-lock events | Validate intraday exits | Event rows on trigger |
| Rebalance order events | Verify target execution | Intent count and submitted count align |
| Account equity/cash | Capacity and risk | Non-error values |

## 9) 15:56 Decision-Time Checklist

Use at/after eval time:

1. Confirm market is open.
2. Confirm intraday data is fresh.
3. Confirm no duplicate execution for today in state.
4. Confirm baseline target and final switched target are computed.
5. Confirm profit-lock exits (if any) are submitted before rebalance.
6. Confirm rebalance intents generated and submitted.
7. Confirm `switch_cycle_complete` event appended.

## 10) End-of-Day Reconciliation Checklist

1. Pull latest account equity, cash, and positions.
2. Compare holdings against `switch_last_final_target`.
3. Check all order statuses for rejects/cancels/partials.
4. Review event log for `switch_profit_lock_intraday_close`, `switch_profit_lock_close`, `switch_rebalance_order`, and `switch_cycle_complete`.
5. Archive logs and state DB snapshot.

## 11) Incident Runbook

### A) Stale data detected

Symptoms:

- Logs show cycle skipped due to stale intraday bars.

Actions:

1. Verify SIP data feed health and API availability.
2. Increase `--stale-data-threshold-minutes` only if justified.
3. Keep process running; do not force trades on stale data.
4. Reconcile later with event logs.

### B) Partial fills

Symptoms:

- Position drift vs target after cycle.

Actions:

1. Inspect open orders and filled quantities.
2. Let next cycle rebalance drift unless manual risk action required.
3. If using market orders, partials are typically transient; recheck shortly.

### C) Rejected orders

Common causes:

- Buying power constraints, invalid qty, symbol constraints, market state mismatch.

Actions:

1. Capture broker reject reason from order metadata.
2. Validate account buying power and permissions.
3. Validate symbols and qty precision.
4. Re-run in paper with same parameters before retrying live.

### D) Process restart mid-day

Actions:

1. Restart with same command and same `--state-db`.
2. Verify `switch_executed_day` protects against duplicate main cycle.
3. Verify intraday slot key behavior resumes safely.

### E) Duplicate runtime processes detected

Actions:

1. Stop all but one process.
2. Confirm single source of execution per account/profile.
3. Reconcile events and positions immediately.

## 12) Safety Controls and Change Policy

| Control | Policy |
|---|---|
| Config changes | Change one parameter at a time and journal it |
| Eval-time changes | Run paper shadow first |
| Mode switch paper -> live | Require paper stability window and sign-off |
| State DB rotation | Only at controlled boundaries |
| Emergency halt | Stop process and review open orders and positions |

## 13) KT: Why results differ vs synthetic backtests

This runtime is a broker-executing loop, so deviations from synthetic backtest are expected due to:

- Real fill prices and spread.
- Order queue/latency/partials.
- Session timing and minute-level path.
- Data freshness gating behavior.

This SOP targets operational consistency, not synthetic-equivalent fills.

## 14) Quick References

- Runtime loop: `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py`
- Broker wrapper: `/home/chewy/projects/trading-compose-dev/soxl_growth/execution/broker.py`
- Base strategy evaluator: `/home/chewy/projects/trading-compose-dev/soxl_growth/composer_port/symphony_soxl_growth_v245_rl.py`
- Overlay logic: `/home/chewy/projects/trading-compose-dev/composer_original/experiment/aggr_v2/overlays.py`
- State DB helper: `/home/chewy/projects/trading-compose-dev/soxl_growth/db.py`
