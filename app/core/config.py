from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://order_user:order_pass@localhost:5432/order_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    NOTIFICATION_SERVICE_URL: str = "172.31.6.25:3002"
    MENU_SERVICE_URL: str = "172.31.2.29:3000"
    JWT_SECRET: str = "supersecret_change_me"
    JWT_ALGORITHM: str = "HS256"
    TEST_REPORT_DIR: str = "./reports"

    class Config:
        env_file = ".env"


settings = Settings()
