"""Event store implementation with multiple backends."""

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_agent.events.models import (
    Event,
    EventType,
    OrderEvent,
    PortfolioEvent,
    PositionEvent,
    RiskEvent,
    SignalEvent,
    TradeEvent,
)

if TYPE_CHECKING:
    from trading_agent.events.models import Projection


@dataclass
class EventStoreConfig:
    """Configuration for event store."""

    # Redis Streams
    redis_url: str = "redis://localhost:6379"
    redis_stream_prefix: str = "trading:events"
    redis_consumer_group: str = "event-store"

    # NATS JetStream
    nats_servers: str = "nats://localhost:4222"
    nats_stream: str = "TRADING_EVENTS"

    # PostgreSQL (for persistence)
    postgres_dsn: str = ""

    # Local file (for development)
    file_path: str = "./data/events.jsonl"

    # Retention
    retention_days: int = 2555  # 7 years for compliance
    max_stream_length: int = 1000000


class EventStoreBackend(ABC):
    """Abstract event store backend."""

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def append(self, event: Event) -> str:
        """Append event, return event ID."""
        pass

    @abstractmethod
    async def append_batch(self, events: list[Event]) -> list[str]:
        pass

    @abstractmethod
    async def read_stream(
        self, stream_name: str, start: str | int = "0", count: int = 100
    ) -> list[Event]:
        pass

    @abstractmethod
    async def read_all(self, start: str | int = "0", count: int = 100) -> list[Event]:
        pass

    @abstractmethod
    async def subscribe(
        self, stream_name: str, handler, consumer_group: str = ""
    ) -> str:
        pass

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> None:
        pass


class RedisEventStore(EventStoreBackend):
    """Redis Streams event store."""

    def __init__(self, config: EventStoreConfig):
        self.config = config
        self._client = None
        self._subscriptions = {}

    async def connect(self) -> None:
        import redis.asyncio as redis

        self._client = redis.from_url(self.config.redis_url, decode_responses=True)
        await self._client.ping()

    async def disconnect(self) -> None:
        for sub_id in list(self._subscriptions.keys()):
            await self.unsubscribe(sub_id)
        if self._client:
            await self._client.close()

    def _stream_name(self, event_type: EventType | str) -> str:
        if isinstance(event_type, EventType):
            event_type = event_type.value
        return f"{self.config.redis_stream_prefix}:{event_type.replace('.', ':')}"

    async def append(self, event: Event) -> str:
        stream = self._stream_name(event.event_type)
        event_id = await self._client.xadd(
            stream,
            {"data": json.dumps(event.to_dict())},
            maxlen=self.config.max_stream_length,
            approximate=True,
        )
        return event_id

    async def append_batch(self, events: list[Event]) -> list[str]:
        pipe = self._client.pipeline()
        for event in events:
            stream = self._stream_name(event.event_type)
            pipe.xadd(
                stream,
                {"data": json.dumps(event.to_dict())},
                maxlen=self.config.max_stream_length,
                approximate=True,
            )
        results = await pipe.execute()
        return results

    async def read_stream(
        self, stream_name: str, start: str | int = "0", count: int = 100
    ) -> list[Event]:
        stream = self._stream_name(stream_name)
        entries = await self._client.xrange(stream, min=start, max="+", count=count)
        return [Event.from_dict(json.loads(e[1]["data"])) for e in entries]

    async def read_all(self, start: str | int = "0", count: int = 100) -> list[Event]:
        # Read from all streams - simplified
        streams = await self._client.keys(f"{self.config.redis_stream_prefix}:*")
        all_events = []
        for stream in streams:
            entries = await self._client.xrange(stream, min=start, max="+", count=count)
            all_events.extend(
                [Event.from_dict(json.loads(e[1]["data"])) for e in entries]
            )
        all_events.sort(key=lambda e: e.timestamp)
        return all_events[:count]

    async def subscribe(
        self, stream_name: str, handler, consumer_group: str = ""
    ) -> str:
        import asyncio

        group = consumer_group or self.config.redis_consumer_group
        stream = self._stream_name(stream_name)

        # Create consumer group
        try:
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # Group exists

        consumer = f"consumer-{uuid.uuid4().hex[:8]}"
        sub_id = f"{stream}:{group}:{consumer}"

        async def consume():
            while True:
                try:
                    messages = await self._client.xreadgroup(
                        group, consumer, {stream: ">"}, count=10, block=5000
                    )
                    for stream_name, entries in messages:
                        for msg_id, data in entries:
                            event = Event.from_dict(json.loads(data["data"]))
                            await handler(event)
                            await self._client.xack(stream, group, msg_id)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Consumer error: {e}")
                    await asyncio.sleep(1)

        task = asyncio.create_task(consume())
        self._subscriptions[sub_id] = task
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        if subscription_id in self._subscriptions:
            self._subscriptions[subscription_id].cancel()
            try:
                await self._subscriptions[subscription_id]
            except asyncio.CancelledError:
                pass
            del self._subscriptions[subscription_id]


