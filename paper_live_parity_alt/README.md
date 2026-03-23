# Paper/Live Parity (Standalone, No composer_original Changes)

This module provides a separate path to get exact post-trade parity between:

- realized paper/live broker execution results
- replayed "backtest" results built from the same broker fills

It does **not** modify `composer_original`.

## What This Solves

Forward-looking exact parity with real broker fills is not possible.
But post-trade exact parity is possible by replaying the exact fills.

## Files

- `fetch_alpaca_fills.py`
  - Pulls Alpaca FILL activities to CSV.
- `replay_from_fills.py`
  - Replays fills and computes realized PnL/return/equity.
- `compare_replay_to_backtest.py`
  - Compares replay metrics with a backtest summary JSON.

## 1) Export Alpaca Fills

```bash
composer_original/.venv/bin/python paper_live_parity_alt/fetch_alpaca_fills.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --mode paper \
  --after 2026-01-01 \
  --until 2026-03-21 \
  --out-csv paper_live_parity_alt/reports/fills_2026q1.csv
```

Expected environment vars:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

## 2) Replay Exact Broker Fills

```bash
composer_original/.venv/bin/python paper_live_parity_alt/replay_from_fills.py \
  --fills-csv paper_live_parity_alt/reports/fills_2026q1.csv \
  --initial-equity 10000 \
  --out-prefix paper_live_parity_alt/reports/replay_2026q1
```

Outputs:

- `..._events.csv`
- `..._summary.json`

## 3) Compare Replay vs Backtest Report

```bash
composer_original/.venv/bin/python paper_live_parity_alt/compare_replay_to_backtest.py \
  --replay-summary paper_live_parity_alt/reports/replay_2026q1_summary.json \
  --backtest-summary /path/to/backtest_summary.json
```

This prints absolute and percentage deltas.

