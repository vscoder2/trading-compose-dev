"""Protective-action priority ladder and net action resolution.

This module implements:
- deterministic class precedence
- single net action per symbol
- block reasons for discarded lower-priority actions
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import ActionIntent

# Lower number => higher priority.
PRIORITY_RANK: dict[str, int] = {
    "hard_brake_exit": 1,
    "session_breaker_exit": 2,
    "profit_lock_exit": 3,
    "rebalance_reduction": 4,
    "rebalance_add": 5,
    "maintenance": 6,
}


def _rank(intent: ActionIntent) -> int:
    """Return deterministic rank for an intent.

    Unknown classes are intentionally lowest priority to avoid accidental
    promotion of unclassified actions.
    """

    return PRIORITY_RANK.get(intent.priority_class, 10_000)


def _normalize_side(side: str) -> str:
    value = str(side or "").strip().lower()
    if value in {"buy", "sell"}:
        return value
    return "unknown"


def resolve_symbol_actions(intents: list[ActionIntent]) -> tuple[list[ActionIntent], list[dict[str, Any]]]:
    """Resolve intents to at most one action per symbol.

    Resolution rules:
    1. Highest-priority class wins.
    2. If tied in class, larger absolute qty wins.
    3. If tied again, lexicographically smaller source wins (deterministic).
    4. Remaining intents are blocked with explicit reason.
    """

    grouped: dict[str, list[ActionIntent]] = {}
    for intent in intents:
        sym = intent.symbol.upper()
        grouped.setdefault(sym, []).append(intent)

    kept: list[ActionIntent] = []
    blocked: list[dict[str, Any]] = []
    for symbol, bucket in grouped.items():
        # Stable deterministic sort ensures reproducible output.
        ordered = sorted(
            bucket,
            key=lambda x: (_rank(x), -abs(float(x.qty)), str(x.source)),
        )
        winner = ordered[0]
        kept.append(winner)
        for loser in ordered[1:]:
            blocked.append(
                {
                    "symbol": symbol,
                    "reason": "lower_priority_or_duplicate_for_symbol",
                    "winner_priority": winner.priority_class,
                    "loser_priority": loser.priority_class,
                    "winner_side": _normalize_side(winner.side),
                    "loser_side": _normalize_side(loser.side),
                    "winner": asdict(winner),
                    "blocked": asdict(loser),
                }
            )
    return kept, blocked

