"""Redis Streams message bus implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from trading_agent.messaging.base import Message, MessageBus, MessageHandler, MessagePriority

logger = logging.getLogger(__name__)

# redis is an optional dependency — defer import so `trading.messaging`
# stays importable even when redis is not installed.
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    redis = None  # type: ignore
    REDIS_AVAILABLE = False


class RedisStreamsBus(MessageBus):
    """Redis Streams based message bus.
    
    Features:
    - Consumer groups for horizontal scaling
    - Message acknowledgment
    - Stream trimming for memory management
    - Priority via stream sharding
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        consumer_group: str = "trading-agent",
        consumer_name: str | None = None,
        max_stream_length: int = 10000,
        auto_create_streams: bool = True,
    ):
        self.url = url
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"consumer-{id(self)}"
        self.max_stream_length = max_stream_length
        self.auto_create_streams = auto_create_streams

        self._client: redis.Redis | None = None
        self._subscriptions: dict[str, asyncio.Task] = {}
        self._handlers: dict[str, MessageHandler] = {}
        self._running = False

    async def connect(self) -> None:
        """Connect to Redis."""
        if not REDIS_AVAILABLE:
            raise RuntimeError(
                "redis is not installed. Install it with: pip install redis"
            )
        self._client = redis.from_url(self.url, decode_responses=True)
        await self._client.ping()
        self._running = True
        logger.info(f"Connected to Redis Streams at {self.url}")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        self._running = False
        
        # Cancel all subscription tasks
        for task in self._subscriptions.values():
            task.cancel()
        
        # Wait for tasks to complete
        if self._subscriptions:
            await asyncio.gather(*self._subscriptions.values(), return_exceptions=True)
        
        if self._client:
            await self._client.close()
        logger.info("Disconnected from Redis Streams")

    async def _ensure_consumer_group(self, stream: str) -> None:
        """Ensure consumer group exists for stream."""
        try:
            await self._client.xgroup_create(stream, self.consumer_group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def publish(
        self, 
        topic: str, 
        payload: dict[str, Any], 
        priority: MessagePriority = MessagePriority.NORMAL,
        **kwargs
    ) -> None:
        """Publish message to Redis stream."""
        if not self._client:
            raise RuntimeError("Not connected")

        message = Message(
            topic=topic,
            payload=payload,
            priority=priority,
            **kwargs
        )

        # Use priority-based stream naming for priority queuing
        stream_name = f"trading:{topic}"
        if priority != MessagePriority.NORMAL:
            stream_name = f"trading:{priority.name.lower()}:{topic}"

        # Ensure stream and consumer group exist
        if self.auto_create_streams:
            await self._ensure_consumer_group(stream_name)

        # Add to stream
        await self._client.xadd(
            stream_name,
            {"data": json.dumps(message.to_dict())},
            maxlen=self.max_stream_length,
            approximate=True
        )

        logger.debug(f"Published to {stream_name}: {message.message_id}")

    async def subscribe(
        self, 
        topic: str, 
        handler: MessageHandler, 
        priority: MessagePriority = MessagePriority.NORMAL,
        **kwargs
    ) -> str:
        """Subscribe to a topic with a handler."""
        if not self._client:
            raise RuntimeError("Not connected")

        stream_name = f"trading:{topic}"
        if priority != MessagePriority.NORMAL:
            stream_name = f"trading:{priority.name.lower()}:{topic}"

        subscription_id = f"{topic}:{priority.name}:{id(handler)}"
        self._handlers[subscription_id] = handler

        # Ensure consumer group exists
        await self._ensure_consumer_group(stream_name)

        # Start consumer task
        task = asyncio.create_task(self._consume(stream_name, subscription_id, handler))
        self._subscriptions[subscription_id] = task

        logger.info(f"Subscribed to {stream_name} as {self.consumer_name}")
        return subscription_id

    async def _consume(self, stream_name: str, subscription_id: str, handler: MessageHandler) -> None:
        """Consume messages from stream."""
        while self._running:
            try:
                # Read from stream using consumer group
                messages = await self._client.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {stream_name: ">"},
                    count=10,
                    block=5000  # 5 second block
                )

                for stream, entries in messages:
                    for msg_id, data in entries:
                        try:
                            message_data = json.loads(data["data"])
                            message = Message.from_dict(message_data)
                            await handler(message)
                            
                            # Acknowledge message
                            await self._client.xack(stream_name, self.consumer_group, msg_id)
                            
                        except Exception as e:
                            logger.error(f"Error processing message {msg_id}: {e}")
                            # Don't ack - will be redelivered
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in consumer loop: {e}")
                await asyncio.sleep(1)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a topic."""
        if subscription_id in self._subscriptions:
            task = self._subscriptions.pop(subscription_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._handlers.pop(subscription_id, None)
        logger.info(f"Unsubscribed {subscription_id}")

    async def request(
        self, 
        topic: str, 
        payload: dict[str, Any], 
        timeout: float = 30.0
    ) -> Message | None:
        """Request-response pattern using temporary reply stream."""
        if not self._client:
            raise RuntimeError("Not connected")

        import uuid
        correlation_id = str(uuid.uuid4())
        reply_stream = f"trading:reply:{correlation_id}"
        
        # Create future for response
        response_future: asyncio.Future = asyncio.get_event_loop().create_future()
        
        async def reply_handler(msg: Message) -> None:
            if not response_future.done():
                response_future.set_result(msg)
        
        # Subscribe to reply stream
        await self._ensure_consumer_group(reply_stream)
        reply_sub_id = await self.subscribe(reply_stream, reply_handler)
        
        try:
            # Send request with reply_to
            await self.publish(
                topic,
                payload,
                correlation_id=correlation_id,
                reply_to=reply_stream
            )
            
            # Wait for response
            response = await asyncio.wait_for(response_future, timeout=timeout)
            return response
            
        except asyncio.TimeoutError:
            logger.warning(f"Request to {topic} timed out")
            return None
        finally:
            await self.unsubscribe(reply_sub_id)