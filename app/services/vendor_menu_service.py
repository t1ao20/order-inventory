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

    def _login_url(self) -> str:
        base_url = settings.LOGIN_SERVICE_URL.strip()
        if not base_url:
            raise HTTPException(status_code=503, detail="Login service URL is not configured")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"http://{base_url}"
        return base_url.rstrip("/")

    async def _login_admin(self) -> dict:
        url = f"{self._login_url()}/auth/login"
        payload = json.dumps(
            {
                "email": settings.ADMIN_EMAIL,
                "password": settings.ADMIN_PASSWORD,
            }
        ).encode("utf-8")
        req = request.Request(
            url=url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        def _fetch() -> dict:
            try:
                with request.urlopen(req, timeout=3) as resp:
                    body = resp.read().decode("utf-8")
            except error.HTTPError as exc:
                raise HTTPException(status_code=exc.code, detail="Failed to login admin")
            except error.URLError:
                raise HTTPException(status_code=503, detail="Login service unavailable")

            try:
                login = json.loads(body)
                if not login.get("token") or login.get("role") != "admin" or login.get("userId") is None:
                    raise ValueError
                return login
            except (TypeError, ValueError, json.JSONDecodeError):
                raise HTTPException(status_code=502, detail="Invalid login response")

        return await asyncio.to_thread(_fetch)

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

    async def get_vendor(self, vendor_id: UUID) -> dict:
        login = await self._login_admin()
        url = f"{self._base_url()}/api/v1/admin/vendors/{vendor_id}"
        req = request.Request(
            url=url,
            method="GET",
            headers={"Authorization": f"Bearer {login['token']}"},
        )

        def _fetch() -> dict:
            try:
                with request.urlopen(req, timeout=3) as resp:
                    body = resp.read().decode("utf-8")
            except error.HTTPError as exc:
                raise HTTPException(status_code=exc.code, detail="Failed to resolve vendor")
            except error.URLError:
                raise HTTPException(status_code=503, detail="Menu service unavailable")

            try:
                vendor = json.loads(body)
                if vendor.get("userId") is None:
                    raise ValueError
                return vendor
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise HTTPException(status_code=502, detail="Invalid vendor response")

        return await asyncio.to_thread(_fetch)
