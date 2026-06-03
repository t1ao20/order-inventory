from datetime import date, datetime, timezone
from contextlib import contextmanager
from typing import Optional
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import vendor_orders as vendor_orders_api
from app.test_time import days_from_today, tw_today, utc_now


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
        "vendor_user_id": 7,
        "vendor_id": str(VENDOR_UUID),
        "menu_id": str(MENU_UUID),
        "menu_name": "Lunch Box",
        "price_snapshot": 120,
        "quantity": quantity,
        "total_price": 120 * quantity,
        "order_date": tw_today(),
        "pickup_date": days_from_today(8),
        "status": status,
        "created_at": utc_now(),
    }


class FakeOrderService:
    def __init__(self):
        self.orders_call = None
        self.vendor_orders_call = None
        self.completed_vendor_orders_call = None
        self.reject_call = None

    async def get_vendor_orders(self, vendor_id: UUID, from_date, to_date, status: Optional[str] = None) -> list[dict]:
        self.orders_call = (vendor_id, from_date, to_date, status)
        return [
            order_payload(order_id=ORDER_UUID, status="confirmed", quantity=1),
            order_payload(order_id=UUID(HISTORY_ORDER_ID), status="cancelled", quantity=2),
        ]

    async def get_vendor_orders_by_vendor_user_id(self, vendor_user_id: int, from_date, to_date, status: Optional[str] = None) -> list[dict]:
        self.vendor_orders_call = (vendor_user_id, from_date, to_date, status)
        return [
            order_payload(order_id=ORDER_UUID, status="confirmed", quantity=1),
            order_payload(order_id=UUID(HISTORY_ORDER_ID), status="cancelled", quantity=2),
        ]

    async def get_completed_orders_by_vendor_user_id(self, vendor_user_id: int, from_date, to_date) -> list[dict]:
        self.completed_vendor_orders_call = (vendor_user_id, from_date, to_date)
        return [
            order_payload(order_id=ORDER_UUID, status="completed", quantity=1),
            order_payload(order_id=UUID(HISTORY_ORDER_ID), status="completed", quantity=2),
        ]

    async def reject_vendor_order(self, order_id: UUID, vendor_id: UUID, cancel_reason=None) -> dict:
        self.reject_call = (str(order_id), vendor_id, cancel_reason)
        return order_payload(order_id=order_id, status="cancelled")


class FakeVendorMenuService:
    def __init__(self):
        self.current_vendor_call = None

    async def get_current_vendor_id(self, user_id: int) -> UUID:
        self.current_vendor_call = user_id
        return VENDOR_UUID


@contextmanager
def make_client(user: dict):
    app = FastAPI()
    app.include_router(vendor_orders_api.router, prefix="/vendor/orders")

    service = FakeOrderService()
    vendor_menu_service = FakeVendorMenuService()
    app.dependency_overrides[vendor_orders_api.get_current_user] = lambda: user
    app.dependency_overrides[vendor_orders_api.get_service] = lambda: service
    app.dependency_overrides[vendor_orders_api.get_vendor_menu_service] = lambda: vendor_menu_service

    with TestClient(app) as client:
        yield client, service, vendor_menu_service


def test_vendor_can_get_today_orders():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        today = tw_today()
        # act: receive a GET /vendor/orders?range=today request
        response = client.get("/vendor/orders", params={"range": "today"})

        # assert: response should be status code 200 and use the vendor id
        assert response.status_code == 200
        vendor_id, from_date, to_date, status = service.orders_call
        assert response.json()["range"] == "today"
        assert response.json()["count"] == 2
        assert response.json()["orders"][0]["id"] == ORDER_ID
        assert vendor_id == VENDOR_UUID
        assert vendor_menu_service.current_vendor_call == 7
        assert from_date == today
        assert to_date == today
        assert status is None


def test_admin_can_get_vendor_order_history():
    # arrange: an authenticated admin and a fake order service
    with make_client({"user_id": 7, "role": "admin"}) as (client, service, vendor_menu_service):
        today = tw_today()
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
        assert vendor_menu_service.current_vendor_call == 7
        assert from_date is None
        assert to_date == today
        assert status is None


