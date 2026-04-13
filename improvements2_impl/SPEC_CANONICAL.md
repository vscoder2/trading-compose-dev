# SPEC_CANONICAL: Improvements2 Control-Plane Program

## 1) Scope

This document canonicalizes implementation scope from:

- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/docs/improvements2.md`

This is **Phase 0 only** (spec and backlog normalization), with explicit constraints:

1. Do not modify existing runtime code during Phase 0.
2. All new work lives under `improvements2_impl/`.
3. Current runtime behavior remains source-of-truth until migration is approved.

## 2) Goals

1. Remove conflicting priorities and duplicated proposals.
2. Define one implementation order and one acceptance framework.
3. Correct mismatched terminology against current codebase.
4. Provide an execution-ready backlog with dependencies.

## 3) Non-Goals

1. No direct refactor of `switch_runtime_v1/runtime_switch_loop.py` in this phase.
2. No strategy/symbol changes.
3. No production behavior change.

## 4) Runtime Truth Baseline (validated)

These are confirmed from current code and treated as factual baseline:

1. Profile uses 5-minute intraday profit-lock cadence.
2. Variant set is `baseline`, `inverse_ma20`, `inverse_ma60`.
3. Adaptive profit-lock threshold uses `TQQQ` realized-vol proxy.
4. Daily cycle executes at/after eval time once per day using `switch_executed_day`.
5. Runtime state DB schema is minimal (`state_kv`, `events`).
6. Re-entry suppression differs by order path and is not globally uniform.

## 5) Canonical Terminology

Required naming alignment:

1. Use `switch_executed_day` (not `legacy parity executed-day key`).
2. Use `switch_intraday_profit_lock_last_slot` for intraday dedupe key.
3. Use `switch_regime_state` for variant-state persistence.
4. Refer to `StateStore` base schema as `state_kv/events` only.

## 6) Canonical Architecture Target

Target architecture for later implementation (separate track):

1. `supervisor.py`: policy eligibility, risk gates, locks.
2. `action_policy.py`: priority ladder and net action resolution.
3. `reconcile.py`: broker/open-order drift reconciliation.
4. `decision_ledger.py`: attribution, decision hash, cycle records.
5. `shadow_eval.py` (sidecar): non-trading comparison paths.

## 7) Canonical Priority Order

Only this sequence is authoritative:

1. Safety/control integrity plumbing
   - priority ladder
   - dry-run validator
   - drift detection
   - pending-order reconciliation
   - lock objects
2. Capital preservation
   - exposure scaling
   - hard drawdown brake
   - recovery probe
   - session PnL breaker
   - no-reentry guard
3. Decision quality controls
   - hysteresis bands
   - confidence score
   - adaptive rebalance threshold
4. Execution and observability hardening
   - conflict resolver
   - reason attribution + decision hash
   - EOD reconciliation + turnover monitor
5. Research sidecars
   - live shadow comparator

## 8) Data Model Expansion (planned, additive)

Planned additive tables (no replacement of base schema):

1. `decision_cycles`
2. `locks`
3. `drift_snapshots`
4. `open_order_state`
5. `risk_state`
6. `session_state`
7. `decision_reasons`
8. `shadow_cycles`

## 9) Acceptance Gate

Implementation may start only after all are true:

1. `BACKLOG_CANONICAL.csv` is internally consistent (IDs, dependencies, phases).
2. `ACCEPTANCE_TEST_MATRIX.csv` covers all backlog IDs marked `planned`.
3. Terminology checks pass against known incorrect legacy references.
4. Validation script returns zero errors.

## 10) Deliverables in This Phase

1. `SPEC_CANONICAL.md` (this file)
2. `BACKLOG_CANONICAL.csv`
3. `ACCEPTANCE_TEST_MATRIX.csv`
4. `ACCEPTANCE_TEST_MATRIX.md`
5. `tools/validate_phase0.py`
6. `reports/phase0_review_report.md`
