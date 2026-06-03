# Order & Inventory Service

FastAPI 訂單與庫存服務，負責員工訂餐、廠商查詢訂單、庫存設定，以及訂單狀態更新。

服務使用 PostgreSQL 儲存訂單與每日庫存，Redis 處理下單時的 atomic inventory reservation 與即時訂單狀態快取，RabbitMQ 發送 `order.created` / `order.cancelled` 事件並由背景 worker 非同步處理建立訂單。

## 功能

- 員工建立、查詢、取消、修改數量與完成訂單。
- 廠商查詢自己的訂單與拒絕訂單。
- Admin 查詢指定 vendor user 的 completed 訂單。
- Vendor / Admin 設定每日菜單庫存。
- Redis Lua script 保證庫存扣減的原子性。
- RabbitMQ durable topic exchange 與 quorum queue 保存訂單事件。
- Prometheus metrics 暴露於 `/metrics`。

## 專案結構

```text
app/api/              FastAPI routers
app/core/             設定與驗證邏輯
app/db/               PostgreSQL / Redis / RabbitMQ client
app/models/           Pydantic request / response models
app/repositories/     資料庫存取層
app/services/         商業邏輯與外部服務 client
app/worker/           RabbitMQ order worker
app/tests/            pytest 測試
migrations/           PostgreSQL 初始化 SQL
reports/              Docker test profile 產生的測試報告
```

## 環境變數

Docker Compose 會提供本地預設值。正式部署時建議用 `.env` 或主機環境變數覆蓋。

| 名稱 | 說明 | 預設 |
| --- | --- | --- |
| `ORDER_SERVICE_PORT` | Host 對外服務 port | `8081` |
| `DATABASE_URL` | PostgreSQL 連線字串 | compose 內指向 `postgres` |
| `REDIS_URL` | Redis 連線字串 | compose 內指向 `redis` |
| `RABBITMQ_URL` | RabbitMQ 連線字串 | compose 內指向 `rabbitmq` |
| `JWT_SECRET` | JWT fallback 驗證 secret | `thisisasecret_shu` |
| `JWT_ALGORITHM` | JWT 演算法 | `HS256` |
| `MENU_SERVICE_URL` | vendor-menu-service base URL | 空字串 |
| `NOTIFICATION_SERVICE_URL` | notification-service base URL | 空字串 |
| `ADMIN_USER_ID` | 預設 admin user id | `14` |
| `TEST_REPORT_DIR` | Docker 測試報告輸出到 host 的路徑 | `./reports` |

## 啟動

```bash
docker compose up -d --build
```

服務預設位於：

```text
http://localhost:8081
```

健康檢查：

```bash
curl http://localhost:8081/health
```

Prometheus metrics：

```bash
curl http://localhost:8081/metrics
```

RabbitMQ management UI：

```text
http://localhost:15672
guest / guest
```

常用資料庫檢查：

```bash
docker exec -it order-postgres psql -U order_user -d order_db -c "\d orders"
docker exec -it order-postgres psql -U order_user -d order_db -c "\d daily_inventory"
```

## API

驗證支援兩種模式：

- Kong / Gateway header：`X-User-Id`、`X-User-Email`、`X-User-Role`
- JWT fallback：`Authorization: Bearer <token>`

`X-User-Role` 常用值：

```text
employee
vendor
admin
```

### Orders

| Method | Path | 權限 | 說明 |
| --- | --- | --- | --- |
| `POST` | `/orders` | `employee`, `admin` | 建立訂單，先扣 Redis 庫存並送出 `order.created` event。 |
| `GET` | `/orders/me` | `employee`, `admin` | 查詢目前使用者訂單，可用 `range`、`from`、`to`、`status` 篩選。 |
| `GET` | `/orders/employee/{employee_id}` | `employee`, `admin` | 查詢指定 employee 的訂單。 |
| `GET` | `/orders/{order_id}` | order owner / vendor owner / admin | 查詢單筆訂單。 |
| `PATCH` | `/orders/{order_id}/quantity` | `employee`, `admin` | 修改訂單數量。 |
| `PATCH` | `/orders/{order_id}/cancel` | `employee`, `admin` | 取消訂單。 |
| `PATCH` | `/orders/{order_id}/complete` | `employee`, `admin` | 將訂單標記為 completed。 |

