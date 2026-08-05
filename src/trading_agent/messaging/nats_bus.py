"""NATS message bus implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from trading_agent.messaging.base import Message, MessageBus, MessageHandler, MessagePriority

logger = logging.getLogger(__name__)

# nats is an optional dependency — defer import so `trading.messaging`
# stays importable even when nats-py is not installed.
try:
    import nats
    from nats.js import JetStreamContext
    from nats.js.api import StreamConfig, ConsumerConfig, AckPolicy, RetentionPolicy
    NATS_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    nats = None
    JetStreamContext = None  # type: ignore
    StreamConfig = None  # type: ignore
    ConsumerConfig = None  # type: ignore
    AckPolicy = None  # type: ignore
    RetentionPolicy = None  # type: ignore
    NATS_AVAILABLE = False


class NATSBus(MessageBus):
    """NATS JetStream based message bus.
    
    Features:
    - JetStream persistence
    - Consumer groups (durable consumers)
    - Message acknowledgment
    - Request-reply pattern
    - Stream replication for HA
    """

    def __init__(
        self,
        servers: str | list[str] = "nats://localhost:4222",
        stream_prefix: str = "TRADING",
        consumer_name: str | None = None,
        max_pending: int = 1000,
        ack_wait: int = 30,
        max_deliver: int = 3,
    ):
        self.servers = servers if isinstance(servers, list) else [servers]
        self.stream_prefix = stream_prefix
        self.consumer_name = consumer_name or f"consumer-{id(self)}"
        self.max_pending = max_pending
        self.ack_wait = ack_wait
        self.max_deliver = max_deliver

        self._nc: nats.NATS | None = None
        self._js: JetStreamContext | None = None
        self._subscriptions: dict[str, Any] = {}
        self._handlers: dict[str, MessageHandler] = {}
        self._running = False

    async def connect(self) -> None:
        """Connect to NATS."""
        if not NATS_AVAILABLE:
            raise RuntimeError(
                "nats-py is not installed. Install it with: pip install nats-py"
            )
        self._nc = await nats.connect(servers=self.servers)
        self._js = self._nc.jetstream()
        self._running = True
        logger.info(f"Connected to NATS at {self.servers}")

    async def disconnect(self) -> None:
        """Disconnect from NATS."""
        self._running = False
        
        # Drain subscriptions
        for sub in self._subscriptions.values():
            await sub.unsubscribe()
        
        if self._nc:
            await self._nc.drain()
        
        logger.info("Disconnected from NATS")

    def _stream_name(self, topic: str, priority: MessagePriority = MessagePriority.NORMAL) -> str:
        """Generate stream name."""
        if priority != MessagePriority.NORMAL:
            return f"{self.stream_prefix}.{priority.name.lower()}.{topic}"
        return f"{self.stream_prefix}.{topic}"

    def _subject(self, topic: str, priority: MessagePriority = MessagePriority.NORMAL) -> str:
        """Generate NATS subject."""
        if priority != MessagePriority.NORMAL:
            return f"{self.stream_prefix}.{priority.name.lower()}.{topic}"
        return f"{self.stream_prefix}.{topic}"

    async def _ensure_stream(self, stream_name: str) -> None:
        """Ensure stream exists."""
        try:
            await self._js.add_stream(
                StreamConfig(
                    name=stream_name,
                    subjects=[f"{stream_name}.>"],
                    retention=RetentionPolicy.WORK_QUEUE,
                    max_msgs=100000,
                    max_bytes=1024 * 1024 * 100,  # 100MB
                    storage="file",
                )
            )
        except Exception as e:
            if "stream name already in use" not in str(e).lower():
                raise

    async def publish(
        self, 
        topic: str, 
        payload: dict[str, Any], 
        priority: MessagePriority = MessagePriority.NORMAL,
        **kwargs
    ) -> None:
        """Publish message to NATS JetStream."""
        if not self._js:
            raise RuntimeError("Not connected")

        message = Message(
            topic=topic,
            payload=payload,
            priority=priority,
            **kwargs
        )

        stream_name = self._stream_name(topic, priority)
        await self._ensure_stream(stream_name)

        subject = self._subject(topic, priority)
        
        await self._js.publish(
            subject,
            json.dumps(message.to_dict()).encode(),
            headers={
                "Nats-Msg-Id": message.message_id,
                "Priority": str(priority.value),
            }
        )

        logger.debug(f"Published to {subject}: {message.message_id}")

    async def subscribe(
        self, 
        topic: str, 
        handler: MessageHandler, 
        priority: MessagePriority = MessagePriority.NORMAL,
        durable: str | None = None,
        **kwargs
    ) -> str:
        """Subscribe to a topic with a handler."""
        if not self._js:
            raise RuntimeError("Not connected")

        stream_name = self._stream_name(topic, priority)
        subject = self._subject(topic, priority)
        
        subscription_id = f"{topic}:{priority.name}:{id(handler)}"
        durable_name = durable or f"{self.consumer_name}-{subscription_id}"

        # Ensure stream exists
        await self._ensure_stream(stream_name)

        # Create or get consumer
        try:
            consumer = await self._js.add_consumer(
                stream_name,
                ConsumerConfig(
                    durable_name=durable_name,
                    ack_policy=AckPolicy.EXPLICIT,
                    ack_wait=self.ack_wait,
                    max_deliver=self.max_deliver,
                    filter_subject=subject,
                    max_pending=self.max_pending,
                )
            )
        except Exception as e:
            if "consumer already exists" in str(e).lower():
                consumer = await self._js.consumer_info(stream_name, durable_name)
            else:
                raise

        # Create pull subscription
        psub = await self._js.pull_subscribe(
            subject,
            durable=durable_name,
            stream=stream_name,
        )

        self._subscriptions[subscription_id] = psub
        self._handlers[subscription_id] = handler

        # Start consumer task
        asyncio.create_task(self._consume(subscription_id, psub, handler))

        logger.info(f"Subscribed to {subject} with durable consumer {durable_name}")
        return subscription_id

    async def _consume(self, subscription_id: str, psub: Any, handler: MessageHandler) -> None:
        """Consume messages from pull subscription."""
        while self._running:
            try:
                # Fetch batch of messages
                messages = await psub.fetch(batch=10, timeout=5)
                
                for msg in messages:
                    try:
                        message_data = json.loads(msg.data.decode())
                        message = Message.from_dict(message_data)
                        await handler(message)
                        await msg.ack()
                    except Exception as e:
                        logger.error(f"Error processing message: {e}")
                        await msg.nak()
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                if "timeout" not in str(e).lower():
                    logger.error(f"Error in consumer loop: {e}")
                await asyncio.sleep(1)

    async def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from a topic."""
        if subscription_id in self._subscriptions:
            psub = self._subscriptions.pop(subscription_id)
            await psub.unsubscribe()
        
        self._handlers.pop(subscription_id, None)
        logger.info(f"Unsubscribed {subscription_id}")

    async def request(
        self, 
        topic: str, 
        payload: dict[str, Any], 
        timeout: float = 30.0
    ) -> Message | None:
        """Request-response pattern."""
        if not self._nc:
            raise RuntimeError("Not connected")

        import uuid
        correlation_id = str(uuid.uuid4())
        reply_subject = f"{self.stream_prefix}.reply.{correlation_id}"
        
        # Create future for response
        response_future: asyncio.Future = asyncio.get_event_loop().create_future()
        
        async def reply_handler(msg: Message) -> None:
            if not response_future.done():
                response_future.set_result(msg)
        
        # Subscribe to reply
        reply_sub_id = await self.subscribe(reply_subject, reply_handler, durable=f"reply-{correlation_id}")
        
        try:
            # Send request
            await self.publish(
                topic,
                payload,
                correlation_id=correlation_id,
                reply_to=reply_subject
            )
            
            # Wait for response
            response = await asyncio.wait_for(response_future, timeout=timeout)
            return response
            
        except asyncio.TimeoutError:
            logger.warning(f"Request to {topic} timed out")
            return None
        finally:
            await self.unsubscribe(reply_sub_id)