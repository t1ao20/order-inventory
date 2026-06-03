import asyncio

import pytest

from app.worker import order_worker
from app.worker.order_worker import OrderWorker


class FailingOrderRepository:
    async def create(self, order):
        raise RuntimeError("db write failed")


class UnusedInventoryRepository:
    async def decrement(self, menu_id, target_date, qty):
        raise AssertionError("inventory should not be decremented after order create fails")


class FakeOrderRepository:
    def __init__(self):
        self.created_order = None

    async def create(self, order):
        self.created_order = order


class FakeInventoryRepository:
    def __init__(self):
        self.decrement_call = None

    async def decrement(self, menu_id, target_date, qty):
        self.decrement_call = (menu_id, target_date, qty)
        return True


class FakeRedis:
    def __init__(self):
        self.set_calls = []

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))


def make_payload():
    return {
        "order_id": "11111111-1111-4111-8111-111111111111",
        "employee_id": 1,
        "vendor_user_id": 7,
        "vendor_id": "00000000-0000-4000-8000-000000000007",
        "menu_id": "00000000-0000-4000-8000-000000000042",
        "menu_name": "Lunch Box",
        "menu_tags": ["BEEF", "AMERICAN"],
        "factoryZone": "A廠",
        "price": 120,
        "quantity": 2,
        "pickup_date": "2026-06-10",
    }


def test_handle_created_writes_order_and_notifies(monkeypatch):
    worker = OrderWorker()
    worker.order_repo = FakeOrderRepository()
    worker.inventory_repo = FakeInventoryRepository()
    rdb = FakeRedis()
    notify_calls = []
    monkeypatch.setattr(order_worker.rdb_mod, "get_redis", lambda: rdb)
    monkeypatch.setattr(order_worker, "notify_order_created", lambda order_id: asyncio.sleep(0, result=notify_calls.append(order_id)))

    asyncio.run(worker.handle_created(make_payload()))

    assert str(worker.order_repo.created_order.id) == "11111111-1111-4111-8111-111111111111"
    assert worker.order_repo.created_order.status == "confirmed"
    assert worker.order_repo.created_order.factoryZone == "A廠"
    assert worker.inventory_repo.decrement_call[2] == 2
    assert rdb.set_calls == [("order:today:11111111-1111-4111-8111-111111111111", "confirmed", 86400)]
    assert notify_calls == ["11111111-1111-4111-8111-111111111111"]


def test_handle_created_reraises_write_failure():
    worker = OrderWorker()
    worker.order_repo = FailingOrderRepository()
    worker.inventory_repo = UnusedInventoryRepository()

    with pytest.raises(RuntimeError, match="db write failed"):
        asyncio.run(worker.handle_created(make_payload()))
