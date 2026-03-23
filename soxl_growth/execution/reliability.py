from __future__ import annotations

from dataclasses import dataclass
import time

from soxl_growth.execution.orders import OrderIntent
from soxl_growth.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExecutionRetryConfig:
    max_retries: int = 2
    poll_seconds: float = 2.0
    stale_seconds: float = 20.0


@dataclass(frozen=True)
class ExecutionOutcome:
    success: bool
    order_id: str
    final_status: str
    filled_qty: float
    attempts: int


def execute_with_retries(
    *,
    submit_fn,
    intent: OrderIntent,
    retry_cfg: ExecutionRetryConfig,
    get_order_fn,
    cancel_order_fn,
    replace_order_fn,
) -> ExecutionOutcome:
    """Submit and supervise one order through fill/stale/retry lifecycle.

    Behavior:
    - Submit once.
    - Poll order status.
    - If stale in NEW/ACCEPTED/PARTIALLY_FILLED state, replace remaining qty.
    - If retries exhausted, cancel and return failure outcome.
    """
    result = submit_fn(intent)
    attempts = 0

    while True:
        order_id = result.order_id
        start = time.monotonic()

        while True:
            order = get_order_fn(order_id)
            status = str(order.get("status", "")).lower()
            filled_qty = float(order.get("filled_qty", 0.0) or 0.0)
            qty = float(order.get("qty", 0.0) or 0.0)

            if status in {"filled"}:
                return ExecutionOutcome(
                    success=True,
                    order_id=order_id,
                    final_status=status,
                    filled_qty=filled_qty,
                    attempts=attempts,
                )

            if status in {"canceled", "expired", "rejected", "done_for_day"}:
                return ExecutionOutcome(
                    success=False,
                    order_id=order_id,
                    final_status=status,
                    filled_qty=filled_qty,
                    attempts=attempts,
                )

            elapsed = time.monotonic() - start
            if elapsed >= retry_cfg.stale_seconds:
                remaining = max(qty - filled_qty, 0.0)
                if attempts >= retry_cfg.max_retries or remaining <= 0:
                    logger.warning("Order stale and retries exhausted order_id=%s status=%s", order_id, status)
                    cancel_order_fn(order_id)
                    return ExecutionOutcome(
                        success=False,
                        order_id=order_id,
                        final_status="stale_canceled",
                        filled_qty=filled_qty,
                        attempts=attempts,
                    )

                attempts += 1
                logger.warning(
                    "Order stale; replacing remaining qty order_id=%s status=%s remaining=%.6f attempt=%d",
                    order_id,
                    status,
                    remaining,
                    attempts,
                )
                try:
                    replace_order_fn(order_id, remaining)
                    # Continue polling the same order id after replace.
                    start = time.monotonic()
                    continue
                except Exception:
                    logger.exception("Replace failed; falling back to cancel+resubmit order_id=%s", order_id)
                    cancel_order_fn(order_id)
                    # resubmit only remaining quantity
                    replacement_intent = OrderIntent(
                        symbol=intent.symbol,
                        side=intent.side,
                        qty=remaining,
                        target_weight=intent.target_weight,
                    )
                    result = submit_fn(replacement_intent)
                    break

            time.sleep(retry_cfg.poll_seconds)
