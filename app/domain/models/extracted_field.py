from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db import Base


UTC = timezone.utc


class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    template_field_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("document_template_fields.id"), nullable=False, index=True)
    # Who captured/edited the value (optional, may be system)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Raw extracted value from OCR and the finalized user-corrected value
    extracted_value: Mapped[str] = mapped_column(String(2000), default="", nullable=False)
    value: Mapped[str] = mapped_column(String(2000), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="extracted_fields")
    field: Mapped["DocumentTemplateField"] = relationship("DocumentTemplateField", back_populates="extracted_values")
    user: Mapped["User"] = relationship("User")
