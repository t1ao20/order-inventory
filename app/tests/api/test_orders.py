from datetime import date, datetime, timezone
from contextlib import contextmanager
from typing import Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import orders as orders_api


ORDER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ORDER_ID = "77777777-7777-4777-8777-777777777777"
HISTORY_ORDER_ID = "22222222-2222-4222-8222-222222222222"

# Test UUIDs
VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000007")
MENU_UUID = UUID("00000000-0000-4000-8000-000000000042")

ORDER_UUID = UUID(ORDER_ID)


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
        self.create_order_call = None
        self.orders_call = None
        self.employee_orders_call = None
        self.get_order_call = None
        self.cancel_order_call = None
        self.update_quantity_call = None
        self.create_order_error = None

    async def create_order(self, req, employee_id: int) -> dict:
        self.create_order_call = (req, employee_id)
        if self.create_order_error:
            raise self.create_order_error
        return {"order_id": ORDER_ID, "status": "pending", "message": "order queued"}

    async def get_today_order(self, employee_id: int) -> dict:
        return order_payload()

    async def get_orders(self, employee_id: int, from_date, to_date, status=None) -> list[dict]:
        self.orders_call = (employee_id, from_date, to_date, status)
        return [
            order_payload(order_id=ORDER_ID, status="confirmed", quantity=1),
            order_payload(order_id=HISTORY_ORDER_ID, status="cancelled", quantity=2),
        ]

    async def get_orders_by_employee_id(self, employee_id: int, from_date, to_date, status=None) -> list[dict]:
        self.employee_orders_call = (employee_id, from_date, to_date, status)
        return await self.get_orders(employee_id, from_date, to_date, status=status)

    async def get_order_for_actor(self, order_id: UUID, actor: dict) -> dict:
        self.get_order_call = (str(order_id), actor)
        return order_payload(order_id)

    async def update_order_quantity(self, order_id: UUID, employee_id: int, quantity: int) -> dict:
        self.update_quantity_call = (str(order_id), employee_id, quantity)
        updated = order_payload(order_id)
        updated["quantity"] = quantity
        updated["total_price"] = 120 * quantity
        return updated
    async def cancel_order(self, order_id: UUID, employee_id: int) -> None:
        self.cancel_order_call = (str(order_id), employee_id)

    async def reject_vendor_order(self, order_id: UUID, vendor_id: UUID) -> dict:
        raise HTTPException(status_code=500, detail="not used in employee tests")
    


@contextmanager
def make_client(user: Optional[dict] = None):
    app = FastAPI()
    app.include_router(orders_api.router, prefix="/orders")

    service = FakeOrderService()
    app.dependency_overrides[orders_api.get_current_user] = lambda: user or {
        "user_id": 1,
        "role": "employee",
    }
    app.dependency_overrides[orders_api.get_service] = lambda: service

    with TestClient(app) as client:
        yield client, service


def test_create_order_uses_authenticated_employee():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 9, "role": "employee"}) as (client, service):
        # act: receive a POST /orders request
        response = client.post(
            "/orders",
            json={
                "vendor_id": str(VENDOR_UUID),
                "menu_id": str(MENU_UUID),
                "menu_name": "Lunch Box",
                "price": 120,
                "quantity": 2,
                "pickup_date": "2026-05-27",
            },
        )

        # assert: response should be status code 201 and use the authenticated employee id
        req, employee_id = service.create_order_call
        assert response.status_code == 201
        assert response.json() == {
            "order_id": ORDER_ID,
            "status": "pending",
            "message": "order queued",
        }
        assert employee_id == 9
        assert req.menu_id == MENU_UUID


def test_create_order_returns_conflict_when_out_of_stock():
    # arrange: an authenticated employee and a fake order service returning out of stock
    with make_client({"user_id": 9, "role": "employee"}) as (client, service):
        service.create_order_error = HTTPException(status_code=409, detail="Out of stock")

        # act: receive a POST /orders request
        response = client.post(
            "/orders",
            json={
                "vendor_id": str(VENDOR_UUID),
                "menu_id": str(MENU_UUID),
                "menu_name": "Lunch Box",
                "price": 120,
                "quantity": 2,
                "pickup_date": "2026-05-27",
            },
        )

        # assert: response should be status code 409 and explain the stock problem
        req, employee_id = service.create_order_call
        assert response.status_code == 409
        assert response.json() == {"detail": "Out of stock"}
        assert employee_id == 9
        assert req.menu_id == MENU_UUID


def test_get_me_returns_current_order():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, _):
        # act: receive a GET /orders/me?range=today request
        response = client.get("/orders/me", params={"range": "today"})

        # assert: response should be status code 200 and return the current order
        assert response.status_code == 200
        assert response.json()["range"] == "today"
        assert response.json()["count"] == 2
        assert response.json()["status"] is None
        assert response.json()["orders"][0]["id"] == ORDER_ID
        assert response.json()["orders"][0]["status"] == "confirmed"


