"""Canonical CLI adapter bridge for LiveBroker instances.

This adapter wraps a ``LiveBroker`` facade and exposes the dict-based interface
expected by ``BrokerGateway``, translating between canonical payloads and the
LiveBroker's ``Order``-based interface.
"""

from __future__ import annotations

from decimal import Decimal

from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
)


class CliBrokerAdapter:
    """Dict-based adapter bridge for LiveBroker → BrokerGateway."""

    def __init__(self, live_broker):
        self._broker = live_broker

    def place_order(self, payload):
        symbol = payload["symbol"]
        # Convert string symbol to Symbol object for LiveBroker
        if isinstance(symbol, str):
            # Parse "BTC/USD" format and create Symbol with default Alpaca settings
            parts = symbol.split("/")
            if len(parts) == 2:
                base, quote = parts
                symbol = Symbol(
                    base=base,
                    quote=quote,
                    asset_class=AssetClass.STOCK,
                    market_type=MarketType.SPOT,
                    exchange="alpaca",
                )
            else:
                raise ValueError(f"Invalid symbol format: {symbol}")

        if not isinstance(symbol, Symbol):
            raise TypeError(f"symbol must be Symbol, got {type(symbol)}")

        side = (
            OrderSide.BUY
            if payload["side"].strip().lower() == "buy"
            else OrderSide.SELL
        )
        order_type_str = payload.get("order_type", "market").strip().lower()
        order_type = OrderType.MARKET
        if order_type_str == "limit":
            order_type = OrderType.LIMIT
        elif order_type_str == "stop":
            order_type = OrderType.STOP
        elif order_type_str == "stop_limit":
            order_type = OrderType.STOP_LIMIT

        price = (
            Decimal(str(payload["price"])) if payload.get("price") is not None else None
        )
        stop_price = (
            Decimal(str(payload["stop_price"]))
            if payload.get("stop_price") is not None
            else None
        )

        order = Order(
            id=str(payload.get("id", "")),
            symbol=symbol,
            side=side,
            type=order_type,
            size=Decimal(str(payload["qty"])),
            price=price,
            stop_price=stop_price,
            client_order_id=payload.get("idempotency_key"),
        )
        result = self._broker.place_order(order)
        if isinstance(result, dict):
            return result
        return {
            "id": getattr(result, "id", None),
            "status": getattr(result, "status", "unknown"),
        }
