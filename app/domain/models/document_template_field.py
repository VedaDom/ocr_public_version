from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db import Base


UTC = timezone.utc


class DocumentTemplateField(Base):
    __tablename__ = "document_template_fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_templates.id"), nullable=False)

    # Field metadata
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # machine name / key
    label: Mapped[str] = mapped_column(String(200), nullable=False)  # display label
    field_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. string, number, date, enum
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    template: Mapped["DocumentTemplate"] = relationship("DocumentTemplate", back_populates="fields")
    extracted_values: Mapped[list["ExtractedField"]] = relationship(
        "ExtractedField",
        back_populates="field",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("template_id", "name", name="uq_template_field_name"),
    )
