"""Canonical venue adapters — the ONLY implementations BrokerGateway may call.

Every venue implementation must satisfy the CanonicalExecutionAdapter protocol.
No legacy signatures, no dict-based contracts, no positional arguments.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from trading_agent.execution.types import OrderSide
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderType,
    Symbol,
    TimeInForce,
)


@runtime_checkable
class CanonicalExecutionAdapter(Protocol):
    """Canonical protocol for all venue adapters.

    BrokerGateway calls ONLY these methods. No legacy signatures,
    no positional arguments, no dict-based contracts.
    """

    capabilities: dict[str, bool]

    def submit_order(self, request: "BrokerOrderRequest") -> "BrokerSubmitFact": ...
    def request_cancel(self, request: "BrokerCancelRequest") -> "BrokerCancelFact": ...
    def fetch_order(self, order_id: str) -> "BrokerOrderFact": ...
    def fetch_positions(self) -> list["BrokerPositionFact"]: ...
    def fetch_balances(self) -> dict[str, Decimal]: ...
    def close_position(
        self, request: "BrokerClosePositionRequest"
    ) -> "BrokerClosePositionFact": ...


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    """Canonical broker order submission request."""

    intent_id: str
    symbol: Symbol
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "DAY"
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for legacy adapter compatibility."""
        return {
            "id": self.intent_id,
            "symbol": str(self.symbol),
            "side": self.side.value.lower(),
            "qty": float(self.quantity),
            "order_type": self.order_type.value.lower(),
            "price": float(self.price) if self.price is not None else None,
            "stop_price": float(self.stop_price)
            if self.stop_price is not None
            else None,
            "time_in_force": self.time_in_force,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class BrokerSubmitFact:
    """Canonical broker submission result — typed, not boolean."""

    state: "BrokerSubmitState"
    broker_order_id: str | None
    client_order_id: str | None
    venue: str
    broker_status: str
    observed_at: datetime
    error: str | None
    raw_response: dict[str, Any]


class BrokerSubmitState(StrEnum):
    """Broker submission state — explicit, not boolean."""

    ACCEPTED = "ACCEPTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    FAILED_LOCAL = "FAILED_LOCAL"


@dataclass(frozen=True, slots=True)
class BrokerCancelRequest:
    """Canonical broker cancel request."""

    broker_order_id: str
    symbol: Symbol | None = None
    client_order_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerCancelFact:
    """Canonical broker cancel result."""

    state: "CancelState"
    broker_order_id: str
    venue: str
    confirmed_at: datetime
    source: str
    error: str | None
    raw_response: dict[str, Any]


class CancelState(StrEnum):
    REQUEST_ACCEPTED = "REQUEST_ACCEPTED"
    PENDING = "PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class BrokerOrderFact:
    """Canonical broker order state."""

    broker_order_id: str
    client_order_id: str | None
    symbol: Symbol
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    filled_quantity: Decimal
    price: Decimal | None
    stop_price: Decimal | None
    status: str
    venue: str
    created_at: datetime
    updated_at: datetime
    raw_response: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BrokerPositionFact:
    """Canonical broker position fact."""

    symbol: Symbol
    quantity: Decimal
    side: OrderSide
    entry_price: Decimal | None
    current_price: Decimal | None
    unrealized_pnl: Decimal | None
    realized_pnl: Decimal | None
    venue: str


@dataclass(frozen=True, slots=True)
class BrokerClosePositionRequest:
    """Canonical broker close position request."""

    symbol: Symbol
    reason: str = "manual"


@dataclass(frozen=True, slots=True)
class BrokerClosePositionFact:
    """Canonical broker close position result."""

    symbol: Symbol
    closed_quantity: Decimal
    venue: str
    orders_canceled: list[str]
    error: str | None


# ── Adapter implementations ──────────────────────────────────────────────


class PaperExecutionAdapter:
    """Canonical adapter for PaperExchange.

    Wraps the legacy PaperExchange positional/keyword API into the
    canonical BrokerOrderRequest/BrokerSubmitFact contract.
    """

    def __init__(self, paper_exchange: Any) -> None:
        self._exchange = paper_exchange
        self.capabilities = {"close_position_protection": True}

    def submit_order(self, request: BrokerOrderRequest) -> BrokerSubmitFact:
        """Submit order via PaperExchange legacy API."""
        # Convert canonical request to legacy call
        # Note: PaperExchange.place_order() doesn't accept time_in_force
        # Use symbol.pair for PaperExchange which expects "BTC/USDT" format
        symbol_pair = (
            request.symbol.pair
            if hasattr(request.symbol, "pair")
            else str(request.symbol)
        )
        order_result = self._exchange.place_order(
            symbol=symbol_pair,
            side=request.side.value.lower(),
            order_type=request.order_type.value.lower(),
            amount=float(request.quantity),
            price=float(request.price) if request.price is not None else None,
            stop_price=float(request.stop_price)
            if request.stop_price is not None
            else None,
            idempotency_key=request.idempotency_key,
            client_order_id=request.idempotency_key,
        )

        # Convert legacy Order object to canonical fact
        # PaperExchange uses trading_agent.execution.types.Order (amount, filled_amount, cost)
        broker_order_id = order_result.id
        broker_status = order_result.status.value
        state = {
            "open": BrokerSubmitState.OPEN,
            "partial": BrokerSubmitState.PARTIALLY_FILLED,
            "filled": BrokerSubmitState.FILLED,
            "rejected": BrokerSubmitState.REJECTED,
            "cancelled": BrokerSubmitState.REJECTED,
            "expired": BrokerSubmitState.REJECTED,
        }.get(broker_status, BrokerSubmitState.UNKNOWN)
        return BrokerSubmitFact(
            state=state,
            broker_order_id=broker_order_id,
            client_order_id=request.idempotency_key,
            venue="paper",
            broker_status=broker_status,
            observed_at=datetime.now(UTC),
            error=None,  # execution.types.Order doesn't have error attribute
            raw_response={
                "id": order_result.id,
                "status": broker_status,
                "symbol": str(order_result.symbol),
                "side": order_result.side.value,
                "type": order_result.type.value,
                "amount": float(order_result.amount),
                "price": float(order_result.price)
                if order_result.price is not None
                else None,
                "filled_amount": float(order_result.filled_amount),
                "filled_qty": float(order_result.filled_amount),
                "avg_fill_price": float(order_result.avg_fill_price)
                if order_result.avg_fill_price is not None
                else None,
                "cost": float(order_result.cost),
            },
        )

    def request_cancel(self, request: BrokerCancelRequest) -> BrokerCancelFact:
        result = self._exchange.cancel_order(request.broker_order_id)
        return BrokerCancelFact(
            state=CancelState.CANCELED if result else CancelState.UNKNOWN,
            broker_order_id=request.broker_order_id,
            venue="paper",
            confirmed_at=datetime.now(UTC),
            source="BROKER",
            error=None if result else "paper exchange did not confirm cancellation",
            raw_response={"canceled": bool(result)},
        )

    def fetch_order(self, order_id: str) -> BrokerOrderFact:
        order = self._exchange.get_order(order_id)
        if order is None:
            return BrokerOrderFact(
                broker_order_id=order_id,
                client_order_id=None,
                symbol=Symbol("", "", AssetClass.CRYPTO, MarketType.SPOT, ""),
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("0"),
                filled_quantity=Decimal("0"),
                price=None,
                stop_price=None,
                status="unknown",
                venue="paper",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                raw_response={},
            )
        symbol_str = str(order.symbol)
        if "/" in symbol_str:
            base, quote = symbol_str.split("/")
            symbol_obj = Symbol(
                base, quote, AssetClass.CRYPTO, MarketType.SPOT, "paper"
            )
        else:
            symbol_obj = Symbol(
                symbol_str, "USD", AssetClass.STOCK, MarketType.SPOT, "paper"
            )
        return BrokerOrderFact(
            broker_order_id=order.id,
            client_order_id=getattr(order, "client_order_id", None),
            symbol=symbol_obj,
            side=OrderSide.BUY if order.side.value == "buy" else OrderSide.SELL,
            order_type=OrderType.MARKET
            if order.type.value == "market"
            else OrderType.LIMIT,
            quantity=Decimal(str(order.amount)),
            filled_quantity=Decimal(str(order.filled_amount)),
            price=Decimal(str(order.price)) if order.price is not None else None,
            stop_price=None,
            status=order.status.value,
            venue="paper",
            created_at=order.timestamp
            if hasattr(order, "timestamp")
            else datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raw_response={
                "id": order.id,
                "status": order.status.value,
                "symbol": str(order.symbol),
                "side": order.side.value,
                "type": order.type.value,
                "amount": float(order.amount),
                "filled_amount": float(order.filled_amount),
                "price": float(order.price) if order.price is not None else None,
                "cost": float(order.cost),
            },
        )

    def fetch_positions(self) -> list[BrokerPositionFact]:
        positions = self._exchange.get_all_positions()
        return [
            BrokerPositionFact(
                symbol=Symbol(
                    p.symbol.split("/")[0],
                    p.symbol.split("/")[1],
                    AssetClass.CRYPTO if "/" in p.symbol else AssetClass.STOCK,
                    MarketType.SPOT,
                    "paper",
                ),
                quantity=Decimal(str(p.quantity)),
                side=OrderSide.BUY if p.side == OrderSide.BUY else OrderSide.SELL,
                entry_price=Decimal(str(p.entry_price)) if p.entry_price else None,
                current_price=Decimal(str(p.current_price))
                if p.current_price
                else None,
                unrealized_pnl=Decimal(str(p.unrealized_pnl))
                if p.unrealized_pnl
                else None,
                realized_pnl=Decimal(str(p.realized_pnl)) if p.realized_pnl else None,
                venue="paper",
            )
            for p in positions
        ]

    def fetch_balances(self) -> dict[str, Decimal]:
        return {k: Decimal(str(v)) for k, v in self._exchange.get_balances().items()}

    def close_position(
        self, request: "BrokerClosePositionRequest"
    ) -> "BrokerClosePositionFact":
        result = self._exchange.close_position(request.symbol, request.reason)
        return BrokerClosePositionFact(
            symbol=request.symbol,
            closed_quantity=Decimal("0"),  # PaperExchange returns dict
            venue="paper",
            orders_canceled=[],
            error=result.get("error"),
        )


class LiveBrokerExecutionAdapter:
    """Canonical adapter for LiveBroker.

    Wraps LiveBroker's Order-based API into canonical contract.
    """

    def __init__(self, live_broker: Any) -> None:
        self._broker = live_broker
        self.capabilities = {"close_position_protection": False}

    def submit_order(self, request: BrokerOrderRequest) -> BrokerSubmitFact:
        order = Order(
            id=request.intent_id,
            symbol=request.symbol,
            side=request.side,
            type=request.order_type,
            size=request.quantity,
            price=request.price,
            stop_price=request.stop_price,
            time_in_force=TimeInForce(request.time_in_force.lower()),
            client_order_id=request.idempotency_key,
        )
        result = self._broker.place_order(order)
        broker_order_id = str(result.get("id") or "") or None
        broker_status = str(result.get("status") or "unknown").lower()
        error = str(result.get("error") or "") or None
        state_map = {
            "accepted": BrokerSubmitState.ACCEPTED,
            "submitted": BrokerSubmitState.ACCEPTED,
            "new": BrokerSubmitState.ACCEPTED,
            "open": BrokerSubmitState.OPEN,
            "partial": BrokerSubmitState.PARTIALLY_FILLED,
            "partially_filled": BrokerSubmitState.PARTIALLY_FILLED,
            "filled": BrokerSubmitState.FILLED,
            "rejected": BrokerSubmitState.REJECTED,
            "cancelled": BrokerSubmitState.REJECTED,
            "canceled": BrokerSubmitState.REJECTED,
            "expired": BrokerSubmitState.REJECTED,
        }
        state = state_map.get(broker_status, BrokerSubmitState.UNKNOWN)
        if error and state not in {
            BrokerSubmitState.PARTIALLY_FILLED,
            BrokerSubmitState.FILLED,
        }:
            state = (
                BrokerSubmitState.REJECTED
                if broker_order_id
                else BrokerSubmitState.FAILED_LOCAL
            )
        elif (
            state
            in {
                BrokerSubmitState.ACCEPTED,
                BrokerSubmitState.OPEN,
                BrokerSubmitState.PARTIALLY_FILLED,
                BrokerSubmitState.FILLED,
            }
            and not broker_order_id
        ):
            state = BrokerSubmitState.UNKNOWN
        return BrokerSubmitFact(
            state=state,
            broker_order_id=broker_order_id,
            client_order_id=request.idempotency_key,
            venue=str(getattr(self._broker, "broker", "live")),
            broker_status=broker_status,
            observed_at=datetime.now(UTC),
            error=error,
            raw_response=result,
        )

    def request_cancel(self, request: BrokerCancelRequest) -> BrokerCancelFact:
        venue = str(getattr(self._broker, "broker", "live"))
        if venue in {"ccxt", "binance", "bybit", "okx"} and request.symbol is None:
            raise ValueError(f"{venue} cancellation requires an explicit symbol")
        canceled = self._broker.cancel_order(request.broker_order_id, request.symbol)
        return BrokerCancelFact(
            state=CancelState.CANCELED if canceled else CancelState.UNKNOWN,
            broker_order_id=request.broker_order_id,
            venue=venue,
            confirmed_at=datetime.now(UTC),
            source="BROKER",
            error=None if canceled else "broker did not confirm cancellation",
            raw_response={"canceled": bool(canceled)},
        )

    def fetch_order(self, order_id: str) -> BrokerOrderFact:
        get_order = getattr(self._broker, "get_order", None)
        if not callable(get_order):
            raise NotImplementedError(
                "LiveBroker does not expose a symbol-safe order lookup by broker ID"
            )
        result = get_order(order_id)
        return BrokerOrderFact(
            broker_order_id=order_id,
            client_order_id=None,
            symbol=Symbol("", "", AssetClass.CRYPTO, MarketType.SPOT, ""),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
            filled_quantity=Decimal("0"),
            price=None,
            stop_price=None,
            status=result.get("status", "unknown"),
            venue="live",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raw_response=result,
        )

    def fetch_positions(self) -> list[BrokerPositionFact]:
        positions = self._broker.get_positions()
        venue = str(getattr(self._broker, "broker", "live"))
        facts: list[BrokerPositionFact] = []
        for position in positions:
            symbol_text = str(position["symbol"])
            if "/" in symbol_text:
                base, quote = symbol_text.split("/", maxsplit=1)
                asset_class = AssetClass.CRYPTO
            else:
                base, quote = symbol_text, "USD"
                asset_class = AssetClass.STOCK
            entry_price = position.get("entry_price") or position.get("avg_entry_price")
            facts.append(
                BrokerPositionFact(
                    symbol=Symbol(
                        base,
                        quote,
                        asset_class,
                        MarketType.SPOT,
                        venue,
                    ),
                    quantity=Decimal(str(position["qty"])),
                    side=OrderSide.BUY
                    if position["side"] == "long"
                    else OrderSide.SELL,
                    entry_price=Decimal(str(entry_price)) if entry_price else None,
                    current_price=Decimal(str(position["current_price"]))
                    if position.get("current_price")
                    else None,
                    unrealized_pnl=Decimal(str(position["unrealized_pnl"]))
                    if position.get("unrealized_pnl")
                    else None,
                    realized_pnl=Decimal(str(position["realized_pnl"]))
                    if position.get("realized_pnl")
                    else None,
                    venue=venue,
                )
            )
        return facts

    def fetch_balances(self) -> dict[str, Decimal]:
        account = self._broker.get_account()
        currency = str(account.get("currency") or "USD")
        return {currency: Decimal(str(account.get("cash") or 0))}

    def close_position(
        self, request: "BrokerClosePositionRequest"
    ) -> "BrokerClosePositionFact":
        raise NotImplementedError(
            "LiveBroker positions must be closed through an authorized reduce order"
        )


class AlpacaExecutionAdapter:
    """Canonical adapter for AlpacaAdapter (async).

    Wraps async AlpacaAdapter into synchronous canonical contract
    using run_coroutine_threadsafe on a dedicated event loop.
    """

    def __init__(self, alpaca_adapter: Any) -> None:
        self._adapter = alpaca_adapter
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._order_symbols: dict[str, Symbol] = {}
        self.capabilities = {"close_position_protection": False}
        self._start_loop()

    def _start_loop(self) -> None:
        import asyncio
        import threading

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro: Any) -> Any:
        import asyncio

        if self._loop is None:
            raise RuntimeError("Event loop not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def submit_order(self, request: BrokerOrderRequest) -> BrokerSubmitFact:
        order_request = Order(
            id=request.intent_id,
            symbol=request.symbol,
            side=request.side,
            type=request.order_type,
            size=request.quantity,
            price=request.price,
            stop_price=request.stop_price,
            time_in_force=TimeInForce(request.time_in_force.lower()),
            client_order_id=request.idempotency_key,
        )
        order = self._run(self._adapter.create_order(order_request))
        broker_order_id = str(getattr(order, "id", "") or "") or None
        status_value = getattr(order, "status", "unknown")
        broker_status = str(getattr(status_value, "value", status_value)).lower()
        error = str(getattr(order, "error", "") or "") or None
        state_map = {
            "open": BrokerSubmitState.OPEN,
            "partial": BrokerSubmitState.PARTIALLY_FILLED,
            "filled": BrokerSubmitState.FILLED,
            "rejected": BrokerSubmitState.REJECTED,
            "cancelled": BrokerSubmitState.REJECTED,
            "expired": BrokerSubmitState.REJECTED,
        }
        state = state_map.get(broker_status, BrokerSubmitState.UNKNOWN)
        if broker_order_id and state == BrokerSubmitState.UNKNOWN and not error:
            state = BrokerSubmitState.ACCEPTED
        if broker_order_id:
            self._order_symbols[broker_order_id] = request.symbol
        raw_response = {
            "id": broker_order_id,
            "client_order_id": getattr(order, "client_order_id", None),
            "status": broker_status,
            "filled_qty": float(getattr(order, "filled_size", 0) or 0),
            "avg_fill_price": float(getattr(order, "avg_fill_price", 0) or 0),
            "error": error,
        }
        return BrokerSubmitFact(
            state=state,
            broker_order_id=broker_order_id,
            client_order_id=request.idempotency_key,
            venue="alpaca",
            broker_status=broker_status,
            observed_at=datetime.now(UTC),
            error=error,
            raw_response=raw_response,
        )

    def request_cancel(self, request: BrokerCancelRequest) -> BrokerCancelFact:
        symbol = request.symbol or self._order_symbols.get(request.broker_order_id)
        if symbol is None:
            raise ValueError("Alpaca cancellation requires an explicit symbol")
        canceled = bool(
            self._run(self._adapter.cancel_order(request.broker_order_id, symbol))
        )
        return BrokerCancelFact(
            state=CancelState.CANCELED if canceled else CancelState.UNKNOWN,
            broker_order_id=request.broker_order_id,
            venue="alpaca",
            confirmed_at=datetime.now(UTC),
            source="BROKER",
            error=None if canceled else "broker did not confirm cancellation",
            raw_response={"canceled": canceled},
        )

    def fetch_order(self, order_id: str) -> BrokerOrderFact:
        symbol = self._order_symbols.get(order_id)
        if symbol is None:
            raise ValueError("Alpaca order lookup requires a known order symbol")
        order = self._run(self._adapter.fetch_order(order_id, symbol))
        return BrokerOrderFact(
            broker_order_id=order_id,
            client_order_id=None,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
            filled_quantity=Decimal("0"),
            price=None,
            stop_price=None,
            status=str(getattr(order, "status", "unknown")),
            venue="alpaca",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raw_response={"order": str(order)},
        )

    def fetch_positions(self) -> list[BrokerPositionFact]:
        positions = self._run(self._adapter.fetch_positions())
        return [
            BrokerPositionFact(
                symbol=p.symbol,
                quantity=Decimal(str(p.size)),
                side=OrderSide.BUY if float(p.size) >= 0 else OrderSide.SELL,
                entry_price=Decimal(str(p.entry_price)) if p.entry_price else None,
                current_price=Decimal(str(p.mark_price)) if p.mark_price else None,
                unrealized_pnl=Decimal(str(p.unrealized_pnl))
                if p.unrealized_pnl
                else None,
                realized_pnl=Decimal(str(p.realized_pnl)) if p.realized_pnl else None,
                venue="alpaca",
            )
            for p in positions
        ]

    def fetch_balances(self) -> dict[str, Decimal]:
        account = self._adapter.get_account_info()
        return {"USD": Decimal(str(account.get("cash") or 0))}

    def close_position(
        self, request: "BrokerClosePositionRequest"
    ) -> "BrokerClosePositionFact":
        raise NotImplementedError(
            "Alpaca positions must be closed through an authorized reduce order"
        )

    def close(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)


class BinanceExecutionAdapter:
    """Canonical adapter for BinanceAdapter (async).

    Similar to AlpacaExecutionAdapter but for Binance venue.
    """

    def __init__(self, binance_adapter: Any) -> None:
        self._adapter = binance_adapter
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.capabilities = {"close_position_protection": True}
        self._start_loop()

    def _start_loop(self) -> None:
        import asyncio
        import threading

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro: Any) -> Any:
        import asyncio

        if self._loop is None:
            raise RuntimeError("Event loop not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def submit_order(self, request: BrokerOrderRequest) -> BrokerSubmitFact:
        # Binance uses ccxt-style create_order
        result = self._run(
            self._adapter.create_order(
                symbol=str(request.symbol),
                side=request.side.value.lower(),
                qty=float(request.quantity),
                order_type=request.order_type.value.lower(),
                limit_price=float(request.price) if request.price is not None else None,
            )
        )
        return BrokerSubmitFact(
            state=BrokerSubmitState.ACCEPTED,
            broker_order_id=result.get("id") or result.get("order_id"),
            client_order_id=request.idempotency_key,
            venue="binance",
            broker_status=result.get("status", "open"),
            observed_at=datetime.now(UTC),
            error=None,
            raw_response=result,
        )

    def request_cancel(self, request: BrokerCancelRequest) -> BrokerCancelFact:
        result = self._run(self._adapter.cancel_order(request.broker_order_id))
        return BrokerCancelFact(
            state=CancelState.REQUEST_ACCEPTED,
            broker_order_id=request.broker_order_id,
            venue="binance",
            confirmed_at=datetime.now(UTC),
            source="BROKER",
            error=None,
            raw_response=result,
        )

    def fetch_order(self, order_id: str) -> BrokerOrderFact:
        result = self._run(self._adapter.fetch_order(order_id))
        return BrokerOrderFact(
            broker_order_id=order_id,
            client_order_id=None,
            symbol=Symbol("", "", AssetClass.CRYPTO, MarketType.SPOT, "binance"),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
            filled_quantity=Decimal("0"),
            price=None,
            stop_price=None,
            status=result.get("status", "unknown"),
            venue="binance",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raw_response=result,
        )

    def fetch_positions(self) -> list[BrokerPositionFact]:
        positions = self._run(self._adapter.fetch_positions())
        return [
            BrokerPositionFact(
                symbol=Symbol(
                    p["symbol"].split("/")[0],
                    p["symbol"].split("/")[1],
                    AssetClass.CRYPTO,
                    MarketType.SPOT,
                    "binance",
                ),
                quantity=Decimal(str(p["qty"])),
                side=OrderSide.BUY if p["side"] == "long" else OrderSide.SELL,
                entry_price=Decimal(str(p["entry_price"]))
                if p.get("entry_price")
                else None,
                current_price=Decimal(str(p["current_price"]))
                if p.get("current_price")
                else None,
                unrealized_pnl=Decimal(str(p["unrealized_pnl"]))
                if p.get("unrealized_pnl")
                else None,
                realized_pnl=Decimal(str(p["realized_pnl"]))
                if p.get("realized_pnl")
                else None,
                venue="binance",
            )
            for p in positions
        ]

    def fetch_balances(self) -> dict[str, Decimal]:
        balances = self._run(self._adapter.fetch_balances())
        return {k: Decimal(str(v)) for k, v in balances.items()}

    def close_position(
        self, request: "BrokerClosePositionRequest"
    ) -> "BrokerClosePositionFact":
        result = self._run(self._adapter.close_position(request.symbol))
        return BrokerClosePositionFact(
            symbol=request.symbol,
            closed_quantity=Decimal("0"),
            venue="binance",
            orders_canceled=[],
            error=result.get("error"),
        )

    def close(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
