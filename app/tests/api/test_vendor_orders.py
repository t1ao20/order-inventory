from datetime import date, datetime, timezone
from contextlib import contextmanager
from typing import Optional
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import vendor_orders as vendor_orders_api


ORDER_ID = "11111111-1111-4111-8111-111111111111"
HISTORY_ORDER_ID = "22222222-2222-4222-8222-222222222222"

# Test UUIDs for orders
ORDER_UUID = UUID(ORDER_ID)

# Test UUIDs
VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000007")
VENDOR_UUID_11 = UUID("00000000-0000-4000-8000-000000000011")
MENU_UUID = UUID("00000000-0000-4000-8000-000000000042")


def order_payload(order_id: UUID = ORDER_UUID, status: str = "confirmed", quantity: int = 1) -> dict:
    return {
        "id": str(order_id),
        "employee_id": 1,
        "vendor_id": str(VENDOR_UUID),
        "menu_id": str(MENU_UUID),
        "menu_name": "Lunch Box",
        "price_snapshot": 120,
        "quantity": quantity,
        "total_price": 120 * quantity,
        "order_date": date(2026, 5, 26),
        "pickup_date": date(2026, 5, 27),
        "status": status,
        "created_at": datetime(2026, 5, 26, 4, 0, tzinfo=timezone.utc),
    }


class FakeOrderService:
    def __init__(self):
        self.orders_call = None
        self.vendor_orders_call = None
        self.reject_call = None

    async def get_vendor_orders(self, vendor_id: UUID, from_date, to_date, status: Optional[str] = None) -> list[dict]:
        self.orders_call = (vendor_id, from_date, to_date, status)
        return [
            order_payload(order_id=ORDER_UUID, status="confirmed", quantity=1),
            order_payload(order_id=UUID(HISTORY_ORDER_ID), status="cancelled", quantity=2),
        ]

    async def get_vendor_orders_by_vendor_id(self, vendor_id: UUID, from_date, to_date, status: Optional[str] = None) -> list[dict]:
        self.vendor_orders_call = (vendor_id, from_date, to_date, status)
        return await self.get_vendor_orders(vendor_id, from_date, to_date, status=status)

    async def reject_vendor_order(self, order_id: UUID, vendor_id: UUID) -> dict:
        self.reject_call = (str(order_id), vendor_id)
        return order_payload(order_id=order_id, status="cancelled")


@contextmanager
def make_client(user: dict):
    app = FastAPI()
    app.include_router(vendor_orders_api.router, prefix="/vendor/orders")

    service = FakeOrderService()
    app.dependency_overrides[vendor_orders_api.get_current_user] = lambda: user
    app.dependency_overrides[vendor_orders_api.get_service] = lambda: service

    with TestClient(app) as client:
        yield client, service


def test_vendor_can_get_today_orders():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /vendor/orders?range=today request
        response = client.get("/vendor/orders", params={"range": "today"})

        # assert: response should be status code 200 and use the vendor id
        assert response.status_code == 200
        vendor_id, from_date, to_date, status = service.orders_call
        assert response.json()["range"] == "today"
        assert response.json()["count"] == 2
        assert response.json()["orders"][0]["id"] == ORDER_ID
        assert vendor_id == VENDOR_UUID
        assert from_date == today
        assert to_date == today
        assert status is None


def test_admin_can_get_vendor_order_history():
    # arrange: an authenticated admin and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "admin"}) as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /vendor/orders?range=history request
        response = client.get("/vendor/orders", params={"range": "history"})

        # assert: response should include orders, count, and parsed date filters
        vendor_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert [order["id"] for order in response.json()["orders"]] == [ORDER_ID, HISTORY_ORDER_ID]
        assert response.json()["orders"][1]["status"] == "cancelled"
        assert response.json()["orders"][1]["quantity"] == 2
        assert response.json()["orders"][1]["total_price"] == 240
        assert response.json()["range"] == "history"
        assert vendor_id == VENDOR_UUID
        assert from_date is None
        assert to_date == today
        assert status is None


def test_vendor_can_get_custom_range_and_status():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        # act: receive a GET /vendor/orders request with custom filters
        response = client.get(
            "/vendor/orders",
            params={"from": "2026-05-01", "to": "2026-05-31", "status": "completed"},
        )

        # assert: response should include the parsed custom filters
        vendor_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["range"] == "custom"
        assert response.json()["status"] == "completed"
        assert response.json()["count"] == 2
        assert vendor_id == VENDOR_UUID
        assert from_date == date(2026, 5, 1)
        assert to_date == date(2026, 5, 31)
        assert status == "completed"


def test_vendor_can_get_orders_by_vendor_id():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /vendor/orders/vendor/{vendor_id} request

        response = client.get(f"/vendor/orders/vendor/{str(VENDOR_UUID_11)}", params={"range": "today"})

        # assert: response should use the path vendor id, not the JWT user id
        vendor_id, from_date, to_date, status = service.vendor_orders_call
        assert response.status_code == 200
        assert response.json()["vendor_id"] == str(VENDOR_UUID_11)
        assert response.json()["range"] == "today"
        assert response.json()["count"] == 2
        assert vendor_id == VENDOR_UUID_11
        assert from_date == today
        assert to_date == today
        assert status is None


def test_employee_cannot_get_vendor_orders():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service):
        # act: receive a GET /vendor/orders request
        response = client.get("/vendor/orders")

        # assert: response should be status code 403 and not call the service
        assert response.status_code == 403
        assert response.json() == {"detail": "Only vendors can access vendor orders"}
        assert service.orders_call is None


def test_vendor_can_reject_order():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        # act: receive a PATCH /vendor/orders/{order_id}/reject request
        response = client.patch(f"/vendor/orders/{ORDER_ID}/reject")

        # assert: response should be status code 200 and call the reject method
        assert response.status_code == 200
        assert response.json()["id"] == ORDER_ID
        assert response.json()["status"] == "cancelled"
        assert service.reject_call == (ORDER_ID, VENDOR_UUID)
