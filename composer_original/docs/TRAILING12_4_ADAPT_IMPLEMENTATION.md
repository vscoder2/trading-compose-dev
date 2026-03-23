# trailing12_4_adapt: Implementation Document

## Objective
Lock and standardize the `trailing12_4_adapt` profile so runs are reproducible and protected from accidental CLI drift.

## What Was Implemented
Implementation file:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

Implemented capabilities:
1. Preset registry `STRATEGY_PRESETS` with locked values for `trailing12_4_adapt`.
2. CLI flag `--strategy-preset` to apply locked profile.
3. Conflict detection to reject mismatched overrides.
4. Guard to block incompatible combo (`--strategy-preset` + `--walk-forward-grid`).
5. Report metadata propagation (`strategy_preset` included in CPU/GPU/summary outputs).

## Code-Level Implementation Map
| Area | Purpose | Location |
|---|---|---|
| Preset declaration | Canonical lock values and CLI-key mapping | file lines 35-68 |
| CLI override parser | Extract user-provided flags for conflict checks | file lines 671-687 |
| Type coercion | Normalize provided values before comparison | file lines 689-703 |
| Preset lock enforcement | Apply locks and reject conflicting overrides | file lines 706-735 |
| CLI option | Adds `--strategy-preset` | file lines 1122-1126 |
| Main integration | Applies preset immediately after parse | file line 1161 |
| Report config metadata | Persists `strategy_preset` to CPU/GPU reports | file lines 1331 and 1381 |
| Summary metadata | Persists `strategy_preset` in summary report | file line 1430 |

Full file path for all line references:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

## Lock Semantics
When `--strategy-preset trailing12_4_adapt` is supplied:
- Effective run values are overwritten with preset values.
- Any conflicting explicit override fails with `ValueError`.
- Matching redundant values are allowed.
- Walk-forward mode must run without preset flag (current guard behavior).

## Validation Runs Performed
### Validation 1: Compile check
- Command: `python -m py_compile` (venv python)
- Result: pass

### Validation 2: Preset run
- Command includes `--strategy-preset trailing12_4_adapt`
- Result: pass
- Evidence in summary report includes:
  - `strategy_preset = trailing12_4_adapt`
  - `profit_lock.mode = trailing`
  - `profit_lock.threshold_pct = 12.0`
  - `profit_lock.trail_pct = 4.0`
  - `profit_lock.adaptive_threshold = true`

### Validation 3: Conflict rejection
- Command includes preset plus conflicting `--profit-lock-threshold-pct 15`
- Result: expected fail (lock error)

### Validation 4: Walk-forward incompatibility guard
- Command includes preset plus `--walk-forward-grid`
- Result: expected fail (`cannot be combined`)

Detailed review evidence:
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.json`
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.md`
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_docs_four_pass_review.json`
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_docs_four_pass_review.md`

## All Files Touched and Implemented for trailing12_4_adapt
### Code
1. `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

### Backtest Reports (profile-specific)
1. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtests_cpu_gpu_trailing12_4_adapt_1m_3m_6m_1y_2y_3y_5y_10k.json`
2. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_1m_trailing12_4_adapt_10k.json`
3. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_3m_trailing12_4_adapt_10k.json`
4. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_6m_trailing12_4_adapt_10k.json`
5. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_1y_trailing12_4_adapt_10k.json`
6. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_2y_trailing12_4_adapt_10k.json`
7. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_3y_trailing12_4_adapt_10k.json`
8. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_cpu_5y_trailing12_4_adapt_10k.json`
9. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_1m_trailing12_4_adapt_10k.json`
10. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_3m_trailing12_4_adapt_10k.json`
11. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_6m_trailing12_4_adapt_10k.json`
12. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_1y_trailing12_4_adapt_10k.json`
13. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_2y_trailing12_4_adapt_10k.json`
14. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_3y_trailing12_4_adapt_10k.json`
15. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_gpu_5y_trailing12_4_adapt_10k.json`
16. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_1m_trailing12_4_adapt_10k.json`
17. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_3m_trailing12_4_adapt_10k.json`
18. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_6m_trailing12_4_adapt_10k.json`
19. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_1y_trailing12_4_adapt_10k.json`
20. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_2y_trailing12_4_adapt_10k.json`
21. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_3y_trailing12_4_adapt_10k.json`
22. `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtest_summary_5y_trailing12_4_adapt_10k.json`

### Comparison and Selection Reports
1. `/home/chewy/projects/trading-compose-dev/composer_original/reports/comparison_original_fixed15_trailing155_trailing155adaptive_trailing124_trailing124adapt_1m_3m_6m_1y_2y_3y_5y_10k.json`
2. `/home/chewy/projects/trading-compose-dev/composer_original/reports/walk_forward_profit_lock_grid_leaderboard_10k.json`
3. `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.json`
4. `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.md`
5. `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_docs_four_pass_review.json`
6. `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_docs_four_pass_review.md`

### Documentation Added
1. `/home/chewy/projects/trading-compose-dev/composer_original/docs/TRAILING12_4_ADAPT_OVERVIEW_RULES_STRATEGY.md`
2. `/home/chewy/projects/trading-compose-dev/composer_original/docs/TRAILING12_4_ADAPT_IMPLEMENTATION.md`
3. `/home/chewy/projects/trading-compose-dev/composer_original/docs/TRAILING12_4_ADAPT_BACKTESTS_CPU_GPU.md`
4. `/home/chewy/projects/trading-compose-dev/composer_original/docs/TRAILING12_4_ADAPT_ARCHITECTURE.md`
5. `/home/chewy/projects/trading-compose-dev/composer_original/docs/TRAILING12_4_ADAPT_DOCUMENTATION_INDEX.md`
