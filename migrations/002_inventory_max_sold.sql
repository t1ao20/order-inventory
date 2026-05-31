-- migrations/002_inventory_max_sold.sql
-- Backfill daily_inventory to track max quantity and sold quantity separately.

ALTER TABLE daily_inventory
    ADD COLUMN IF NOT EXISTS max_quantity INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sold_quantity INT NOT NULL DEFAULT 0;

UPDATE daily_inventory
SET max_quantity = remaining_quantity,
    sold_quantity = 0,
    remaining_quantity = remaining_quantity;

ALTER TABLE daily_inventory
    ALTER COLUMN max_quantity SET DEFAULT 0,
    ALTER COLUMN sold_quantity SET DEFAULT 0,
    ALTER COLUMN remaining_quantity SET DEFAULT 0;
