# Runtime V2 Control-Plane Entry

Path:

- `switch_runtime_v1/runtime_switch_loop_v2_controlplane.py`

## Safety Goal

This entrypoint is designed to avoid breaking existing production flow:

1. If `--controlplane-enable` is **not** passed, it delegates directly to legacy runtime path (`runtime_switch_loop._run_loop`).
2. Existing profile `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m` and legacy runtime file are untouched.
3. Control-plane behavior is opt-in and isolated behind explicit flags.

## Legacy-Equivalent Command (delegated path)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop_v2_controlplane.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## Control-Plane Enabled Command

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop_v2_controlplane.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode paper \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:55 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --controlplane-enable \
  --controlplane-log-confidence \
  --controlplane-enable-shadow-eval \
  --execute-orders
```

## Added Control-Plane Features (opt-in)

1. Hysteresis regime filter (`I2-021`).
2. Confidence score + optional reason logging (`I2-022`).
3. Adaptive rebalance threshold under noisy conditions (`I2-023`).
4. Execution conflict resolver before submit (`I2-024`).
5. Decision cycle persistence + decision hash (`I2-025`).
6. Risk snapshot writes into control-plane DB.
7. Optional non-submitting shadow comparison cycle (`I2-027`).

## Control-Plane DB

Default:

- `switch_runtime_v1_controlplane.db`

Auto-migrations applied on startup unless `--controlplane-no-apply-migrations` is used:

- `improvements2_impl/migrations/001_control_plane.sql`
- `improvements2_impl/migrations/002_execution_observability.sql`

## Recommended Rollout

1. `--run-once` without `--execute-orders` in paper mode.
2. paper mode with `--execute-orders`.
3. live mode after operational signoff.

## Notes

1. This is a new runtime entrypoint and does not modify existing runtime behavior unless used explicitly.
2. For strict legacy behavior, continue to use `runtime_switch_loop.py` directly or run v2 without `--controlplane-enable`.
