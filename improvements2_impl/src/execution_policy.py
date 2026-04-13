"""Phase 4 execution-integrity policy helpers.

Focus:
1. Resolve intent conflicts into at most one net action per symbol.
2. Reconcile with pending open-order state to avoid contradictory submits.
3. Emit explicit block reasons for auditability.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .action_policy import resolve_symbol_actions
from .models import ActionIntent, OpenOrder
from .reconcile import build_pending_order_map


PROTECTIVE_CLASSES = {
    "hard_brake_exit",
    "session_breaker_exit",
    "profit_lock_exit",
    "rebalance_reduction",
}


def _norm_side(side: str) -> str:
    value = str(side or "").strip().lower()
    if value in {"buy", "sell"}:
        return value
    return "unknown"


def resolve_order_conflicts(
    intents: list[ActionIntent],
    open_orders: list[OpenOrder],
) -> tuple[list[ActionIntent], list[dict[str, Any]], dict[str, Any]]:
    """Resolve intent conflicts with pending-order awareness.

    Pipeline:
    1. Use priority ladder to keep one intent per symbol.
    2. Apply pending-order conflict filters.
    3. Allow protective exits to override pending buys.
    """

    ladder_kept, ladder_blocked = resolve_symbol_actions(intents)
    pending_map = build_pending_order_map(open_orders)

    kept: list[ActionIntent] = []
    blocked: list[dict[str, Any]] = list(ladder_blocked)

    for intent in ladder_kept:
        sym = intent.symbol.upper()
        side = _norm_side(intent.side)
        pending = pending_map.get(sym, {})
        pending_buy = float(pending.get("pending_buy_qty", 0.0))
        pending_sell = float(pending.get("pending_sell_qty", 0.0))

        # If both sides are pending already, freeze symbol to avoid churn/race.
        if pending_buy > 0 and pending_sell > 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "pending_both_sides_freeze_symbol",
                    "pending_buy_qty": pending_buy,
                    "pending_sell_qty": pending_sell,
                    "blocked": asdict(intent),
                }
            )
            continue

        # Opening buys should not race against pending exits.
        if side == "buy" and pending_sell > 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "pending_sell_blocks_buy",
                    "pending_sell_qty": pending_sell,
                    "blocked": asdict(intent),
                }
            )
            continue

        # Duplicate same-side pending should be blocked as duplicate submit.
        if side == "buy" and pending_buy > 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "pending_buy_blocks_duplicate_buy",
                    "pending_buy_qty": pending_buy,
                    "blocked": asdict(intent),
                }
            )
            continue
        if side == "sell" and pending_sell > 0:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "pending_sell_blocks_duplicate_sell",
                    "pending_sell_qty": pending_sell,
                    "blocked": asdict(intent),
                }
            )
            continue

        # Protective exits may still run even if a pending buy exists.
        if side == "sell" and pending_buy > 0:
            if intent.priority_class in PROTECTIVE_CLASSES:
                kept.append(intent)
                continue
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "pending_buy_blocks_non_protective_sell",
                    "pending_buy_qty": pending_buy,
                    "blocked": asdict(intent),
                }
            )
            continue

        kept.append(intent)

    # Final guard: enforce one action per symbol deterministically.
    final_kept: list[ActionIntent] = []
    seen: set[str] = set()
    for intent in kept:
        sym = intent.symbol.upper()
        if sym in seen:
            blocked.append(
                {
                    "symbol": sym,
                    "reason": "post_filter_duplicate_symbol_guard",
                    "blocked": asdict(intent),
                }
            )
            continue
        seen.add(sym)
        final_kept.append(intent)

    diagnostics = {
        "pending_map": pending_map,
        "resolved_symbols": sorted(seen),
        "blocked_count": len(blocked),
    }
    return final_kept, blocked, diagnostics


def estimate_turnover_notional(
    intents: list[ActionIntent],
    price_by_symbol: dict[str, float],
) -> dict[str, float]:
    """Compute buy/sell turnover notionals for EOD monitoring."""

    buy_notional = 0.0
    sell_notional = 0.0
    trade_count = 0
    for intent in intents:
        sym = intent.symbol.upper()
        px = float(price_by_symbol.get(sym, 0.0))
        if px <= 0:
            continue
        notional = abs(float(intent.qty)) * px
        side = _norm_side(intent.side)
        if side == "buy":
            buy_notional += notional
            trade_count += 1
        elif side == "sell":
            sell_notional += notional
            trade_count += 1
    total = buy_notional + sell_notional
    return {
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "total_notional": total,
        "trade_count": float(trade_count),
    }
