# Research Sweep Workspace

This folder is isolated for simulation research. It does **not** modify existing strategy/runtime code.

## What is included

- `sweep_runner.py`: parallel parameter sweep runner
- `gpu_native_daily_batch.py`: vectorized GPU-native daily-synthetic batch engine
- `configs/quick_grid.json`: quick practical grid
- `configs/wide_grid_template.json`: broad search template
- `reports/`: auto-generated run outputs

## How it works

`research/sweep_runner.py` launches many independent runs of:

- `composer_original/tools/intraday_profit_lock_verification.py`

For each parameter combination, it collects:

- CPU final equity / return / PnL / max drawdown
- GPU final equity / return / PnL / max drawdown
- CPU-GPU parity difference (bps)
- full parameter values used

Then it ranks results with:

- `cpu_score = cpu_return_pct - dd_weight * cpu_maxdd_pct`
- `gpu_score = gpu_return_pct - dd_weight * gpu_maxdd_pct`
- `combined_score = mean(cpu_score, gpu_score)`

## Parallel processing

By default it uses:

- `--max-workers = os.cpu_count()`

This maximizes CPU job parallelism. Each job computes both CPU and GPU metrics via the verifier.

If your GPU path contends at high process counts, reduce `--max-workers` to a stable value.

## Engine modes

`sweep_runner.py` now supports two execution engines:

1. `--engine verifier_subprocess` (default)
- Existing behavior: one verifier subprocess per combo.
- Best for exact parity with current verifier workflow.

2. `--engine gpu_native_daily_batch`
- New research engine under `research/` only.
- Runs all combos in one vectorized batch with daily synthetic mechanics.
- Uses CuPy GPU path when available, otherwise deterministic CPU fallback.
- Writes backend diagnostics to `run_meta.json -> engine_meta`.

Important:
- `gpu_native_daily_batch` is a research engine, not production runtime execution.
- It intentionally models daily-synthetic behavior and does not emulate minute-level resting order lifecycle.

## Quick pilot run (recommended first)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/research/sweep_runner.py \
  --grid-config /home/chewy/projects/trading-compose-dev/research/configs/quick_grid.json \
  --start-date 2026-03-13 \
  --end-date 2026-03-26 \
  --initial-equity 10000 \
  --initial-principal 10000 \
  --data-feed sip \
  --max-workers $(nproc) \
  --run-name quick_pilot_20260326
```

## GPU-native batch run example

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/research/sweep_runner.py \
  --engine gpu_native_daily_batch \
  --grid-config /home/chewy/projects/trading-compose-dev/research/configs/pilot_grid.json \
  --start-date 2026-03-13 \
  --end-date 2026-03-26 \
  --initial-equity 10000 \
  --initial-principal 10000 \
  --data-feed sip \
  --run-name gpu_native_pilot_20260327
```

## GPU diagnostics (full-GPU readiness)

Run:

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/research/gpu_env_check.py
```

If it reports `cupy kernel test FAILED` with CUDA root detection errors:
- set `CUDA_PATH` to your CUDA toolkit root (example: `/usr/local/cuda`)
- ensure NVRTC/toolkit components are installed and visible to the venv
- rerun `gpu_env_check.py` until kernel test passes

Once kernel test passes, `gpu_native_daily_batch` will use real GPU backend.

## Wide run (heavy)

Create your custom grid (copy `wide_grid_template.json`), then run:

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/research/sweep_runner.py \
  --grid-config /home/chewy/projects/trading-compose-dev/research/configs/wide_grid_template.json \
  --start-date 2025-03-26 \
  --end-date 2026-03-26 \
  --initial-equity 10000 \
  --initial-principal 10000 \
  --data-feed sip \
  --max-workers $(nproc) \
  --run-name wide_search_20260326
```

## Output files per run

Under `research/reports/<run_name>/`:

- `run_meta.json`
- `results_ranked.csv`
- `results_ranked.json`
- `results_top.csv`
- `failures.csv` (only if some runs fail)

## Notes

- This framework is for research/simulation exploration.
- It ranks by return and drawdown balance; adjust `--dd-weight` to prefer higher return or lower risk.
- If needed, run separate sweeps for different windows and compare stable winners.
