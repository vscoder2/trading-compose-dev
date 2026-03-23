# trailing12_4_adapt: Overview, Rules, and Strategy

## Scope
This document defines the locked `trailing12_4_adapt` profile used for `composer_original` backtests and parity replay.

Primary strategy source:
- `/home/chewy/projects/trading-compose-dev/composer_original/files/composer_original_file.txt`

Primary implementation runner:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

## Locked Preset Definition
Preset name:
- `trailing12_4_adapt`

Locked values:

| Parameter | Locked Value |
|---|---|
| `enable_profit_lock` | `true` |
| `profit_lock_mode` | `trailing` |
| `profit_lock_threshold_pct` | `12.0` |
| `profit_lock_trail_pct` | `4.0` |
| `profit_lock_partial_sell_pct` | `50.0` (unused in trailing mode) |
| `profit_lock_adaptive_threshold` | `true` |
| `profit_lock_adaptive_symbol` | `TQQQ` |
| `profit_lock_adaptive_rv_window` | `14` |
| `profit_lock_adaptive_rv_baseline_pct` | `85.0` |
| `profit_lock_adaptive_min_threshold_pct` | `8.0` |
| `profit_lock_adaptive_max_threshold_pct` | `30.0` |
| `profit_lock_trend_filter` | `false` |
| `profit_lock_regime_gated` | `false` |

Implementation location:
- preset table at line 35 in `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

## Rules
1. When `--strategy-preset trailing12_4_adapt` is set, these locked values are force-applied.
2. If any conflicting CLI override is supplied (example: `--profit-lock-threshold-pct 15`), execution fails fast with a lock error.
3. `--strategy-preset` is not allowed with `--walk-forward-grid` in the same command.
4. Strategy logic remains driven by the original strategy evaluator (`--strategy-mode original`), with profit-lock replay layered on top.
5. For Composer-like parity backtests, use `--composer-like-mode` (adjusted prices + anchored behavior in current tooling).

Lock enforcement location:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py` lines 706-735

## Strategy Logic (Profit-Lock Layer)
This profile uses daily replay logic with intraday high emulation.

### Adaptive Threshold Model
For day `t`:
- Base threshold = `12%`
- Realized volatility is computed from prior `14` closes of `TQQQ` and annualized
- Adaptive factor = `rv_t / 85`
- Raw threshold = `12 * adaptive_factor`
- Final threshold = `clamp(raw, 8, 30)`

Implementation:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py` lines 212-248

### Trigger and Exit Rule
For each held symbol on day `t`:
1. Trigger price = previous close `* (1 + threshold_t)`
2. If day high reaches trigger, trailing stop activates
3. Trailing stop = day high `* (1 - 0.04)`
4. If day close <= trailing stop, position exits (full exit in `trailing` mode)

Implementation:
- CPU replay logic: lines 311-470
- GPU replay logic: lines 476-624

## Run Commands
### Locked preset run
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py \
  --composer-like-mode \
  --strategy-mode original \
  --strategy-preset trailing12_4_adapt \
  --initial-equity 10000 \
  --start-date 2025-03-19 \
  --end-date 2026-03-19
```

### Example of blocked override (expected failure)
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py \
  --composer-like-mode \
  --strategy-mode original \
  --strategy-preset trailing12_4_adapt \
  --profit-lock-threshold-pct 15 \
  --initial-equity 10000 \
  --start-date 2026-02-19 \
  --end-date 2026-03-19
```

## Non-Goals
- This preset does not turn on trend gating.
- This preset does not turn on volatility regime gating.
- This preset does not alter core strategy allocation logic in `composer_original_file.txt`; it standardizes replay risk-management settings.
