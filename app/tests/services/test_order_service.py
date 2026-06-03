import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models.order import Order, OrderStatus, PlaceOrderRequest, UpdateOrderRequest
from app.services import order_service
from app.services.order_service import OrderService
from app.test_time import after_cutoff_dt, before_cutoff_dt, cutoff_dt, days_from_today, tw_now, tw_today, utc_now
from uuid import UUID


ORDER_ID = "11111111-1111-4111-8111-111111111111"
ORDER_UUID = UUID(ORDER_ID)

# Test UUIDs
VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000007")
OTHER_VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000099")
MENU_UUID = UUID("00000000-0000-4000-8000-000000000042")


def make_order(
    *,
    order_id: UUID = ORDER_UUID,
    employee_id: int = 1,
    vendor_user_id: int = 7,
    vendor_id: UUID = VENDOR_UUID,
    quantity: int = 1,
    status: OrderStatus = OrderStatus.confirmed,
    pickup_date: date = days_from_today(2),
) -> Order:
    return Order(
        id=order_id,
        employee_id=employee_id,
        vendor_user_id=vendor_user_id,
        vendor_id=vendor_id,
        menu_id=MENU_UUID,
        menu_name="Lunch Box",
        price_snapshot=120,
        quantity=quantity,
        total_price=120 * quantity,
        order_date=tw_today(),
        pickup_date=pickup_date,
        status=status,
        created_at=utc_now(),
    )


class FakeRedis:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []
        self.delete_calls = []

    async def eval(self, script: str, numkeys: int, key: str):
        return 1

    async def get(self, key: str):
        return self.cached

    async def set(self, key: str, value: str, ex: int):
        self.set_calls.append((key, value, ex))

    async def delete(self, key: str):
        self.delete_calls.append(key)


class FakeOrderRepository:
    def __init__(self, order=None, today_order=None, employee_orders=None, vendor_orders=None):
        self.order = order
        self.today_order = today_order
        self.employee_orders = employee_orders
        self.vendor_orders = vendor_orders
        self.update_status_call = None
        self.update_quantity_call = None
        self.get_today_order_call = None
        self.list_by_employee_call = None
        self.list_by_vendor_call = None
        self.list_by_vendor_user_id_call = None
        self.list_by_vendor_user_id_and_status_call = None
        self.list_today_by_vendor_call = None

    async def get_by_id(self, order_id: UUID):
        return self.order

    async def get_today_order(self, employee_id: int):
        self.get_today_order_call = employee_id
        return self.today_order if self.today_order is not None else self.order

    async def list_by_employee(self, employee_id: int, from_dt: datetime, to_dt: datetime):
        self.list_by_employee_call = (employee_id, from_dt, to_dt)
        if self.employee_orders is not None:
            return self.employee_orders
        return [self.order] if self.order is not None else []

    async def list_by_vendor(self, vendor_id: UUID, from_dt: datetime, to_dt: datetime):
        self.list_by_vendor_call = (vendor_id, from_dt, to_dt)
        if self.vendor_orders is not None:
            return self.vendor_orders
        return [self.order] if self.order is not None else []

    async def list_by_vendor_user_id(self, vendor_user_id: int, from_dt: datetime, to_dt: datetime):
        self.list_by_vendor_user_id_call = (vendor_user_id, from_dt, to_dt)
        if self.vendor_orders is not None:
            return self.vendor_orders
        return [self.order] if self.order is not None else []

    async def list_by_vendor_user_id_and_status(
        self,
        vendor_user_id: int,
        status: OrderStatus,
        from_dt: datetime,
        to_dt: datetime,
    ):
        self.list_by_vendor_user_id_and_status_call = (vendor_user_id, status, from_dt, to_dt)
        if self.vendor_orders is not None:
            return self.vendor_orders
        return [self.order] if self.order is not None else []

    async def list_today_by_vendor(self, vendor_id: UUID):
        self.list_today_by_vendor_call = vendor_id
        if self.vendor_orders is not None:
            return self.vendor_orders
        return [self.order] if self.order is not None else []

    async def update_status(self, order_id: UUID, status: OrderStatus):
        self.update_status_call = (str(order_id), status)
        if self.order:
            self.order.status = status
        return True

    async def update_quantity(self, order_id: UUID, quantity: int, total_price: int):
        self.update_quantity_call = (str(order_id), quantity, total_price)
        if self.order:
            self.order.quantity = quantity
            self.order.total_price = total_price
        return True


class FakeInventoryRepository:
    def __init__(self, decrement_result=True):
        self.increment_call = None
        self.decrement_call = None
        self.decrement_result = decrement_result

    async def decrement(self, menu_id: UUID, target_date: date, qty: int):
        self.decrement_call = (menu_id, target_date, qty)
        return self.decrement_result

    async def increment(self, menu_id: UUID, target_date: date, qty: int):
        self.increment_call = (menu_id, target_date, qty)


