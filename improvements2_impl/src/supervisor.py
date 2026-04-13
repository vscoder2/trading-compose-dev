"""Phase 1 supervisory kernel scaffold.

This module deliberately focuses on control integrity primitives:
- priority ladder resolution
- pending-order suppression
- lock gating
- dry-run validation
- cycle-level severity + reason codes
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .action_policy import resolve_symbol_actions
from .models import ActionIntent, DecisionContext, DecisionResult, LockState
from .reconcile import build_pending_order_map, detect_state_drift, drift_records_to_dict


def _is_buy(intent: ActionIntent) -> bool:
    return str(intent.side).strip().lower() == "buy"


def _has_active_lock(locks: list[LockState], *, lock_type: str, symbol: str | None = None) -> bool:
    for lock in locks:
        if not bool(lock.active):
            continue
        if lock.lock_type != lock_type:
            continue
        if lock.subject is None:
            return True
        if symbol is not None and lock.subject.upper() == symbol.upper():
            return True
    return False


def dry_run_validate(
    *,
    intents: list[ActionIntent],
    positions: dict[str, float],
    buying_power: float,
    market_open: bool,
    data_fresh: bool,
) -> tuple[list[ActionIntent], list[dict[str, Any]], list[str], str]:
    """Validate candidate actions before submit.

    Returns:
    - allowed intents
    - blocked reason rows
    - cycle reason codes
    - severity
    """

    blocked: list[dict[str, Any]] = []
    allowed: list[ActionIntent] = []
    reason_codes: list[str] = []
    severity = "ok"

    if not market_open:
        reason_codes.append("market_closed")
        severity = "warn"
    if not data_fresh:
        reason_codes.append("data_not_fresh")
        severity = "warn"

    for intent in intents:
        sym = intent.symbol.upper()
        qty = float(intent.qty)
        if qty <= 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "invalid_non_positive_qty",
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("invalid_non_positive_qty")
            severity = "error"
            continue

        if not market_open:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "market_closed",
                    "blocked": asdict(intent),
                }
            )
            continue

        if not data_fresh and _is_buy(intent):
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "stale_data_blocks_adds",
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("stale_data_blocks_adds")
            continue

        if _is_buy(intent) and buying_power <= 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "insufficient_buying_power",
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("insufficient_buying_power")
            severity = "error"
            continue

        # Do not sell more than current position in dry-run.
        if str(intent.side).strip().lower() == "sell":
            available = max(0.0, float(positions.get(sym, 0.0)))
            if qty > available + 1e-9:
                blocked.append(
                    {
                        "symbol": sym,
                        "reason": "sell_qty_exceeds_position",
                        "available_qty": available,
                        "blocked": asdict(intent),
                    }
                )
                reason_codes.append("sell_qty_exceeds_position")
                severity = "error"
                continue

        allowed.append(intent)

    # Deduplicate reason list in stable order.
    seen: set[str] = set()
    deduped: list[str] = []
    for rc in reason_codes:
        if rc in seen:
            continue
        seen.add(rc)
        deduped.append(rc)
    return allowed, blocked, deduped, severity


def evaluate(ctx: DecisionContext) -> DecisionResult:
    """Evaluate one decision cycle and return approved net actions.

    The supervisor intentionally performs policy operations in a strict order:
    1. drift detection
    2. priority/net action resolution
    3. lock/pending-order gating
    4. dry-run validation
    """

    diagnostics: dict[str, Any] = {}
    blocked_actions: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    severity = "ok"

    # Step 1: drift detection (read-only in Phase 1 scaffold).
    drift = detect_state_drift(
        expected_qty={k.upper(): float(v) for k, v in ctx.positions.items()},
        broker_qty={k.upper(): float(v) for k, v in ctx.positions.items()},
        open_orders=list(ctx.open_orders),
    )
    diagnostics["drift_records"] = drift_records_to_dict(drift)
    if any(d.severity == "error" for d in drift):
        severity = "warn"
        reason_codes.append("drift_detected")

    # Step 2: collapse to at most one action per symbol by priority.
    resolved, blocked = resolve_symbol_actions(list(ctx.intents))
    blocked_actions.extend(blocked)

    # Step 3: pending-order and lock gating.
    pending_map = build_pending_order_map(list(ctx.open_orders))
    diagnostics["pending_order_map"] = pending_map
    gated: list[ActionIntent] = []
    for intent in resolved:
        sym = intent.symbol.upper()
        side = str(intent.side).strip().lower()

        # Global hard-brake lock blocks net-new adds.
        if _has_active_lock(ctx.locks, lock_type="hard_brake") and side == "buy":
            blocked_actions.append(
                {
                    "symbol": sym,
                    "reason": "hard_brake_blocks_add",
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("hard_brake_blocks_add")
            continue

        # Session no-reentry lock (symbol-scoped).
        if _has_active_lock(ctx.locks, lock_type="reentry_block", symbol=sym) and side == "buy":
            blocked_actions.append(
                {
                    "symbol": sym,
                    "reason": "reentry_lock_blocks_add",
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("reentry_lock_blocks_add")
            continue

        # Avoid duplicate side submission while broker order is pending.
        pending = pending_map.get(sym, {})
        pending_buy = float(pending.get("pending_buy_qty", 0.0))
        pending_sell = float(pending.get("pending_sell_qty", 0.0))
        if side == "buy" and pending_buy > 0:
            blocked_actions.append(
                {
                    "symbol": sym,
                    "reason": "pending_buy_exists",
                    "pending_buy_qty": pending_buy,
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("pending_buy_exists")
            continue
        if side == "sell" and pending_sell > 0:
            blocked_actions.append(
                {
                    "symbol": sym,
                    "reason": "pending_sell_exists",
                    "pending_sell_qty": pending_sell,
                    "blocked": asdict(intent),
                }
            )
            reason_codes.append("pending_sell_exists")
            continue

        gated.append(intent)

    # Step 4: dry-run validation.
    allowed, blocked, dry_run_reasons, dry_run_severity = dry_run_validate(
        intents=gated,
        positions={k.upper(): float(v) for k, v in ctx.positions.items()},
        buying_power=float(ctx.buying_power),
        market_open=bool(ctx.market_open),
        data_fresh=bool(ctx.data_fresh),
    )
    blocked_actions.extend(blocked)
    reason_codes.extend(dry_run_reasons)

    if dry_run_severity == "error":
        severity = "error"
    elif dry_run_severity == "warn" and severity != "error":
        severity = "warn"

    # Stable dedupe for reason codes.
    seen: set[str] = set()
    deduped: list[str] = []
    for rc in reason_codes:
        if rc in seen:
            continue
        seen.add(rc)
        deduped.append(rc)

    return DecisionResult(
        allowed_actions=allowed,
        blocked_actions=blocked_actions,
        severity=severity,
        reason_codes=deduped,
        diagnostics=diagnostics,
    )

