import asyncio
import json
from uuid import UUID

from app.services.vendor_menu_service import VendorMenuService


VENDOR_UUID = UUID("00000000-0000-4000-8000-000000000007")


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"id": str(VENDOR_UUID)}).encode("utf-8")


class FakeAdminVendorResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"id": str(VENDOR_UUID), "userId": 17}).encode("utf-8")


class FakeLoginResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"token": "admin-token", "role": "admin", "userId": 14}).encode("utf-8")


class FakeMenuResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "id": str(VENDOR_UUID),
                "vendorId": str(VENDOR_UUID),
                "name": "Lunch Box",
                "price": 120,
                "tags": ["BEEF", "AMERICAN"],
            }
        ).encode("utf-8")


def test_get_current_vendor_id_calls_vendor_menu_service(monkeypatch):
    service = VendorMenuService()
    captured = {}
    monkeypatch.setattr("app.services.vendor_menu_service.settings.MENU_SERVICE_URL", "172.31.2.29:3000")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.vendor_menu_service.request.urlopen", fake_urlopen)

    result = asyncio.run(service.get_current_vendor_id(user_id=7))

    assert result == VENDOR_UUID
    assert captured["url"] == "http://172.31.2.29:3000/api/v1/vendors/me"
    assert captured["headers"]["X-user-id"] == "7"
    assert captured["timeout"] == 3


def test_get_vendor_calls_admin_vendor_endpoint(monkeypatch):
    service = VendorMenuService()
    captured = {}
    monkeypatch.setattr("app.services.vendor_menu_service.settings.MENU_SERVICE_URL", "32.236.51.177:8000")
    monkeypatch.setattr("app.services.vendor_menu_service.settings.ADMIN_USER_ID", 14)

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return FakeAdminVendorResponse()

    monkeypatch.setattr("app.services.vendor_menu_service.request.urlopen", fake_urlopen)

    result = asyncio.run(service.get_vendor(VENDOR_UUID))

    assert result == {"id": str(VENDOR_UUID), "userId": 17}
    assert captured["url"] == f"http://32.236.51.177:8000/api/v1/admin/vendors/{VENDOR_UUID}"
    assert captured["headers"]["X-user-id"] == "14"
    assert captured["headers"]["X-user-role"] == "admin"
    assert captured["timeout"] == 3


def test_get_menu_calls_public_menu_endpoint(monkeypatch):
    service = VendorMenuService()
    captured = {}
    monkeypatch.setattr("app.services.vendor_menu_service.settings.MENU_SERVICE_URL", "32.236.51.177:8000")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["timeout"] = timeout
        return FakeMenuResponse()

    monkeypatch.setattr("app.services.vendor_menu_service.request.urlopen", fake_urlopen)

    result = asyncio.run(service.get_menu(VENDOR_UUID))

    assert result["vendorId"] == str(VENDOR_UUID)
    assert result["name"] == "Lunch Box"
    assert result["price"] == 120
    assert result["tags"] == ["BEEF", "AMERICAN"]
    assert captured["url"] == f"http://32.236.51.177:8000/api/v1/menus/{VENDOR_UUID}"
    assert captured["timeout"] == 3
