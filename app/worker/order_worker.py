import logging
from datetime import date, datetime, timezone

from app.db import rabbitmq as mq_mod, redis as rdb_mod
from app.models.order import Order, OrderStatus
from app.repositories.order_repository import OrderRepository
from app.repositories.inventory_repository import InventoryRepository
from app.services.notification_service import notify_order_created

logger = logging.getLogger(__name__)


class OrderWorker:
    """
    Consumes order.created events from RabbitMQ and writes to PostgreSQL.
    Runs as a background asyncio task alongside the FastAPI server.
    """

    def __init__(self):
        self.order_repo = OrderRepository()
        self.inventory_repo = InventoryRepository()

    async def start(self):
        logger.info("[Worker] Starting order consumer...")
        await mq_mod.consume(mq_mod.ORDER_CREATED_QUEUE, self.handle_created)
        # Keep task alive
        import asyncio
        while True:
            await asyncio.sleep(3600)

    async def handle_created(self, payload: dict):
        order_id = payload.get("order_id", "")
        try:
            now = datetime.now(timezone.utc)
            pickup_date = date.fromisoformat(payload["pickup_date"])
            order = Order(
                id=order_id,
                employee_id=payload["employee_id"],
                vendor_user_id=payload["vendor_user_id"],
                vendor_id=payload.get("vendor_id"),
                menu_id=payload["menu_id"],
                menu_name=payload.get("menu_name", ""),
                menu_tags=payload.get("menu_tags", []),
                price_snapshot=payload.get("price", 0),
                quantity=payload.get("quantity", 1),
                total_price=payload.get("price", 0) * payload.get("quantity", 1),
                order_date=now.date(),
                pickup_date=pickup_date,
                status=OrderStatus.confirmed,
                created_at=now,
                factoryZone=payload.get("factoryZone", ""),
            )

            # Write to PostgreSQL
            await self.order_repo.create(order)

            # Decrement DB inventory for the pickup date (Redis already decremented atomically)
            updated = await self.inventory_repo.decrement(payload["menu_id"], pickup_date, payload.get("quantity", 1))
            if not updated:
                raise RuntimeError("Inventory decrement failed")

            # Update live status in Redis: pending → confirmed
            rdb = rdb_mod.get_redis()
            await rdb.set(rdb_mod.order_status_key(order_id), "confirmed", ex=86400)

            logger.info(f"[Worker] Order {order_id} written to DB and confirmed")
            await notify_order_created(str(order_id))

        except Exception as e:
            logger.error(f"[Worker] Failed to process order {order_id}: {e}")
            raise
