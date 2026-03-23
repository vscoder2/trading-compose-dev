# Switch Runtime V1 Shift Handoff Template

## Handoff Metadata

| Field | Value |
|---|---|
| Date (NY) |  |
| Handoff time (NY) |  |
| Outgoing operator |  |
| Incoming operator |  |
| Environment | `paper` / `live` |
| Profile | `aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m` |
| Feed | `sip` |
| Eval time | `15:56` |
| Runtime command version |  |
| State DB path |  |

## Process and Infra Status

| Check | Status | Notes |
|---|---|---|
| Runtime process active | PASS / FAIL | PID, host |
| Single active process only | PASS / FAIL | No duplicate worker |
| Last heartbeat/log line recent | PASS / FAIL | Last timestamp |
| Alpaca trading API reachable | PASS / FAIL |  |
| Alpaca data API reachable | PASS / FAIL |  |
| Clock endpoint healthy | PASS / FAIL | is_open, next_open |
| Data freshness within threshold | PASS / FAIL | stale minutes |

## Strategy/State Snapshot

| Key | Value |
|---|---|
| `switch_executed_day` |  |
| `switch_last_profile` |  |
| `switch_last_variant` |  |
| `switch_last_baseline_target` hash/summary |  |
| `switch_last_final_target` hash/summary |  |
| `switch_regime_state` summary |  |
| `switch_intraday_profit_lock_last_slot` |  |

## Risk and Exposure Snapshot

| Metric | Value |
|---|---|
| Account equity |  |
| Cash |  |
| Buying power |  |
| Open positions count |  |
| Largest symbol weight |  |
| Unrealized P/L total |  |
| Realized P/L today |  |
| Max intraday drawdown today |  |

### Current Positions

| Symbol | Qty | Avg entry | Market value | Weight % | Unrealized P/L % |
|---|---:|---:|---:|---:|---:|
|  |  |  |  |  |  |

## Event Timeline Since Last Handoff

| Time (NY) | Event Type | Symbol | Action | Outcome | Notes |
|---|---|---|---|---|---|
|  | `switch_variant_changed` / `switch_profit_lock_intraday_close` / `switch_profit_lock_close` / `switch_rebalance_order` / `switch_cycle_complete` |  |  |  |  |

## Open Orders and Exceptions

### Open Orders

| Order ID | Symbol | Side | Type | Qty | Status | Created (NY) |
|---|---|---|---|---:|---|---|
|  |  |  |  |  |  |  |

### Exceptions / Alerts

| Time (NY) | Severity | Issue | Impact | Mitigation Taken | Follow-up |
|---|---|---|---|---|---|
|  | Low/Med/High |  |  |  |  |

## Pending Actions for Incoming Operator

| Priority | Action | Owner | Due (NY) |
|---|---|---|---|
| P1/P2/P3 |  |  |  |

## Next Decision Window Checklist

- Confirm process is running and no duplicates.
- Confirm clock, market status, and data freshness.
- Confirm profile, mode, eval-time, and feed match policy.
- Confirm risk snapshot within expected bounds.
- Confirm no unresolved critical exceptions.

## Sign-off

- Outgoing operator sign-off: ____________________
- Incoming operator acknowledgment: ____________________

## Notes

- Use with SOP: `switch_runtime_v1/docs/SWITCH_RUNTIME_V1_TRADING_DESK_SOP.md`.
- Keep one handoff record per shift/day and archive with logs.
