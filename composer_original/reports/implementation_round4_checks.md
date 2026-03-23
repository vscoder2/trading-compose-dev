# Implementation Round 4: Four Deep Checks (CPU+GPU)

- Python interpreter: `/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python`
- Check count: 4
- Passed: 4
- Failed: 0

## Check 1 - PASS

- Command: `/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python /home/chewy/projects/trading-compose-dev/composer_original/tools/freeze_baseline.py --verify`
- Return code: `0`

### Stdout

```text
{
  "current_hashes": {
    "original_clj_sha256": "251236fbee29786c4a04e835b9d2a2cd5d20672a6e7810fbcf4d85d38d4b11d5",
    "python_tree_sha256": "7d423fc2aca22dd8877e86c3a5a4f6dea0a480c4051e95fbb35d901ac5cfa16a"
  },
  "current_threshold_count": 15,
  "frozen_hashes": {
    "original_clj_sha256": "251236fbee29786c4a04e835b9d2a2cd5d20672a6e7810fbcf4d85d38d4b11d5",
    "python_tree_sha256": "7d423fc2aca22dd8877e86c3a5a4f6dea0a480c4051e95fbb35d901ac5cfa16a"
  },
  "frozen_threshold_count": 15,
  "ok": true,
  "snapshot_path": "/home/chewy/projects/trading-compose-dev/composer_original/spec/baseline_snapshot_v245.json"
}
```

### Stderr

```text

```

## Check 2 - PASS

- Command: `/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python -m unittest -v composer_original.tests.test_phase1_hardening composer_original.tests.test_phase2_data_indicator composer_original.tests.test_phase3_backtest_parity_cli`
- Return code: `0`

### Stdout

```text

```

### Stderr

```text
test_cumret_soxl_32_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_cumret_soxl_32_edge) ... ok
test_cumret_tqqq_30_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_cumret_tqqq_30_edge) ... ok
test_cumret_tqqq_8_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_cumret_tqqq_8_edge) ... ok
test_mdd_soxl_250_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_mdd_soxl_250_edge) ... ok
test_mdd_soxl_60_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_mdd_soxl_60_edge) ... ok
test_mdd_tqqq_200_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_mdd_tqqq_200_edge) ... ok
test_output_symbol_universe_subset (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_output_symbol_universe_subset) ... ok
test_rsi_soxl_30_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_rsi_soxl_30_edge) ... ok
test_rsi_soxl_32_inner_condition_is_unreachable_else (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_rsi_soxl_32_inner_condition_is_unreachable_else) ... ok
test_rsi_soxl_32_outer_split_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_rsi_soxl_32_outer_split_edge) ... ok
test_rsi_tqqq_30_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_rsi_tqqq_30_edge) ... ok
test_stdev_soxl_105_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_stdev_soxl_105_edge) ... ok
test_stdev_soxl_30_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_stdev_soxl_30_edge) ... ok
test_stdev_tqqq_100_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_stdev_tqqq_100_edge) ... ok
test_stdev_tqqq_14_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_stdev_tqqq_14_edge) ... ok
test_stdev_tqqq_30_edge (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_stdev_tqqq_30_edge) ... ok
test_weight_values_finite (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_weight_values_finite) ... ok
test_weights_sum_to_one_and_non_negative_in_scenarios (composer_original.tests.test_phase1_hardening.Phase1HardeningTest.test_weights_sum_to_one_and_non_negative_in_scenarios) ... ok
test_backtest_history_alignment_assumptions (composer_original.tests.test_phase2_data_indicator.Phase2DataIndicatorTest.test_backtest_history_alignment_assumptions) ... ok
test_backtest_runs_with_shared_non_trading_day_gaps (composer_original.tests.test_phase2_data_indicator.Phase2DataIndicatorTest.test_backtest_runs_with_shared_non_trading_day_gaps) ... ok
test_evaluate_strategy_raises_on_insufficient_context (composer_original.tests.test_phase2_data_indicator.Phase2DataIndicatorTest.test_evaluate_strategy_raises_on_insufficient_context) ... ok
test_indicator_insufficient_history_by_required_windows (composer_original.tests.test_phase2_data_indicator.Phase2DataIndicatorTest.test_indicator_insufficient_history_by_required_windows) ... ok
test_selector_tie_breaking_is_deterministic_and_stable (composer_original.tests.test_phase2_data_indicator.Phase2DataIndicatorTest.test_selector_tie_breaking_is_deterministic_and_stable) ... ok
test_backtest_matches_golden_snapshot (composer_original.tests.test_phase3_backtest_parity_cli.Phase3BacktestParityCliTest.test_backtest_matches_golden_snapshot) ... ok
test_cli_smoke_backtest_and_parity_calibrate_rsi (composer_original.tests.test_phase3_backtest_parity_cli.Phase3BacktestParityCliTest.test_cli_smoke_backtest_and_parity_calibrate_rsi) ... ok
test_parity_mismatch_report_schema (composer_original.tests.test_phase3_backtest_parity_cli.Phase3BacktestParityCliTest.test_parity_mismatch_report_schema) ... ok

----------------------------------------------------------------------
Ran 26 tests in 1.107s

OK
```

