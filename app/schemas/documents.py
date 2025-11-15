from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    template_id: str | None = None
    reference_id: str | None = Field(default=None, max_length=200)


class DocumentOut(BaseModel):
    id: str
    url: str
    reference_id: str | None
    created_at: datetime
    updated_at: datetime


class OcrJobOut(BaseModel):
    id: str
    document_id: str
    template_id: str | None
    status: str
    provider: str
    error_message: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DocumentBatchOut(BaseModel):
    id: str
    created_at: datetime
    documents: list[DocumentOut]
    jobs: list[OcrJobOut]


class DocumentUploadResponse(BaseModel):
    batch_id: str | None
    documents: list[DocumentOut]
    jobs: list[OcrJobOut]
