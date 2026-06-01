from datetime import date
from typing import Optional
from uuid import UUID

from app.db.postgres import get_pool
from app.models.order import DailyInventory


class InventoryRepository:

    async def get(self, menu_id: UUID, target_date: date) -> Optional[DailyInventory]:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT id, menu_id, target_date, max_quantity, sold_quantity, remaining_quantity FROM daily_inventory "
            "WHERE menu_id = $1 AND target_date = $2",
            menu_id, target_date,
        )
        return DailyInventory(**dict(row)) if row else None

    async def decrement(self, menu_id: UUID, target_date: date, qty: int = 1) -> bool:
        pool = get_pool()
        result = await pool.execute(
            """
            UPDATE daily_inventory
            SET sold_quantity = sold_quantity + $1,
                remaining_quantity = GREATEST(max_quantity - (sold_quantity + $1), 0)
            WHERE menu_id = $2 AND target_date = $3 AND remaining_quantity >= $1
            """,
            qty, menu_id, target_date,
        )
        return result == "UPDATE 1"

    async def increment(self, menu_id: UUID, target_date: date, qty: int = 1) -> None:
        pool = get_pool()
        await pool.execute(
            """
            UPDATE daily_inventory
            SET sold_quantity = GREATEST(sold_quantity - $1, 0),
                remaining_quantity = max_quantity - GREATEST(sold_quantity - $1, 0)
            WHERE menu_id = $2 AND target_date = $3
            """,
            qty, menu_id, target_date,
        )

    async def upsert(self, menu_id: UUID, target_date: date, qty: int) -> None:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO daily_inventory (menu_id, target_date, max_quantity, sold_quantity, remaining_quantity)
            VALUES ($1, $2, $3, 0, $3)
            ON CONFLICT (menu_id, target_date) DO UPDATE SET
                max_quantity = EXCLUDED.max_quantity,
                remaining_quantity = GREATEST(EXCLUDED.max_quantity - daily_inventory.sold_quantity, 0)
            """,
            menu_id, target_date, qty,
        )
