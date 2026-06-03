import asyncio
from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.models.order import DailyInventory, Order, OrderStatus
from app.services import inventory_service
from app.services.inventory_service import InventoryService
from app.test_time import days_from_today, tw_now, tw_today, utc_now

# Test UUIDs
MENU_UUID = UUID("00000000-0000-4000-8000-000000000042")


class FakeRedis:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []
        self.eval_calls = []

    async def get(self, key: str):
        return self.cached

    async def set(self, key: str, value, ex: int):
        self.set_calls.append((key, value, ex))

    async def eval(self, script: str, numkeys: int, key: str):
        self.eval_calls.append((script, numkeys, key))
        return 1


class FakeInventoryRepository:
    def __init__(self, item=None):
        self.item = item
        self.get_call = None
        self.upsert_call = None
        self.increment_calls = []

    async def get(self, menu_id: UUID, target_date: date):
        self.get_call = (menu_id, target_date)
        return self.item

    async def upsert(self, menu_id: UUID, target_date: date, qty: int):
        self.upsert_call = (menu_id, target_date, qty)
        if self.item is not None:
            self.item.max_quantity = qty
            self.item.remaining_quantity = qty - self.item.sold_quantity

    async def increment(self, menu_id: UUID, target_date: date, qty: int = 1):
        self.increment_calls.append((menu_id, target_date, qty))
        if self.item is not None:
            self.item.sold_quantity = max(self.item.sold_quantity - qty, 0)
            self.item.remaining_quantity = self.item.max_quantity - self.item.sold_quantity


class FakeOrderRepository:
    def __init__(self, orders=None):
        self.orders = orders or []
        self.list_confirmed_call = None
        self.update_status_calls = []

    async def list_confirmed_by_menu_and_date(self, menu_id: UUID, target_date: date):
        self.list_confirmed_call = (menu_id, target_date)
        return self.orders

    async def update_status(self, order_id: UUID, status: OrderStatus):
        self.update_status_calls.append((order_id, status))


def make_order(order_id: UUID, quantity: int, created_minutes_ago: int) -> Order:
    return Order(
        id=order_id,
        employee_id=1,
        vendor_user_id=7,
        vendor_id=UUID("00000000-0000-4000-8000-000000000007"),
        menu_id=MENU_UUID,
        menu_name="Lunch Box",
        price_snapshot=120,
        quantity=quantity,
        total_price=120 * quantity,
        order_date=tw_today(),
        pickup_date=days_from_today(8),
        status=OrderStatus.confirmed,
        created_at=utc_now() - timedelta(minutes=created_minutes_ago),
    )


def test_get_inventory_returns_cached_quantity(monkeypatch):
    # arrange: an inventory service with Redis containing cached stock
    rdb = FakeRedis(cached="8")
    svc = InventoryService()
    svc.repo = FakeInventoryRepository()
    monkeypatch.setattr(inventory_service.rdb_mod, "get_redis", lambda: rdb)

    # act: get inventory for a menu and date
    result = asyncio.run(svc.get_inventory(MENU_UUID, days_from_today(-5)))

    # assert: result should come from Redis and not call the repository
    assert result == 8
    assert svc.repo.get_call is None


def test_get_inventory_warms_cache_on_miss(monkeypatch):
    # arrange: an inventory service with Redis cache miss and DB inventory
    rdb = FakeRedis(cached=None)
    inv = DailyInventory(
        id=1,
        menu_id=MENU_UUID,
        target_date=days_from_today(-5),
        max_quantity=20,
        sold_quantity=8,
        remaining_quantity=12,
    )
    svc = InventoryService()
    svc.repo = FakeInventoryRepository(item=inv)
    monkeypatch.setattr(inventory_service.rdb_mod, "get_redis", lambda: rdb)

    # act: get inventory for a menu and date
    result = asyncio.run(svc.get_inventory(MENU_UUID, days_from_today(-5)))

    # assert: result should come from DB and warm Redis
    assert result == 12
    assert svc.repo.get_call == (MENU_UUID, days_from_today(-5))
    assert rdb.set_calls == [(f"inventory:{str(MENU_UUID)}:{days_from_today(-5).isoformat()}", 12, 600)]


