# csp47_overlay_research_v1

Standalone research sandbox for `C_sp4.7_rv75_tr1.10_th1.20`.

## Guarantees
- Does **not** modify existing runtime/profile code.
- Uses existing data/simulation helpers via imports.
- Writes outputs only under `csp47_overlay_research_v1/reports/`.

## Overlay ideas tested
- Shock guard: force defensive target after severe SOXL down day.
- Drawdown brake: force defensive target when SOXL rolling drawdown breaches threshold.
- Re-entry confirmation: require consecutive positive closes before releasing defensive latch.

## Run
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/csp47_overlay_research_v1/tools/sweep_csp47_overlays.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --data-feed sip \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --end-date 2026-04-10 \
  --tag first_pass
```

## Outputs
- `quick_window_metrics.csv`
- `quick_ranked.csv`
- `full_window_metrics.csv`
- `full_ranked.csv`
- `leaderboard_top30.csv`
- `baseline_full_windows_locked47.csv`
- `run_meta.json`
