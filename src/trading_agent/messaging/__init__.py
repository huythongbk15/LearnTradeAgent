"""Trading messaging layer - NATS and Redis Streams support."""

from trading_agent.messaging.base import (
    Message,
    MessageHandler,
    MessageBus,
    MessagePriority,
)

# Bus implementations depend on optional third-party packages (nats-py, redis).
# They are imported lazily inside connect(), so these imports are safe, but
# we keep the module importable even if a transport dependency is missing.
try:
    from trading_agent.messaging.redis_streams import RedisStreamsBus
except ImportError:  # pragma: no cover - depends on environment
    RedisStreamsBus = None  # type: ignore

try:
    from trading_agent.messaging.nats_bus import NATSBus
except ImportError:  # pragma: no cover - depends on environment
    NATSBus = None  # type: ignore

__all__ = [
    "Message",
    "MessageHandler",
    "MessageBus",
    "MessagePriority",
    "RedisStreamsBus",
    "NATSBus",
]
