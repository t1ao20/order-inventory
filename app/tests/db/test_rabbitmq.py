import asyncio
import json
from uuid import UUID

from app.db import rabbitmq


class FakeExchange:
    def __init__(self):
        self.publish_call = None

    async def publish(self, message, routing_key: str):
        self.publish_call = (message, routing_key)


def test_publish_serializes_uuid_payload(monkeypatch):
    exchange = FakeExchange()
    monkeypatch.setattr(rabbitmq, "_exchange", exchange)
    order_id = UUID("11111111-1111-4111-8111-111111111111")

    asyncio.run(rabbitmq.publish("order.created", {"order_id": order_id}))

    message, routing_key = exchange.publish_call
    assert routing_key == "order.created"
    assert json.loads(message.body) == {"order_id": str(order_id)}
