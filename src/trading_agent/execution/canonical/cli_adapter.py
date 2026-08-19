"""Canonical CLI adapter bridge for exchange adapters.

This adapter wraps an exchange adapter (e.g. ``AlpacaAdapter``) and exposes
the dict-based interface expected by ``BrokerGateway``, translating between
canonical payloads and the adapter's ``Order``-based interface.

It is the synchronous bridge in the canonical pipeline:
    BrokerGateway → CliBrokerAdapter → ExchangeAdapter (async) → broker API
"""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from typing import Any

from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
)


class _SyncAsyncBridge:
    """Run an async adapter's coroutines synchronously via a background loop."""

    def __init__(self, async_adapter) -> None:
        self._adapter = async_adapter
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def create_order(self, order: Order) -> dict[str, Any]:
        result = self._run(self._adapter.create_order(order))
        if hasattr(result, "id"):
            return {
                "id": result.id,
                "client_order_id": getattr(result, "client_order_id", None),
                "status": getattr(result, "status", "filled"),
            }
        return result if isinstance(result, dict) else {"status": "unknown"}

    def cancel_order(self, order_id: str, symbol: Symbol) -> dict[str, Any]:
        result = self._run(self._adapter.cancel_order(order_id, symbol))
        return result if isinstance(result, dict) else {"success": bool(result)}

    def fetch_order(self, order_id: str, symbol: Symbol) -> dict[str, Any]:
        result = self._run(self._adapter.fetch_order(order_id, symbol))
        if hasattr(result, "id"):
            return {
                "id": result.id,
                "status": getattr(result, "status", "unknown"),
                "filled_qty": getattr(result, "filled_qty", 0.0),
                "avg_fill_price": getattr(result, "avg_fill_price", None),
            }
        return result if isinstance(result, dict) else {}

    def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        result = self._run(self._adapter.fetch_positions(symbol))
        out = []
        for p in result:
            out.append(
                {
                    "symbol": p.symbol.pair
                    if hasattr(p.symbol, "pair")
                    else str(p.symbol),
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.entry_price),
                    "current_price": float(p.mark_price),
                    "market_value": float(p.notional),
                    "unrealized_pl": float(p.unrealized_pl),
                    "side": "long" if p.is_long else "short",
                }
            )
        return out

    def fetch_balances(self) -> dict[str, Any]:
        result = self._run(self._adapter.fetch_balance())
        return result if isinstance(result, dict) else {}

    def fetch_ticker(self, symbol: Symbol) -> dict[str, Any]:
        result = self._run(self._adapter.fetch_ticker(symbol))
        return {
            "last": getattr(result, "last", None) or getattr(result, "price", None),
            "bid": getattr(result, "bid", None),
            "ask": getattr(result, "ask", None),
        }

    def close_all_positions(self) -> dict[str, Any]:
        result = self._run(self._adapter.close_all_positions())
        return result if isinstance(result, dict) else {"closed": len(result)}


class CliBrokerAdapter:
    """Dict-based adapter bridge for ExchangeAdapter → BrokerGateway.

    Translates canonical dict payloads into the adapter's ``Order``-based
    interface and back.  This is the ONLY place in the CLI execution path
    that touches the exchange adapter directly.
    """

    def __init__(self, async_adapter) -> None:
        self._bridge = _SyncAsyncBridge(async_adapter)

    # ── ExchangeAdapter protocol ────────────────────────────────────────

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = payload["symbol"]
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
            id="",
            symbol=symbol,
            side=side,
            type=order_type,
            size=Decimal(str(payload["qty"])),
            price=price,
            stop_price=stop_price,
            client_order_id=payload.get("idempotency_key"),
        )
        return self._bridge.create_order(order)

    def cancel_order(self, order_id: str, symbol: Any = None) -> dict[str, Any]:
        if symbol is None:
            symbol = Symbol("BTC", "USD", AssetClass.CRYPTO, MarketType.SPOT, "binance")
        return self._bridge.cancel_order(order_id, symbol)

    def fetch_order(self, order_id: str, symbol: Any = None) -> dict[str, Any]:
        if symbol is None:
            symbol = Symbol("BTC", "USD", AssetClass.CRYPTO, MarketType.SPOT, "binance")
        return self._bridge.fetch_order(order_id, symbol)

    def fetch_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self._bridge.fetch_positions(symbol)

    def fetch_balances(self) -> dict[str, Any]:
        return self._bridge.fetch_balances()

    def fetch_ticker(self, symbol: Any) -> dict[str, Any]:
        if isinstance(symbol, str):
            base, _, quote = symbol.partition("/")
            symbol = Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance")
        return self._bridge.fetch_ticker(symbol)

    def close_all_positions(self) -> dict[str, Any]:
        return self._bridge.close_all_positions()


__all__ = ["CliBrokerAdapter"]