查詢參數：

```text
range=today|upcoming|history
from=YYYY-MM-DD
to=YYYY-MM-DD
status=pending|confirmed|cancelled|completed
```

### Vendor Orders

| Method | Path | 權限 | 說明 |
| --- | --- | --- | --- |
| `GET` | `/vendor/orders` | `vendor`, `admin` | 依目前 vendor user 查詢廠商訂單。 |
| `GET` | `/vendor/orders/vendor/{vendor_user_id}` | `vendor`, `admin` | 依 vendor user id 查詢廠商訂單；vendor 只能查自己。 |
| `GET` | `/vendor/orders/completed/{vendor_user_id}` | `admin` | 查詢指定 vendor user 的 completed 訂單。 |
| `PATCH` | `/vendor/orders/{order_id}/reject` | `vendor` | 廠商拒絕 / 取消訂單。 |

查詢 completed 訂單範例：

```bash
curl "http://localhost:8081/vendor/orders/completed/37?from=2026-06-01&to=2026-06-30" \
  -H "X-User-Id: 14" \
  -H "X-User-Role: admin"
```

### Inventory

| Method | Path | 權限 | 說明 |
| --- | --- | --- | --- |
| `GET` | `/inventory/{menu_id}?target_date=YYYY-MM-DD` | authenticated user | 查詢指定菜單日期剩餘庫存。 |
| `PUT` | `/inventory/{menu_id}` | `vendor`, `admin` | 設定指定菜單日期庫存。 |

設定庫存 request body：

```json
{
  "date": "2026-06-10",
  "quantity": 50
}
```

## 測試

本機直接跑 pytest：

```bash
python -m pytest app/tests -q
```

使用 Docker test profile，並產生 HTML / coverage 報告：

```bash
docker compose --profile test run --rm --build order-service-test
```

報告輸出位置：

```text
reports/test-report.html
reports/coverage/index.html
reports/coverage.xml
```

清理測試容器與 volume：

```bash
docker compose --profile test down -v --remove-orphans
```

## CI/CD

GitHub Actions workflow 位於：

```text
.github/workflows/ci-cd.yml
```

觸發規則：

- push 任意 branch：執行測試。
- pull request 到 `main`：執行測試。
- push / merge 到 `main`：執行測試、build Docker image、push GHCR、部署 EC2。
- `workflow_dispatch`：可手動觸發。

Image 會推到：

```text
ghcr.io/<github-owner>/order-service:latest
ghcr.io/<github-owner>/order-service:<commit-sha>
```

### GitHub Secrets

到 GitHub repository 設定：

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

需要設定：

| Secret | 說明 | 範例 |
| --- | --- | --- |
| `EC2_HOST` | EC2 public IP 或 domain | `13.229.xx.xx` |
| `EC2_USER` | SSH 使用者 | `ubuntu` 或 `ec2-user` |
| `EC2_SSH_KEY` | EC2 private key 內容 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `EC2_PROJECT_DIR` | EC2 上專案目錄 | `/home/ubuntu/order-service` |
| `EC2_PORT` | SSH port，可不填 | `22` |

`EC2_SSH_KEY` 請填 private key 的完整內容，不要填檔案路徑，也不要 commit `.pem` 到 repo。

部署時 workflow 會在 EC2 執行：

```bash
cd "$EC2_PROJECT_DIR"
git fetch origin main
git pull --ff-only origin main
docker compose up -d --build order-service
docker compose ps order-service
```

## 常用維運指令

重建服務：

```bash
docker compose up -d --build order-service
```

只重啟服務：

```bash
docker compose restart order-service
```

查看容器：

```bash
docker compose ps
```

查看服務 log：

```bash
docker logs -f order-service
```

查詢今天 pickup 的訂單：

```bash
docker exec -it order-postgres psql -U order_user -d order_db -c "SELECT id, employee_id, vendor_user_id, menu_name, quantity, total_price, order_date, pickup_date, status FROM orders WHERE pickup_date = CURRENT_DATE ORDER BY created_at DESC;"
```
