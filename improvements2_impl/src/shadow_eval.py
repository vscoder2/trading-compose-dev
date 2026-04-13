"""Phase 5 live shadow comparator sidecar (non-trading).

Purpose:
1. Evaluate and record hypothetical alternative actions/targets.
2. Persist comparison artifacts into `shadow_cycles`.
3. Enforce strict no-submit behavior (never routes broker orders).

This module is designed for parallel sidecar analysis and regression
investigation. It intentionally has no dependency on broker clients.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import ActionIntent
from .state_adapter import ControlPlaneStore


def _to_action_dict(intent: ActionIntent) -> dict[str, Any]:
    payload = asdict(intent)
    payload["symbol"] = str(intent.symbol).upper()
    payload["side"] = str(intent.side).lower()
    payload["qty"] = float(intent.qty)
    return payload


def _net_qty_by_symbol(intents: list[ActionIntent]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in intents:
        sym = str(i.symbol).upper()
        side = str(i.side).lower()
        qty = float(i.qty)
        signed = qty if side == "buy" else -qty if side == "sell" else 0.0
        out[sym] = out.get(sym, 0.0) + signed
    return out


def build_shadow_diff(
    *,
    primary_actions: list[ActionIntent],
    shadow_actions: list[ActionIntent],
    primary_target: dict[str, float] | None = None,
    shadow_target: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build deterministic diff payload between primary and shadow paths."""

    p_target = {str(k).upper(): float(v) for k, v in (primary_target or {}).items()}
    s_target = {str(k).upper(): float(v) for k, v in (shadow_target or {}).items()}

    p_net = _net_qty_by_symbol(primary_actions)
    s_net = _net_qty_by_symbol(shadow_actions)
    symbols = sorted(set(p_net) | set(s_net))
    net_delta = {sym: float(s_net.get(sym, 0.0) - p_net.get(sym, 0.0)) for sym in symbols}

    target_syms = sorted(set(p_target) | set(s_target))
    target_delta = {sym: float(s_target.get(sym, 0.0) - p_target.get(sym, 0.0)) for sym in target_syms}

    return {
        "primary_action_count": len(primary_actions),
        "shadow_action_count": len(shadow_actions),
        "action_count_delta": len(shadow_actions) - len(primary_actions),
        "primary_symbols": sorted({str(a.symbol).upper() for a in primary_actions}),
        "shadow_symbols": sorted({str(a.symbol).upper() for a in shadow_actions}),
        "net_qty_delta_by_symbol": net_delta,
        "target_delta_by_symbol": target_delta,
        "submitted_order_count": 0,
        "submission_mode": "shadow_no_submit",
    }


@dataclass(frozen=True)
class ShadowCycleResult:
    shadow_cycle_id: int
    cycle_id: str
    variant_name: str
    submitted_order_count: int
    hypothetical_actions_count: int
    diff: dict[str, Any]


def run_shadow_cycle(
    *,
    store: ControlPlaneStore,
    cycle_id: str,
    variant_name: str,
    shadow_effective_target: dict[str, float],
    shadow_actions: list[ActionIntent],
    primary_actions: list[ActionIntent] | None = None,
    primary_target: dict[str, float] | None = None,
    allow_submit: bool = False,
    ts: str | None = None,
) -> ShadowCycleResult:
    """Persist one shadow comparison cycle and guarantee zero order submission.

    The optional `allow_submit` exists only as an explicit guardrail check.
    Setting it to True raises immediately; this sidecar must never submit.
    """

    if allow_submit:
        raise RuntimeError("shadow comparator is no-submit only; broker submission is forbidden")

    primary = list(primary_actions or [])
    shadow = list(shadow_actions)
    diff = build_shadow_diff(
        primary_actions=primary,
        shadow_actions=shadow,
        primary_target=primary_target,
        shadow_target=shadow_effective_target,
    )

    hypothetical_actions = [_to_action_dict(x) for x in shadow]
    shadow_cycle_id = store.append_shadow_cycle(
        cycle_id=cycle_id,
        variant_name=variant_name,
        effective_target={str(k).upper(): float(v) for k, v in shadow_effective_target.items()},
        hypothetical_actions=hypothetical_actions,
        diff=diff,
        ts=ts,
    )

    return ShadowCycleResult(
        shadow_cycle_id=int(shadow_cycle_id),
        cycle_id=str(cycle_id),
        variant_name=str(variant_name),
        submitted_order_count=0,
        hypothetical_actions_count=len(hypothetical_actions),
        diff=diff,
    )