class FakeVendorMenuService:
    def __init__(self, vendor_user_id: int = 7):
        self.vendor_user_id = vendor_user_id
        self.menu_call = None
        self.vendor_call = None

    async def get_menu(self, menu_id: UUID) -> dict:
        self.menu_call = menu_id
        return {
            "id": str(menu_id),
            "vendorId": str(VENDOR_UUID),
            "name": "Lunch Box",
            "price": 120,
            "tags": ["BEEF", "AMERICAN"],
        }

    async def get_vendor(self, vendor_id: UUID) -> dict:
        self.vendor_call = vendor_id
        return {"id": str(vendor_id), "userId": self.vendor_user_id}


def test_resolve_next_status_prefers_status():
    # arrange: an order service and a payload with explicit status
    svc = OrderService()
    payload = UpdateOrderRequest(status=OrderStatus.completed, action="cancel")

    # act: resolve the next status
    result = svc._resolve_next_status(payload)

    # assert: result should use the explicit status value
    assert result == OrderStatus.completed


def test_create_order_raises_conflict_when_out_of_stock(monkeypatch):
    # arrange: an order service and Redis inventory decrement returning sold out
    svc = OrderService()
    svc.vendor_menu_service = FakeVendorMenuService(vendor_user_id=7)
    req = PlaceOrderRequest(
        menu_id=MENU_UUID,
        quantity=2,
        pickup_date=days_from_today(8),
        factoryZone="A廠",
    )
    monkeypatch.setattr(order_service.rdb_mod, "reserve_inventory", lambda menu_id, target_date, quantity: asyncio.sleep(0, result=-1))

    # act: create an order for an out-of-stock menu
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.create_order(req, employee_id=1))

    # assert: response should be a 409 out-of-stock error
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Out of stock"


def test_create_order_success_persists_pending_state_and_publishes(monkeypatch):
    # arrange: an order service with stock available and a fake Redis cache
    svc = OrderService()
    svc.vendor_menu_service = FakeVendorMenuService(vendor_user_id=7)
    req = PlaceOrderRequest(
        menu_id=MENU_UUID,
        quantity=2,
        pickup_date=days_from_today(8),
        factoryZone="A廠",
    )
    rdb = FakeRedis()
    publish_calls = []
    reserve_calls = []

    monkeypatch.setattr(order_service.rdb_mod, "reserve_inventory", lambda menu_id, target_date, quantity: asyncio.sleep(0, result=reserve_calls.append((menu_id, target_date, quantity)) or 4))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=1))
    monkeypatch.setattr(order_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(order_service.uuid, "uuid4", lambda: "12345678-1234-4123-8123-123456789abc")

    # act: create a normal order
    result = asyncio.run(svc.create_order(req, employee_id=9))

    # assert: the order should be queued, cached, and published
    assert result == {
        "order_id": "12345678-1234-4123-8123-123456789abc",
        "status": "pending",
        "message": "order queued",
    }
    assert rdb.set_calls == [("order:today:12345678-1234-4123-8123-123456789abc", "pending", 86400)]
    assert publish_calls[0][0] == order_service.ORDER_CREATED
    assert str(publish_calls[0][1]["order_id"]) == "12345678-1234-4123-8123-123456789abc"
    assert publish_calls[0][1]["employee_id"] == 9
    assert publish_calls[0][1]["vendor_user_id"] == 7
    assert publish_calls[0][1]["vendor_id"] == VENDOR_UUID
    assert publish_calls[0][1]["menu_name"] == "Lunch Box"
    assert publish_calls[0][1]["price"] == 120
    assert publish_calls[0][1]["menu_tags"] == ["BEEF", "AMERICAN"]
    assert publish_calls[0][1]["factoryZone"] == "A廠"
    assert publish_calls[0][1]["quantity"] == 2
    assert publish_calls[0][1]["pickup_date"] == req.pickup_date.isoformat()
    assert reserve_calls == [(MENU_UUID, req.pickup_date.isoformat(), 2)]


def test_create_order_rolls_back_when_queue_publish_fails(monkeypatch):
    # arrange: an order service with stock available and a broken RabbitMQ publish
    svc = OrderService()
    svc.vendor_menu_service = FakeVendorMenuService(vendor_user_id=7)
    req = PlaceOrderRequest(
        menu_id=MENU_UUID,
        quantity=2,
        pickup_date=days_from_today(8),
        factoryZone="A廠",
    )
    rdb = FakeRedis()
    incr_calls = []
    reserve_calls = []

    async def failing_publish(routing_key: str, payload: dict):
        raise RuntimeError("queue down")

    monkeypatch.setattr(order_service.rdb_mod, "reserve_inventory", lambda menu_id, target_date, quantity: asyncio.sleep(0, result=reserve_calls.append((menu_id, target_date, quantity)) or 3))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))))
    monkeypatch.setattr(order_service.mq_mod, "publish", failing_publish)
    monkeypatch.setattr(order_service.uuid, "uuid4", lambda: "abcdefab-cdef-4abc-8def-abcdefabcdef")

    # act: create an order when the queue is unavailable
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.create_order(req, employee_id=9))

    # assert: stock should be rolled back, cache cleared, and a 500 returned
    assert exc_info.value.status_code == 500
    assert "Queue error: queue down" in exc_info.value.detail
    assert incr_calls == [(MENU_UUID, req.pickup_date.isoformat()), (MENU_UUID, req.pickup_date.isoformat())]
    assert reserve_calls == [(MENU_UUID, req.pickup_date.isoformat(), 2)]
    assert rdb.delete_calls == ["order:today:abcdefab-cdef-4abc-8def-abcdefabcdef"]


