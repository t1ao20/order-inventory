import asyncio
import json

from app.services import notification_service


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_notify_order_cancelled_posts_expected_payload(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req, timeout))
        return FakeResponse(status=200)

    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_SERVICE_URL", "http://notify-service:3002")
    monkeypatch.setattr(notification_service.request, "urlopen", fake_urlopen)

    asyncio.run(notification_service.notify_order_cancelled("abc-order", 1))

    assert len(calls) == 1
    req, timeout = calls[0]
    assert timeout == 3
    assert req.full_url == "http://notify-service:3002/notifications"
    assert req.method == "POST"

    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {
        "user_id": 1,
        "title": "通知訂單abc-order取消",
        "content": "您的訂單已被商家取消",
    }


def test_notify_order_cancelled_noop_without_base_url(monkeypatch):
    called = {"value": False}

    def fake_urlopen(req, timeout=0):
        called["value"] = True
        return FakeResponse(status=200)

    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_SERVICE_URL", "")
    monkeypatch.setattr(notification_service.request, "urlopen", fake_urlopen)

    asyncio.run(notification_service.notify_order_cancelled("abc-order", 1))

    assert called["value"] is False
