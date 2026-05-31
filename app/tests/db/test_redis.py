import asyncio
from uuid import UUID

import pytest

from app.db import redis as redis_mod
from app.test_time import tw_today


class FakeRedis:
    def __init__(self, result):
        self.result = result
        self.eval_call = None

    async def eval(self, script: str, numkeys: int, key: str):
        self.eval_call = (script, numkeys, key)
        return self.result


def test_inventory_key_formats_menu_and_date():
    # arrange: a menu id and date string
    menu_id = UUID("00000000-0000-4000-8000-000000000042")
    target_date = tw_today().isoformat()

    # act: build the Redis inventory key
    result = redis_mod.inventory_key(menu_id, target_date)

    # assert: key should include menu id and date
    assert result == f"inventory:{str(menu_id)}:{target_date}"


def test_order_status_key_formats_order_id():
    # arrange: an order id
    order_id = "11111111-1111-4111-8111-111111111111"

    # act: build the Redis order status key
    result = redis_mod.order_status_key(order_id)

    # assert: key should include the order id
    assert result == "order:today:11111111-1111-4111-8111-111111111111"


def test_rate_limit_key_formats_user_id():
    # arrange: a user id
    user_id = 7

    # act: build the Redis rate limit key
    result = redis_mod.rate_limit_key(user_id)

    # assert: key should include the user id
    assert result == "rate_limit:7"


def test_get_redis_raises_before_initialisation(monkeypatch):
    # arrange: Redis module without an initialized client
    monkeypatch.setattr(redis_mod, "_redis", None)

    # act: get the Redis client
    with pytest.raises(RuntimeError) as exc_info:
        redis_mod.get_redis()

    # assert: response should explain Redis is not initialized
    assert str(exc_info.value) == "Redis not initialised"


def test_decr_inventory_runs_lua_against_inventory_key(monkeypatch):
    # arrange: a fake Redis client returning a numeric string
    rdb = FakeRedis(result="5")
    monkeypatch.setattr(redis_mod, "_redis", rdb)

    # act: decrement inventory through the Redis helper
    menu_id = UUID("00000000-0000-4000-8000-000000000042")
    target_date = tw_today().isoformat()
    result = asyncio.run(redis_mod.decr_inventory(menu_id, target_date))

    # assert: result should be converted to int and use the inventory key
    assert result == 5
    assert rdb.eval_call == (redis_mod.DECR_INVENTORY_SCRIPT, 1, f"inventory:{str(menu_id)}:{target_date}")


def test_incr_inventory_runs_lua_against_inventory_key(monkeypatch):
    # arrange: a fake Redis client returning a numeric string
    rdb = FakeRedis(result="6")
    monkeypatch.setattr(redis_mod, "_redis", rdb)

    # act: increment inventory through the Redis helper
    menu_id = UUID("00000000-0000-4000-8000-000000000042")
    target_date = tw_today().isoformat()
    result = asyncio.run(redis_mod.incr_inventory(menu_id, target_date))

    # assert: result should be converted to int and use the inventory key
    assert result == 6
    assert rdb.eval_call == (redis_mod.INCR_INVENTORY_SCRIPT, 1, f"inventory:{str(menu_id)}:{target_date}")
