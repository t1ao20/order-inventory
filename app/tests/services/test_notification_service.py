import asyncio
import json
from datetime import date, datetime, timezone
from uuid import UUID

from app.models.order import Order, OrderStatus
from app.services import notification_service


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeOrderRepository:
    def __init__(self, order):
        self.order = order
        self.requested_order_id = None

    async def get_by_id(self, order_id: UUID):
        self.requested_order_id = order_id
        return self.order


def make_order(order_id: UUID, status: OrderStatus = OrderStatus.confirmed) -> Order:
    return Order(
        id=order_id,
        employee_id=1,
        vendor_user_id=37,
        vendor_id=UUID("22222222-2222-4222-8222-222222222222"),
        menu_id=UUID("33333333-3333-4333-8333-333333333333"),
        menu_name="招牌烤雞",
        menu_tags=["BEEF", "AMERICAN"],
        price_snapshot=109,
        quantity=2,
        total_price=218,
        order_date=date(2026, 6, 2),
        pickup_date=date(2026, 6, 10),
        status=status,
        created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        factoryZone="A廠",
    )


def setup_notification(monkeypatch, order):
    calls = []
    fake_repo = FakeOrderRepository(order)

    def fake_urlopen(req, timeout=0):
        calls.append((req, timeout))
        return FakeResponse(status=200)

    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_SERVICE_URL", "http://notify-service:3002")
    monkeypatch.setattr(notification_service.settings, "ADMIN_USER_ID", 14)
    monkeypatch.setattr(notification_service, "OrderRepository", lambda: fake_repo)
    monkeypatch.setattr(notification_service.request, "urlopen", fake_urlopen)
    return calls, fake_repo


def payloads(calls):
    return [json.loads(call[0].data.decode("utf-8")) for call in calls]


def test_notify_order_created_posts_to_employee_and_vendor(monkeypatch):
    order_id = UUID("11111111-1111-4111-8111-111111111111")
    calls, fake_repo = setup_notification(monkeypatch, make_order(order_id))

    asyncio.run(notification_service.notify_order_created(str(order_id)))

    assert fake_repo.requested_order_id == order_id
    assert len(calls) == 2
    req, timeout = calls[0]
    assert timeout == 3
    assert req.full_url == "http://notify-service:3002/notifications"
    assert req.method == "POST"
    assert req.headers["X-user-id"] == "14"
    assert req.headers["X-user-role"] == "admin"

    sent_payloads = payloads(calls)
    assert [payload["user_id"] for payload in sent_payloads] == [1, 37]
    assert sent_payloads[0]["title"] == "訂單已建立：招牌烤雞"
    assert "訂單編號：11111111-1111-4111-8111-111111111111" in sent_payloads[0]["content"]
    assert "餐點名稱：招牌烤雞" in sent_payloads[0]["content"]
    assert "廠區：A廠" in sent_payloads[0]["content"]
    assert "數量：2" in sent_payloads[0]["content"]


def test_notify_order_quantity_updated_posts_to_employee_and_vendor(monkeypatch):
    order_id = UUID("11111111-1111-4111-8111-111111111111")
    calls, fake_repo = setup_notification(monkeypatch, make_order(order_id))

    asyncio.run(notification_service.notify_order_quantity_updated(str(order_id), old_quantity=1, new_quantity=2))

    assert fake_repo.requested_order_id == order_id
    sent_payloads = payloads(calls)
    assert [payload["user_id"] for payload in sent_payloads] == [1, 37]
    assert sent_payloads[0]["title"] == "訂單數量已更新：招牌烤雞"
    assert "原本數量：1" in sent_payloads[0]["content"]
    assert "更新後數量：2" in sent_payloads[0]["content"]
    assert "廠區：A廠" in sent_payloads[0]["content"]
    assert "總金額：218" in sent_payloads[0]["content"]


def test_notify_order_cancelled_posts_to_employee_and_vendor(monkeypatch):
    order_id = UUID("11111111-1111-4111-8111-111111111111")
    calls, fake_repo = setup_notification(monkeypatch, make_order(order_id, status=OrderStatus.cancelled))

    asyncio.run(notification_service.notify_order_cancelled(str(order_id), "今日食材不足"))

    assert fake_repo.requested_order_id == order_id
    sent_payloads = payloads(calls)
    assert [payload["user_id"] for payload in sent_payloads] == [1, 37]
    assert sent_payloads[0]["title"] == "訂單已取消：招牌烤雞"
    assert "取消原因：今日食材不足" in sent_payloads[0]["content"]
    assert "餐點標籤：BEEF、AMERICAN" in sent_payloads[0]["content"]
    assert "廠區：A廠" in sent_payloads[0]["content"]
    assert "目前狀態：cancelled" in sent_payloads[0]["content"]


def test_notify_order_cancelled_skips_when_order_cannot_be_loaded(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req, timeout))
        return FakeResponse(status=200)

    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_SERVICE_URL", "http://notify-service:3002")
    monkeypatch.setattr(notification_service.settings, "ADMIN_USER_ID", 14)
    monkeypatch.setattr(notification_service.request, "urlopen", fake_urlopen)

    asyncio.run(notification_service.notify_order_cancelled("abc-order"))

    assert calls == []


def test_notify_order_cancelled_noop_without_base_url(monkeypatch):
    called = {"value": False}

    def fake_urlopen(req, timeout=0):
        called["value"] = True
        return FakeResponse(status=200)

    monkeypatch.setattr(notification_service.settings, "NOTIFICATION_SERVICE_URL", "")
    monkeypatch.setattr(notification_service.request, "urlopen", fake_urlopen)

    asyncio.run(notification_service.notify_order_cancelled("abc-order"))

    assert called["value"] is False
