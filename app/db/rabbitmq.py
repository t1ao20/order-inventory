import asyncio
import json
import logging
from typing import Callable, Awaitable, Optional

import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode
from app.core.config import settings

logger = logging.getLogger(__name__)

ORDER_EXCHANGE = "order_events"
ORDER_CREATED_QUEUE = "order.created"
ORDER_CANCELLED_QUEUE = "order.cancelled"

_connection: Optional[aio_pika.RobustConnection] = None
_channel: Optional[aio_pika.RobustChannel] = None
_exchange: Optional[aio_pika.Exchange] = None


async def init_rabbitmq():
    global _connection, _channel, _exchange

    # Retry until RabbitMQ is ready (it can be slow to start)
    for attempt in range(15):
        try:
            _connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            break
        except Exception as e:
            logger.warning(f"RabbitMQ not ready ({attempt+1}/15): {e}")
            await asyncio.sleep(3)
    else:
        raise RuntimeError("Could not connect to RabbitMQ after 15 attempts")

    _channel = await _connection.channel()
    await _channel.set_qos(prefetch_count=10)

    _exchange = await _channel.declare_exchange(
        ORDER_EXCHANGE, ExchangeType.TOPIC, durable=True
    )

    # Declare Quorum Queues (persistent, high-availability)
    for queue_name in [ORDER_CREATED_QUEUE, ORDER_CANCELLED_QUEUE]:
        queue = await _channel.declare_queue(
            queue_name,
            durable=True,
            arguments={"x-queue-type": "quorum"},
        )
        await queue.bind(_exchange, routing_key=queue_name)

    logger.info("RabbitMQ connected and queues declared")


async def close_rabbitmq():
    if _connection:
        await _connection.close()


async def publish(routing_key: str, payload: dict):
    if _exchange is None:
        raise RuntimeError("RabbitMQ not initialised")
    body = json.dumps(payload, default=str).encode()
    await _exchange.publish(
        Message(
            body=body,
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
        ),
        routing_key=routing_key,
    )


async def consume(queue_name: str, callback: Callable[[dict], Awaitable[None]]):
    """Start consuming a queue. callback receives the decoded JSON payload."""
    if _channel is None:
        raise RuntimeError("RabbitMQ not initialised")
    queue = await _channel.get_queue(queue_name)

    async def _on_message(message: aio_pika.IncomingMessage):
        async with message.process(requeue=True):
            try:
                payload = json.loads(message.body)
                await callback(payload)
            except Exception as e:
                logger.exception(f"Worker error processing message: {e}")
                raise

    await queue.consume(_on_message)
    logger.info(f"Consumer started on queue: {queue_name}")
