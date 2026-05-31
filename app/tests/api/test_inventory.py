from datetime import date
from contextlib import contextmanager
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import inventory as inventory_api
from app.test_time import days_from_today, tw_today

# Test UUIDs
MENU_UUID = UUID("00000000-0000-4000-8000-000000000042")
VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000007")


class FakeInventoryService:
    def __init__(self):
        self.get_inventory_call = None
        self.set_inventory_call = None

    async def get_inventory(self, menu_id: UUID, target_date: date) -> int:
        self.get_inventory_call = (menu_id, target_date)
        return 12

    async def set_inventory(self, menu_id: UUID, target_date: date, qty: int) -> None:
        self.set_inventory_call = (menu_id, target_date, qty)


@contextmanager
def make_client(user: dict):
    app = FastAPI()
    app.include_router(inventory_api.router, prefix="/inventory")

    service = FakeInventoryService()
    app.dependency_overrides[inventory_api.get_current_user] = lambda: user
    app.dependency_overrides[inventory_api.get_service] = lambda: service

    with TestClient(app) as client:
        yield client, service


def test_get_inventory_returns_remaining_quantity():
    # arrange: an authenticated employee and a fake inventory service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service):
        # act: receive a GET /inventory/{menu_id} request
        response = client.get(f"/inventory/{str(MENU_UUID)}", params={"target_date": tw_today().isoformat()})

        # assert: response should include the remaining inventory quantity
        assert response.status_code == 200
        assert response.json() == {
            "menu_id": str(MENU_UUID),
            "date": tw_today().isoformat(),
            "remaining_quantity": 12,
        }
        assert service.get_inventory_call == (MENU_UUID, tw_today())


def test_vendor_can_set_inventory():
    # arrange: an authenticated vendor and a fake inventory service
    with make_client({"user_id": VENDOR_UUID, "role": "vendor"}) as (client, service):
        # act: receive a PUT /inventory/{menu_id} request
        response = client.put(
            f"/inventory/{str(MENU_UUID)}",
            json={"date": days_from_today(8).isoformat(), "quantity": 30},
        )

        # assert: response should be status code 200 and call set_inventory
        assert response.status_code == 200
        assert response.json() == {
            "message": "inventory updated",
            "menu_id": str(MENU_UUID),
            "date": days_from_today(8).isoformat(),
            "quantity": 30,
        }
        assert service.set_inventory_call == (MENU_UUID, days_from_today(8), 30)

def test_admin_can_set_inventory():
    # arrange: an authenticated admin and a fake inventory service
    with make_client({"user_id": 9, "role": "admin"}) as (client, service):
        # act: receive a PUT /inventory/{menu_id} request
        response = client.put(
            f"/inventory/{str(MENU_UUID)}",
            json={"date": days_from_today(8).isoformat(), "quantity": 30},
        )

        # assert: response should be status code 200 and call set_inventory
        assert response.status_code == 200
        assert response.json() == {
            "message": "inventory updated",
            "menu_id": str(MENU_UUID),
            "date": days_from_today(8).isoformat(),
            "quantity": 30,
        }
        assert service.set_inventory_call == (MENU_UUID, days_from_today(8), 30)

def test_employee_cannot_set_inventory():
    # arrange: an authenticated employee and a fake inventory service
    with make_client({"user_id": 1, "role": "employee"}) as (client, service):
        # act: receive a PUT /inventory/{menu_id} request
        response = client.put(
            f"/inventory/{str(MENU_UUID)}",
            json={"date": days_from_today(8).isoformat(), "quantity": 30},
        )

        # assert: response should be status code 403 and not call set_inventory
        assert response.status_code == 403
        assert response.json() == {"detail": "Only vendors and admins can set inventory"}
        assert service.set_inventory_call is None
