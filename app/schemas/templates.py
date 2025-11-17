from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    callback_url: str | None = Field(default=None, max_length=1024)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    callback_url: str | None = Field(default=None, max_length=1024)


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str
    callback_url: str | None
    created_at: datetime
    updated_at: datetime


class TemplateFieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    field_type: str = Field(min_length=1, max_length=50)
    required: bool = False
    description: str = ""
    order_index: int = 0


class TemplateFieldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    field_type: str | None = Field(default=None, min_length=1, max_length=50)
    required: bool | None = None
    description: str | None = None
    order_index: int | None = None


class TemplateFieldOut(BaseModel):
    id: str
    template_id: str
    name: str
    label: str
    field_type: str
    required: bool
    description: str
    order_index: int
    created_at: datetime
    updated_at: datetime


class TemplateDetailOut(TemplateOut):
    fields: list[TemplateFieldOut] = []


class TemplateGenJobCreate(BaseModel):
    pdf_url: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default="", max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=128)
    callback_url: str | None = Field(default=None, max_length=1024)


class TemplateGenJobOut(BaseModel):
    id: str
    pdf_url: str
    name: str | None
    description: str
    status: str
    error_message: str
    template_id: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
