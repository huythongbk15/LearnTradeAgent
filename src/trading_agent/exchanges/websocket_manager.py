"""
Unified WebSocket Manager — real-time market data for multiple exchanges.

Provides:
- Pluggable stream providers per exchange (Binance, CCXT Pro, mock, ...)
- Unified subscribe API: ticker / orderbook / trades
- Handler dispatch with subscription IDs
- Auto-reconnect with exponential backoff
- Heartbeat watchdog (stale-connection detection)
- Per-provider message throttling

The manager itself is transport-agnostic: any provider implementing
``StreamProvider`` can be registered. Concrete providers lazily import
their WebSocket client library so this module stays importable everywhere.

Run a quick demo (mock provider):
    python -m trading.exchanges.websocket_manager
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable, Optional

from trading_agent.execution.data_trust import DiffStreamState, SequenceGapError
from trading_agent.exchanges.models import Symbol

logger = logging.getLogger(__name__)


class WSChannel(str, Enum):
    """Supported real-time data channels."""
    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    TRADES = "trades"


class WSMessageType(str, Enum):
    """Message envelope types."""
    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    TRADES = "trades"
    STATUS = "status"
    ERROR = "error"


@dataclass(slots=True)
class WSMessage:
    """Normalized message delivered to handlers."""
    exchange: str
    channel: WSChannel
    symbol: Symbol
    data: dict
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "channel": self.channel.value,
            "symbol": self.symbol.pair,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


Handler = Callable[[WSMessage], Awaitable[None] | None]


class StreamProvider(ABC):
    """A single exchange's real-time stream transport."""

    def __init__(self, exchange: str):
        self.exchange = exchange
        self._manager: Optional["WebSocketManager"] = None
        self._subscriptions: set[tuple[str, WSChannel]] = set()
        self._connected = False
        self._last_message_at = 0.0
        self._message_count = 0
        self._reconnects = 0

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    async def connect(self) -> None:
        """Open the underlying connection(s)."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the underlying connection(s)."""

    # --- subscriptions ---------------------------------------------------
    async def subscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        """Subscribe to a symbol/channel pair on the exchange."""
        self._subscriptions.add((symbol.pair, channel))
        await self._on_subscribe(symbol, channel)

    async def unsubscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        """Unsubscribe from a symbol/channel pair."""
        self._subscriptions.discard((symbol.pair, channel))
        await self._on_unsubscribe(symbol, channel)

    @abstractmethod
    async def _on_subscribe(self, symbol: Symbol, channel: WSChannel) -> None: ...

    @abstractmethod
    async def _on_unsubscribe(self, symbol: Symbol, channel: WSChannel) -> None: ...

    # --- message pump ----------------------------------------------------
    async def _deliver(self, message: WSMessage) -> None:
        """Hand off a message to the manager for dispatch."""
        if self._manager:
            self._last_message_at = time.monotonic()
            self._message_count += 1
            await self._manager._dispatch(message)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_message_at(self) -> float:
        return self._last_message_at

    @property
    def message_count(self) -> int:
        return self._message_count

    @property
    def reconnect_count(self) -> int:
        return self._reconnects

    @property
    def active_subscriptions(self) -> list[tuple[str, WSChannel]]:
        return sorted(self._subscriptions)


