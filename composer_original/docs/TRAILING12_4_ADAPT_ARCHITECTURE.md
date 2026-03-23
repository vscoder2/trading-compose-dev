# trailing12_4_adapt: Architecture Document

## System Context
The `trailing12_4_adapt` profile is implemented in the backtest/replay runner and applies a locked profit-lock policy on top of strategy allocations from the original composer logic.

Core file:
- `/home/chewy/projects/trading-compose-dev/composer_original/tools/run_last_6m_cpu_gpu_backtests.py`

## High-Level Flow
```text
CLI args
  -> parse_args
  -> apply preset lock (optional)
  -> fetch daily data (stooq or yfinance)
  -> build close/high wide tables
  -> run base strategy allocator (run_backtest)
  -> replay with profit-lock (CPU)
  -> replay with profit-lock (GPU)
  -> slice window metrics
  -> emit CPU/GPU/summary JSON reports
```

## Components
### 1. Preset Lock Layer
Responsibilities:
- Define immutable preset parameters.
- Map CLI flags to internal argument names.
- Compare user overrides vs locked values.
- Reject conflicts and enforce deterministic execution.

Key implementation locations:
- Preset declaration: lines 35-68
- Lock application: lines 706-735
- CLI exposure: lines 1122-1126
- Main integration: line 1161

### 2. Data Ingestion Layer
Responsibilities:
- Pull daily bars from configured source.
- Normalize into `date`, `close`, `high`.
- Persist raw and merged parquet/csv artifacts.

Key functions:
- `_fetch_symbol_daily` (stooq)
- `_fetch_symbol_daily_yfinance` (yfinance)
- `_build_wide_table`, `_build_wide_high_table`

### 3. Adaptive Threshold Builder
Responsibilities:
- Compute annualized realized volatility on adaptive symbol (`TQQQ` in preset).
- Scale base threshold and clamp between min and max bounds.

Key function:
- `_build_profit_lock_threshold_series` lines 212-248

Rule expression:
- `threshold_t = clamp(base * (rv_t / baseline), min_threshold, max_threshold)`

### 4. CPU Replay Engine
Responsibilities:
- Replay allocations day-by-day.
- Apply trigger-and-trailing exit logic before rebalance leg.
- Produce CPU equity curve and trade counts.

Key function:
- `_cpu_replay_from_allocations` lines 311-470

### 5. GPU Replay Engine
Responsibilities:
- Mirror CPU replay math using vectorized cupy path.
- Maintain near-zero parity drift vs CPU output.

Key function:
- `_gpu_replay_from_cpu_allocations` lines 476-624

### 6. Reporting Layer
Responsibilities:
- Build CPU and GPU report objects.
- Compute parity basis-point drift.
- Persist JSON artifacts for each run.

Key output blocks:
- CPU report config: starts line 1325
- GPU report config: starts line 1375
- Summary object: starts line 1427

## Runtime Configuration Interaction
Order of precedence:
1. Parse CLI args.
2. Apply `--strategy-preset` lock (if provided).
3. Run backtest/replay with effective values.

For `trailing12_4_adapt`:
- Profit lock always enabled.
- Mode always trailing.
- Adaptive threshold always enabled.
- Trend/regime gates always disabled.

## Failure Modes and Safeguards
1. Conflicting CLI override under preset.
- Behavior: hard fail with explicit mismatch message.

2. Preset with walk-forward grid.
- Behavior: hard fail (`cannot be combined`).

3. Missing adaptive symbol in history.
- Behavior: fallback to first available symbol in threshold builder.

4. CPU/GPU divergence.
- Behavior: parity diff emitted in summary report (`final_equity_diff_bps_vs_cpu`).

## Artifacts and Observability
Primary outputs:
- `backtest_cpu_last6m.json`
- `backtest_gpu_last6m.json`
- `backtest_cpu_gpu_last6m_summary.json`

Preset traceability:
- `strategy_preset` is persisted in CPU/GPU config and summary JSON.

## Architectural Constraints
- Current model is daily-bar replay with intraday-high trigger emulation, not minute-by-minute market replay.
- Walk-forward grid and preset lock are intentionally separate execution modes.
