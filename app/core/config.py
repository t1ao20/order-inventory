from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://order_user:order_pass@localhost:5432/order_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    NOTIFICATION_SERVICE_URL: str = ""
    MENU_SERVICE_URL: str = ""
    LOGIN_SERVICE_URL: str = ""
    JWT_SECRET: str = "supersecret_change_me"
    JWT_ALGORITHM: str = "HS256"
    TEST_REPORT_DIR: str = "./reports"
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
