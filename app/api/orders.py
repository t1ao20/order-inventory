from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.order import CancelOrderRequest, Order, PlaceOrderRequest, UpdateOrderQuantityRequest
from app.services.order_service import OrderService

router = APIRouter()
TW_TZ = ZoneInfo("Asia/Taipei")


def get_service() -> OrderService:
    return OrderService()


def require_employee(user: dict) -> None:
    if user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Only employees can access employee orders")

def require_employee_or_admin(user: dict) -> None:
    if user["role"] not in ("employee", "admin"):
        raise HTTPException(status_code=403, detail="Only employees and admins can access this endpoint")

# POST /orders
@router.post("", status_code=201)
async def create_order(
    req: PlaceOrderRequest,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
):
    require_employee_or_admin(user)
    return await svc.create_order(req, employee_id=user["user_id"])


# GET /orders/me
@router.get("/me")
async def get_my_orders(
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
    range: Optional[str] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    status: Optional[str] = Query(default=None),
):
    require_employee_or_admin(user)

    now = datetime.now(TW_TZ)
    today = now.date()
    range_value = range or ("custom" if from_date is not None or to_date is not None else "today")

    if range_value == "today":
        from_date = today
        to_date = today
    elif range_value == "upcoming":
        from_date = today
        to_date = None
    elif range_value == "history":
        from_date = None
        to_date = today

    orders = await svc.get_orders(user["user_id"], from_date, to_date, status=status)
    return {"orders": orders, "count": len(orders), "range": range_value, "status": status}


# GET /orders/employee/{employee_id}
@router.get("/employee/{employee_id}")
async def get_orders_by_employee_id(
    employee_id: int,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
    range: Optional[str] = Query(default=None),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    status: Optional[str] = Query(default=None),
):
    require_employee_or_admin(user)

    now = datetime.now(TW_TZ)
    today = now.date()
    range_value = range or ("custom" if from_date is not None or to_date is not None else "today")

    if range_value == "today":
        from_date = today
        to_date = today
    elif range_value == "upcoming":
        from_date = today
        to_date = None
    elif range_value == "history":
        from_date = None
        to_date = today

    orders = await svc.get_orders_by_employee_id(employee_id, from_date, to_date, status=status)
    return {"orders": orders, "count": len(orders), "range": range_value, "status": status, "employee_id": employee_id}


# GET /orders/{order_id}
@router.get("/{order_id}", response_model=Order)
async def get_order(
    order_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
):
    return await svc.get_order_for_actor(order_id, actor=user)


# PATCH /orders/{order_id}/quantity
@router.patch("/{order_id}/quantity", response_model=Order)
async def update_order_quantity(
    order_id: UUID,
    req: UpdateOrderQuantityRequest,
    user: Annotated[dict, Depends(get_current_user)],
    svc: OrderService = Depends(get_service),
):
    require_employee_or_admin(user)
    return await svc.update_order_quantity(order_id, employee_id=user["user_id"], quantity=req.quantity)


# PATCH /orders/{order_id}/cancel
@router.patch("/{order_id}/cancel", response_model=Order)
async def cancel_order(
    order_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    req: Optional[CancelOrderRequest] = None,
    svc: OrderService = Depends(get_service),
):
    require_employee_or_admin(user)
    cancel_reason = req.cancel_reason if req is not None else None
    await svc.cancel_order(order_id, employee_id=user["user_id"], cancel_reason=cancel_reason)
    return await svc.get_order_for_actor(order_id, actor=user)
