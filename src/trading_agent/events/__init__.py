"""Event sourcing for trading system audit trail."""

from trading_agent.events.store import EventStore, EventStoreConfig
from trading_agent.events.models import (
    Event,
    EventType,
    TradeEvent,
    SignalEvent,
    RiskEvent,
    OrderEvent,
    PositionEvent,
    PortfolioEvent,
)
from trading_agent.events.projections import (
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
