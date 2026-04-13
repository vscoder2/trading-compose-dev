"""Core data models for the Phase 1 control kernel.

The goal is explicit typing and deterministic behavior. Models are plain
dataclasses to keep dependencies minimal and to simplify test coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ActionIntent:
    """Candidate action emitted by strategy/risk/protective layers.

    `priority_class` is interpreted by `action_policy` and must be one of:
    - hard_brake_exit
    - session_breaker_exit
    - profit_lock_exit
    - rebalance_reduction
    - rebalance_add
    - maintenance
    """

    symbol: str
    side: str
    qty: float
    priority_class: str
    source: str
    reason_code: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionState:
    """Position snapshot used by reconciliation and dry-run checks."""

    symbol: str
    qty: float


@dataclass(frozen=True)
class OpenOrder:
    """Open broker order snapshot used by pending-order reconciliation."""

    order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    created_ts: datetime | None = None


@dataclass(frozen=True)
class LockState:
    """Durable control lock object (additive schema concept)."""

    lock_type: str
    scope: str
    subject: str | None
    active: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftRecord:
    """Symbol-level drift finding comparing broker vs expected state."""

    symbol: str
    expected_qty: float
    broker_qty: float
    qty_drift: float
    unexpected_open_orders: int
    severity: str


@dataclass
class DecisionContext:
    """Aggregated context passed into the supervisory kernel."""

    cycle_id: str
    intents: list[ActionIntent]
    positions: dict[str, float]
    open_orders: list[OpenOrder]
    locks: list[LockState]
    buying_power: float
    market_open: bool
    data_fresh: bool


@dataclass
class DecisionResult:
    """Supervisor output contract.

    - `allowed_actions`: actions approved for submission
    - `blocked_actions`: actions rejected with reasons
    - `severity`: overall cycle severity
    - `reason_codes`: cycle-level reasons for auditability
    """

    allowed_actions: list[ActionIntent]
    blocked_actions: list[dict[str, Any]]
    severity: str
    reason_codes: list[str]
    diagnostics: dict[str, Any] = field(default_factory=dict)

