# 3-Pass Review of `improvements2.md`

Source reviewed:

- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/docs/improvements2.md`

Runtime cross-check references:

- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py`
- `/home/chewy/projects/trading-compose-dev/soxl_growth/db.py`

## Pass 1: Structure and Consistency

Findings:

1. The file is a concatenated conversation, not a single design spec.
2. Priorities are repeated with multiple conflicting ranking lists.
3. It mixes:
   - operational guide commentary
   - strategy research ideas
   - pseudo-architecture proposals
   - backlog/ticket text
4. It contains non-actionable conversational artifacts (for example: "If you want, I can...").
5. It should be normalized into one canonical baseline before any coding.

Verdict:

- Good idea inventory: `YES`
- Production-ready spec document: `NO`
- Needs canonicalization: `MANDATORY`

## Pass 2: Factual Validation (Right / Wrong / Needs Change)

| Topic from `improvements2.md` | Validation | Result |
|---|---|---|
| 5-minute intraday profit-lock cadence exists | `runtime_switch_loop.py` profile sets `intraday_profit_lock_check_minutes=5` | `RIGHT` |
| Three variant states (`baseline`, `inverse_ma20`, `inverse_ma60`) | `_choose_variant` and variant overlay logic confirm this | `RIGHT` |
| Adaptive profit-lock threshold uses `TQQQ` realized vol | Profile + `_current_threshold_pct` confirm this | `RIGHT` |
| Rebalance executes at configured eval time and once/day | Daily gating with `switch_executed_day` confirms once/day behavior | `RIGHT` |
| Existing persistent schema is minimal (`state_kv`, `events`) | `soxl_growth/db.py` confirms only these two tables | `RIGHT` |
| Key name `legacy parity executed-day key` | Actual key in switch runtime is `switch_executed_day` | `WRONG` |
| "Profit-lock symbols are always blocked from same-cycle re-entry" | Blocking currently only happens for `stop_order`/`trailing_stop` path in rebalance filter | `NEEDS CHANGE` |
| "Baseline means safe mode" | Baseline can still hold high-risk leveraged exposure; not a true safety mode | `NEEDS CHANGE` |
| Drawdown-focused recommendations are high priority | Consistent with long-window risk profile and runtime behavior | `RIGHT` |
| Many proposal lists are mutually consistent | Multiple lists conflict in exact ordering and grouping | `WRONG` |

## Pass 3: Implementation Feasibility and Risk

High-confidence, low-regret items:

1. Priority/action ladder before submit.
2. Pre-submit dry-run validator.
3. Broker/open-order reconciliation and drift detection.
4. Durable lock objects (instead of sparse ad hoc keys).
5. Explicit exposure scalar + drawdown brake layer.

Medium-confidence items (need calibration protocol first):

1. Hysteresis bands.
2. Regime confidence score.
3. Adaptive rebalance threshold.
4. Session-aware policy tuning.

Research-heavy items (should be sidecars first, not directly in live path):

1. Live shadow comparator.
2. Attribution engine.
3. Adaptive cadence.
4. Fill-quality adaptive controller.

## Final Review Conclusion

`improvements2.md` contains many strong ideas, but as-is it should not be implemented directly.

Before coding, convert it into a canonical backlog with:

1. one non-contradictory priority order
2. exact acceptance tests
3. explicit "do now" vs "research sidecar"
4. strict naming aligned to actual runtime keys and behavior

