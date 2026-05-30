from datetime import date, timedelta, datetime, timezone

from fastapi import HTTPException
from uuid import UUID

from app.db import redis as rdb_mod
from app.repositories.inventory_repository import InventoryRepository


class InventoryService:
    def __init__(self):
        self.repo = InventoryRepository()

    async def get_inventory(self, menu_id: UUID, target_date: date) -> int:
        """Returns remaining stock. Checks Redis first (Cache-aside pattern)."""
        date_str = target_date.isoformat()
        rdb = rdb_mod.get_redis()
        key = rdb_mod.inventory_key(menu_id, date_str)

        # Cache hit
        cached = await rdb.get(key)
        if cached is not None:
            return int(cached)

        # Cache miss → query DB, warm cache
        inv = await self.repo.get(menu_id, target_date)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"No inventory for menu {menu_id} on {date_str}")

        await rdb.set(key, inv.remaining_quantity, ex=600)  # 10-min TTL
        return inv.remaining_quantity

    async def set_inventory(self, menu_id: UUID, target_date: date, qty: int) -> None:
        """Upsert stock in DB and warm Redis (called by vendor/admin)."""
        await self.repo.upsert(menu_id, target_date, qty)

        # Warm Redis; TTL = seconds until end of day
        key = rdb_mod.inventory_key(menu_id, target_date.isoformat())
        end_of_day = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=timezone.utc)
        ttl = max(int((end_of_day - datetime.now(timezone.utc)).total_seconds()), 1)

        rdb = rdb_mod.get_redis()
        await rdb.set(key, qty, ex=ttl)