class FileEventStore(EventStoreBackend):
    """File-based event store (for development)."""

    def __init__(self, config: EventStoreConfig):
        self.config = config
        self._file = None
        self._lock = asyncio.Lock()
        self._subscriptions = {}

    async def connect(self) -> None:
        import os

        os.makedirs(os.path.dirname(self.config.file_path) or ".", exist_ok=True)
        self._file = open(self.config.file_path, "a+", buffering=1)
        self._file.seek(0)

    async def disconnect(self) -> None:
        for sub_id in list(self._subscriptions.keys()):
            await self.unsubscribe(sub_id)
        if self._file:
            self._file.close()

    async def append(self, event: Event) -> str:
        async with self._lock:
            self._file.write(json.dumps(event.to_dict()) + "\n")
            self._file.flush()
        return event.event_id

    async def append_batch(self, events: list[Event]) -> list[str]:
        async with self._lock:
            for event in events:
                self._file.write(json.dumps(event.to_dict()) + "\n")
            self._file.flush()
        return [e.event_id for e in events]

    async def read_stream(
        self, stream_name: str, start: str | int = "0", count: int = 100
    ) -> list[Event]:
        events = []
        self._file.seek(0)
        for line in self._file:
            if len(events) >= count:
                break
            event = Event.from_dict(json.loads(line))
            if event.event_type.value.startswith(stream_name.replace(":", ".")):
                events.append(event)
        return events

    async def read_all(self, start: str | int = "0", count: int = 100) -> list[Event]:
        events = []
        self._file.seek(0)
        for line in self._file:
            if len(events) >= count:
                break
            events.append(Event.from_dict(json.loads(line)))
        return events

    async def subscribe(
        self, stream_name: str, handler, consumer_group: str = ""
    ) -> str:
        # Not implemented for file store
        return ""

    async def unsubscribe(self, subscription_id: str) -> None:
        pass


