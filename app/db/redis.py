from typing import Optional, Union
from uuid import UUID

import redis.asyncio as aioredis
from app.core.config import settings

_redis: Optional[aioredis.Redis] = None


async def init_redis():
    global _redis
    _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    await _redis.ping()


async def close_redis():
    if _redis:
        await _redis.aclose()


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised")
    return _redis


# ── Key helpers ────────────────────────────────────────────────

def inventory_key(menu_id: Union[int, str, UUID], date: str) -> str:
    """Redis key for daily inventory: inventory:<menu_id>:<date>"""
    return f"inventory:{str(menu_id)}:{date}"


def order_status_key(order_id: Union[str, UUID]) -> str:
    """Redis key for live order status: order:today:<order_id>"""
    return f"order:today:{str(order_id)}"


def rate_limit_key(user_id: int) -> str:
    return f"rate_limit:{user_id}"


# ── Lua Scripts ────────────────────────────────────────────────
# Redis executes Lua atomically (single-threaded), so this is
# race-condition-free even under heavy concurrency.

# Returns: remaining stock after decrement (≥1), 0 if out of stock, -1 if key missing
DECR_INVENTORY_SCRIPT = """
local key   = KEYS[1]
local stock = redis.call('GET', key)
if not stock then return -2 end
stock = tonumber(stock)
if stock <= 0 then return -1 end
return redis.call('DECR', key)
"""

# Returns: stock after increment, -1 if key missing
INCR_INVENTORY_SCRIPT = """
local key = KEYS[1]
local cur = redis.call('GET', key)
if not cur then return -1 end
return redis.call('INCR', key)
"""

# Returns: remaining stock after reserve, -1 if not enough, -2 if key missing
RESERVE_INVENTORY_SCRIPT = """
local key = KEYS[1]
local qty = tonumber(ARGV[1])

local stock = tonumber(redis.call('GET', key) or '-1')

if stock == -1 then
    return -2
end

if stock < qty then
    return -1
end

redis.call('DECRBY', key, qty)
return stock - qty
"""


async def decr_inventory(menu_id: Union[int, str, UUID], date: str) -> int:
    """Atomically decrement inventory. Returns remaining stock, -1=sold out, -2=not set."""
    rdb = get_redis()
    key = inventory_key(menu_id, date)
    result = await rdb.eval(DECR_INVENTORY_SCRIPT, 1, key)
    return int(result)


async def incr_inventory(menu_id: Union[int, str, UUID], date: str) -> int:
    """Atomically restore one unit (used on cancellation)."""
    rdb = get_redis()
    key = inventory_key(menu_id, date)
    result = await rdb.eval(INCR_INVENTORY_SCRIPT, 1, key)
    return int(result)


async def reserve_inventory(menu_id: Union[int, str, UUID], date: str, quantity: int) -> int:
    """Atomically reserve quantity units. Returns remaining stock, -1=not enough, -2=not set."""
    rdb = get_redis()
    key = inventory_key(menu_id, date)
    result = await rdb.eval(RESERVE_INVENTORY_SCRIPT, 1, key, quantity)
    return int(result)
