"""Reconciliation utilities for broker/local state integrity."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import DriftRecord, OpenOrder


def detect_state_drift(
    *,
    expected_qty: dict[str, float],
    broker_qty: dict[str, float],
    open_orders: list[OpenOrder],
    qty_threshold: float = 1e-6,
) -> list[DriftRecord]:
    """Detect quantity and open-order drift at symbol level.

    A symbol gets a drift record if:
    - absolute quantity drift exceeds threshold OR
    - there are open orders for symbol that are not represented by expectations.
    """

    order_count_by_symbol: dict[str, int] = {}
    for order in open_orders:
        sym = order.symbol.upper()
        order_count_by_symbol[sym] = order_count_by_symbol.get(sym, 0) + 1

    symbols = set(s.upper() for s in expected_qty) | set(s.upper() for s in broker_qty) | set(order_count_by_symbol)
    records: list[DriftRecord] = []
    for sym in sorted(symbols):
        exp = float(expected_qty.get(sym, 0.0))
        bro = float(broker_qty.get(sym, 0.0))
        drift = bro - exp
        unexpected = int(order_count_by_symbol.get(sym, 0))
        if abs(drift) <= qty_threshold and unexpected == 0:
            continue
        severity = "warn"
        if abs(drift) > max(qty_threshold, 0.05):
            severity = "error"
        if unexpected >= 2:
            severity = "error"
        records.append(
            DriftRecord(
                symbol=sym,
                expected_qty=exp,
                broker_qty=bro,
                qty_drift=drift,
                unexpected_open_orders=unexpected,
                severity=severity,
            )
        )
    return records


def build_pending_order_map(open_orders: list[OpenOrder]) -> dict[str, dict[str, Any]]:
    """Build symbol-level pending order state.

    The map is intentionally compact and deterministic for easy supervisor use.
    """

    out: dict[str, dict[str, Any]] = {}
    for order in open_orders:
        sym = order.symbol.upper()
        slot = out.setdefault(
            sym,
            {
                "symbol": sym,
                "pending_buy_qty": 0.0,
                "pending_sell_qty": 0.0,
                "open_order_ids": [],
            },
        )
        side = str(order.side).lower()
        qty = max(0.0, float(order.qty))
        if side == "buy":
            slot["pending_buy_qty"] += qty
        elif side == "sell":
            slot["pending_sell_qty"] += qty
        slot["open_order_ids"].append(str(order.order_id))

    for sym in out:
        out[sym]["open_order_ids"] = sorted(set(out[sym]["open_order_ids"]))
    return out


def drift_records_to_dict(rows: list[DriftRecord]) -> list[dict[str, Any]]:
    """Serialize drift records to plain dictionaries for ledgers/events."""

    return [asdict(r) for r in rows]

