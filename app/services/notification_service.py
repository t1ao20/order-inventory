import asyncio
import json
import logging
from typing import Optional, Tuple
from urllib import error, request
from uuid import UUID

from app.core.config import settings
from app.models.order import Order
from app.repositories.order_repository import OrderRepository

logger = logging.getLogger(__name__)

DEFAULT_CANCEL_REASON = "商家未提供取消原因"


async def _load_order(order_id: str) -> Optional[Order]:
    try:
        return await OrderRepository().get_by_id(UUID(str(order_id)))
    except Exception as exc:
        logger.warning("Failed to load order details for notification order_id=%s: %s", order_id, exc)
        return None


def _normalize_cancel_reason(cancel_reason: Optional[str]) -> str:
    if cancel_reason is None:
        return DEFAULT_CANCEL_REASON
    reason = cancel_reason.strip()
    return reason or DEFAULT_CANCEL_REASON


def _recipient_user_ids(order: Order) -> list[int]:
    recipients = []
    for user_id in [order.employee_id, order.vendor_user_id]:
        if user_id not in recipients:
            recipients.append(user_id)
    return recipients


def _format_order_details(order_id: str, order: Order) -> list[str]:
    tags = "、".join(order.menu_tags) if order.menu_tags else "無"
    factory_zone = order.factoryZone or "無"
    return [
        f"訂單編號：{order_id}",
        f"餐點名稱：{order.menu_name}",
        f"餐點標籤：{tags}",
        f"廠區：{factory_zone}",
        f"數量：{order.quantity}",
        f"單價：{order.price_snapshot}",
        f"總金額：{order.total_price}",
        f"取餐日期：{order.pickup_date.isoformat()}",
        f"目前狀態：{order.status.value}",
    ]


def _build_created_notification(order_id: str, order: Order) -> Tuple[str, str]:
    title = f"訂單已建立：{order.menu_name}"
    content = "\n".join(
        [
            "您的訂單已建立，系統已同步更新訂單狀態。",
            "",
            *_format_order_details(order_id, order),
        ]
    )
    return title, content


def _build_quantity_updated_notification(
    order_id: str,
    order: Order,
    old_quantity: Optional[int] = None,
    new_quantity: Optional[int] = None,
) -> Tuple[str, str]:
    title = f"訂單數量已更新：{order.menu_name}"
    lines = [
        "您的訂單數量已更新，系統已同步更新訂單狀態。",
        "",
    ]
    if old_quantity is not None:
        lines.append(f"原本數量：{old_quantity}")
    if new_quantity is not None:
        lines.append(f"更新後數量：{new_quantity}")
    lines.extend(_format_order_details(order_id, order))
    return title, "\n".join(lines)


def _build_cancelled_notification(
    order_id: str,
    order: Order,
    cancel_reason: Optional[str] = None,
) -> Tuple[str, str]:
    reason = _normalize_cancel_reason(cancel_reason)
    title = f"訂單已取消：{order.menu_name}"
    content = "\n".join(
        [
            "您的訂單已被取消，系統已同步更新訂單狀態。",
            "",
            f"取消原因：{reason}",
            *_format_order_details(order_id, order),
        ]
    )
    return title, content


async def _send_notification(user_id: int, title: str, content: str, log_event: str, order_id: str) -> None:
    base_url = settings.NOTIFICATION_SERVICE_URL.strip()
    if not base_url:
        return

    url = f"{base_url.rstrip('/')}/notifications"
    payload = json.dumps(
        {
            "user_id": user_id,
            "title": title,
            "content": content,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-user-id": str(settings.ADMIN_USER_ID),
            "x-user-role": "admin",
        },
    )

    def _send() -> None:
        try:
            with request.urlopen(req, timeout=3) as resp:
                if resp.status >= 400:
                    logger.warning("Notification service returned HTTP %s for order_id=%s", resp.status, order_id)
                else:
                    logger.info(
                        "%s notification sent for order_id=%s user_id=%s status=%s",
                        log_event,
                        order_id,
                        user_id,
                        resp.status,
                    )
        except error.HTTPError as exc:
            logger.warning("Notification service returned HTTP %s for order_id=%s", exc.code, order_id)
        except error.URLError as exc:
            logger.warning("Failed to send notification for order_id=%s: %s", order_id, exc)

    await asyncio.to_thread(_send)


async def _notify_order(order_id: str, log_event: str, builder) -> None:
    if not settings.NOTIFICATION_SERVICE_URL.strip():
        return

    order = await _load_order(order_id)
    if order is None:
        logger.warning("Skip %s notification because order was not found order_id=%s", log_event, order_id)
        return

    title, content = builder(order)
    for user_id in _recipient_user_ids(order):
        await _send_notification(user_id, title, content, log_event, order_id)


async def notify_order_created(order_id: str) -> None:
    """Send creation notification without breaking main business flow on failure."""
    await _notify_order(
        order_id,
        "Created",
        lambda order: _build_created_notification(order_id, order),
    )


async def notify_order_quantity_updated(
    order_id: str,
    old_quantity: Optional[int] = None,
    new_quantity: Optional[int] = None,
) -> None:
    """Send quantity update notification without breaking main business flow on failure."""
    await _notify_order(
        order_id,
        "Quantity updated",
        lambda order: _build_quantity_updated_notification(
            order_id,
            order,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
        ),
    )


async def notify_order_cancelled(
    order_id: str,
    cancel_reason: Optional[str] = None,
) -> None:
    """Send cancellation notification without breaking main business flow on failure."""
    await _notify_order(
        order_id,
        "Cancellation",
        lambda order: _build_cancelled_notification(order_id, order, cancel_reason=cancel_reason),
    )
