-- migrations/001_init.sql

CREATE TABLE IF NOT EXISTS orders (
    id              UUID PRIMARY KEY,
    employee_id     BIGINT       NOT NULL,
    vendor_user_id  BIGINT         NOT NULL,
    vendor_id       UUID         NOT NULL,
    menu_id         UUID         NOT NULL,
    menu_name       VARCHAR(255) NOT NULL,
    menu_tags       TEXT[]       NOT NULL DEFAULT '{}',
    factory_zone    VARCHAR(255) NOT NULL DEFAULT '',
    price_snapshot  BIGINT       NOT NULL,
    quantity        INT          NOT NULL DEFAULT 1,
    total_price     BIGINT       NOT NULL,
    order_date      DATE         NOT NULL,
    pickup_date     DATE         NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE orders ADD COLUMN IF NOT EXISTS menu_tags TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS factory_zone VARCHAR(255) NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_orders_employee_id   ON orders (employee_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date    ON orders (order_date);
CREATE INDEX IF NOT EXISTS idx_orders_employee_date ON orders (employee_id, order_date);

CREATE TABLE IF NOT EXISTS daily_inventory (
    id                 BIGSERIAL PRIMARY KEY,
    menu_id            UUID NOT NULL,
    target_date        DATE   NOT NULL,
    max_quantity       INT    NOT NULL DEFAULT 0,
    sold_quantity      INT    NOT NULL DEFAULT 0,
    remaining_quantity INT    NOT NULL DEFAULT 0,
    UNIQUE (menu_id, target_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_inv_menu_date ON daily_inventory (menu_id, target_date);
