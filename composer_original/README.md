# Composer Original: Local Implementation Workspace

This folder contains the end-to-end implementation planning and deep verification artifacts for:

- `files/composer_original_file.txt` (`SOXL Growth v2.4.5 RL`)

## Contents

- `IMPLEMENTATION_TASKS_END_TO_END.md`: execution plan and acceptance criteria.
- `adr/ADR-0001-canonical-evaluator.md`: canonical evaluator decision record.
- `spec/baseline_snapshot_v245.json`: frozen source hashes + threshold map.
- `spec/backtest_golden_snapshot.json`: fixed-fixture golden baseline for backtest summary/allocation samples.
- `tests/test_phase1_hardening.py`: threshold-edge and tree-invariant hardening suite.
- `tests/test_phase2_data_indicator.py`: indicator/data parity hardening suite.
- `tests/test_phase3_backtest_parity_cli.py`: golden backtest + parity schema + CLI smoke suite.
- `tools/run_deep_checks.py`: four deeper checks runner.
- `tools/freeze_baseline.py`: baseline freeze/verify utility.
- `tools/generate_backtest_golden_snapshot.py`: regenerate golden backtest snapshot from fixture.
- `tools/run_cpu_gpu_backtests.py`: runs fixture CPU and GPU backtests and reports parity.
- `tools/run_implementation_checks.py`: orchestrates four deep implementation checks (including CPU+GPU).
- `tools/run_three_pass_review.py`: executes the four-check harness three consecutive times and archives each pass.
- `tools/run_four_pass_review.py`: executes the four-check harness four consecutive times and archives each pass.
- `tools/run_last_6m_cpu_gpu_backtests.py`: windowed CPU/GPU runner with locked profiles (`original_composer`, `trailing12_4_adapt`, `aggr_adapt_t10_tr2_rv14_b85_m8_M30`).
- `tools/runtime_backtest_parity_loop.py`: live/paper parity loop aligned to backtest sequencing (daily cycle near close).
- `docs/RUNTIME_BACKTEST_PARITY_NOTES.md`: parity loop usage for the 3 locked profiles.
- `reports/deep_checks_report.json`: machine-readable check output.
- `reports/deep_checks_report.md`: human-readable check output.
- `reports/implementation_round3_checks.json`: round 3 four-check machine report.
- `reports/implementation_round3_checks.md`: round 3 four-check human report.
- `reports/implementation_round4_checks.json`: round 4 four-check machine report (CPU+GPU included).
- `reports/implementation_round4_checks.md`: round 4 four-check human report.
- `reports/cpu_gpu_backtest_report.json`: latest CPU vs GPU parity report.
- `reports/three_pass_review_report.json`: machine report for three consecutive review passes.
- `reports/three_pass_review_report.md`: human report for three consecutive review passes.
- `reports/four_pass_review_report.json`: machine report for four consecutive review passes.
- `reports/four_pass_review_report.md`: human report for four consecutive review passes.
- `fixtures/deep_check_prices.csv`: deterministic synthetic fixture used by CLI/backtest deep checks.

## Run the Four Deep Checks

```bash
python3 composer_original/tools/run_deep_checks.py
```

The command exits non-zero if any check fails.

## Freeze / Verify Baseline Snapshot

```bash
python3 composer_original/tools/freeze_baseline.py
python3 composer_original/tools/freeze_baseline.py --verify
```

## Run CPU and GPU Backtests

```bash
composer_original/.venv/bin/python composer_original/tools/run_cpu_gpu_backtests.py
```

Optional (include full GPU equity curve payload):

```bash
composer_original/.venv/bin/python composer_original/tools/run_cpu_gpu_backtests.py \
  --include-equity-curve
```

## Run Daily Parity Runtime Loop (Paper/Live)

```bash
python3 composer_original/tools/runtime_backtest_parity_loop.py \
  --mode paper \
  --strategy-profile trailing12_4_adapt \
  --data-feed sip \
  --eval-time 15:55
```

## Run Full Round-4 Four-Check Harness

```bash
python3 composer_original/tools/run_implementation_checks.py
```

Outputs:

- `composer_original/reports/implementation_round4_checks.json`
- `composer_original/reports/implementation_round4_checks.md`
- `composer_original/reports/cpu_gpu_backtest_report.json`

## Run Three-Pass Review (Run Harness 3 Times)

```bash
python3 composer_original/tools/run_three_pass_review.py
```

Outputs:

- `composer_original/reports/three_pass_review_report.json`
- `composer_original/reports/three_pass_review_report.md`
- `composer_original/reports/implementation_round4_checks_pass1.json`
- `composer_original/reports/implementation_round4_checks_pass2.json`
- `composer_original/reports/implementation_round4_checks_pass3.json`

## Run Four-Pass Review (Run Harness 4 Times)

```bash
python3 composer_original/tools/run_four_pass_review.py
```

Outputs:

- `composer_original/reports/four_pass_review_report.json`
- `composer_original/reports/four_pass_review_report.md`
- `composer_original/reports/implementation_round4_checks_pass1.json`
- `composer_original/reports/implementation_round4_checks_pass2.json`
- `composer_original/reports/implementation_round4_checks_pass3.json`
- `composer_original/reports/implementation_round4_checks_pass4.json`

## Run Phase Suites

```bash
python3 -m unittest -v \
  composer_original.tests.test_phase1_hardening \
  composer_original.tests.test_phase2_data_indicator \
  composer_original.tests.test_phase3_backtest_parity_cli
```

## Regenerate Golden Backtest Snapshot

```bash
python3 composer_original/tools/generate_backtest_golden_snapshot.py
```

## Deep Check Definitions (`run_deep_checks.py`)

1. Threshold parity check:
   compares all strategy condition signatures between original `.txt` tree and Python evaluator tree.
2. Branch coverage check:
   evaluates 11 targeted branch scenarios and validates expected allocations.
3. Backtest invariants check:
   validates allocation normalization, symbol universe, finite metrics, and trade sanity.
4. CLI round-trip check:
   runs `python3 -m soxl_growth backtest` on fixture data and validates summary schema + numeric output.
