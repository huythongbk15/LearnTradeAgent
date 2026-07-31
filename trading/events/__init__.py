"""Event sourcing for trading system audit trail."""

from trading.events.store import EventStore, EventStoreConfig
from trading.events.models import Event, EventType, TradeEvent, SignalEvent, RiskEvent, OrderEvent, PositionEvent, PortfolioEvent
from trading.events.projections import (
    Projection,
    TradeProjection,
    PositionProjection,
    PortfolioProjection,
    RiskProjection,
    OrderProjection,
    SignalProjection,
)

__all__ = [
    "EventStore",
    "EventStoreConfig",
    "Event",
    "EventType",
    "TradeEvent",
    "SignalEvent",
    "RiskEvent",
    "OrderEvent",
    "PositionEvent",
    "PortfolioEvent",
    "Projection",
    "TradeProjection",
    "PositionProjection",
    "PortfolioProjection",
    "RiskProjection",
    "OrderProjection",
    "SignalProjection",
]