from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = []
    rate_limit_per_min: int | None = None
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):
    id: str
    org_id: str
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_per_min: int | None
    revoked: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class ApiKeyCreateResponse(ApiKeyOut):
    key: str