class WebSocketManager:
    """Unified multi-exchange real-time data manager."""

    def __init__(
        self,
        heartbeat_timeout: float = 30.0,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        reconnect_factor: float = 2.0,
        min_message_interval: float = 0.0,
    ):
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.reconnect_factor = reconnect_factor
        self.min_message_interval = min_message_interval  # seconds between messages per provider

        self._providers: dict[str, StreamProvider] = {}
        self._handlers: dict[str, list[tuple[Symbol, WSChannel, Handler]]] = {}
        self._subscription_id = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_sent: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # --- provider management ---------------------------------------------
    def register_provider(self, provider: StreamProvider) -> None:
        """Register a stream provider for an exchange."""
        provider._manager = self
        self._providers[provider.exchange] = provider
        logger.info(f"Registered stream provider for {provider.exchange}")

    def get_provider(self, exchange: str) -> Optional[StreamProvider]:
        return self._providers.get(exchange)

    @property
    def exchanges(self) -> list[str]:
        return sorted(self._providers.keys())

    # --- subscriptions ---------------------------------------------------
    async def subscribe(
        self,
        symbol: Symbol,
        channel: WSChannel,
        handler: Handler,
        exchange: Optional[str] = None,
    ) -> str:
        """Subscribe a handler to (symbol, channel).

        If ``exchange`` is None, the first registered provider is used.
        Returns a subscription id usable with ``unsubscribe``.
        """
        exchange = exchange or (next(iter(self._providers)) if self._providers else None)
        if exchange is None:
            raise ValueError("No providers registered")
        provider = self._providers.get(exchange)
        if provider is None:
            raise ValueError(f"No provider for exchange {exchange}")

        async with self._lock:
            self._subscription_id += 1
            sub_id = f"{exchange}:{self._subscription_id}"
            self._handlers.setdefault(sub_id, []).append((symbol, channel, handler))

        # De-duplicate underlying transport subscription
        if (symbol.pair, channel) not in provider._subscriptions:
            await provider.subscribe(symbol, channel)

        logger.debug(f"Subscribed {sub_id} -> {symbol.pair}/{channel.value} on {exchange}")
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription by id."""
        async with self._lock:
            entries = self._handlers.pop(sub_id, [])
        for symbol, channel, _ in entries:
            provider = self._providers.get(sub_id.split(":")[0])
            if provider:
                # Only unsubscribe from transport if no other handlers remain
                remaining = any(
                    (s, c) == (symbol, channel)
                    for subs in self._handlers.values()
                    for s, c, _ in subs
                )
                if not remaining:
                    await provider.unsubscribe(symbol, channel)

    # --- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        """Start all providers and the watchdog."""
        if self._running:
            return
        self._running = True
        for provider in self._providers.values():
            try:
                await provider.connect()
                provider._connected = True
            except Exception as e:
                logger.error(f"Provider {provider.exchange} initial connect failed: {e}")
                asyncio.create_task(self._reconnect_loop(provider))
        self._tasks.append(asyncio.create_task(self._watchdog_loop()))
        logger.info(f"WebSocket manager started with {len(self._providers)} providers")

    async def stop(self) -> None:
        """Stop all providers and background tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        for provider in self._providers.values():
            try:
                await provider.disconnect()
                provider._connected = False
            except Exception as e:
                logger.error(f"Provider {provider.exchange} disconnect error: {e}")
        self._tasks.clear()
        logger.info("WebSocket manager stopped")

    # --- dispatch --------------------------------------------------------
    async def _dispatch(self, message: WSMessage) -> None:
        """Route a message to matching handlers with throttle."""
        # Throttle per provider
        if self.min_message_interval > 0:
            now = time.monotonic()
            last = self._last_sent.get(message.exchange, 0.0)
            if now - last < self.min_message_interval:
                return
            self._last_sent[message.exchange] = now

        for sub_id, entries in list(self._handlers.items()):
            if not sub_id.startswith(message.exchange + ":"):
                continue
            for symbol, channel, handler in entries:
                if symbol.pair == message.symbol.pair and channel == message.channel:
                    try:
                        result = handler(message)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Handler error for {sub_id}: {e}")

    # --- reconnect & watchdog -------------------------------------------
    async def _reconnect_loop(self, provider: StreamProvider) -> None:
        delay = self.reconnect_initial_delay
        while self._running:
            await asyncio.sleep(delay)
            if not self._running:
                break
            try:
                await provider.connect()
                provider._connected = True
                provider._reconnects += 1
                logger.info(f"Reconnected provider {provider.exchange}")
                # Re-establish transport subscriptions
                for pair, channel in list(provider._subscriptions):
                    await provider._on_subscribe(self._symbol_for(pair, provider.exchange), channel)
                delay = self.reconnect_initial_delay
            except Exception as e:
                logger.warning(f"Reconnect failed for {provider.exchange}: {e}")
                delay = min(delay * self.reconnect_factor, self.reconnect_max_delay)

    async def _watchdog_loop(self) -> None:
        """Detect stale providers and force a reconnect."""
        while self._running:
            await asyncio.sleep(max(5.0, self.heartbeat_timeout / 3))
            if not self._running:
                break
            now = time.monotonic()
            for provider in self._providers.values():
                if not provider.is_connected:
                    continue
                if provider._subscriptions and now - provider.last_message_at > self.heartbeat_timeout:
                    logger.warning(
                        f"Provider {provider.exchange} stale "
                        f"({now - provider.last_message_at:.0f}s no messages) — reconnecting"
                    )
                    provider._connected = False
                    asyncio.create_task(self._reconnect_loop(provider))

    @staticmethod
    def _symbol_for(pair: str, exchange: str) -> Symbol:
        base, _, quote = pair.partition("/")
        from trading_agent.exchanges.models import crypto_symbol
        return crypto_symbol(base, quote, exchange=exchange)

    # --- status ----------------------------------------------------------
    def get_status(self) -> dict:
        return {
            "providers": {
                name: {
                    "connected": p.is_connected,
                    "messages": p.message_count,
                    "reconnects": p.reconnect_count,
                    "subscriptions": p.active_subscriptions,
                }
                for name, p in self._providers.items()
            },
            "handlers": len(self._handlers),
            "running": self._running,
        }


