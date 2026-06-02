import asyncio
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger

from app.core.config import settings
from app.db.postgres import init_db, close_db
from app.db.redis import init_redis, close_redis
from app.db.rabbitmq import init_rabbitmq, close_rabbitmq
from app.worker.order_worker import OrderWorker
from app.api.orders import router as orders_router
from app.api.inventory import router as inventory_router
from app.api.vendor_orders import router as vendor_orders_router


handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter())
logging.getLogger().handlers = [handler]
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────
    await init_db()
    await init_redis()
    await init_rabbitmq()

    # Start async worker in background
    worker = OrderWorker()
    task = asyncio.create_task(worker.start())

    logger.info("Order Service started on port 8080")
    yield

    # ── Shutdown ──────────────────────────────────────────────
    task.cancel()
    await close_rabbitmq()
    await close_redis()
    await close_db()
    logger.info("Order Service shut down")


app = FastAPI(
    title="Order & Inventory Service",
    version="1.0.0",
    lifespan=lifespan,
)
# add CORS middleware to allow frontend access from different origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://140.113.62.166:8080",
        "http://localhost:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders_router, prefix="/orders", tags=["orders"])
app.include_router(vendor_orders_router, prefix="/vendor/orders", tags=["vendor-orders"])
app.include_router(inventory_router, prefix="/inventory", tags=["inventory"])
Instrumentator().instrument(app).expose(app)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}
