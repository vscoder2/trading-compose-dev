# Action Plan (Canonical, Separate Implementation Track)

Scope:

- Build all new work under this separate folder track.
- Do not modify existing runtime files during planning/phase-0.
- Existing runtime remains source-of-truth until explicit migration decision.

Working root for new implementation:

- `/home/chewy/projects/trading-compose-dev/improvements2_impl`

## Phase 0: Canonical Spec Freeze (Required Before Code)

Tasks:

1. Freeze a single backlog order (remove conflicting ranking variants).
2. Map each item to one of:
   - `Core safety`
   - `Execution integrity`
   - `Decision quality`
   - `Observability`
   - `Research sidecar`
3. Normalize terminology to actual runtime names:
   - use `switch_executed_day` (not `parity_executed_day`)
   - define exact behavior for re-entry guard by order type
4. Define acceptance tests per item before implementation.

Deliverables:

1. `SPEC_CANONICAL.md`
2. `BACKLOG_CANONICAL.csv`
3. `ACCEPTANCE_TEST_MATRIX.md`

## Phase 1: New Control Kernel Skeleton (No Runtime Mutation Yet)

Tasks:

1. Create standalone module skeleton:
   - `src/supervisor.py`
   - `src/action_policy.py`
   - `src/reconcile.py`
   - `src/decision_ledger.py`
2. Implement pure functions first (no broker calls).
3. Add fixture-driven unit tests for:
   - priority ladder behavior
   - dry-run validation failures
   - drift detection logic

Deliverables:

1. Testable sidecar kernel package
2. Unit tests with deterministic fixtures

## Phase 2: Deterministic State Layer Extension

Tasks:

1. Define migration SQL for additional tables (without replacing `state_kv/events`).
2. Implement lock objects:
   - drawdown lock
   - re-entry lock
   - cooldown lock
   - turnover budget lock
3. Add read/write adapters with rollback-safe behavior.

Deliverables:

1. `migrations/001_control_plane.sql`
2. `src/state_adapter.py`
3. migration verification tests

## Phase 3: Safety Controls (Highest ROI)

Tasks:

1. Dynamic exposure scalar function.
2. Hard drawdown brake state machine.
3. Recovery probe mode.
4. Session PnL circuit-breaker logic.

Deliverables:

1. `src/risk_controls.py`
2. scenario tests (calm, stress, drawdown, recovery)

## Phase 4: Execution Integrity Controls

Tasks:

1. Order conflict resolver (single net action per symbol).
2. Pending-order reconciliation policy.
3. Decision dry-run validator integration path (sidecar simulation first).

Deliverables:

1. `src/execution_policy.py`
2. sidecar replay tests with synthetic open-order states

## Phase 5: Decision Quality Controls

Tasks:

1. Hysteresis thresholds (enter/exit bands).
2. Regime confidence score (bounded `[0,1]`).
3. Adaptive rebalance threshold policy.

Deliverables:

1. `src/regime_policy.py`
2. regression tests on oscillating threshold fixtures

## Phase 6: Observability and Proof Layer

Tasks:

1. Rule-reason attribution events.
2. Decision snapshot hashing.
3. End-of-day reconciliation artifact generator.
4. Expected vs realized turnover monitor.

Deliverables:

1. `src/audit_export.py`
2. `src/hash_ledger.py`
3. reproducibility tests

## Phase 7: Research Sidecars (Optional / Controlled)

Tasks:

1. Live shadow comparator sidecar.
2. Trade attribution sidecar.
3. Fill-quality guard calibration experiments.

Deliverables:

1. `research_sidecar/` package
2. result tables + calibration notes

## Right / Wrong / Needs-Change Gate Before Coding

Must-fix mismatches from `improvements2.md` before build:

1. Rename incorrect key references (`parity_executed_day` -> `switch_executed_day`).
2. Clarify re-entry rules by order type (not globally true today).
3. Remove implicit claim that baseline is equivalent to capital-safe mode.
4. Collapse duplicate/conflicting priority lists into one canonical sequence.

## Task Tracking (Initial Ticket Set)

1. `I2-001` Canonical backlog normalization
2. `I2-002` Acceptance matrix definition
3. `I2-003` Kernel module scaffolding
4. `I2-004` Priority ladder + dry-run validator
5. `I2-005` Drift detector + pending-order reconciler
6. `I2-006` Persistent lock object schema + adapter
7. `I2-007` Exposure scalar + drawdown brake + recovery probe
8. `I2-008` Session PnL breaker
9. `I2-009` Hysteresis + confidence score + adaptive threshold
10. `I2-010` Attribution/reason logging + decision hashing

## Go/No-Go Criteria for Starting Implementation

Go only if all are true:

1. Canonical spec approved.
2. Acceptance tests defined for each Phase 1-4 item.
3. Naming and behavior mismatches resolved.
4. New-folder-only enforcement confirmed.