# ---------------------------------------------------------------------------
# Mock provider (also used as a demo / for tests)
# ---------------------------------------------------------------------------

class MockStreamProvider(StreamProvider):
    """In-memory provider that can push synthetic messages."""

    def __init__(self, exchange: str = "mock"):
        super().__init__(exchange)
        self._queue: asyncio.Queue[WSMessage] = asyncio.Queue()
        self._pump_task: Optional[asyncio.Task] = None
        self.fail_on_connect = False

    async def connect(self) -> None:
        if self.fail_on_connect:
            raise ConnectionError("simulated connect failure")
        self._connected = True
        self._pump_task = asyncio.create_task(self._pump())

    async def disconnect(self) -> None:
        self._connected = False
        if self._pump_task:
            self._pump_task.cancel()
            self._pump_task = None

    async def _on_subscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        pass

    async def _on_unsubscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        pass

    async def push(self, message: WSMessage) -> None:
        """Push a synthetic message (simulates exchange data)."""
        await self._queue.put(message)

    async def _pump(self) -> None:
        while True:
            msg = await self._queue.get()
            await self._deliver(msg)


def create_mock_manager() -> WebSocketManager:
    """Convenience factory: manager with a mock provider."""
    manager = WebSocketManager()
    manager.register_provider(MockStreamProvider("mock"))
    return manager


def apply_order_book_delta(
    levels: dict[float, float],
    delta: list[list],
) -> None:
    """Apply a Binance depth delta to a price→size map.

    Zero/negative size removes the level (Binance convention); otherwise the
    level is overwritten. Keeps the map sorted on read, not on write, so
    bursts of 100ms diffs stay cheap.
    """
    for raw in delta:
        try:
            price = float(raw[0])
            size = float(raw[1])
        except (TypeError, ValueError, IndexError):
            raise SequenceGapError(f"malformed depth delta level: {raw!r}") from None
        if price <= 0 or not math.isfinite(price) or not math.isfinite(size):
            raise SequenceGapError(f"invalid depth delta level: {raw!r}")
        if size <= 0:
            levels.pop(price, None)
        else:
            levels[price] = size


