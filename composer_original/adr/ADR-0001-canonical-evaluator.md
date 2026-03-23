# ADR-0001: Canonical Evaluator for Composer Original v2.4.5 RL

## Status

Accepted

## Date

2026-03-16

## Context

The repository contains multiple strategy versions and runtime paths. For `composer_original_file.txt` parity, we need one canonical evaluator entrypoint for all checks and integration paths.

## Decision

Canonical evaluator is:

- `soxl_growth.composer_port.symphony_soxl_growth_v245_rl.evaluate_strategy`

Canonical tree builder is:

- `soxl_growth.composer_port.symphony_soxl_growth_v245_rl.build_tree`

Backtest and CLI parity for original strategy are validated against this evaluator.

## Consequences

- Any parity check for original strategy must resolve through this evaluator.
- Threshold/branch changes in the evaluator must fail `composer_original` deep checks unless snapshot/spec is intentionally updated.
- Future strategy variants (e.g., 3.3/3.3B/3.3C) do not alter this canonical reference.

