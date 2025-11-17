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
    # Email (Resend)
    resend_api_key: Optional[str] = None
    resend_from_email: Optional[str] = None

    # Gemini
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-pro"
    document_languages: list[str] = ["fr", "rw", "en"]
    gemini_requests_per_minute: int = 4000
    gemini_max_concurrency: int = 8

    # Google OAuth
    google_client_id: Optional[str] = None

    # App URLs
    app_url: str = "http://localhost:3000"

    # Analytics
    analytics_endpoint_url: Optional[str] = None

    # Auth/JWT
    jwt_secret: str = "devsecret"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60
    refresh_expires_days: int = 30

    # Runtime (Uvicorn)
    uvicorn_host: str | None = None
    uvicorn_port: int | None = None
    uvicorn_log_level: str | None = None
    uvicorn_workers: int | None = None

    # Pricing
    ocr_page_cost: int = 1
    template_gen_cost: int = 1

    # Temp files cleanup
    temp_cleanup_enabled: bool = True
    temp_cleanup_ttl_seconds: int = 86400
    temp_cleanup_interval_seconds: int = 3600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
