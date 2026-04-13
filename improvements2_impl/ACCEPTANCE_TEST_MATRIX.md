# Acceptance Test Matrix (Canonical)

Derived from `ACCEPTANCE_TEST_MATRIX.csv`.

| Item | Test ID | Test Name | Level | Pass Criteria |
|---|---|---|---|---|
| I2-001 | T-001 | Single canonical priority order exists | doc | One and only one priority list in `SPEC_CANONICAL` |
| I2-002 | T-002 | Runtime keys aligned to code baseline | doc | No banned legacy keys in canonical docs |
| I2-003 | T-003 | Every planned backlog item has tests | doc | All planned item IDs appear in acceptance matrix |
| I2-010 | T-010 | Supervisor scaffold import and interface test | unit | Module loads and `evaluate()` returns typed contract |
| I2-011 | T-011 | Priority precedence protective over add | unit | Protective action always outranks add on same symbol/cycle |
| I2-012 | T-012 | Dry-run rejects invalid intents | unit | Invalid intent set blocked with reason codes |
| I2-013 | T-013 | Drift detector catches qty mismatch | unit | Qty mismatch above threshold emits drift event |
| I2-014 | T-014 | Pending order suppresses duplicate submit | unit | Open order state blocks duplicate intent |
| I2-015 | T-015 | Lock persists across restart | integration | Lock state restored and honored after restart load |
| I2-016 | T-016 | Exposure scalar bounded and monotonic | unit | Scalar in [0,1] and decreases with stress inputs |
| I2-017 | T-017 | Hard DD brake blocks net-new buys | integration | Buy intents blocked while protective exits still allowed |
| I2-018 | T-018 | Recovery probe step-up only after criteria | integration | Probe remains capped until success condition met |
| I2-019 | T-019 | Session breaker blocks adds by threshold | integration | Adds disabled after threshold breach in same session |
| I2-020 | T-020 | No reentry after intraday PL exit | integration | Symbol exited via PL not re-added same session |
| I2-021 | T-021 | Hysteresis avoids threshold oscillation flips | unit | Alternating boundary values do not flip each cycle |
| I2-022 | T-022 | Confidence score bounded and logged | unit | Confidence in [0,1] persisted per cycle |
| I2-023 | T-023 | Adaptive threshold widens in noisy regime | unit | Threshold higher under high vol/chop inputs |
| I2-024 | T-024 | Conflict resolver emits single net action | unit | At most one action per symbol per cycle |
| I2-025 | T-025 | Decision hash deterministic for same inputs | unit | Identical inputs produce identical hash |
| I2-026 | T-026 | EOD report emits exactly one row per day | integration | No duplicates and includes turnover fields |
| I2-027 | T-027 | Shadow comparator never submits broker orders | integration | Sidecar updates state only with zero submissions |
