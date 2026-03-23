# trailing12_4_adapt: Backtest Document (CPU and GPU)

## Test Setup
- Principal / Initial equity: `$10,000`
- Strategy mode: `original`
- Data mode: `composer-like` (`--composer-like-mode`)
- End date used: `2026-03-19`
- Profile: `trailing12_4_adapt`

Primary report:
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/backtests_cpu_gpu_trailing12_4_adapt_1m_3m_6m_1y_2y_3y_5y_10k.json`

## trailing12_4_adapt Window Results
| Window | Start Date | End Date | CPU Final Equity | CPU Return % | CPU Max DD % | CPU Trades | GPU Final Equity | GPU Return % | GPU Max DD % | GPU Trades | Diff (bps) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1m | 2026-02-19 | 2026-03-19 | 19,596.54 | 95.9654 | 8.6207 | 13 | 19,596.54 | 95.9654 | 8.6207 | 13 | 0.0 |
| 3m | 2025-12-19 | 2026-03-19 | 22,184.54 | 121.8454 | 36.0000 | 29 | 22,184.54 | 121.8454 | 36.0000 | 29 | 0.0 |
| 6m | 2025-09-19 | 2026-03-19 | 31,727.46 | 217.2746 | 38.7788 | 50 | 31,727.46 | 217.2746 | 38.7788 | 50 | 0.0 |
| 1y | 2025-03-19 | 2026-03-19 | 54,646.62 | 446.4662 | 38.7788 | 258 | 54,646.62 | 446.4662 | 38.7788 | 251 | 0.0 |
| 2y | 2024-03-19 | 2026-03-19 | 37,266.99 | 272.6699 | 60.9042 | 464 | 37,266.99 | 272.6699 | 60.9042 | 460 | ~0.0 |
| 3y | 2023-03-19 | 2026-03-19 | 19,735.53 | 97.3553 | 83.5730 | 510 | 19,735.53 | 97.3553 | 83.5730 | 498 | ~0.0 |
| 5y | 2021-03-19 | 2026-03-19 | 37,783.94 | 277.8394 | 84.2476 | 1089 | 37,783.94 | 277.8394 | 84.2476 | 1081 | 0.0 |

Notes:
- CPU/GPU parity was effectively exact across windows (differences are numerical noise near zero).
- Trade counts can differ slightly between CPU and GPU while final equity parity remains near-identical.

## Comparative Benchmark Matrix
Comparison report:
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/comparison_original_fixed15_trailing155_trailing155adaptive_trailing124_trailing124adapt_1m_3m_6m_1y_2y_3y_5y_10k.json`

CPU return % by strategy and window:

| Window | original | fixed15 | trailing15_5 | trailing15_5_adaptive | trailing12_4 | trailing12_4_adapt |
|---|---:|---:|---:|---:|---:|---:|
| 1m | 87.6976 | 87.5234 | 87.6976 | 88.9942 | 89.5803 | 95.9654 |
| 3m | 85.5937 | 86.8099 | 86.5925 | 100.1853 | 94.8559 | 121.8454 |
| 6m | 106.4282 | 101.2829 | 107.5391 | 128.3681 | 121.6322 | 217.2747 |
| 1y | 236.9837 | 229.4490 | 246.4874 | 285.1841 | 290.3662 | 446.4663 |
| 2y | 117.8776 | 81.3591 | 124.0221 | 155.3523 | 152.3923 | 272.6698 |
| 3y | 13.5090 | -9.0466 | 16.7100 | 33.0323 | 31.4901 | 97.3552 |
| 5y | 62.2412 | 45.5250 | 77.1780 | 115.2288 | 106.0356 | 277.8396 |

Summary from comparison run:
- `trailing12_4_adapt` won all 7 windows by CPU return.
- Average CPU return across windows:
  - `trailing12_4_adapt`: `218.4881%`
  - `trailing15_5_adaptive`: `129.4779%`
  - `trailing12_4`: `126.6218%`
  - `trailing15_5`: `106.6038%`
  - `original`: `101.4759%`
  - `fixed15`: `88.9861%`

## Walk-Forward Selection Evidence
Walk-forward leaderboard report:
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/walk_forward_profit_lock_grid_leaderboard_10k.json`

From that walk-forward run, `trailing12_4_adapt` ranked first by average test final equity.

## Four-Pass Review Evidence
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.json`
- `/home/chewy/projects/trading-compose-dev/composer_original/reports/trailing12_4_adapt_four_pass_review.md`

Overall status in review report: `pass`.