def test_create_order_rejects_after_deadline():
    # arrange: an order service at 17:01 on the day before pickup
    svc = OrderService()
    req = PlaceOrderRequest(
        menu_id=MENU_UUID,
        quantity=1,
        pickup_date=days_from_today(8),
        factoryZone="A廠",
    )
    svc._now = lambda: cutoff_dt(req.pickup_date)

    # act: create an order after the cutoff
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.create_order(req, employee_id=1))

    # assert: response should be a 422 deadline error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Order change deadline passed"


def test_resolve_next_status_maps_reject_action_to_cancelled():
    # arrange: an order service and a vendor reject action payload
    svc = OrderService()
    payload = UpdateOrderRequest(action="reject")

    # act: resolve the next status
    result = svc._resolve_next_status(payload)

    # assert: result should map reject to cancelled
    assert result == OrderStatus.cancelled


def test_resolve_next_status_requires_status_or_action():
    # arrange: an order service and an empty update payload
    svc = OrderService()
    payload = UpdateOrderRequest()

    # act: resolve the next status
    with pytest.raises(HTTPException) as exc_info:
        svc._resolve_next_status(payload)

    # assert: response should be a 400 validation error
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "status or action is required"


def test_get_order_for_actor_allows_matching_vendor(monkeypatch):
    # arrange: an order service with a vendor-owned order and no cached override
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: get the order as the matching vendor
    result = asyncio.run(svc.get_order_for_actor(ORDER_UUID, {"user_id": 7, "role": "vendor"}))

    # assert: result should be the vendor order
    assert result.id == ORDER_UUID
    assert result.vendor_id == VENDOR_UUID


def test_get_order_allows_matching_employee(monkeypatch):
    # arrange: an order service with an employee-owned order and no cached override
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=1))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: get the order as the matching employee
    result = asyncio.run(svc.get_order(ORDER_UUID, employee_id=1))

    # assert: result should be the employee order
    assert result.id == ORDER_UUID
    assert result.employee_id == 1


def test_get_order_rejects_wrong_employee():
    # arrange: an order service with an order owned by a different employee
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=2))

    # act: get the order as another employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_order(ORDER_UUID, employee_id=1))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_get_order_for_actor_rejects_wrong_vendor():
    # arrange: an order service with an order owned by a different vendor
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(vendor_id=VENDOR_UUID))

    # act: get the order as another vendor
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_order_for_actor(ORDER_UUID, {"user_id": 99, "role": "vendor"}))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your vendor order"


def test_get_today_order_overlays_cached_status(monkeypatch):
    # arrange: an order service with a current order and cached status override
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(today_order=make_order(status=OrderStatus.confirmed))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached="cancelled"))

    # act: get today's order
    result = asyncio.run(svc.get_today_order(employee_id=1))

    # assert: cached status should override the DB status
    assert result.status == OrderStatus.cancelled
    assert svc.order_repo.get_today_order_call == 1


def test_get_today_order_raises_not_found():
    # arrange: an order service with no current order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(today_order=None, order=None)

    # act: get today's order
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_today_order(employee_id=1))

    # assert: response should be a 404 no-order error
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No order today"


