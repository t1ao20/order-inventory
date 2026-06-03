import time
from datetime import date, timedelta, datetime, timezone

from fastapi import HTTPException
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db import redis as rdb_mod, rabbitmq as mq_mod
from app.models.order import Order, OrderEvent, OrderStatus
from app.repositories.inventory_repository import InventoryRepository
from app.repositories.order_repository import OrderRepository
from app.services.notification_service import notify_order_cancelled


class InventoryService:
    def __init__(self):
        self.repo = InventoryRepository()
        self.order_repo = OrderRepository()

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo("Asia/Taipei"))

    async def get_inventory(self, menu_id: UUID, target_date: date) -> int:
        """Returns remaining stock. Checks Redis first (Cache-aside pattern)."""
        date_str = target_date.isoformat()
        rdb = rdb_mod.get_redis()
        key = rdb_mod.inventory_key(menu_id, date_str)

        # Cache hit
        cached = await rdb.get(key)
        if cached is not None:
            return int(cached)

        # Cache miss → query DB, warm cache
        inv = await self.repo.get(menu_id, target_date)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"No inventory for menu {menu_id} on {date_str}")

        await rdb.set(key, inv.remaining_quantity, ex=600)  # 10-min TTL
        return inv.remaining_quantity

    async def set_inventory(self, menu_id: UUID, target_date: date, qty: int) -> None:
        """Upsert max stock in DB and warm Redis (called by vendor/admin)."""
        current = await self.repo.get(menu_id, target_date)
        sold_quantity = current.sold_quantity if current is not None else 0
        if qty < sold_quantity:
            await self._cancel_overflow_orders(menu_id, target_date, sold_quantity - qty)
            current = await self.repo.get(menu_id, target_date)
            sold_quantity = current.sold_quantity if current is not None else 0
            if qty < sold_quantity:
                raise HTTPException(status_code=409, detail="Cannot reduce inventory below sold quantity")

        await self.repo.upsert(menu_id, target_date, qty)

        remaining_quantity = qty - sold_quantity

        # Warm Redis; TTL = seconds until end of day
        key = rdb_mod.inventory_key(menu_id, target_date.isoformat())
        end_of_day = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=timezone.utc)
        ttl = max(int((end_of_day - datetime.now(timezone.utc)).total_seconds()), 1)

        rdb = rdb_mod.get_redis()
        await rdb.set(key, remaining_quantity, ex=ttl)

    async def _cancel_overflow_orders(self, menu_id: UUID, target_date: date, overflow: int) -> None:
        orders = await self.order_repo.list_confirmed_by_menu_and_date(menu_id, target_date)
        cancelled_quantity = 0
        for order in orders:
            if cancelled_quantity >= overflow:
                break
            await self._cancel_order_force(order)
            cancelled_quantity += order.quantity

    async def _cancel_order_force(self, order: Order) -> None:
        await self.order_repo.update_status(order.id, OrderStatus.cancelled)

        target_date = order.pickup_date.isoformat()
        for _ in range(order.quantity):
            await rdb_mod.incr_inventory(order.menu_id, target_date)
        await self.repo.increment(order.menu_id, order.pickup_date, order.quantity)

        rdb = rdb_mod.get_redis()
        await rdb.set(rdb_mod.order_status_key(order.id), "cancelled", ex=86400)

        event = OrderEvent(
            event="order.cancelled",
            order_id=order.id,
            employee_id=order.employee_id,
            vendor_user_id=order.vendor_user_id,
            vendor_id=order.vendor_id,
            menu_id=order.menu_id,
            menu_name=order.menu_name,
            menu_tags=order.menu_tags,
            factoryZone=order.factoryZone,
            cancel_reason="庫存調整，餐點數量不足",
            quantity=order.quantity,
            pickup_date=target_date,
            status=OrderStatus.cancelled,
            timestamp=int(time.time()),
        )
        await mq_mod.publish("order.cancelled", event.model_dump())
        await notify_order_cancelled(str(order.id), "庫存調整，餐點數量不足")