class EventStore:
    """Main event store with multiple backend support."""

    def __init__(self, config: EventStoreConfig | None = None):
        self.config = config or EventStoreConfig()
        self._backend: EventStoreBackend | None = None
        self._projections = {}

    async def connect(self, backend: str = "file") -> None:
        if backend == "redis":
            self._backend = RedisEventStore(self.config)
        elif backend == "file":
            self._backend = FileEventStore(self.config)
        else:
            raise ValueError(f"Unknown backend: {backend}")
        await self._backend.connect()

    async def disconnect(self) -> None:
        if self._backend:
            await self._backend.disconnect()

    async def append(self, event: Event) -> str:
        if not self._backend:
            raise RuntimeError("Not connected")
        event_id = await self._backend.append(event)

        # Update projections
        for proj in self._projections.values():
            await proj.project(event)

        return event_id

    async def append_batch(self, events: list[Event]) -> list[str]:
        if not self._backend:
            raise RuntimeError("Not connected")
        ids = await self._backend.append_batch(events)
        for event in events:
            for proj in self._projections.values():
                await proj.project(event)
        return ids

    def add_projection(self, name: str, projection: "Projection") -> None:
        self._projections[name] = projection

    async def read_stream(
        self, stream_name: str, start: str | int = "0", count: int = 100
    ) -> list[Event]:
        if not self._backend:
            raise RuntimeError("Not connected")
        return await self._backend.read_stream(stream_name, start, count)

    async def read_all(self, start: str | int = "0", count: int = 100) -> list[Event]:
        if not self._backend:
            raise RuntimeError("Not connected")
        return await self._backend.read_all(start, count)

    async def subscribe(
        self, stream_name: str, handler, consumer_group: str = ""
    ) -> str:
        if not self._backend:
            raise RuntimeError("Not connected")
        return await self._backend.subscribe(stream_name, handler, consumer_group)

    async def unsubscribe(self, subscription_id: str) -> None:
        if self._backend:
            await self._backend.unsubscribe(subscription_id)

    # Convenience methods for creating events
    def create_trade_event(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        fee: Decimal,
        fee_currency: str,
        exchange: str,
        order_id: str,
        strategy_id: str,
        is_maker: bool = False,
    ) -> TradeEvent:
        return TradeEvent(
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            exchange=exchange,
            order_id=order_id,
            strategy_id=strategy_id,
            is_maker=is_maker,
        )

    def create_signal_event(
        self,
        symbol: str,
        signal_type: str,
        strength: float,
        strategy_id: str,
        timeframe: str,
        indicators: dict = None,
        regime: str = "",
    ) -> SignalEvent:
        return SignalEvent(
            symbol=symbol,
            signal_type=signal_type,
            strength=strength,
            strategy_id=strategy_id,
            timeframe=timeframe,
            indicators=indicators or {},
            regime=regime,
        )

    def create_risk_event(
        self,
        check_type: str,
        passed: bool,
        metric: str,
        value: Decimal,
        threshold: Decimal,
        symbol: str = "",
        strategy_id: str = "",
        details: str = "",
    ) -> RiskEvent:
        return RiskEvent(
            check_type=check_type,
            passed=passed,
            metric=metric,
            value=value,
            threshold=threshold,
            symbol=symbol,
            strategy_id=strategy_id,
            details=details,
        )

    def create_order_event(
        self,
        order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        size: Decimal,
        price: Decimal | None,
        status: str,
        filled_size: Decimal = Decimal(0),
        avg_fill_price: Decimal = Decimal(0),
        exchange: str = "",
        strategy_id: str = "",
        error: str = "",
    ) -> OrderEvent:
        return OrderEvent(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            status=status,
            filled_size=filled_size,
            avg_fill_price=avg_fill_price,
            exchange=exchange,
            strategy_id=strategy_id,
            error=error,
        )

    def create_position_event(
        self,
        symbol: str,
        size: Decimal,
        entry_price: Decimal,
        mark_price: Decimal,
        unrealized_pnl: Decimal,
        realized_pnl: Decimal,
        leverage: Decimal,
        strategy_id: str,
    ) -> PositionEvent:
        return PositionEvent(
            symbol=symbol,
            size=size,
            entry_price=entry_price,
            mark_price=mark_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            leverage=leverage,
            strategy_id=strategy_id,
        )

    def create_portfolio_event(
        self,
        portfolio_id: str,
        total_value: Decimal,
        cash: Decimal,
        positions_value: Decimal,
        drawdown_pct: Decimal,
        strategy_weights: dict,
    ) -> PortfolioEvent:
        return PortfolioEvent(
            portfolio_id=portfolio_id,
            total_value=total_value,
            cash=cash,
            positions_value=positions_value,
            drawdown_pct=drawdown_pct,
            strategy_weights=strategy_weights,
        )