def test_get_vendor_order_allows_matching_vendor(monkeypatch):
    # arrange: an order service with a vendor-owned order and no cached override
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(vendor_id=VENDOR_UUID))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: get the order as the matching vendor
    result = asyncio.run(svc.get_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: result should be the vendor order
    assert result.id == ORDER_UUID
    assert result.vendor_id == VENDOR_UUID


def test_get_orders_history_returns_employee_orders(monkeypatch):
    # arrange: an order service with two historical orders
    history_orders = [make_order(order_id=ORDER_ID), make_order(order_id="22222222-2222-4222-8222-222222222222")]
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(employee_orders=history_orders)
    from_dt = datetime.combine(days_from_today(-30), time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(days_from_today(0), time.max, tzinfo=timezone.utc)
    fake_redis = FakeRedis(cached=None)
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: fake_redis)

    # act: fetch order history
    result = asyncio.run(svc.get_orders_history(employee_id=1, from_dt=from_dt, to_dt=to_dt))

    # assert: repository should be called and return the same orders
    assert result == history_orders
    assert svc.order_repo.list_by_employee_call == (1, from_dt.date(), to_dt.date())


def test_cancel_order_updates_status_inventory_and_cache(monkeypatch):
    # arrange: an order service with a cancellable employee order
    order = make_order(pickup_date=days_from_today(8), quantity=3)
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    incr_calls = []
    publish_calls = []
    notify_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))))
    monkeypatch.setattr(order_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(order_service, "notify_order_cancelled", lambda order_id, cancel_reason=None: asyncio.sleep(0, result=notify_calls.append((order_id, cancel_reason))))

    # act: cancel the order
    asyncio.run(svc.cancel_order(ORDER_UUID, employee_id=1))

    # assert: status, Redis cache, and publish event should be updated
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.cancelled)
    assert incr_calls == [(MENU_UUID, order.pickup_date.isoformat())] * 3
    assert svc.inventory_repo.increment_call == (MENU_UUID, order.pickup_date, 3)
    assert publish_calls[0][1]["pickup_date"] == order.pickup_date.isoformat()
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "cancelled", 86400)]
    assert publish_calls[0][0] == order_service.ORDER_CANCELLED
    assert str(publish_calls[0][1]["order_id"]) == ORDER_ID
    assert publish_calls[0][1]["cancel_reason"] == "使用者自行取消訂單"
    assert notify_calls == [(ORDER_ID, "使用者自行取消訂單")]


def test_cancel_order_rejects_after_deadline():
    # arrange: an order service with an order whose cancellation deadline has passed
    svc = OrderService()
    order = make_order(pickup_date=days_from_today(8))
    svc.order_repo = FakeOrderRepository(order=order)
    svc._now = lambda: after_cutoff_dt(order.pickup_date)

    # act: cancel the order after the deadline
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_order(ORDER_UUID, employee_id=1))

    # assert: response should be a 422 deadline error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Cancellation deadline passed"


def test_update_order_quantity_rejects_after_deadline():
    # arrange: an order service with an order whose change deadline has passed
    svc = OrderService()
    order = make_order(pickup_date=days_from_today(8))
    svc.order_repo = FakeOrderRepository(order=order)
    svc._now = lambda: cutoff_dt(order.pickup_date)

    # act: update the order quantity after the cutoff
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: response should be a 422 deadline error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Order change deadline passed"


def test_cancel_order_rejects_wrong_owner():
    # arrange: an order service with an order owned by another employee
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=2))

    # act: cancel the order as the wrong employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_order(ORDER_UUID, employee_id=1))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_cancel_order_rejects_missing_order():
    # arrange: an order service without a matching order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=None)

    # act: cancel a missing order
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_order(ORDER_UUID, employee_id=1))

    # assert: response should be a 404 not found error
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Order not found"


def test_update_order_quantity_can_decrease_inventory(monkeypatch):
    # arrange: an order service with a larger existing order
    order = make_order(quantity=3)
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    rdb = FakeRedis()
    notify_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=1))
    monkeypatch.setattr(order_service, "notify_order_quantity_updated", lambda order_id, old_quantity=None, new_quantity=None: asyncio.sleep(0, result=notify_calls.append((order_id, old_quantity, new_quantity))))

    # act: decrease the quantity from 3 to 1
    result = asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=1))

    # assert: inventory and repository should be adjusted downwards
    assert result.quantity == 1
    assert result.total_price == 120
    assert svc.inventory_repo.increment_call == (MENU_UUID, order.pickup_date, 2)
    assert svc.order_repo.update_quantity_call == (ORDER_ID, 1, 120)
    assert notify_calls == [(ORDER_ID, 3, 1)]


def test_update_order_quantity_noop_when_same_quantity(monkeypatch):
    # arrange: an order service with an order that already has the requested quantity
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(quantity=2))
    svc.inventory_repo = FakeInventoryRepository()
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: update with the same quantity
    result = asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: no inventory or repository quantity change should happen
    assert result.quantity == 2
    assert result.total_price == 240
    assert svc.order_repo.update_quantity_call is None
    assert svc.inventory_repo.increment_call is None
    assert svc.inventory_repo.decrement_call is None


def test_update_order_quantity_rejects_wrong_owner():
    # arrange: an order service with an order owned by another employee
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=2))

    # act: update the order as a different employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_update_order_quantity_rejects_missing_order():
    # arrange: an order service without a matching order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=None)

    # act: update a missing order
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: response should be a 404 not found error
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Order not found"


