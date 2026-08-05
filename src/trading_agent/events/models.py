"""Event model base classes and types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
import re
import uuid

# Matches decimal numbers (including integers, floats, scientific notation)
_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


class EventType(str, Enum):
    """Event type enumeration."""
    # Trade events
    TRADE_EXECUTED = "trade.executed"
    TRADE_FILLED = "trade.filled"
    TRADE_REJECTED = "trade.rejected"
    
    # Signal events
    SIGNAL_GENERATED = "signal.generated"
    SIGNAL_CANCELLED = "signal.cancelled"
    
    # Risk events
    RISK_CHECK_PASSED = "risk.check_passed"
    RISK_CHECK_FAILED = "risk.check_failed"
    RISK_LIMIT_BREACH = "risk.limit_breach"
    
    # Order events
    ORDER_CREATED = "order.created"
    ORDER_SUBMITTED = "order.submitted"
    ORDER_FILLED = "order.filled"
    ORDER_PARTIAL = "order.partial"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_REJECTED = "order.rejected"
    
    # Position events
    POSITION_OPENED = "position.opened"
    POSITION_UPDATED = "position.updated"
    POSITION_CLOSED = "position.closed"
    POSITION_LIQUIDATED = "position.liquidated"
    
    # Portfolio events
    PORTFOLIO_REBALANCED = "portfolio.rebalanced"
    PORTFOLIO_SNAPSHOT = "portfolio.snapshot"
    PORTFOLIO_DRAWDOWN = "portfolio.drawdown"
    
    # System events
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """Base event class."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType = EventType.SYSTEM_ERROR
    timestamp: datetime = field(default_factory=datetime.utcnow)
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        data["timestamp"] = self.timestamp.isoformat()
        # Convert Decimal to string
        return self._serialize_decimals(data)
    
    def _serialize_decimals(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        elif isinstance(obj, dict):
            return {k: self._serialize_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_decimals(v) for v in obj]
        return obj
    
    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        data = data.copy()
        data["event_type"] = EventType(data["event_type"])
        if isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        # Convert string decimals back
        data = cls._deserialize_decimals(data)
        # Dispatch to the concrete event class based on event_type
        target_cls = _EVENT_CLASS_REGISTRY.get(data["event_type"], cls)
        field_names = {f.name for f in fields(target_cls)}
        kwargs = {k: v for k, v in data.items() if k in field_names}
        return target_cls(**kwargs)
    
    @classmethod
    def _deserialize_decimals(cls, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: cls._deserialize_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [cls._deserialize_decimals(v) for v in obj]
        elif isinstance(obj, str):
            # Convert numeric-looking strings back to Decimal
            try:
                if _NUMERIC_RE.match(obj):
                    return Decimal(obj)
            except Exception:
                pass
            return obj
        return obj


# Specific event types
@dataclass
class TradeEvent(Event):
    """Trade execution event."""
    event_type: EventType = EventType.TRADE_EXECUTED
    symbol: str = ""
    side: str = ""  # buy/sell
    size: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    fee_currency: str = ""
    exchange: str = ""
    order_id: str = ""
    strategy_id: str = ""
    is_maker: bool = False


@dataclass
class SignalEvent(Event):
    """Signal generation event."""
    event_type: EventType = EventType.SIGNAL_GENERATED
    symbol: str = ""
    signal_type: str = ""  # buy/sell/hold/close
    strength: float = 0.0
    strategy_id: str = ""
    timeframe: str = ""
    indicators: dict = field(default_factory=dict)
    regime: str = ""


@dataclass
class RiskEvent(Event):
    """Risk check event."""
    event_type: EventType = EventType.RISK_CHECK_PASSED
    check_type: str = ""
    passed: bool = True
    metric: str = ""
    value: Decimal = Decimal(0)
    threshold: Decimal = Decimal(0)
    symbol: str = ""
    strategy_id: str = ""
    details: str = ""


@dataclass
class OrderEvent(Event):
    """Order lifecycle event."""
    event_type: EventType = EventType.ORDER_CREATED
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    size: Decimal = Decimal(0)
    price: Optional[Decimal] = None
    status: str = ""
    filled_size: Decimal = Decimal(0)
    avg_fill_price: Decimal = Decimal(0)
    exchange: str = ""
    strategy_id: str = ""
    error: str = ""


@dataclass
class PositionEvent(Event):
    """Position update event."""
    event_type: EventType = EventType.POSITION_UPDATED
    symbol: str = ""
    size: Decimal = Decimal(0)
    entry_price: Decimal = Decimal(0)
    mark_price: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    leverage: Decimal = Decimal(1)
    strategy_id: str = ""


@dataclass
class PortfolioEvent(Event):
    """Portfolio snapshot event."""
    event_type: EventType = EventType.PORTFOLIO_SNAPSHOT
    portfolio_id: str = ""
    total_value: Decimal = Decimal(0)
    cash: Decimal = Decimal(0)
    positions_value: Decimal = Decimal(0)
    drawdown_pct: Decimal = Decimal(0)
    strategy_weights: dict = field(default_factory=dict)


# Event type -> concrete class dispatch table for from_dict()
_EVENT_CLASS_REGISTRY: dict[EventType, type] = {
    EventType.TRADE_EXECUTED: TradeEvent,
    EventType.TRADE_FILLED: TradeEvent,
    EventType.TRADE_REJECTED: TradeEvent,
    EventType.SIGNAL_GENERATED: SignalEvent,
    EventType.SIGNAL_CANCELLED: SignalEvent,
    EventType.RISK_CHECK_PASSED: RiskEvent,
    EventType.RISK_CHECK_FAILED: RiskEvent,
    EventType.RISK_LIMIT_BREACH: RiskEvent,
    EventType.ORDER_CREATED: OrderEvent,
    EventType.ORDER_SUBMITTED: OrderEvent,
    EventType.ORDER_FILLED: OrderEvent,
    EventType.ORDER_PARTIAL: OrderEvent,
    EventType.ORDER_CANCELLED: OrderEvent,
    EventType.ORDER_REJECTED: OrderEvent,
    EventType.POSITION_OPENED: PositionEvent,
    EventType.POSITION_UPDATED: PositionEvent,
    EventType.POSITION_CLOSED: PositionEvent,
    EventType.POSITION_LIQUIDATED: PositionEvent,
    EventType.PORTFOLIO_REBALANCED: PortfolioEvent,
    EventType.PORTFOLIO_SNAPSHOT: PortfolioEvent,
    EventType.PORTFOLIO_DRAWDOWN: PortfolioEvent,
}


# Projection system
class Projection(ABC):
    """Base class for event projections (read models)."""
    
    @abstractmethod
    async def project(self, event: Event) -> None:
        """Process an event and update projection."""
        pass
    
    @abstractmethod
    async def get_state(self) -> dict:
        """Get current projection state."""
        pass


class TradeProjection(Projection):
    """Projection for trade history and P&L."""
    
    def __init__(self):
        self.trades: list[TradeEvent] = []
        self.pnl_by_symbol: dict[str, Decimal] = {}
        self.pnl_by_strategy: dict[str, Decimal] = {}
        self.total_fees: Decimal = Decimal(0)
    
    async def project(self, event: Event) -> None:
        if isinstance(event, TradeEvent):
            self.trades.append(event)
            
            pnl = (event.price * event.size) if event.side == "sell" else -(event.price * event.size)
            pnl -= event.fee
            
            self.pnl_by_symbol[event.symbol] = self.pnl_by_symbol.get(event.symbol, Decimal(0)) + pnl
            self.pnl_by_strategy[event.strategy_id] = self.pnl_by_strategy.get(event.strategy_id, Decimal(0)) + pnl
            self.total_fees += event.fee
    
    async def get_state(self) -> dict:
        return {
            "total_trades": len(self.trades),
            "total_fees": str(self.total_fees),
            "pnl_by_symbol": {k: str(v) for k, v in self.pnl_by_symbol.items()},
            "pnl_by_strategy": {k: str(v) for k, v in self.pnl_by_strategy.items()},
        }


class PositionProjection(Projection):
    """Projection for current positions."""
    
    def __init__(self):
        self.positions: dict[str, PositionEvent] = {}
    
    async def project(self, event: Event) -> None:
        if isinstance(event, PositionEvent):
            key = f"{event.symbol}:{event.strategy_id}"
            if event.size == 0:
                self.positions.pop(key, None)
            else:
                self.positions[key] = event
    
    async def get_state(self) -> dict:
        return {
            "positions": {
                k: {
                    "symbol": v.symbol,
                    "size": str(v.size),
                    "entry_price": str(v.entry_price),
                    "mark_price": str(v.mark_price),
                    "unrealized_pnl": str(v.unrealized_pnl),
                    "realized_pnl": str(v.realized_pnl),
                    "leverage": str(v.leverage),
                }
                for k, v in self.positions.items()
            }
        }


class PortfolioProjection(Projection):
    """Projection for portfolio state."""
    
    def __init__(self):
        self.snapshots: list[PortfolioEvent] = []
        self.current: Optional[PortfolioEvent] = None
    
    async def project(self, event: Event) -> None:
        if isinstance(event, PortfolioEvent):
            self.snapshots.append(event)
            self.current = event
    
    async def get_state(self) -> dict:
        if not self.current:
            return {}
        return {
            "portfolio_id": self.current.portfolio_id,
            "total_value": str(self.current.total_value),
            "cash": str(self.current.cash),
            "positions_value": str(self.current.positions_value),
            "drawdown_pct": str(self.current.drawdown_pct),
            "strategy_weights": {k: str(v) for k, v in self.current.strategy_weights.items()},
            "snapshots_count": len(self.snapshots),
        }


class RiskProjection(Projection):
    """Projection for risk monitoring."""
    
    def __init__(self):
        self.checks: list[RiskEvent] = []
        self.breaches: list[RiskEvent] = []
        self.current_metrics: dict[str, Decimal] = {}
    
    async def project(self, event: Event) -> None:
        if isinstance(event, RiskEvent):
            self.checks.append(event)
            self.current_metrics[event.metric] = event.value
            if not event.passed or event.event_type == EventType.RISK_LIMIT_BREACH:
                self.breaches.append(event)
    
    async def get_state(self) -> dict:
        return {
            "total_checks": len(self.checks),
            "breaches": len(self.breaches),
            "current_metrics": {k: str(v) for k, v in self.current_metrics.items()},
            "recent_breaches": [
                {
                    "check_type": b.check_type,
                    "metric": b.metric,
                    "value": str(b.value),
                    "threshold": str(b.threshold),
                    "symbol": b.symbol,
                }
                for b in self.breaches[-10:]
            ],
        }