## Check 3 - PASS

- Command: `/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python /home/chewy/projects/trading-compose-dev/composer_original/tools/run_deep_checks.py`
- Return code: `0`

### Stdout

```text
{
  "check_count": 4,
  "checks": [
    {
      "details": {
        "clj_condition_count": 15,
        "extra_in_python": [],
        "missing_in_python": [],
        "python_condition_count": 15
      },
      "name": "check_1_threshold_parity",
      "passed": true
    },
    {
      "details": {
        "failures": [],
        "scenario_count": 11,
        "unreachable_path_confirmed": true
      },
      "name": "check_2_branch_coverage",
      "passed": true
    },
    {
      "details": {
        "allocation_days": 160,
        "bad_allocation_days": [],
        "bad_trade_count": 0,
        "bad_trade_sample": [],
        "equity_points": 420,
        "final_equity": 80023.01040817637,
        "total_return_pct": -19.976989591823624,
        "trade_count": 1
      },
      "name": "check_3_backtest_invariants",
      "passed": true
    },
    {
      "details": {
        "csv_fixture": "/home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv",
        "missing_keys": [],
        "numeric_ok": true,
        "parse_error": "",
        "returncode": 0,
        "stderr": "2026-03-20 02:22:55 INFO soxl_growth.backtest.engine: Starting backtest days=420 symbols=8\n2026-03-20 02:22:55 INFO soxl_growth.execution.orders: Built 1 rebalance intents\n2026-03-20 02:22:55 INFO soxl_growth.backtest.engine: Backtest complete final_equity=80023.01 total_return_pct=-19.98 trades=1",
        "stdout": "{\n  \"avg_daily_return_pct\": -0.053150356081771315,\n  \"cagr_pct\": -12.989563975229668,\n  \"final_equity\": 80023.01040817637,\n  \"max_drawdown_pct\": 19.976989591823628,\n  \"total_return_pct\": -19.976989591823624,\n  \"trade_count\": 1\n}"
      },
      "name": "check_4_cli_roundtrip",
      "passed": true
    }
  ],
  "composer_original_dir": "/home/chewy/projects/trading-compose-dev/composer_original",
  "failed_count": 0,
  "passed_count": 4,
  "root": "/home/chewy/projects/trading-compose-dev"
}
```

### Stderr

```text

```

## Check 4 - PASS

- Command: `/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python /home/chewy/projects/trading-compose-dev/composer_original/tools/run_cpu_gpu_backtests.py --output-json /home/chewy/projects/trading-compose-dev/composer_original/reports/cpu_gpu_backtest_report.json`
- Return code: `0`

### Stdout

```text
{
  "config": {
    "initial_equity": 100000.0,
    "sell_fee_bps": 0.0,
    "slippage_bps": 1.0,
    "warmup_days": 260
  },
  "cpu": {
    "allocation_days": 160,
    "equity_points": 420,
    "final_equity": 80023.01040817637,
    "total_return_pct": -19.976989591823624,
    "trade_count": 1
  },
  "gpu": {
    "allocation_days": 160,
    "equity_points": 420,
    "final_equity": 80023.01040817637,
    "total_return_pct": -19.976989591823624,
    "trade_count": 1
  },
  "parity": {
    "final_equity_abs_diff": 0.0,
    "final_equity_diff_bps_vs_cpu": 0.0
  },
  "prices_csv": "/home/chewy/projects/trading-compose-dev/composer_original/fixtures/deep_check_prices.csv"
}
```

### Stderr

```text

```
