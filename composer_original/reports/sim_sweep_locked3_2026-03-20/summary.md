# Locked-Profile Simulation Sweep

- runs attempted: 81
- successful runs: 81
- failed runs: 0

## Leaderboard (Avg CPU Return Across Windows)

| Rank | Exec Model | Profile | Avg CPU Return % | Avg CPU Max DD % | Avg Return/DD | Best Window | Best Window Return % |
|---|---|---|---:|---:|---:|---|---:|
| 1 | paper_live_style_optimistic | aggr_adapt_t10_tr2_rv14_b85_m8_M30 | 428.53 | 49.25 | 10.28 | 5y | 964.28 |
| 2 | synthetic | aggr_adapt_t10_tr2_rv14_b85_m8_M30 | 428.53 | 49.25 | 10.28 | 5y | 964.28 |
| 3 | paper_live_style_optimistic | trailing12_4_adapt | 187.59 | 51.05 | 5.82 | 1y | 441.07 |
| 4 | synthetic | trailing12_4_adapt | 187.59 | 51.05 | 5.82 | 1y | 441.07 |
| 5 | market_close | original_composer | 85.74 | 51.96 | 3.58 | 1y | 233.65 |
| 6 | synthetic | original_composer | 85.74 | 51.96 | 3.58 | 1y | 233.65 |
| 7 | paper_live_style_optimistic | original_composer | 85.74 | 51.96 | 3.58 | 1y | 233.65 |
| 8 | market_close | trailing12_4_adapt | 85.34 | 51.98 | 3.57 | 1y | 232.92 |
| 9 | market_close | aggr_adapt_t10_tr2_rv14_b85_m8_M30 | 84.95 | 52.00 | 3.56 | 1y | 232.40 |

## Cases That Beat aggr Return (same window + exec model)

| Window | Exec Model | Candidate | Candidate Return % | aggr Return % | Delta % pts | Candidate Max DD % | aggr Max DD % | Delta DD % pts |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 1m | market_close | original_composer | 83.38 | 83.20 | 0.18 | 8.63 | 8.63 | 0.00 |
| 1m | market_close | trailing12_4_adapt | 83.27 | 83.20 | 0.07 | 8.63 | 8.63 | 0.00 |
| 2m | market_close | original_composer | 137.30 | 136.92 | 0.38 | 14.56 | 14.58 | -0.02 |
| 2m | market_close | trailing12_4_adapt | 137.01 | 136.92 | 0.09 | 14.58 | 14.58 | 0.00 |
| 3m | market_close | original_composer | 83.73 | 83.39 | 0.33 | 36.00 | 36.03 | -0.03 |
| 3m | market_close | trailing12_4_adapt | 83.50 | 83.39 | 0.11 | 36.01 | 36.03 | -0.01 |
| 6m | market_close | original_composer | 105.02 | 104.40 | 0.61 | 39.12 | 39.16 | -0.04 |
| 6m | market_close | trailing12_4_adapt | 104.61 | 104.40 | 0.20 | 39.14 | 39.16 | -0.01 |
| 1y | market_close | original_composer | 233.65 | 232.40 | 1.25 | 39.12 | 39.16 | -0.04 |
| 1y | market_close | trailing12_4_adapt | 232.92 | 232.40 | 0.52 | 39.14 | 39.16 | -0.01 |
| 2y | market_close | original_composer | 132.67 | 131.45 | 1.22 | 62.27 | 62.29 | -0.03 |
| 2y | market_close | trailing12_4_adapt | 132.07 | 131.45 | 0.62 | 62.28 | 62.29 | -0.02 |
| 3y | market_close | original_composer | 13.51 | 12.66 | 0.84 | 84.61 | 84.65 | -0.04 |
| 3y | market_close | trailing12_4_adapt | 13.17 | 12.66 | 0.50 | 84.63 | 84.65 | -0.02 |
| 5y | market_close | original_composer | 50.10 | 48.43 | 1.67 | 88.20 | 88.27 | -0.07 |
| 5y | market_close | trailing12_4_adapt | 49.41 | 48.43 | 0.98 | 88.22 | 88.27 | -0.05 |
| 10y | market_close | original_composer | -67.73 | -68.35 | 0.62 | 95.14 | 95.21 | -0.07 |
| 10y | market_close | trailing12_4_adapt | -67.95 | -68.35 | 0.40 | 95.16 | 95.21 | -0.05 |