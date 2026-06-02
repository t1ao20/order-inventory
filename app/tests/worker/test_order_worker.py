import asyncio

import pytest

from app.worker.order_worker import OrderWorker


class FailingOrderRepository:
    async def create(self, order):
        raise RuntimeError("db write failed")


class UnusedInventoryRepository:
    async def decrement(self, menu_id, target_date, qty):
        raise AssertionError("inventory should not be decremented after order create fails")


def test_handle_created_reraises_write_failure():
    worker = OrderWorker()
    worker.order_repo = FailingOrderRepository()
    worker.inventory_repo = UnusedInventoryRepository()
    payload = {
        "order_id": "11111111-1111-4111-8111-111111111111",
        "employee_id": 1,
        "vendor_user_id": 7,
        "vendor_id": "00000000-0000-4000-8000-000000000007",
        "menu_id": "00000000-0000-4000-8000-000000000042",
        "menu_name": "Lunch Box",
        "menu_tags": ["BEEF", "AMERICAN"],
        "price": 120,
        "quantity": 2,
        "pickup_date": "2026-06-10",
    }

    with pytest.raises(RuntimeError, match="db write failed"):
        asyncio.run(worker.handle_created(payload))