def test_get_vendor_order_rejects_wrong_vendor():
    # arrange: an order service with a vendor-owned order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(vendor_id=VENDOR_UUID))

    # act: fetch the order as a different vendor
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_vendor_order(ORDER_UUID, vendor_id=OTHER_VENDOR_UUID))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your vendor order"


def test_get_vendor_orders_today_overlays_cached_status(monkeypatch):
    # arrange: an order service with vendor orders and cached status override
    vendor_orders = [make_order(order_id=ORDER_ID), make_order(order_id="22222222-2222-4222-8222-222222222222", status=OrderStatus.confirmed)]
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(vendor_orders=vendor_orders)

    class SequencedRedis:
        def __init__(self):
            self.calls = []

        async def get(self, key: str):
            self.calls.append(key)
            if key.endswith(ORDER_ID):
                return "cancelled"
            return None

    rdb = SequencedRedis()
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)

    # act: fetch today's vendor orders
    result = asyncio.run(svc.get_vendor_orders_today(vendor_id=VENDOR_UUID))

    # assert: cached status should be applied to the first order
    assert result[0].status == OrderStatus.cancelled
    assert result[1].status == OrderStatus.confirmed
    assert svc.order_repo.list_today_by_vendor_call == VENDOR_UUID


def test_get_vendor_orders_history_returns_orders(monkeypatch):
    # arrange: an order service with historical vendor orders
    vendor_orders = [make_order(order_id=ORDER_ID)]
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(vendor_orders=vendor_orders)
    from_dt = datetime.combine(days_from_today(-30), time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(days_from_today(0), time.max, tzinfo=timezone.utc)
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: fetch the vendor history
    result = asyncio.run(svc.get_vendor_orders_history(vendor_id=VENDOR_UUID, from_dt=from_dt, to_dt=to_dt))

    # assert: repository should be called and return the same orders
    assert result == vendor_orders
    assert svc.order_repo.list_by_vendor_call == (VENDOR_UUID, from_dt.date(), to_dt.date())


def test_get_vendor_orders_by_vendor_user_id_filters_orders(monkeypatch):
    # arrange: an order service with vendor orders and a status filter
    vendor_orders = [
        make_order(order_id=ORDER_ID, status=OrderStatus.confirmed),
        make_order(order_id="22222222-2222-4222-8222-222222222222", status=OrderStatus.cancelled),
    ]
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(vendor_orders=vendor_orders)
    from_date = days_from_today(-30)
    to_date = tw_today()
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: fetch vendor orders by vendor user id
    result = asyncio.run(
        svc.get_vendor_orders_by_vendor_user_id(
            vendor_user_id=37,
            from_date=from_date,
            to_date=to_date,
            status="cancelled",
        )
    )

    # assert: repository should query by vendor_user_id and apply the status filter
    assert [order.status for order in result] == [OrderStatus.cancelled]
    assert svc.order_repo.list_by_vendor_user_id_call == (37, from_date, to_date)


def test_get_completed_orders_by_vendor_user_id_uses_completed_status(monkeypatch):
    # arrange: an order service with completed orders, a vendor user id, and a date range
    completed_orders = [make_order(status=OrderStatus.completed)]
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(vendor_orders=completed_orders)
    from_date = days_from_today(-30)
    to_date = tw_today()
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))

    # act: fetch completed orders for the vendor user
    result = asyncio.run(
        svc.get_completed_orders_by_vendor_user_id(
            vendor_user_id=37,
            from_date=from_date,
            to_date=to_date,
        )
    )

    # assert: repository should be called with vendor_user_id and completed status
    assert result == completed_orders
    assert svc.order_repo.list_by_vendor_user_id_and_status_call == (
        37,
        OrderStatus.completed,
        from_date,
        to_date,
    )


def test_cancel_vendor_order_updates_everything(monkeypatch):
    # arrange: an order service with a vendor-owned order and fake inventory dependencies
    order = make_order(quantity=2)
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    incr_calls = []
    publish_calls = []
    notify_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))))
    monkeypatch.setattr(order_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(order_service, "notify_order_cancelled", lambda order_id, cancel_reason=None: asyncio.sleep(0, result=notify_calls.append((order_id, cancel_reason))))

    # act: cancel the vendor order
    asyncio.run(svc.cancel_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: repository, inventory, cache, and publish event should all be updated
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.cancelled)
    assert incr_calls == [(MENU_UUID, order.pickup_date.isoformat())] * 2
    assert svc.inventory_repo.increment_call == (MENU_UUID, order.pickup_date, 2)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "cancelled", 86400)]
    assert publish_calls[0][0] == order_service.ORDER_CANCELLED
    assert publish_calls[0][1]["pickup_date"] == order.pickup_date.isoformat()
    assert publish_calls[0][1]["vendor_id"] == VENDOR_UUID
    assert publish_calls[0][1]["cancel_reason"] == "商家取消訂單"
    assert notify_calls == [(ORDER_ID, "商家取消訂單")]


