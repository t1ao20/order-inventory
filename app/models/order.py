from datetime import date, datetime
from enum import Enum
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


# ── DB record ─────────────────────────────────────────────────

class Order(BaseModel):
    id: UUID
    employee_id: int
    vendor_id: UUID
    menu_id: UUID
    menu_name: str          # price snapshot
    price_snapshot: int     # unit price in cents
    quantity: int
    total_price: int
    order_date: date
    pickup_date: date
    status: OrderStatus
    created_at: datetime
    menu_tags: List[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class DailyInventory(BaseModel):
    id: int
    menu_id: UUID
    target_date: date
    max_quantity: int
    sold_quantity: int
    remaining_quantity: int


# ── Request schemas ────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    vendor_id: UUID
    menu_id: UUID
    menu_name: str
    price: int = Field(..., description="Unit price in cents", gt=0)
    quantity: int = Field(1, ge=1)
    pickup_date: date


class SetInventoryRequest(BaseModel):
    date: date
    quantity: int = Field(..., ge=0)


class UpdateOrderRequest(BaseModel):
    status: Optional[OrderStatus] = None
    action: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=1)


class UpdateOrderQuantityRequest(BaseModel):
    quantity: int = Field(..., ge=1)


# ── RabbitMQ event ─────────────────────────────────────────────

class OrderEvent(BaseModel):
    event: str
    order_id: UUID
    employee_id: int
    vendor_id: Optional[UUID] = None
    menu_id: UUID
    menu_name: str = ""
    price: int = 0
    quantity: int = 1
    pickup_date: str = ""
    status: OrderStatus = OrderStatus.pending
    timestamp: int
    menu_tags: List[str] = Field(default_factory=list)
