# Order & Inventory Service

這是一個以 FastAPI 實作的訂餐與庫存服務，負責員工訂餐、商家查單/拒單，以及每日菜單庫存管理。

服務使用 PostgreSQL 作為主要資料庫，Redis 做下單時的 atomic inventory reservation 與即時訂單狀態快取，RabbitMQ 負責非同步建立訂單。

## 主要功能

- 員工建立訂單、取消訂單、修改數量、查詢訂單。
- 商家/管理員查詢訂單與拒單。
- 以 `menu_id` + `target_date` 管理每日庫存。
- 使用 Redis Lua script 做原子庫存扣減。
- 使用 RabbitMQ `order.created` event，由 worker 非同步寫入 PostgreSQL。
- 取消訂單時透過 `NOTIFICATION_SERVICE_URL` 呼叫通知服務。
- 商家身份會透過 vendor-menu-service 轉換：
  `GET {MENU_SERVICE_URL}/api/v1/vendors/me`，並帶上 `x-user-id` header。

## 專案結構

```text
app/api/              FastAPI routers
app/core/             設定與驗證
app/db/               PostgreSQL / Redis / RabbitMQ client
app/models/           Pydantic models 與 request schema
app/repositories/     資料庫存取層
app/services/         商業邏輯與外部服務 client
app/worker/           RabbitMQ order worker
app/tests/            pytest 測試
migrations/           資料庫初始化 SQL
```

## 資料模型

目前程式預期訂單、商家、菜單相關 ID 都是 UUID：

```sql
orders.id        UUID PRIMARY KEY
orders.vendor_id UUID NOT NULL
orders.menu_id   UUID NOT NULL

daily_inventory.menu_id UUID NOT NULL
UNIQUE (menu_id, target_date)
```

## API 總覽

### Orders

| Method | Path | 說明 |
| --- | --- | --- |
| `POST` | `/orders` | 建立員工訂單。會先扣 Redis 庫存，並送出 `order.created` event。 |
| `GET` | `/orders/me` | 查詢目前使用者的訂單，支援 `range`、`from`、`to`、`status`。 |
| `GET` | `/orders/employee/{employee_id}` | 查詢指定員工訂單。 |
| `GET` | `/orders/{order_id}` | 依角色查詢單筆訂單。 |
| `PATCH` | `/orders/{order_id}/quantity` | 在截止時間前修改訂單數量。 |
| `PATCH` | `/orders/{order_id}/cancel` | 在截止時間前取消自己的訂單。 |

### Vendor Orders

| Method | Path | 說明 |
| --- | --- | --- |
| `GET` | `/vendor/orders` | 用目前使用者的 `user_id` 呼叫 menu service 取得 vendor UUID，再查詢商家訂單。 |
| `GET` | `/vendor/orders/vendor/{vendor_id}` | 用明確的 vendor UUID 查詢商家訂單。 |
| `PATCH` | `/vendor/orders/{order_id}/reject` | 用目前使用者解析出的 vendor UUID 拒絕/取消商家訂單。 |

查詢參數：

- `range=today|upcoming|history`
- `from=YYYY-MM-DD`
- `to=YYYY-MM-DD`
- `status=pending|confirmed|cancelled|completed`

### Inventory

| Method | Path | 說明 |
| --- | --- | --- |
| `GET` | `/inventory/{menu_id}?target_date=YYYY-MM-DD` | 查詢剩餘庫存。未帶 `target_date` 時，預設使用 Asia/Taipei 的今天。 |
| `PUT` | `/inventory/{menu_id}` | 設定指定日期的最大庫存，需要 `vendor` 或 `admin`。 |

Request body：

```json
{
  "date": "2026-06-10",
  "quantity": 50
}
```

## 本機啟動

啟動所有服務：

```bash
docker compose up -d --build
```

Health check：

```bash
curl http://localhost:8081/health
```

RabbitMQ management UI：

```text
http://localhost:15672
guest / guest
```

確認資料庫 schema：

```bash
docker exec -it order-postgres psql -U order_user -d order_db -c "\d orders"
docker exec -it order-postgres psql -U order_user -d order_db -c "\d daily_inventory"
```

## 測試

本機測試：

```bash
python -m pytest app/tests -q
```

Docker test profile：

```bash
docker compose --profile test run --rm --build order-service-test
```

測試主要使用 FastAPI dependency override 與 fake external service，避免單元測試直接依賴 Postgres/Redis/RabbitMQ/vendor-menu-service/notification-service。