def test_cancel_vendor_order_rejects_wrong_vendor():
    # arrange: an order service with another vendor's order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(vendor_id=VENDOR_UUID))

    # act: cancel the vendor order as another vendor
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_vendor_order(ORDER_UUID, vendor_id=OTHER_VENDOR_UUID))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your vendor order"


def test_cancel_vendor_order_rejects_missing_order():
    # arrange: an order service without a matching vendor order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=None)

    # act: cancel a missing order
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: response should be a 404 not found error
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Order not found"


def test_cancel_vendor_order_rejects_already_cancelled():
    # arrange: an order service with an already cancelled vendor order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(status=OrderStatus.cancelled))

    # act: cancel again
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: response should be a 422 already-cancelled error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Already cancelled"


def test_cancel_vendor_order_rejects_after_deadline():
    # arrange: an order service with a vendor order past the cutoff
    svc = OrderService()
    order = make_order(pickup_date=days_from_today(8))
    svc.order_repo = FakeOrderRepository(order=order)
    svc._now = lambda: cutoff_dt(order.pickup_date)

    # act: cancel the vendor order after the cutoff
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.cancel_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: response should be a 422 deadline error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Order change deadline passed"


def test_reject_vendor_order_delegates_to_cancel_and_fetch(monkeypatch):
    # arrange: an order service with stubbed cancel and fetch methods
    svc = OrderService()
    calls = []

    async def fake_cancel(order_id: UUID, vendor_id: UUID, cancel_reason=None):
        calls.append(("cancel", str(order_id), vendor_id, cancel_reason))

    async def fake_fetch(order_id: UUID, vendor_id: UUID):
        calls.append(("fetch", str(order_id), vendor_id))
        return make_order(order_id=order_id, vendor_id=vendor_id, status=OrderStatus.cancelled)

    svc.cancel_vendor_order = fake_cancel
    svc.get_vendor_order = fake_fetch

    # act: reject the order
    result = asyncio.run(svc.reject_vendor_order(ORDER_UUID, vendor_id=VENDOR_UUID))

    # assert: reject should call cancel first and then fetch the updated order
    assert calls == [("cancel", ORDER_ID, VENDOR_UUID, "商家取消訂單"), ("fetch", ORDER_ID, VENDOR_UUID)]
    assert result.status == OrderStatus.cancelled


def test_update_order_rejects_unsupported_role():
    # arrange: an order service with a valid order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())

    # act: update the order as an unsupported role
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.update_order(
                ORDER_UUID,
                actor={"user_id": 1, "role": "guest"},
                payload=UpdateOrderRequest(status=OrderStatus.completed),
            )
        )

    # assert: response should be a 403 unsupported-role error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Unsupported role"


def test_employee_update_order_rejects_non_cancel_status():
    # arrange: an order service with an employee-owned order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())

    # act: try to mark it completed as the employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.update_order(
                ORDER_UUID,
                actor={"user_id": 1, "role": "employee"},
                payload=UpdateOrderRequest(status=OrderStatus.completed),
            )
        )

    # assert: employees can only cancel orders
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Employees can only cancel orders"


def test_vendor_update_order_rejects_non_cancel_status():
    # arrange: an order service with a vendor-owned order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())

    # act: try to mark it completed as the vendor
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.update_order(
                ORDER_UUID,
                actor={"user_id": 7, "role": "vendor"},
                payload=UpdateOrderRequest(status=OrderStatus.completed),
            )
        )

    # assert: vendors can only reject or cancel orders
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Vendors can only reject or cancel orders"


def test_admin_update_order_cancel_uses_loaded_cancel_path(monkeypatch):
    # arrange: an order service with an order and fake cache/inventory dependencies
    order = make_order(employee_id=99, quantity=2)
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    incr_calls = []
    publish_calls = []
    notify_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))))
    monkeypatch.setattr(order_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(order_service, "notify_order_cancelled", lambda order_id, cancel_reason=None: asyncio.sleep(0, result=notify_calls.append((order_id, cancel_reason))))

    # act: cancel the order as admin
    result = asyncio.run(
        svc.update_order(
            ORDER_UUID,
            actor={"user_id": 99, "role": "admin"},
            payload=UpdateOrderRequest(status=OrderStatus.cancelled),
        )
    )

    # assert: admin cancel should use the loaded cancel path and cache cancelled
    assert result.status == OrderStatus.cancelled
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.cancelled)
    assert incr_calls == [(MENU_UUID, order.pickup_date.isoformat())] * 2
    assert svc.inventory_repo.increment_call == (MENU_UUID, order.pickup_date, 2)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "cancelled", 86400)]
    assert publish_calls[0][0] == order_service.ORDER_CANCELLED
    assert publish_calls[0][1]["pickup_date"] == order.pickup_date.isoformat()
    assert publish_calls[0][1]["cancel_reason"] == "管理員取消訂單"
    assert notify_calls == [(ORDER_ID, "管理員取消訂單")]


