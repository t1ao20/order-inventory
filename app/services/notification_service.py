import asyncio
import json
import logging
from urllib import error, request

from app.core.config import settings

logger = logging.getLogger(__name__)


async def notify_order_cancelled(order_id: str, user_id: int) -> None:
    """Send cancellation notification without breaking main business flow on failure."""
    base_url = settings.NOTIFICATION_SERVICE_URL.strip()
    if not base_url:
        return

    url = f"{base_url.rstrip('/')}/notifications"
    payload = json.dumps(
        {
            "user_id": user_id,
            "title": f"通知訂單{order_id}取消",
            "content": "您的訂單已被商家取消",
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    def _send() -> None:
        try:
            with request.urlopen(req, timeout=3) as resp:
                if resp.status >= 400:
                    logger.warning("Notification service returned HTTP %s for order_id=%s", resp.status, order_id)
        except error.URLError as exc:
            logger.warning("Failed to send cancellation notification for order_id=%s: %s", order_id, exc)

    await asyncio.to_thread(_send)
