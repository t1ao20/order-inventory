import asyncio
import json
from urllib import error, request
from uuid import UUID

from fastapi import HTTPException

from app.core.config import settings


class VendorMenuService:
    def _base_url(self) -> str:
        base_url = settings.MENU_SERVICE_URL.strip()
        if not base_url:
            raise HTTPException(status_code=503, detail="Menu service URL is not configured")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"http://{base_url}"
        return base_url.rstrip("/")

    async def get_current_vendor_id(self, user_id: int) -> UUID:
        url = f"{self._base_url()}/api/v1/vendors/me"
        req = request.Request(
            url=url,
            method="GET",
            headers={"x-user-id": str(user_id)},
        )

        def _fetch() -> UUID:
            try:
                with request.urlopen(req, timeout=3) as resp:
                    body = resp.read().decode("utf-8")
            except error.HTTPError as exc:
                raise HTTPException(status_code=exc.code, detail="Failed to resolve vendor id")
            except error.URLError:
                raise HTTPException(status_code=503, detail="Menu service unavailable")

            try:
                vendor = json.loads(body)
                return UUID(str(vendor["id"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise HTTPException(status_code=502, detail="Invalid vendor response")

        return await asyncio.to_thread(_fetch)
