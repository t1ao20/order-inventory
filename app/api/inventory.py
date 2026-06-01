from datetime import date, datetime
from typing import Annotated, Optional
from zoneinfo import ZoneInfo
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.order import SetInventoryRequest
from app.services.inventory_service import InventoryService

router = APIRouter()
TW_TZ = ZoneInfo("Asia/Taipei")


def get_service() -> InventoryService:
    return InventoryService()


# GET /inventory/{menu_id}?date=2026-05-22
@router.get("/{menu_id}")
async def get_inventory(
    menu_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    svc: InventoryService = Depends(get_service),
    target_date: Optional[date] = Query(default=None),
):
    target_date = target_date or datetime.now(TW_TZ).date()
    qty = await svc.get_inventory(menu_id, target_date)
    return {"menu_id": menu_id, "date": target_date.isoformat(), "remaining_quantity": qty}


# PUT /inventory/{menu_id}  (vendor / admin only)
@router.put("/{menu_id}")
async def set_inventory(
    menu_id: UUID,
    req: SetInventoryRequest,
    user: Annotated[dict, Depends(get_current_user)],
    svc: InventoryService = Depends(get_service),
):
    if user["role"] not in ("vendor", "admin"):
        raise HTTPException(status_code=403, detail="Only vendors and admins can set inventory")
    await svc.set_inventory(menu_id, req.date, req.quantity)
    return {"message": "inventory updated", "menu_id": menu_id, "date": req.date, "quantity": req.quantity}
