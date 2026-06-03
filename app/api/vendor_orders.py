from datetime import date, datetime
from typing import Annotated, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.order import CancelOrderRequest, Order
from app.services.order_service import OrderService
from app.services.vendor_menu_service import VendorMenuService

router = APIRouter()
TW_TZ = ZoneInfo("Asia/Taipei")


def get_service() -> OrderService:
    return OrderService()


def get_vendor_menu_service() -> VendorMenuService:
    return VendorMenuService()


def require_vendor(user: dict) -> None:
    if user["role"] != "vendor":
        raise HTTPException(status_code=403, detail="Only vendors can access vendor orders")

def require_vendor_or_admin(user: dict) -> None:
    if user["role"] not in ("vendor", "admin"):
        raise HTTPException(status_code=403, detail="Only vendors/admin can access vendor orders")


def require_admin(user: dict) -> None:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can access completed orders")


# GET /vendor/orders
@router.get("")
async def get_vendor_orders(
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
    vendor_menu_svc: VendorMenuService = Depends(get_vendor_menu_service),
    range: Optional[str] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    status: Optional[str] = Query(default=None),
):
    require_vendor_or_admin(user)

    now = datetime.now(TW_TZ)
    today = now.date()
    range_value = range or ("custom" if from_date is not None or to_date is not None or status is not None else "today")

    if range_value == "today":
        from_date = today
        to_date = today
    elif range_value == "upcoming":
        from_date = today
        to_date = None
    elif range_value == "history":
        from_date = None
        to_date = today

    vendor_id = await vendor_menu_svc.get_current_vendor_id(user["user_id"])
    orders = await svc.get_vendor_orders(
        vendor_id=vendor_id,
        from_date=from_date,
        to_date=to_date,
        status=status,
    )
    return {"orders": orders, "count": len(orders), "range": range_value, "status": status}


# GET /vendor/orders/completed/{vendor_user_id}
@router.get("/completed/{vendor_user_id}")
async def get_completed_orders_by_vendor_user_id(
    vendor_user_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
):
    require_admin(user)
    orders = await svc.get_completed_orders_by_vendor_user_id(
        vendor_user_id=vendor_user_id,
        from_date=from_date,
        to_date=to_date,
    )
    return {"orders": orders, "count": len(orders), "status": "completed", "vendor_user_id": vendor_user_id}


# GET /vendor/orders/vendor/{vendor_user_id}
@router.get("/vendor/{vendor_user_id}")
async def get_vendor_orders_by_vendor_user_id(
    vendor_user_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
    range: Optional[str] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    status: Optional[str] = Query(default=None),
):
    require_vendor_or_admin(user)
    if user["role"] == "vendor" and user["user_id"] != vendor_user_id:
        raise HTTPException(status_code=403, detail="Not your vendor orders")

    now = datetime.now(TW_TZ)
    today = now.date()
    range_value = range or ("custom" if from_date is not None or to_date is not None or status is not None else "today")

    if range_value == "today":
        from_date = today
        to_date = today
    elif range_value == "upcoming":
        from_date = today
        to_date = None
    elif range_value == "history":
        from_date = None
        to_date = today

    orders = await svc.get_vendor_orders_by_vendor_user_id(
        vendor_user_id=vendor_user_id,
        from_date=from_date,
        to_date=to_date,
        status=status,
    )
    return {
        "orders": orders,
        "count": len(orders),
        "range": range_value,
        "status": status,
        "vendor_user_id": vendor_user_id,
    }


# PATCH /vendor/orders/{order_id}/reject
@router.patch("/{order_id}/reject", response_model=Order)
async def reject_vendor_order(
    order_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    req: Optional[CancelOrderRequest] = None,
    svc: OrderService = Depends(get_service),
    vendor_menu_svc: VendorMenuService = Depends(get_vendor_menu_service),
):
    require_vendor(user)
    vendor_id = await vendor_menu_svc.get_current_vendor_id(user["user_id"])
    cancel_reason = req.cancel_reason if req is not None else None
    return await svc.reject_vendor_order(order_id, vendor_id=vendor_id, cancel_reason=cancel_reason)