def test_get_me_history_returns_orders_and_count():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /orders/me?range=history request
        response = client.get("/orders/me", params={"range": "history"})

        # assert: response should include orders, count, and parsed date filters
        employee_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert response.json()["status"] is None
        assert [order["id"] for order in response.json()["orders"]] == [ORDER_ID, HISTORY_ORDER_ID]
        assert response.json()["orders"][1]["status"] == "cancelled"
        assert response.json()["orders"][1]["quantity"] == 2
        assert response.json()["orders"][1]["total_price"] == 240
        assert employee_id == 1
        assert from_date is None
        assert to_date == today
        assert status is None


def test_get_me_upcoming_uses_open_ended_from_today():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /orders/me?range=upcoming request
        response = client.get("/orders/me", params={"range": "upcoming"})

        # assert: response should include orders and an open-ended future filter
        employee_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["range"] == "upcoming"
        assert response.json()["count"] == 2
        assert response.json()["status"] is None
        assert employee_id == 1
        assert from_date == today
        assert to_date is None
        assert status is None


def test_get_me_custom_range_uses_query_dates():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, service):
        # act: receive a GET /orders/me request with custom date bounds
        response = client.get(
            "/orders/me",
            params={"from": "2026-05-01", "to": "2026-05-31", "status": "confirmed"},
        )

        # assert: response should include the parsed custom date range
        employee_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["range"] == "custom"
        assert response.json()["count"] == 2
        assert response.json()["status"] == "confirmed"
        assert employee_id == 1
        assert from_date == date(2026, 5, 1)
        assert to_date == date(2026, 5, 31)
        assert status == "confirmed"


def test_get_orders_by_employee_id_uses_path_employee_id():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 9, "role": "employee"}) as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /orders/employee/{employee_id} request
        response = client.get("/orders/employee/3", params={"range": "history", "status": "cancelled"})

        # assert: response should use the path employee id, not the JWT user id
        employee_id, from_date, to_date, status = service.employee_orders_call
        assert response.status_code == 200
        assert response.json()["employee_id"] == 3
        assert response.json()["range"] == "history"
        assert response.json()["count"] == 2
        assert response.json()["status"] == "cancelled"
        assert employee_id == 3
        assert from_date is None
        assert to_date == today
        assert status == "cancelled"


def test_get_me_can_filter_by_status():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /orders/me request with a status filter
        response = client.get("/orders/me", params={"status": "cancelled"})

        # assert: response should include the status filter and pass it to the service
        employee_id, from_date, to_date, status = service.orders_call
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert employee_id == 1
        assert from_date == today
        assert to_date == today
        assert status == "cancelled"


def test_get_orders_by_employee_id_can_filter_by_status():
    # arrange: an authenticated employee and a fake order service
    with make_client() as (client, service):
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        # act: receive a GET /orders/employee/{employee_id} request with a status filter
        response = client.get("/orders/employee/3", params={"status": "confirmed"})

        # assert: response should include the status filter and pass it to the service
        employee_id, from_date, to_date, status = service.employee_orders_call
        assert response.status_code == 200
        assert response.json()["status"] == "confirmed"
        assert response.json()["employee_id"] == 3
        assert employee_id == 3
        assert from_date == today
        assert to_date == today
        assert status == "confirmed"


def test_get_order_uses_actor_context():
    # arrange: an authenticated vendor and a fake order service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        # act: receive a GET /orders/{order_id} request
        response = client.get(f"/orders/{OTHER_ORDER_ID}")

        # assert: response should pass the actor context to the service
        assert response.status_code == 200
        assert response.json()["id"] == OTHER_ORDER_ID
        assert service.get_order_call == (OTHER_ORDER_ID, {"user_id": VENDOR_UUID, "role": "vendor"})


def test_employee_can_cancel_own_order():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service):
        # act: receive a PATCH /orders/{order_id}/cancel request
        response = client.patch(f"/orders/{OTHER_ORDER_ID}/cancel")

        # assert: response should return the current order and call the cancel method
        assert response.status_code == 200
        assert response.json()["id"] == OTHER_ORDER_ID
        assert response.json()["status"] == "confirmed"
        assert service.cancel_order_call == (OTHER_ORDER_ID, 1)


def test_employee_can_update_order_quantity_through_patch():
    # arrange: an authenticated employee and a fake order service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service):
        # act: receive a PATCH /orders/{order_id}/quantity request with quantity only
        response = client.patch(f"/orders/{OTHER_ORDER_ID}/quantity", json={"quantity": 3})

        # assert: response should be status code 200 and return the updated quantity
        order_id, employee_id, quantity = service.update_quantity_call
        assert response.status_code == 200
        assert response.json()["quantity"] == 3
        assert response.json()["total_price"] == 360
        assert order_id == OTHER_ORDER_ID
        assert employee_id == 1
        assert quantity == 3