class BinanceDepthProvider(StreamProvider):
    """Binance Spot order-book provider — snapshot + diff stream.

    Implements the official local order-book management protocol
    (``@depth@100ms`` diffs + REST snapshot on a gap):
      https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams

    Fails closed: any gap, stale or out-of-order update forces a REST
    snapshot resync. While a book is unsynced the provider publishes a
    STATUS(``orderbook_resync``) message instead of an ORDERBOOK message,
    so consumers can never act on a partially-rebuilt book.

    The protocol logic lives in :class:`DiffStreamState` (network-free);
    this class only owns the WebSocket transport and the local book maps.
    """

    DEPTH_STREAM_TEMPLATE = "{pair_lower}@depth@100ms"
    DEFAULT_WS_URL = "wss://stream.binance.com:9443/ws"
    DEFAULT_REST_DEPTH_URL = "https://api.binance.com/api/v3/depth"

    def __init__(
        self,
        exchange: str = "binance",
        *,
        ws_url: str = DEFAULT_WS_URL,
        rest_depth_url: str = DEFAULT_REST_DEPTH_URL,
        snapshot_limit: int = 100,
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__(exchange)
        self.ws_url = ws_url
        self.rest_depth_url = rest_depth_url
        self.snapshot_limit = int(snapshot_limit)
        self.timeout_s = timeout_s
        self._books: dict[str, DiffStreamState] = {}
        self._local: dict[str, dict[str, dict[float, float]]] = {}
        self._ws: Optional[object] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._next_id = 1

    # --- lifecycle -------------------------------------------------------
    async def connect(self) -> None:
        import websockets  # lazy: transport lib only needed for a real stream

        self._ws = await websockets.connect(self.ws_url, max_size=2**23)
        self._connected = True
        self._pump_task = asyncio.create_task(self._pump())

    async def disconnect(self) -> None:
        self._connected = False
        if self._pump_task:
            self._pump_task.cancel()
            self._pump_task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception as exc:  # pragma: no cover — transport teardown
                logger.warning(f"{self.exchange} ws close error: {exc}")

    # --- subscriptions ---------------------------------------------------
    def _stream_name(self, symbol: Symbol) -> str:
        return self.DEPTH_STREAM_TEMPLATE.format(pair_lower=symbol.pair.lower().replace("/", ""))

    async def _on_subscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        if channel is not WSChannel.ORDERBOOK:
            return
        if symbol.pair not in self._books:
            self._books[symbol.pair] = DiffStreamState(symbol.pair)
            self._local[symbol.pair] = {"bids": {}, "asks": {}}
        if self._ws is not None:
            await self._ws.send(json.dumps({
                "method": "SUBSCRIBE",
                "params": [self._stream_name(symbol)],
                "id": self._next_id,
            }))
            self._next_id += 1

    async def _on_unsubscribe(self, symbol: Symbol, channel: WSChannel) -> None:
        if channel is not WSChannel.ORDERBOOK or self._ws is None:
            return
        await self._ws.send(json.dumps({
            "method": "UNSUBSCRIBE",
            "params": [self._stream_name(symbol)],
            "id": self._next_id,
        }))
        self._next_id += 1

    # --- message pump ----------------------------------------------------
    async def _pump(self) -> None:
        while True:
            try:
                raw = await self._ws.recv()
            except Exception as exc:
                logger.warning(f"{self.exchange} depth stream closed: {exc}")
                break
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(f"{self.exchange} dropped non-JSON payload")
                continue
            await self._handle_payload(payload)

    async def _handle_payload(self, payload: dict) -> None:
        event = payload.get("e")
        symbol_pair = payload.get("s")
        symbol = None
        for pair in self._books:
            if pair.replace("/", "").lower() == (symbol_pair or "").lower():
                symbol = self._symbol_for(pair, self.exchange)
                break
        if event != "depthUpdate" or symbol is None:
            return
        state = self._books[symbol.pair]
        status = state.apply_diff(
            first_update_id=int(payload["U"]),
            final_update_id=int(payload["u"]),
            previous_update_id=int(payload.get("pu")),
            bids=[],
            asks=[],
        )
        book = self._local[symbol.pair]
        if status == "gap":
            # Fail closed: drop the local book and rebuild from REST.
            await self._resync(symbol, state, book)
            return
        if status == "stale" or status == "ready_first":
            # ready_first: diff is buffered and book is in sync — apply it.
            pass
        if state.last_u is None:
            return
        try:
            apply_order_book_delta(book["bids"], payload.get("b", []))
            apply_order_book_delta(book["asks"], payload.get("a", []))
        except SequenceGapError:
            await self._resync(symbol, state, book)
            return
        await self._deliver(WSMessage(
            exchange=self.exchange,
            channel=WSChannel.ORDERBOOK,
            symbol=symbol,
            data={
                "bids": self._top(book["bids"]),
                "asks": self._top(book["asks"]),
                "sequence": state.last_u,
                "first_update_id": payload.get("U"),
                "final_update_id": payload.get("u"),
                "previous_update_id": payload.get("pu"),
            },
        ))

    async def _resync(
        self,
        symbol: Symbol,
        state: DiffStreamState,
        book: dict[str, dict[float, float]],
    ) -> None:
        """Fetch a REST snapshot and seed the local book (fail-closed)."""
        state.needs_resync = True
        book["bids"].clear()
        book["asks"].clear()
        try:
            snapshot = await self._fetch_snapshot(symbol)
        except Exception as exc:
            logger.error(f"{self.exchange} depth resync failed for {symbol.pair}: {exc}")
            await self._deliver(WSMessage(
                exchange=self.exchange,
                channel=WSChannel.STATUS,
                symbol=symbol,
                data={"event": "orderbook_resync_failed", "error": str(exc)[:300]},
            ))
            return
        state.initialize(int(snapshot["lastUpdateId"]))
        for price, size in snapshot.get("bids", []):
            book["bids"][float(price)] = float(size)
        for price, size in snapshot.get("asks", []):
            book["asks"][float(price)] = float(size)
        # A diff that arrived while resyncing will be handled on the next
        # pump because apply_diff sees needs_resync=True; the first valid
        # straddling diff re-opens the stream (last_u is set) — exactly the
        # Binance protocol.
        await self._deliver(WSMessage(
            exchange=self.exchange,
            channel=WSChannel.STATUS,
            symbol=symbol,
            data={"event": "orderbook_resynced", "sequence": state.last_update_id},
        ))

    async def _fetch_snapshot(self, symbol: Symbol) -> dict:
        import urllib.request

        url = (
            f"{self.rest_depth_url}?symbol="
            f"{symbol.pair.replace('/', '')}&limit={self.snapshot_limit}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "trading-agent/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _top(levels: dict[float, float], n: int = 20) -> list[list[float]]:
        return [[price, size] for price, size in sorted(levels.items())[:n]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from trading_agent.exchanges.models import crypto_symbol

    async def demo():
        manager = create_mock_manager()
        provider = manager.get_provider("mock")
        assert isinstance(provider, MockStreamProvider)

        btc = crypto_symbol("BTC", "USDT")

        async def on_ticker(msg: WSMessage):
            print(f"[ticker] {msg.symbol.pair}: last={msg.data.get('last')}")

        sub_id = await manager.subscribe(btc, WSChannel.TICKER, on_ticker)
        await manager.start()

        await provider.push(WSMessage(
            exchange="mock", channel=WSChannel.TICKER, symbol=btc,
            data={"last": "65000.0"},
        ))
        await asyncio.sleep(0.2)
        print("status:", manager.get_status())
        await manager.unsubscribe(sub_id)
        await manager.stop()

    asyncio.run(demo())