def test_get_order_for_actor_rejects_wrong_employee():
    # arrange: an order service with an order owned by a different employee
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())

    # act: get the order as another employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_order_for_actor(ORDER_UUID, {"user_id": 99, "role": "employee"}))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_get_order_for_actor_rejects_wrong_admin():
    # arrange: an order service with another user's order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=1))

    # act: get the order as a different admin
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_order_for_actor(ORDER_UUID, {"user_id": 99, "role": "admin"}))

    # assert: admins follow the same ownership rule as employees
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_admin_update_order_can_set_non_cancelled_status(monkeypatch):
    # arrange: an order service with an order and fake Redis cache
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=99))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)

    # act: update the order as admin to completed
    result = asyncio.run(
        svc.update_order(
            ORDER_UUID,
            actor={"user_id": 99, "role": "admin"},
            payload=UpdateOrderRequest(status=OrderStatus.completed),
        )
    )

    # assert: repository and Redis should receive the completed status
    assert result.status == OrderStatus.completed
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.completed)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "completed", 86400)]


def test_employee_can_complete_own_order(monkeypatch):
    # arrange: an order service with an employee-owned order and fake Redis cache
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=1, status=OrderStatus.confirmed))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)

    # act: complete the order as the owning employee
    result = asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 1, "role": "employee"}))

    # assert: repository and Redis should receive the completed status
    assert result.status == OrderStatus.completed
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.completed)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "completed", 86400)]


def test_admin_can_complete_own_order(monkeypatch):
    # arrange: an order service with an admin-owned order and fake Redis cache
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=99, status=OrderStatus.confirmed))
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)

    # act: complete the order as the owning admin
    result = asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 99, "role": "admin"}))

    # assert: repository and Redis should receive the completed status
    assert result.status == OrderStatus.completed
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.completed)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "completed", 86400)]


def test_complete_order_rejects_wrong_admin():
    # arrange: an order service with another user's order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=1, status=OrderStatus.confirmed))

    # act: complete the order as a different admin
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 99, "role": "admin"}))

    # assert: admins follow the same ownership rule as employees
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_complete_order_rejects_wrong_employee():
    # arrange: an order service with another employee's order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(employee_id=2, status=OrderStatus.confirmed))

    # act: complete the order as a different employee
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 1, "role": "employee"}))

    # assert: response should be a 403 ownership error
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not your order"


def test_complete_order_rejects_vendor():
    # arrange: an order service with a vendor-owned order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(vendor_user_id=7, status=OrderStatus.confirmed))

    # act: complete the order as a vendor
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 7, "role": "vendor"}))

    # assert: vendors cannot complete orders
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only employees/admin can complete orders"


def test_complete_order_rejects_cancelled_order():
    # arrange: an order service with a cancelled order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(status=OrderStatus.cancelled))

    # act: complete a cancelled order
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 1, "role": "employee"}))

    # assert: cancelled orders cannot be completed
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Cannot complete cancelled order"


def test_complete_order_rejects_already_completed_order():
    # arrange: an order service with an already completed order
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order(status=OrderStatus.completed))

    # act: complete again
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.complete_order(ORDER_UUID, actor={"user_id": 1, "role": "employee"}))

    # assert: response should be a 422 already-completed error
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Already completed"


def test_employee_update_order_can_increase_quantity(monkeypatch):
    # arrange: an order service with an employee-owned order and available inventory
    order = make_order()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    decr_calls = []
    notify_calls = []
    monkeypatch.setattr(
        order_service.rdb_mod,
        "decr_inventory",
        lambda menu_id, target_date: asyncio.sleep(0, result=decr_calls.append((menu_id, target_date)) or 5),
    )
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))
    monkeypatch.setattr(order_service, "notify_order_quantity_updated", lambda order_id, old_quantity=None, new_quantity=None: asyncio.sleep(0, result=notify_calls.append((order_id, old_quantity, new_quantity))))

    # act: update the order quantity as the owning employee
    result = asyncio.run(
        svc.update_order(
            ORDER_UUID,
            actor={"user_id": 1, "role": "employee"},
            payload=UpdateOrderRequest(quantity=3),
        )
    )

    # assert: order quantity, total price, Redis stock, and DB inventory should be updated
    assert result.quantity == 3
    assert result.total_price == 360
    assert len(decr_calls) == 2
    assert svc.inventory_repo.decrement_call == (MENU_UUID, order.pickup_date, 2)
    assert svc.order_repo.update_quantity_call == (ORDER_ID, 3, 360)
    assert notify_calls == [(ORDER_ID, 1, 3)]


