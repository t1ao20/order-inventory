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


def test_get_vendor_logs_in_and_calls_admin_vendor_endpoint(monkeypatch):
    service = VendorMenuService()
    calls = []
    monkeypatch.setattr("app.services.vendor_menu_service.settings.LOGIN_SERVICE_URL", "172.31.6.25:3001")
    monkeypatch.setattr("app.services.vendor_menu_service.settings.MENU_SERVICE_URL", "32.236.51.177:8000")
    monkeypatch.setattr("app.services.vendor_menu_service.settings.ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr("app.services.vendor_menu_service.settings.ADMIN_PASSWORD", "secret")

    def fake_urlopen(req, timeout):
        calls.append(
            {
                "url": req.full_url,
                "headers": req.headers,
                "timeout": timeout,
                "data": getattr(req, "data", None),
            }
        )
        if req.full_url.endswith("/auth/login"):
            return FakeLoginResponse()
        return FakeAdminVendorResponse()

    monkeypatch.setattr("app.services.vendor_menu_service.request.urlopen", fake_urlopen)

    result = asyncio.run(service.get_vendor(VENDOR_UUID))

    assert result == {"id": str(VENDOR_UUID), "userId": 17}
    assert calls[0]["url"] == "http://172.31.6.25:3001/auth/login"
    assert calls[0]["headers"]["Content-type"] == "application/json"
    assert json.loads(calls[0]["data"].decode("utf-8")) == {
        "email": "admin@example.com",
        "password": "secret",
    }
    assert calls[0]["timeout"] == 3
    assert calls[1]["url"] == f"http://32.236.51.177:8000/api/v1/admin/vendors/{VENDOR_UUID}"
    assert calls[1]["headers"]["Authorization"] == "Bearer admin-token"
    assert calls[1]["timeout"] == 3
