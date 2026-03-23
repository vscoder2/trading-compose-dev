# Switch Runtime V1 Pre-Live Go/No-Go Form

## Purpose

Use this form before running `--mode live --execute-orders` for:

- `switch_runtime_v1/runtime_switch_loop.py`
- Profile: `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m`
- Feed: `sip`
- Eval time target: `15:56`

## Release Metadata

| Field | Value |
|---|---|
| Date (NY) |  |
| Release ID / ticket |  |
| Operator |  |
| Reviewer |  |
| Planned start time (NY) |  |
| Runtime host |  |
| State DB path |  |
| Mode to launch | `live` |

## Hard Gates (All must be PASS)

| Gate | PASS/FAIL | Evidence |
|---|---|---|
| Paper run success in same config family |  | last paper run ID/date |
| No open critical incidents |  | incident tracker |
| Correct env file loaded |  | `.env.dev` checksum/date |
| Credentials valid and least-privilege |  | API auth check |
| Account mode confirmed (live) |  | broker account id/type |
| Data feed confirmed (`sip`) |  | startup logs |
| Eval time confirmed (`15:56`) |  | launch command |
| `--execute-orders` intentional |  | launch command |
| Single runtime process enforced |  | process list |
| State DB points to approved path |  | launch command |
| Risk limits configured and reviewed |  | limits doc |
| Dry-run sanity completed today |  | command/output |

## Configuration Diff Review

| Item | Approved value | Launch value | Match |
|---|---|---|---|
| `--strategy-profile` | `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m` |  | Yes/No |
| `--data-feed` | `sip` |  | Yes/No |
| `--eval-time` | `15:56` |  | Yes/No |
| `--profit-lock-order-type` | `market_order` |  | Yes/No |
| `--rebalance-order-type` | `market` |  | Yes/No |
| `--mode` | `live` |  | Yes/No |
| `--execute-orders` | enabled |  | Yes/No |
| `--state-db` | approved path |  | Yes/No |

## Risk, Capital, and Exposure Controls

| Control | Target | Current | PASS/FAIL |
|---|---:|---:|---|
| Max per-symbol exposure |  |  |  |
| Max total gross exposure |  |  |  |
| Max intraday drawdown stop |  |  |  |
| Min available buying power buffer |  |  |  |
| Allowed symbols universe | policy match |  |  |
| Order reject escalation path | defined |  |  |

## Operational Readiness Checks

| Check | PASS/FAIL | Notes |
|---|---|---|
| Log pipeline healthy |  |  |
| Event DB writable |  |  |
| Time sync/clock sanity |  |  |
| On-call contact available |  |  |
| Rollback/stop procedure validated |  |  |
| Dashboard/monitoring page reachable |  |  |

## Launch Command (Final)

```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py \
  --env-file /home/chewy/projects/trading-compose-dev/.env.dev \
  --env-override \
  --mode live \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --data-feed sip \
  --eval-time 15:56 \
  --profit-lock-order-type market_order \
  --rebalance-order-type market \
  --execute-orders
```

## Immediate Post-Launch Validation (T+0 to T+10 min)

| Check | PASS/FAIL | Evidence |
|---|---|---|
| Process started without exceptions |  | startup log |
| Clock open/next session resolved |  | log/event |
| State keys initialized |  | db query/snapshot |
| No duplicate process |  | process list |
| Event flow present |  | `switch_cycle_complete` or heartbeat evidence |
| No immediate order rejections |  | broker/order logs |

## Go/No-Go Decision

- Decision: `GO` / `NO-GO`
- Decision time (NY): ____________________
- Approver: ____________________
- Operator: ____________________
- Notes/conditions: __________________________________________

## If NO-GO, Required Actions

1. Do not launch live execution.
2. Log blocker and owner.
3. Run paper validation with identical config.
4. Re-submit this form after blocker closure.

## References

- SOP: `switch_runtime_v1/docs/SWITCH_RUNTIME_V1_TRADING_DESK_SOP.md`
- Runtime guide: `switch_runtime_v1/docs/SWITCH_RUNTIME_V1_PAPER_LIVE_GUIDE.md`
