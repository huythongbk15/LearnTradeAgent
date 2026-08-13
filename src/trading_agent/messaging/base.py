"""Base messaging abstractions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from enum import Enum
import uuid


class MessagePriority(Enum):
    """Message priority levels."""

    LOW = 0
    NORMAL = 50
    HIGH = 100
    CRITICAL = 200


@dataclass
class Message:
    """Universal message envelope."""

    topic: str
    payload: dict[str, Any]
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    priority: MessagePriority = MessagePriority.NORMAL
    headers: dict[str, str] = field(default_factory=dict)
    correlation_id: str | None = None
    reply_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "topic": self.topic,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "priority": self.priority.value,
            "headers": self.headers,
            "correlation_id": self.correlation_id,
            "reply_to": self.reply_to,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            topic=data["topic"],
            payload=data["payload"],
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.utcnow(),
            priority=MessagePriority(
                data.get("priority", MessagePriority.NORMAL.value)
            ),
            headers=data.get("headers", {}),
            correlation_id=data.get("correlation_id"),
            reply_to=data.get("reply_to"),
        )


MessageHandler = Callable[[Message], None]


class MessageBus(ABC):
    """Abstract message bus interface."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the message broker."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the message broker."""
        pass

    @abstractmethod
    async def publish(self, topic: str, payload: dict[str, Any], **kwargs) -> None:
        """Publish a message to a topic."""
        pass

    @abstractmethod
    async def subscribe(self, topic: str, handler: MessageHandler, **kwargs) -> str:
        """Subscribe to a topic with a handler. Returns subscription ID."""
        pass

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe by subscription ID."""
        pass

    @abstractmethod
    async def request(
        self, topic: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> Message | None:
        """Request-response pattern."""
        pass