def test_vendor_can_get_custom_range_and_status():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        # act: receive a GET /vendor/orders request with custom filters
        response = client.get(
            "/vendor/orders",
            params={"from": days_from_today(-30).isoformat(), "to": tw_today().isoformat(), "status": "completed"},
        )

        # assert: response should include the parsed custom filters
        vendor_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["range"] == "custom"
        assert response.json()["status"] == "completed"
        assert response.json()["count"] == 2
        assert vendor_id == VENDOR_UUID
        assert vendor_menu_service.current_vendor_call == 7
        assert from_date == days_from_today(-30)
        assert to_date == tw_today()
        assert status == "completed"


def test_admin_can_get_completed_orders_by_vendor_user_id():
    # arrange: an authenticated admin, vendor user id, and completed-order date range
    with make_client({"user_id": 14, "role": "admin"}) as (client, service, vendor_menu_service):
        # act: receive a GET /vendor/orders/completed/{vendor_user_id} request
        response = client.get(
            "/vendor/orders/completed/37",
            params={"from": days_from_today(-30).isoformat(), "to": tw_today().isoformat()},
        )

        # assert: response should only expose the completed-order admin query for the selected vendor user
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["vendor_user_id"] == 37
        assert response.json()["count"] == 2
        assert [order["status"] for order in response.json()["orders"]] == ["completed", "completed"]
        assert service.completed_vendor_orders_call == (37, days_from_today(-30), tw_today())
        assert service.orders_call is None
        assert vendor_menu_service.current_vendor_call is None


def test_vendor_cannot_get_completed_orders_by_vendor_user_id():
    # arrange: a non-admin vendor
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        # act: receive a GET /vendor/orders/completed/{vendor_user_id} request
        response = client.get("/vendor/orders/completed/37")

        # assert: response should be forbidden and not call services
        assert response.status_code == 403
        assert response.json() == {"detail": "Only admins can access completed orders"}
        assert service.completed_vendor_orders_call is None
        assert vendor_menu_service.current_vendor_call is None


def test_admin_can_get_orders_by_vendor_user_id():
    # arrange: an authenticated admin and a fake order service
    with make_client({"user_id": 7, "role": "admin"}) as (client, service, vendor_menu_service):
        today = tw_today()
        # act: receive a GET /vendor/orders/vendor/{vendor_user_id} request

        response = client.get("/vendor/orders/vendor/11", params={"range": "today"})

        # assert: response should use the path vendor user id, not the JWT user id
        vendor_user_id, from_date, to_date, status = service.vendor_orders_call
        assert response.status_code == 200
        assert response.json()["vendor_user_id"] == 11
        assert response.json()["range"] == "today"
        assert response.json()["count"] == 2
        assert vendor_user_id == 11
        assert vendor_menu_service.current_vendor_call is None
        assert from_date == today
        assert to_date == today
        assert status is None


def test_vendor_can_get_own_orders_by_vendor_user_id():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        response = client.get("/vendor/orders/vendor/7", params={"range": "today"})

        vendor_user_id, _, _, _ = service.vendor_orders_call
        assert response.status_code == 200
        assert response.json()["vendor_user_id"] == 7
        assert vendor_user_id == 7
        assert vendor_menu_service.current_vendor_call is None


def test_vendor_cannot_get_other_vendor_user_orders():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        response = client.get("/vendor/orders/vendor/11", params={"range": "today"})

        assert response.status_code == 403
        assert response.json() == {"detail": "Not your vendor orders"}
        assert service.vendor_orders_call is None
        assert vendor_menu_service.current_vendor_call is None


def test_employee_cannot_get_vendor_orders():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service, vendor_menu_service):
        # act: receive a GET /vendor/orders request
        response = client.get("/vendor/orders")

        # assert: response should be status code 403 and not call the service
        assert response.status_code == 403
        assert response.json() == {"detail": "Only vendors/admin can access vendor orders"}
        assert service.orders_call is None
        assert vendor_menu_service.current_vendor_call is None


def test_vendor_can_reject_order():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": 7, "role": "vendor"}) as (client, service, vendor_menu_service):
        # act: receive a PATCH /vendor/orders/{order_id}/reject request
        response = client.patch(f"/vendor/orders/{ORDER_ID}/reject")

        # assert: response should be status code 200 and call the reject method
        assert response.status_code == 200
        assert response.json()["id"] == ORDER_ID
        assert response.json()["status"] == "cancelled"
        assert service.reject_call == (ORDER_ID, VENDOR_UUID, None)
        assert vendor_menu_service.current_vendor_call == 7
