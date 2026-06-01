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
