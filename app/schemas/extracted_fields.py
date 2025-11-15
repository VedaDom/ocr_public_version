from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class ExtractedFieldCreate(BaseModel):
    template_field_id: str
    value: str | None = None
    extracted_value: str | None = None


class ExtractedFieldUpdate(BaseModel):
    value: str | None = None
    extracted_value: str | None = None


class ExtractedFieldOut(BaseModel):
    id: str
    document_id: str
    template_field_id: str
    extracted_value: str
    value: str
    field_name: str
    field_label: str
    created_at: datetime
    updated_at: datetime
