import time
import uuid
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from app.db import redis as rdb_mod, rabbitmq as mq_mod
from app.models.order import Order, OrderEvent, OrderStatus, PlaceOrderRequest, UpdateOrderRequest
from app.repositories.order_repository import OrderRepository
from app.repositories.inventory_repository import InventoryRepository
from app.services.notification_service import notify_order_cancelled, notify_order_quantity_updated
from app.services.vendor_menu_service import VendorMenuService

ORDER_CREATED = "order.created"
ORDER_CANCELLED = "order.cancelled"
TW_TZ = ZoneInfo("Asia/Taipei")


class OrderService:
    def __init__(self):
        self.order_repo = OrderRepository()
        self.inventory_repo = InventoryRepository()
        self.vendor_menu_service = VendorMenuService()

    def _now(self) -> datetime:
        return datetime.now(TW_TZ)

    def _change_deadline(self, pickup_date: date) -> datetime:
        return datetime.combine(
            pickup_date - timedelta(days=1),
            dt_time(hour=17),
            tzinfo=TW_TZ,
        )

    def _ensure_before_change_deadline(self, pickup_date: date, detail: str) -> None:
        if self._now() > self._change_deadline(pickup_date):
            raise HTTPException(status_code=422, detail=detail)

# For Employee APIs
    # ── Place Order ────────────────────────────────────────────
    async def create_order(self, req: PlaceOrderRequest, employee_id: int) -> dict:
        self._ensure_before_change_deadline(req.pickup_date, "Order change deadline passed")
        target_date = req.pickup_date.isoformat()
        try:
            menu = await self.vendor_menu_service.get_menu(req.menu_id)
            vendor_id = UUID(str(menu["vendorId"]))
            menu_name = str(menu["name"])
            price = int(menu["price"])
            menu_tags = [str(tag) for tag in menu.get("tags", [])]
            vendor_user_id = int((await self.vendor_menu_service.get_vendor(vendor_id))["userId"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=502, detail="Invalid menu or vendor response")

        # Step 1: Atomically reserve inventory in Redis for requested quantity
        reserved_qty = await self._reserve_inventory(req.menu_id, target_date, req.quantity)

        # Step 2: Build order
        order_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        order = Order(
            id=order_id,
            employee_id=employee_id,
            vendor_user_id=vendor_user_id,
            vendor_id=vendor_id,
            menu_id=req.menu_id,
            menu_name=menu_name,
            price_snapshot=price,
            quantity=req.quantity,
            total_price=price * req.quantity,
            order_date=now.date(),
            pickup_date=req.pickup_date,
            status=OrderStatus.pending,
            created_at=now,
            menu_tags=menu_tags,
            factoryZone=req.factoryZone,
        )

        # Step 3: Cache live order status in Redis (TTL 24h)
        rdb = rdb_mod.get_redis()
        await rdb.set(rdb_mod.order_status_key(order_id), "pending", ex=86400)

        # Step 4: Publish to RabbitMQ → worker writes DB async
        event = OrderEvent(
            event=ORDER_CREATED,
            order_id=order_id,
            employee_id=employee_id,
            vendor_user_id=vendor_user_id,
            vendor_id=vendor_id,
            menu_id=req.menu_id,
            menu_name=menu_name,
            price=price,
            quantity=req.quantity,
            pickup_date=target_date,
            status=OrderStatus.pending,
            timestamp=int(now.timestamp()),
            menu_tags=menu_tags,
            factoryZone=req.factoryZone,
        )
        try:
            await mq_mod.publish(ORDER_CREATED, event.model_dump())
        except Exception as e:
            # Compensate: restore all reserved Redis stock for this order
            for _ in range(reserved_qty):
                await rdb_mod.incr_inventory(req.menu_id, target_date)
            await rdb.delete(rdb_mod.order_status_key(order_id))
            raise HTTPException(status_code=500, detail=f"Queue error: {e}")

        return {"order_id": str(order_id), "status": "pending", "message": "order queued"}

    # ── Cancel Order ───────────────────────────────────────────
    async def cancel_order(self, order_id: UUID, employee_id: int, cancel_reason: Optional[str] = None) -> None:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Not your order")
        if order.status == OrderStatus.cancelled:
            raise HTTPException(status_code=422, detail="Already cancelled")

        # Business rule: must cancel before 17:00 the day before pickup
        if self._now() > self._change_deadline(order.pickup_date):
            raise HTTPException(status_code=422, detail="Cancellation deadline passed")

        reason = self._normalize_cancel_reason(cancel_reason, "使用者自行取消訂單")
        await self.order_repo.update_status(order_id, OrderStatus.cancelled)

        # Restore Redis inventory for the pickup date
        await self._restore_order_inventory(order)

        # Update cached status
        rdb = rdb_mod.get_redis()
        await rdb.set(rdb_mod.order_status_key(order_id), "cancelled", ex=86400)

        # Publish cancellation event
        event = OrderEvent(
            event=ORDER_CANCELLED,
            order_id=order_id,
            employee_id=employee_id,
            vendor_user_id=order.vendor_user_id,
            vendor_id=order.vendor_id,
            menu_id=order.menu_id,
            menu_name=order.menu_name,
            menu_tags=order.menu_tags,
            factoryZone=order.factoryZone,
            cancel_reason=reason,
            pickup_date=order.pickup_date.isoformat(),
            timestamp=int(time.time()),
        )
        await mq_mod.publish(ORDER_CANCELLED, event.model_dump())
        await notify_order_cancelled(str(order_id), reason)

    # ── Get Order ──────────────────────────────────────────────
    async def get_order(self, order_id: UUID, employee_id: int) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Not your order")

        return await self._overlay_live_status(order)

    async def get_order_for_actor(self, order_id: UUID, actor: dict) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        role = actor["role"]
        user_id = actor["user_id"]
        if role in ("employee", "admin") and order.employee_id != user_id:
            raise HTTPException(status_code=403, detail="Not your order")
        if role == "vendor" and order.vendor_user_id != user_id:
            raise HTTPException(status_code=403, detail="Not your vendor order")
        if role not in ("employee", "vendor", "admin"):
            raise HTTPException(status_code=403, detail="Unsupported role")

        return await self._overlay_live_status(order)

    async def update_order(self, order_id: UUID, actor: dict, payload: UpdateOrderRequest) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        role = actor["role"]
        user_id = actor["user_id"]
        has_status_update = payload.status is not None or payload.action is not None

        if role == "employee":
            if order.employee_id != user_id:
                raise HTTPException(status_code=403, detail="Not your order")
            if payload.quantity is not None and not has_status_update:
                await self._update_order_quantity(order, payload.quantity)
                return await self.get_order_for_actor(order_id, actor)
            next_status = self._resolve_next_status(payload)
            if next_status != OrderStatus.cancelled:
                raise HTTPException(status_code=403, detail="Employees can only cancel orders")
            await self.cancel_order(order_id, employee_id=user_id, cancel_reason=payload.cancel_reason)
        elif role == "vendor":
            if order.vendor_user_id != user_id:
                raise HTTPException(status_code=403, detail="Not your vendor order")
            next_status = self._resolve_next_status(payload)
            if next_status != OrderStatus.cancelled:
                raise HTTPException(status_code=403, detail="Vendors can only reject or cancel orders")
            await self.cancel_vendor_order(order_id, vendor_id=order.vendor_id, cancel_reason=payload.cancel_reason)
        elif role == "admin":
            next_status = self._resolve_next_status(payload)
            if next_status == OrderStatus.cancelled:
                await self._cancel_loaded_order(order, actor, cancel_reason=payload.cancel_reason)
            else:
                await self.order_repo.update_status(order_id, next_status)
                await self._cache_status(order_id, next_status)
        else:
            raise HTTPException(status_code=403, detail="Unsupported role")

        return await self.get_order_for_actor(order_id, actor)

    async def update_order_quantity(self, order_id: UUID, employee_id: int, quantity: int) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Not your order")

        self._ensure_before_change_deadline(order.pickup_date, "Order change deadline passed")

        await self._update_order_quantity(order, quantity)
        order.quantity = quantity
        order.total_price = order.price_snapshot * quantity
        return await self._overlay_live_status(order)

    async def complete_order(self, order_id: UUID, actor: dict) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        role = actor["role"]
        user_id = actor["user_id"]
        if role in ("employee", "admin") and order.employee_id != user_id:
            raise HTTPException(status_code=403, detail="Not your order")
        if role not in ("employee", "admin"):
            raise HTTPException(status_code=403, detail="Only employees/admin can complete orders")
        if order.status == OrderStatus.cancelled:
            raise HTTPException(status_code=422, detail="Cannot complete cancelled order")
        if order.status == OrderStatus.completed:
            raise HTTPException(status_code=422, detail="Already completed")

        await self.order_repo.update_status(order_id, OrderStatus.completed)
        await self._cache_status(order_id, OrderStatus.completed)
        return await self.get_order_for_actor(order_id, actor)
    
    # ── Today's Order ──────────────────────────────────────────
    async def get_today_order(self, employee_id: int) -> Order:
        order = await self.order_repo.get_today_order(employee_id)
        if not order:
            raise HTTPException(status_code=404, detail="No order today")
        return await self._overlay_live_status(order)

    # ── My Orders ──────────────────────────────────────────────
    async def get_orders(
        self,
        employee_id: int,
        from_date: Optional[date],
        to_date: Optional[date],
        status: Optional[str] = None,
    ) -> list[Order]:
        orders = await self.order_repo.list_by_employee(employee_id, from_date, to_date)
        orders = await self._overlay_live_statuses(orders)
        if status:
            orders = [order for order in orders if order.status.value == status]
        return orders

    async def get_orders_by_employee_id(
        self,
        employee_id: int,
        from_date: Optional[date],
        to_date: Optional[date],
        status: Optional[str] = None,
    ) -> list[Order]:
        return await self.get_orders(employee_id, from_date, to_date, status=status)

    async def get_orders_history(self, employee_id: int, from_dt: datetime, to_dt: datetime) -> list[Order]:
        return await self.get_orders(employee_id, from_dt.date(), to_dt.date())
    
# For Vendor APIs

    async def get_vendor_order(self, order_id: UUID, vendor_id: UUID) -> Order:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.vendor_id != vendor_id:
            raise HTTPException(status_code=403, detail="Not your vendor order")

        return await self._overlay_live_status(order)

    async def get_vendor_orders(
        self,
        vendor_id: UUID,
        from_date: Optional[date],
        to_date: Optional[date],
        status: Optional[str] = None,
    ) -> list[Order]:
        orders = await self.order_repo.list_by_vendor(vendor_id, from_date, to_date)
        orders = await self._overlay_live_statuses(orders)
        if status:
            orders = [order for order in orders if order.status.value == status]
        return orders

    async def get_vendor_orders_by_vendor_user_id(
        self,
        vendor_user_id: int,
        from_date: Optional[date],
        to_date: Optional[date],
        status: Optional[str] = None,
    ) -> list[Order]:
        orders = await self.order_repo.list_by_vendor_user_id(vendor_user_id, from_date, to_date)
        orders = await self._overlay_live_statuses(orders)
        if status:
            orders = [order for order in orders if order.status.value == status]
        return orders

    async def get_completed_orders_by_vendor_user_id(
        self,
        vendor_user_id: int,
        from_date: Optional[date],
        to_date: Optional[date],
    ) -> list[Order]:
        orders = await self.order_repo.list_by_vendor_user_id_and_status(
            vendor_user_id,
            OrderStatus.completed,
            from_date,
            to_date,
        )
        return await self._overlay_live_statuses(orders)

    async def cancel_vendor_order(
        self,
        order_id: UUID,
        vendor_id: UUID,
        cancel_reason: Optional[str] = None,
    ) -> None:
        order = await self.order_repo.get_by_id(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.vendor_id != vendor_id:
            raise HTTPException(status_code=403, detail="Not your vendor order")
        if order.status == OrderStatus.cancelled:
            raise HTTPException(status_code=422, detail="Already cancelled")

        self._ensure_before_change_deadline(order.pickup_date, "Order change deadline passed")

        reason = self._normalize_cancel_reason(cancel_reason, "商家取消訂單")
        await self.order_repo.update_status(order_id, OrderStatus.cancelled)

        await self._restore_order_inventory(order)

        rdb = rdb_mod.get_redis()
        await rdb.set(rdb_mod.order_status_key(order_id), "cancelled", ex=86400)

        event = OrderEvent(
            event=ORDER_CANCELLED,
            order_id=order_id,
            employee_id=order.employee_id,
            vendor_user_id=order.vendor_user_id,
            vendor_id=order.vendor_id,
            menu_id=order.menu_id,
            menu_name=order.menu_name,
            menu_tags=order.menu_tags,
            factoryZone=order.factoryZone,
            cancel_reason=reason,
            pickup_date=order.pickup_date.isoformat(),
            timestamp=int(time.time()),
        )
        await mq_mod.publish(ORDER_CANCELLED, event.model_dump())
        await notify_order_cancelled(str(order_id), reason)

    async def reject_vendor_order(
        self,
        order_id: UUID,
        vendor_id: UUID,
        cancel_reason: Optional[str] = None,
    ) -> Order:
        reason = self._normalize_cancel_reason(cancel_reason, "商家取消訂單")
        await self.cancel_vendor_order(order_id, vendor_id, cancel_reason=reason)
        return await self.get_vendor_order(order_id, vendor_id)

    async def get_vendor_orders_today(self, vendor_id: UUID) -> list[Order]:
        orders = await self.order_repo.list_today_by_vendor(vendor_id)
        return await self._overlay_live_statuses(orders)

    async def get_vendor_orders_history(self, vendor_id: UUID, from_dt: datetime, to_dt: datetime) -> list[Order]:
        orders = await self.order_repo.list_by_vendor(vendor_id, from_dt.date(), to_dt.date())
        return await self._overlay_live_statuses(orders)

    async def _overlay_live_status(self, order: Order) -> Order:
        rdb = rdb_mod.get_redis()
        cached = await rdb.get(rdb_mod.order_status_key(order.id))
        if cached:
            order.status = OrderStatus(cached)
        return order

    async def _overlay_live_statuses(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            await self._overlay_live_status(order)
        return orders

    def _resolve_next_status(self, payload: UpdateOrderRequest) -> OrderStatus:
        if payload.status is not None:
            return payload.status

        if payload.action is None:
            raise HTTPException(status_code=400, detail="status or action is required")

        action = payload.action.lower()
        if action in ("cancel", "cancelled", "reject", "rejected"):
            return OrderStatus.cancelled
        raise HTTPException(status_code=400, detail="Unsupported action")

    def _normalize_cancel_reason(self, cancel_reason: Optional[str], default_reason: str) -> str:
        if cancel_reason is None:
            return default_reason
        reason = cancel_reason.strip()
        return reason or default_reason

    async def _cache_status(self, order_id: UUID, status_value: OrderStatus) -> None:
        rdb = rdb_mod.get_redis()
        await rdb.set(rdb_mod.order_status_key(order_id), status_value.value, ex=86400)

    async def _restore_order_inventory(self, order: Order) -> None:
        target_date = order.pickup_date.isoformat()
        for _ in range(order.quantity):
            await rdb_mod.incr_inventory(order.menu_id, target_date)
        await self.inventory_repo.increment(order.menu_id, order.pickup_date, order.quantity)

    async def _update_order_quantity(self, order: Order, quantity: int) -> None:
        if order.status == OrderStatus.cancelled:
            raise HTTPException(status_code=422, detail="Cannot update cancelled order")
        if quantity == order.quantity:
            return

        old_quantity = order.quantity
        diff = quantity - order.quantity
        target_date = order.pickup_date
        target_date_str = target_date.isoformat()

        if diff > 0:
            reserved = 0
            try:
                for _ in range(diff):
                    remaining = await rdb_mod.decr_inventory(order.menu_id, target_date_str)
                    if remaining == -2:
                        raise HTTPException(status_code=404, detail="Inventory not found")
                    if remaining == -1:
                        raise HTTPException(status_code=409, detail="Out of stock")
                    reserved += 1

                updated = await self.inventory_repo.decrement(order.menu_id, target_date, diff)
                if not updated:
                    raise HTTPException(status_code=409, detail="Inventory update failed")
            except HTTPException:
                for _ in range(reserved):
                    await rdb_mod.incr_inventory(order.menu_id, target_date_str)
                raise
        else:
            restore_qty = abs(diff)
            for _ in range(restore_qty):
                await rdb_mod.incr_inventory(order.menu_id, target_date_str)
            await self.inventory_repo.increment(order.menu_id, target_date, restore_qty)

        await self.order_repo.update_quantity(
            order.id,
            quantity,
            order.price_snapshot * quantity,
        )
        await notify_order_quantity_updated(
            str(order.id),
            old_quantity=old_quantity,
            new_quantity=quantity,
        )

    async def _reserve_inventory(self, menu_id: UUID, target_date: str, quantity: int) -> int:
        remaining = await rdb_mod.reserve_inventory(menu_id, target_date, quantity)
        if remaining == -2:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inventory not found")
        if remaining == -1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Out of stock")
        return quantity

    async def _cancel_loaded_order(
        self,
        order: Order,
        actor: dict,
        cancel_reason: Optional[str] = None,
    ) -> None:
        if order.status == OrderStatus.cancelled:
            raise HTTPException(status_code=422, detail="Already cancelled")

        self._ensure_before_change_deadline(order.pickup_date, "Order change deadline passed")

        actor_role = actor.get("role", "admin")
        default_reason = "管理員取消訂單" if actor_role == "admin" else "訂單已取消"
        reason = self._normalize_cancel_reason(cancel_reason, default_reason)
        await self.order_repo.update_status(order.id, OrderStatus.cancelled)

        await self._restore_order_inventory(order)
        await self._cache_status(order.id, OrderStatus.cancelled)

        event = OrderEvent(
            event=ORDER_CANCELLED,
            order_id=order.id,
            employee_id=order.employee_id,
            vendor_user_id=order.vendor_user_id,
            vendor_id=order.vendor_id,
            menu_id=order.menu_id,
            menu_name=order.menu_name,
            menu_tags=order.menu_tags,
            factoryZone=order.factoryZone,
            cancel_reason=reason,
            pickup_date=order.pickup_date.isoformat(),
            timestamp=int(time.time()),
        )
        await mq_mod.publish(ORDER_CANCELLED, event.model_dump())
        await notify_order_cancelled(str(order.id), reason)