def test_employee_update_order_quantity_allows_decrement_to_zero_stock(monkeypatch):
    # arrange: an order service where Redis returns 0 after taking the last item
    order = make_order(quantity=1)
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    decr_calls = []
    notify_calls = []
    monkeypatch.setattr(
        order_service.rdb_mod,
        "decr_inventory",
        lambda menu_id, target_date: asyncio.sleep(0, result=decr_calls.append((menu_id, target_date)) or 0),
    )
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: FakeRedis(cached=None))
    monkeypatch.setattr(order_service, "notify_order_quantity_updated", lambda order_id, old_quantity=None, new_quantity=None: asyncio.sleep(0, result=notify_calls.append((order_id, old_quantity, new_quantity))))

    # act: increase the order by one when only one extra item remains
    result = asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: a remaining stock value of 0 should still be a successful reservation
    assert result.quantity == 2
    assert result.total_price == 240
    assert decr_calls == [(MENU_UUID, order.pickup_date.isoformat())]
    assert svc.inventory_repo.decrement_call == (MENU_UUID, order.pickup_date, 1)
    assert svc.order_repo.update_quantity_call == (ORDER_ID, 2, 240)
    assert notify_calls == [(ORDER_ID, 1, 2)]


def test_employee_update_order_quantity_rolls_back_redis_when_db_decrement_fails(monkeypatch):
    # arrange: Redis reserves stock, but the DB inventory update affects no rows
    order = make_order(quantity=1)
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository(decrement_result=False)
    incr_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "decr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=4))
    monkeypatch.setattr(
        order_service.rdb_mod,
        "incr_inventory",
        lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))),
    )

    # act: increase the order quantity when the database inventory row cannot be updated
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.update_order_quantity(ORDER_UUID, employee_id=1, quantity=2))

    # assert: reserved Redis stock should be restored and the order should not be updated
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Inventory update failed"
    assert incr_calls == [(MENU_UUID, order.pickup_date.isoformat())]
    assert svc.inventory_repo.decrement_call == (MENU_UUID, order.pickup_date, 1)
    assert svc.order_repo.update_quantity_call is None


def test_employee_update_order_quantity_returns_conflict_when_out_of_stock(monkeypatch):
    # arrange: an order service with an employee-owned order and no extra inventory
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=make_order())
    svc.inventory_repo = FakeInventoryRepository()
    monkeypatch.setattr(order_service.rdb_mod, "decr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=-1))

    # act: update the order quantity above available inventory
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            svc.update_order(
                ORDER_UUID,
                actor={"user_id": 1, "role": "employee"},
                payload=UpdateOrderRequest(quantity=3),
            )
        )

    # assert: response should be a 409 out-of-stock error and not update the order
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Out of stock"
    assert svc.order_repo.update_quantity_call is None
    assert svc.inventory_repo.decrement_call is None


def test_vendor_update_order_can_cancel_own_order(monkeypatch):
    # arrange: an order service with a vendor-owned order and fake inventory dependencies
    order = make_order(quantity=2)
    rdb = FakeRedis()
    svc = OrderService()
    svc.order_repo = FakeOrderRepository(order=order)
    svc.inventory_repo = FakeInventoryRepository()
    incr_calls = []
    publish_calls = []
    notify_calls = []
    monkeypatch.setattr(order_service.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_service.rdb_mod, "incr_inventory", lambda menu_id, target_date: asyncio.sleep(0, result=incr_calls.append((menu_id, target_date))))
    monkeypatch.setattr(order_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(order_service, "notify_order_cancelled", lambda order_id, cancel_reason=None: asyncio.sleep(0, result=notify_calls.append((order_id, cancel_reason))))

    # act: update the order as vendor to cancelled
    result = asyncio.run(
        svc.update_order(
            ORDER_UUID,
                actor={"user_id": 7, "role": "vendor"},
            payload=UpdateOrderRequest(status=OrderStatus.cancelled),
        )
    )

    # assert: repository, inventory, Redis, and RabbitMQ should receive the cancellation
    assert result.status == OrderStatus.cancelled
    assert svc.order_repo.update_status_call == (ORDER_ID, OrderStatus.cancelled)
    assert incr_calls == [(MENU_UUID, order.pickup_date.isoformat())] * 2
    assert svc.inventory_repo.increment_call == (MENU_UUID, order.pickup_date, 2)
    assert rdb.set_calls == [(f"order:today:{ORDER_ID}", "cancelled", 86400)]
    assert publish_calls[0][0] == order_service.ORDER_CANCELLED
    assert str(publish_calls[0][1]["order_id"]) == ORDER_ID
    assert publish_calls[0][1]["pickup_date"] == order.pickup_date.isoformat()
    assert publish_calls[0][1]["cancel_reason"] == "商家取消訂單"
    assert notify_calls == [(ORDER_ID, "商家取消訂單")]
