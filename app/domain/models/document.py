from __future__ import annotations

import uuid
from datetime import datetime, timezone as tz

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db import Base


UTC = tz.utc


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True, unique=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_batches.id"), nullable=True, index=True)
    # Pages grouping and ordering
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    page_number: Mapped[int] = mapped_column(default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    extracted_fields: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField", back_populates="document", cascade="all, delete-orphan"
    )
    ocr_jobs: Mapped[list["OcrJob"]] = relationship(
        "OcrJob", back_populates="document", cascade="all, delete-orphan"
    )
    batch: Mapped["DocumentBatch"] = relationship("DocumentBatch", back_populates="documents")
