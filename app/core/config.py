from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Studio API"
    environment: str = "dev"
    api_v1_prefix: str = "/api/v1"
    backend_cors_origins: list[str] = ["http://localhost:3000"]

    # Infra
    database_url: Optional[str] = "postgresql+psycopg://postgres:postgres@localhost:5432/aistudio"
    rustfs_base_url: Optional[str] = None
    rustfs_api_key: Optional[str] = None
    rustfs_secret: Optional[str] = None
    rustfs_bucket: Optional[str] = "aistudio"
    # Email (Resend)
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None

    # App URLs
    app_url: str = "http://localhost:3000"

    # Auth/JWT
    jwt_secret: str = "devsecret"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60
    refresh_expires_days: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
