from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class MemberOut(BaseModel):
    id: str
    user_id: str
    email: EmailStr
    names: str | None = None
    role_id: str
    role_name: str
    created_at: datetime


class MemberAddRequest(BaseModel):
    email: EmailStr
    names: str | None = None
    role_id: str | None = Field(default=None)


class MemberUpdateRequest(BaseModel):
    role_id: str | None = Field(default=None)
