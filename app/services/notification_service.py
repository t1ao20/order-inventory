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


def _build_cancelled_notification(
    order_id: str,
    order: Optional[Order],
    cancel_reason: Optional[str] = None,
) -> Tuple[str, str]:
    reason = _normalize_cancel_reason(cancel_reason)
    if order is None:
        return (
            f"通知訂單 {order_id} 取消",
            "\n".join(
                [
                    "您的訂單已被取消，系統已同步更新訂單狀態。",
                    "",
                    f"訂單編號：{order_id}",
                    f"取消原因：{reason}",
                ]
            ),
        )

    tags = "、".join(order.menu_tags) if order.menu_tags else "無"
    title = f"您的訂單已取消：{order.menu_name}"
    content = "\n".join(
        [
            "您的訂單已被取消，系統已同步更新訂單狀態。",
            "",
            f"訂單編號：{order_id}",
            f"取消原因：{reason}",
            f"餐點名稱：{order.menu_name}",
            f"數量：{order.quantity}",
            f"單價：{order.price_snapshot}",
            f"總金額：{order.total_price}",
            f"取餐日期：{order.pickup_date.isoformat()}",
        ]
    )
    return title, content


async def notify_order_cancelled(
    order_id: str,
    user_id: int,
    cancel_reason: Optional[str] = None,
) -> None:
    """Send cancellation notification without breaking main business flow on failure."""
    base_url = settings.NOTIFICATION_SERVICE_URL.strip()
    if not base_url:
        return

    order = await _load_order(order_id)
    title, content = _build_cancelled_notification(order_id, order, cancel_reason=cancel_reason)
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
                        "Cancellation notification sent for order_id=%s user_id=%s status=%s",
                        order_id,
                        user_id,
                        resp.status,
                    )
        except error.HTTPError as exc:
            logger.warning("Notification service returned HTTP %s for order_id=%s", exc.code, order_id)
        except error.URLError as exc:
            logger.warning("Failed to send cancellation notification for order_id=%s: %s", order_id, exc)

    await asyncio.to_thread(_send)
