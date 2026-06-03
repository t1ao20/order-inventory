from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg

from app.db.postgres import get_pool
from app.models.order import Order, OrderStatus


class OrderRepository:

    async def create(self, order: Order) -> None:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO orders
                (id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags, factory_zone, price_snapshot,
                 quantity, total_price, order_date, pickup_date, status, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            """,
            order.id, order.employee_id, order.vendor_user_id, order.vendor_id,
            order.menu_id, order.menu_name, order.menu_tags, order.factoryZone, order.price_snapshot, order.quantity,
            order.total_price, order.order_date, order.pickup_date,
            order.status.value, order.created_at,
        )

    async def get_by_id(self, order_id: UUID) -> Optional[Order]:
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags,
                   factory_zone AS "factoryZone", price_snapshot,
                   quantity, total_price, order_date, pickup_date, status, created_at
            FROM orders WHERE id = $1
            """,
            order_id,
        )
        return Order(**dict(row)) if row else None

    async def update_status(self, order_id: UUID, status: OrderStatus) -> bool:
        pool = get_pool()
        result = await pool.execute(
            "UPDATE orders SET status = $1 WHERE id = $2",
            status.value, order_id,
        )
        return result == "UPDATE 1"

    async def update_quantity(self, order_id: UUID, quantity: int, total_price: int) -> bool:
        pool = get_pool()
        result = await pool.execute(
            "UPDATE orders SET quantity = $1, total_price = $2 WHERE id = $3",
            quantity, total_price, order_id,
        )
        return result == "UPDATE 1"

    async def list_by_employee(
        self, employee_id: int, from_date: Optional[date], to_date: Optional[date]
    ) -> list[Order]:
        pool = get_pool()
        query = (
            "SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags, "
            "       factory_zone AS \"factoryZone\", price_snapshot, "
            "       quantity, total_price, order_date, pickup_date, status, created_at "
            "FROM orders WHERE employee_id = $1"
        )
        args: list[object] = [employee_id]

        if from_date is not None:
            query += f" AND pickup_date >= ${len(args) + 1}"
            args.append(from_date)
        if to_date is not None:
            query += f" AND pickup_date <= ${len(args) + 1}"
            args.append(to_date)

        query += " ORDER BY pickup_date DESC, created_at DESC"
        rows = await pool.fetch(query, *args)
        return [Order(**dict(r)) for r in rows]

    async def list_by_vendor(
        self, vendor_id: UUID, from_date: Optional[date], to_date: Optional[date]
    ) -> list[Order]:
        pool = get_pool()
        query = (
            "SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags, "
            "       factory_zone AS \"factoryZone\", price_snapshot, "
            "       quantity, total_price, order_date, pickup_date, status, created_at "
            "FROM orders WHERE vendor_id = $1"
        )
        args: list[object] = [vendor_id]

        if from_date is not None:
            query += f" AND pickup_date >= ${len(args) + 1}"
            args.append(from_date)
        if to_date is not None:
            query += f" AND pickup_date <= ${len(args) + 1}"
            args.append(to_date)

        query += " ORDER BY pickup_date DESC, created_at DESC"
        rows = await pool.fetch(query, *args)
        return [Order(**dict(r)) for r in rows]

    async def list_by_vendor_user_id(
        self, vendor_user_id: int, from_date: Optional[date], to_date: Optional[date]
    ) -> list[Order]:
        pool = get_pool()
        query = (
            "SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags, "
            "       factory_zone AS \"factoryZone\", price_snapshot, "
            "       quantity, total_price, order_date, pickup_date, status, created_at "
            "FROM orders WHERE vendor_user_id = $1"
        )
        args: list[object] = [vendor_user_id]

        if from_date is not None:
            query += f" AND pickup_date >= ${len(args) + 1}"
            args.append(from_date)
        if to_date is not None:
            query += f" AND pickup_date <= ${len(args) + 1}"
            args.append(to_date)

        query += " ORDER BY pickup_date DESC, created_at DESC"
        rows = await pool.fetch(query, *args)
        return [Order(**dict(r)) for r in rows]

    async def list_by_vendor_user_id_and_status(
        self,
        vendor_user_id: int,
        status: OrderStatus,
        from_date: Optional[date],
        to_date: Optional[date],
    ) -> list[Order]:
        pool = get_pool()
        query = (
            "SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags, "
            "       factory_zone AS \"factoryZone\", price_snapshot, "
            "       quantity, total_price, order_date, pickup_date, status, created_at "
            "FROM orders WHERE vendor_user_id = $1 AND status = $2"
        )
        args: list[object] = [vendor_user_id, status.value]

        if from_date is not None:
            query += f" AND pickup_date >= ${len(args) + 1}"
            args.append(from_date)
        if to_date is not None:
            query += f" AND pickup_date <= ${len(args) + 1}"
            args.append(to_date)

        query += " ORDER BY pickup_date DESC, created_at DESC"
        rows = await pool.fetch(query, *args)
        return [Order(**dict(r)) for r in rows]

    async def get_today_order(self, employee_id: int) -> Optional[Order]:
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags,
                   factory_zone AS "factoryZone", price_snapshot,
                   quantity, total_price, order_date, pickup_date, status, created_at
            FROM orders
                        WHERE employee_id = $1 AND pickup_date = CURRENT_DATE
              AND status != 'cancelled'
            ORDER BY created_at DESC LIMIT 1
            """,
            employee_id,
        )
        return Order(**dict(row)) if row else None

    async def list_today_by_vendor(self, vendor_id: UUID) -> list[Order]:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags,
                   factory_zone AS "factoryZone", price_snapshot,
                   quantity, total_price, order_date, pickup_date, status, created_at
            FROM orders
                        WHERE vendor_id = $1 AND pickup_date = CURRENT_DATE
              AND status != 'cancelled'
            ORDER BY created_at DESC
            """,
            vendor_id,
        )
        return [Order(**dict(r)) for r in rows]

    async def list_confirmed_by_menu_and_date(self, menu_id: UUID, target_date: date) -> list[Order]:
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, employee_id, vendor_user_id, vendor_id, menu_id, menu_name, menu_tags,
                   factory_zone AS "factoryZone", price_snapshot,
                   quantity, total_price, order_date, pickup_date, status, created_at
            FROM orders
            WHERE menu_id = $1
              AND pickup_date = $2
              AND status = 'confirmed'
            ORDER BY created_at DESC, id DESC
            """,
            menu_id, target_date,
        )
        return [Order(**dict(r)) for r in rows]