def test_get_inventory_raises_when_missing(monkeypatch):
    # arrange: an inventory service with Redis cache miss and no DB inventory
    rdb = FakeRedis(cached=None)
    svc = InventoryService()
    svc.repo = FakeInventoryRepository(item=None)
    monkeypatch.setattr(inventory_service.rdb_mod, "get_redis", lambda: rdb)

    # act: get inventory for a menu and date
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(svc.get_inventory(MENU_UUID, days_from_today(-5)))

    # assert: response should be a 404 missing inventory error
    assert exc_info.value.status_code == 404
    assert f"No inventory for menu {str(MENU_UUID)}" in exc_info.value.detail


def test_set_inventory_upserts_and_warms_cache(monkeypatch):
    # arrange: an inventory service with fake repository and Redis
    rdb = FakeRedis()
    svc = InventoryService()
    svc.repo = FakeInventoryRepository()
    svc._now = tw_now
    monkeypatch.setattr(inventory_service.rdb_mod, "get_redis", lambda: rdb)

    # act: set inventory for a menu and date
    asyncio.run(svc.set_inventory(MENU_UUID, days_from_today(8), 30))

    # assert: repository should be updated and Redis should be warmed
    assert svc.repo.upsert_call == (MENU_UUID, days_from_today(8), 30)
    assert rdb.set_calls[0][0] == f"inventory:{str(MENU_UUID)}:{days_from_today(8).isoformat()}"
    assert rdb.set_calls[0][1] == 30
    assert rdb.set_calls[0][2] >= 1


def test_set_inventory_cancels_latest_orders_when_lowering_max(monkeypatch):
    # arrange: an inventory service where 45 items are already sold
    rdb = FakeRedis()
    inv = DailyInventory(
        id=1,
        menu_id=MENU_UUID,
        target_date=days_from_today(8),
        max_quantity=50,
        sold_quantity=45,
        remaining_quantity=5,
    )
    svc = InventoryService()
    svc.repo = FakeInventoryRepository(item=inv)
    svc.order_repo = FakeOrderRepository(
        orders=[
            make_order(UUID("00000000-0000-4000-8000-000000000103"), 1, 0),
            make_order(UUID("00000000-0000-4000-8000-000000000203"), 3, 1),
            make_order(UUID("00000000-0000-4000-8000-000000000303"), 3, 2),
        ]
    )
    monkeypatch.setattr(inventory_service.rdb_mod, "get_redis", lambda: rdb)
    publish_calls = []
    notify_calls = []
    monkeypatch.setattr(inventory_service.mq_mod, "publish", lambda routing_key, payload: asyncio.sleep(0, result=publish_calls.append((routing_key, payload))))
    monkeypatch.setattr(inventory_service, "notify_order_cancelled", lambda order_id, cancel_reason=None: asyncio.sleep(0, result=notify_calls.append((order_id, cancel_reason))))

    # act: lower the max inventory from 50 to 40
    asyncio.run(svc.set_inventory(MENU_UUID, days_from_today(8), 40))

    # assert: latest orders should be cancelled until the overflow is covered
    assert svc.order_repo.list_confirmed_call == (MENU_UUID, days_from_today(8))
    assert svc.order_repo.update_status_calls == [
        (UUID("00000000-0000-4000-8000-000000000103"), OrderStatus.cancelled),
        (UUID("00000000-0000-4000-8000-000000000203"), OrderStatus.cancelled),
        (UUID("00000000-0000-4000-8000-000000000303"), OrderStatus.cancelled),
    ]
    assert svc.repo.increment_calls == [
        (MENU_UUID, days_from_today(8), 1),
        (MENU_UUID, days_from_today(8), 3),
        (MENU_UUID, days_from_today(8), 3),
    ]
    assert svc.repo.upsert_call == (MENU_UUID, days_from_today(8), 40)
    assert svc.repo.item.sold_quantity == 38
    assert svc.repo.item.remaining_quantity == 2
    assert rdb.set_calls[-1] == (f"inventory:{str(MENU_UUID)}:{days_from_today(8).isoformat()}", 2, rdb.set_calls[-1][2])
    assert publish_calls[0][0] == "order.cancelled"
    assert notify_calls == [
        ("00000000-0000-4000-8000-000000000103", "庫存調整，餐點數量不足"),
        ("00000000-0000-4000-8000-000000000203", "庫存調整，餐點數量不足"),
        ("00000000-0000-4000-8000-000000000303", "庫存調整，餐點數量不足"),
    ]
