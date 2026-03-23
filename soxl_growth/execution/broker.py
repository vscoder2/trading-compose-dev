from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from soxl_growth.logging_setup import get_logger

logger = get_logger(__name__)

@dataclass(frozen=True)
class OrderResult:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float


class AlpacaBroker:
    """Thin wrapper around alpaca-py TradingClient for live/paper modes."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self._client: Any = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from alpaca.trading.client import TradingClient
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "alpaca-py is required for live/paper trading operations."
            ) from exc
        self._client = TradingClient(self.api_key, self.api_secret, paper=self.paper)
        return self._client

    def submit_market_order(self, symbol: str, side: str, qty: float) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")
        client = self._ensure_client()
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        side_key = side.strip().lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        side_enum = OrderSide.BUY if side_key == "buy" else OrderSide.SELL

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            time_in_force=TimeInForce.DAY,
        )
        logger.info("Submitting market order symbol=%s side=%s qty=%.6f", symbol, side, qty)
        order = client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=symbol,
            side=side_key,
            qty=float(qty),
        )

    def submit_notional_market_order(self, symbol: str, side: str, notional: float) -> OrderResult:
        if notional <= 0:
            raise ValueError("notional must be positive")
        client = self._ensure_client()
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        side_key = side.strip().lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        side_enum = OrderSide.BUY if side_key == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=side_enum,
            time_in_force=TimeInForce.DAY,
        )
        logger.info(
            "Submitting notional market order symbol=%s side=%s notional=%.2f",
            symbol,
            side,
            notional,
        )
        order = client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=symbol,
            side=side_key,
            qty=float(getattr(order, "qty", 0.0) or 0.0),
        )

    def submit_bracket_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")
        client = self._ensure_client()
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        side_key = side.strip().lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        side_enum = OrderSide.BUY if side_key == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit_price),
            stop_loss=StopLossRequest(stop_price=stop_loss_price),
        )
        logger.info(
            "Submitting bracket order symbol=%s side=%s qty=%.6f tp=%.4f sl=%.4f",
            symbol,
            side,
            qty,
            take_profit_price,
            stop_loss_price,
        )
        order = client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=symbol,
            side=side_key,
            qty=float(qty),
        )

    def submit_stop_order(self, symbol: str, side: str, qty: float, stop_price: float) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        client = self._ensure_client()
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import StopOrderRequest
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("alpaca-py StopOrderRequest unavailable") from exc

        side_key = side.strip().lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        side_enum = OrderSide.BUY if side_key == "buy" else OrderSide.SELL
        req = StopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            time_in_force=TimeInForce.DAY,
            stop_price=stop_price,
        )
        logger.info(
            "Submitting stop order symbol=%s side=%s qty=%.6f stop=%.4f",
            symbol,
            side,
            qty,
            stop_price,
        )
        order = client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=symbol,
            side=side_key,
            qty=float(qty),
        )

    def submit_trailing_stop_order(self, symbol: str, side: str, qty: float, trail_percent: float) -> OrderResult:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if trail_percent <= 0:
            raise ValueError("trail_percent must be positive")
        client = self._ensure_client()
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import TrailingStopOrderRequest
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("alpaca-py TrailingStopOrderRequest unavailable") from exc

        side_key = side.strip().lower()
        if side_key not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        side_enum = OrderSide.BUY if side_key == "buy" else OrderSide.SELL
        req = TrailingStopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            time_in_force=TimeInForce.DAY,
            trail_percent=trail_percent,
        )
        logger.info(
            "Submitting trailing-stop order symbol=%s side=%s qty=%.6f trail_percent=%.4f",
            symbol,
            side,
            qty,
            trail_percent,
        )
        order = client.submit_order(req)
        return OrderResult(
            order_id=str(order.id),
            client_order_id=str(order.client_order_id),
            symbol=symbol,
            side=side_key,
            qty=float(qty),
        )

    def close_position(self, symbol: str) -> None:
        client = self._ensure_client()
        logger.info("Closing position symbol=%s", symbol)
        client.close_position(symbol)

    def get_order(self, order_id: str) -> dict[str, Any]:
        client = self._ensure_client()
        order = client.get_order_by_id(order_id)
        return {
            "id": str(order.id),
            "symbol": str(getattr(order, "symbol", "")),
            "status": str(getattr(order, "status", "")),
            "qty": float(getattr(order, "qty", 0.0) or 0.0),
            "filled_qty": float(getattr(order, "filled_qty", 0.0) or 0.0),
        }

    def cancel_order(self, order_id: str) -> None:
        client = self._ensure_client()
        logger.warning("Cancelling order id=%s", order_id)
        client.cancel_order_by_id(order_id)

    def replace_order(self, order_id: str, qty: float) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        client = self._ensure_client()
        logger.info("Replacing order id=%s qty=%.6f", order_id, qty)
        try:
            from alpaca.trading.requests import ReplaceOrderRequest
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError("alpaca-py ReplaceOrderRequest unavailable") from exc
        req = ReplaceOrderRequest(qty=qty)
        client.replace_order_by_id(order_id, req)

    def cancel_all_orders(self) -> None:
        client = self._ensure_client()
        logger.warning("Cancelling all open orders")
        client.cancel_orders()

    def list_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        client = self._ensure_client()
        orders = client.get_orders()
        out: list[dict[str, Any]] = []
        wanted_symbol = symbol.upper() if symbol else None
        for o in orders:
            order_symbol = str(getattr(o, "symbol", "")).upper()
            if wanted_symbol and order_symbol != wanted_symbol:
                continue
            status = str(getattr(o, "status", "")).lower()
            if status in {"filled", "canceled", "cancelled", "rejected", "expired"}:
                continue
            out.append(
                {
                    "id": str(getattr(o, "id", "")),
                    "symbol": order_symbol,
                    "side": str(getattr(o, "side", "")),
                    "type": str(getattr(o, "type", "")),
                    "status": status,
                    "qty": float(getattr(o, "qty", 0.0) or 0.0),
                    "filled_qty": float(getattr(o, "filled_qty", 0.0) or 0.0),
                }
            )
        return out

    def cancel_open_orders_for_symbol(self, symbol: str) -> int:
        cancelled = 0
        for row in self.list_open_orders(symbol=symbol):
            oid = str(row.get("id", ""))
            if not oid:
                continue
            self.cancel_order(oid)
            cancelled += 1
        return cancelled

    def get_account(self) -> dict[str, Any]:
        client = self._ensure_client()
        acc = client.get_account()
        return {
            "equity": float(acc.equity),
            "cash": float(acc.cash),
            "buying_power": float(acc.buying_power),
            "multiplier": str(acc.multiplier),
        }

    def list_positions(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        out: list[dict[str, Any]] = []
        for pos in client.get_all_positions():
            qty = float(pos.qty)
            market_value = float(pos.market_value)
            out.append(
                {
                    "symbol": pos.symbol,
                    "qty": qty,
                    "market_value": market_value,
                    "avg_entry_price": float(pos.avg_entry_price),
                    "unrealized_plpc": float(getattr(pos, "unrealized_plpc", 0.0) or 0.0),
                    "unrealized_intraday_plpc": float(getattr(pos, "unrealized_intraday_plpc", 0.0) or 0.0),
                }
            )
        return out

    def get_last_equity(self) -> float:
        return float(self.get_account()["equity"])

    def get_clock(self) -> dict[str, Any]:
        client = self._ensure_client()
        clock = client.get_clock()
        return {
            "timestamp": clock.timestamp,
            "is_open": bool(clock.is_open),
            "next_open": clock.next_open,
            "next_close": clock.next_close,
        }

    def get_calendar(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        client = self._ensure_client()
        rows = client.get_calendar(start=start.date().isoformat(), end=end.date().isoformat())
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "date": str(row.date),
                    "open": str(row.open),
                    "close": str(row.close),
                }
            )
        return out
