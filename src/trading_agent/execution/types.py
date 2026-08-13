"""
Data types for execution layer: Order, Trade, Position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
import hashlib
import time


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT = "take_profit"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


def generate_idempotency_key(
    symbol: str,
    side: OrderSide | str,
    order_type: OrderType | str,
    amount: float,
    price: float | None = None,
    nonce: str | None = None,
) -> str:
    """
    Generate a deterministic idempotency key for order deduplication.

    Key format: hash(symbol|side|type|amount|price|timestamp_minute|nonce)
    This ensures the same order submitted twice within the same minute
    gets the same key (if nonce is not provided).

    For true idempotency, caller should provide a unique nonce per order attempt.
    """
    side_str = side.value if isinstance(side, OrderSide) else str(side)
    type_str = (
        order_type.value if isinstance(order_type, OrderType) else str(order_type)
    )
    price_str = f"{price:.8f}" if price is not None else "market"
    ts_minute = str(int(time.time() // 60))  # Minute-level timestamp
    nonce_str = nonce or ""

    data = f"{symbol}|{side_str}|{type_str}|{amount:.8f}|{price_str}|{ts_minute}|{nonce_str}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]


@dataclass
class Order:
    """A single order placed on an exchange."""

    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float  # base currency (e.g. BTC in BTC/USDT)
    price: float | None = None  # None for MARKET orders
    stop_price: float | None = None  # for stop-loss orders
    status: OrderStatus = OrderStatus.PENDING
    filled_amount: float = 0.0
    avg_fill_price: float | None = None
    cost: float = 0.0  # total cost (quote currency)
    fee: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)
    # Idempotency key for duplicate detection
    idempotency_key: str | None = None
    # Client order ID (for exchange correlation)
    client_order_id: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
        )

    @property
    def is_closed(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def remaining_amount(self) -> float:
        return max(0.0, self.amount - self.filled_amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "type": self.type.value,
            "amount": self.amount,
            "price": self.price,
            "stop_price": self.stop_price,
            "status": self.status.value,
            "filled_amount": self.filled_amount,
            "avg_fill_price": self.avg_fill_price,
            "cost": self.cost,
            "fee": self.fee,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "idempotency_key": self.idempotency_key,
            "client_order_id": self.client_order_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Order:
        return cls(
            id=d["id"],
            symbol=d["symbol"],
            side=OrderSide(d["side"]),
            type=OrderType(d["type"]),
            amount=d["amount"],
            price=d.get("price"),
            stop_price=d.get("stop_price"),
            status=OrderStatus(d.get("status", "pending")),
            filled_amount=d.get("filled_amount", 0.0),
            avg_fill_price=d.get("avg_fill_price"),
            cost=d.get("cost", 0.0),
            fee=d.get("fee", 0.0),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
            idempotency_key=d.get("idempotency_key"),
            client_order_id=d.get("client_order_id"),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Trade:
    """A completed trade (open + close pair)."""

    id: str
    symbol: str
    side: OrderSide  # initial side (entry)
    entry_price: float
    exit_price: float | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    quantity: float = 0.0
    pnl: float = 0.0  # in quote currency
    pnl_pct: float = 0.0  # percentage return
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    reason: str | None = None  # "manual", "stop_loss", "take_profit", "signal"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None

    @property
    def bars_held(self) -> int:
        if self.entry_time and self.exit_time:
            return int((self.exit_time - self.entry_time).total_seconds() / 3600)
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "entry_fee": self.entry_fee,
            "exit_fee": self.exit_fee,
            "entry_order_id": self.entry_order_id,
            "exit_order_id": self.exit_order_id,
            "reason": self.reason,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Trade:
        return cls(
            id=d["id"],
            symbol=d["symbol"],
            side=OrderSide(d["side"]),
            entry_price=d["entry_price"],
            exit_price=d.get("exit_price"),
            quantity=d["quantity"],
            pnl=d.get("pnl", 0.0),
            pnl_pct=d.get("pnl_pct", 0.0),
            entry_fee=d.get("entry_fee", 0.0),
            exit_fee=d.get("exit_fee", 0.0),
            reason=d.get("reason"),
            entry_time=datetime.fromisoformat(d["entry_time"])
            if d.get("entry_time")
            else None,
            exit_time=datetime.fromisoformat(d["exit_time"])
            if d.get("exit_time")
            else None,
            entry_order_id=d.get("entry_order_id"),
            exit_order_id=d.get("exit_order_id"),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Position:
    """An open position."""

    symbol: str
    side: OrderSide  # long only for now
    quantity: float  # base currency
    entry_price: float  # average entry
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stop_loss: float | None = None
    take_profit: float | None = None
    trailing_stop_pct: float | None = (
        None  # ratchet SL as price moves in our favour (None = off)
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.quantity > 0

    @property
    def market_value(self) -> float:
        """Current market value in quote currency."""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """Total cost basis in quote currency."""
        return self.quantity * self.entry_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "realized_pnl": self.realized_pnl,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trailing_stop_pct": self.trailing_stop_pct,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Position:
        return cls(
            symbol=d["symbol"],
            side=OrderSide(d["side"]),
            quantity=d["quantity"],
            entry_price=d["entry_price"],
            current_price=d.get("current_price", 0.0),
            unrealized_pnl=d.get("unrealized_pnl", 0.0),
            unrealized_pnl_pct=d.get("unrealized_pnl_pct", 0.0),
            realized_pnl=d.get("realized_pnl", 0.0),
            opened_at=datetime.fromisoformat(d["opened_at"])
            if d.get("opened_at")
            else datetime.now(UTC),
            updated_at=datetime.fromisoformat(d["updated_at"])
            if d.get("updated_at")
            else datetime.now(UTC),
            stop_loss=d.get("stop_loss"),
            take_profit=d.get("take_profit"),
            trailing_stop_pct=d.get("trailing_stop_pct"),
            metadata=d.get("metadata", {}),
        )
