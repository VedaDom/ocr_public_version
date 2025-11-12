from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.infrastructure.db import SessionLocal
from app.domain.models.ocr_job import OcrJob
from app.domain.models.document import Document
from app.domain.models.template import DocumentTemplate
from app.domain.models.extracted_field import ExtractedField
from app.services.rustfs import get_rustfs_client
from app.services.ocr.gemini import GeminiProvider

UTC = timezone.utc


def _guess_content_type_from_url(url: str) -> str:
    ct, _ = mimetypes.guess_type(url)
    return ct or "application/pdf"


def process_ocr_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
        if not job:
            return

        # Transition to running if queued
        try:
            if hasattr(OcrJob, "Status"):
                if job.status in (OcrJob.Status.succeeded, OcrJob.Status.failed, OcrJob.Status.cancelled):
                    return
                if job.status == OcrJob.Status.queued:
                    job.status = OcrJob.Status.running
            else:
                if str(job.status) in ("succeeded", "failed", "cancelled"):
                    return
                if str(job.status) == "queued":
                    job.status = "running"  # type: ignore
        except Exception:
            pass

        if not job.started_at:
            job.started_at = datetime.now(UTC)
        job.provider = "gemini"
        db.add(job)
        db.commit()

        # Load document
        doc = db.query(Document).filter(Document.id == job.document_id, Document.org_id == job.org_id).first()
        if not doc:
            raise RuntimeError("Document not found for job")

        # Download the page bytes
        rustfs = get_rustfs_client()
        page_bytes = rustfs.download_by_url_sync(doc.url)
        content_type = _guess_content_type_from_url(doc.url)

        # Prepare provider
        provider = GeminiProvider()

        schema = None
        system_prompt = None
        fields = []
        if job.template_id is not None:
            tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == job.template_id).first()
            if tpl:
                fields = list(tpl.fields)
                schema = provider.build_schema_from_fields(fields)
                system_prompt = provider.build_system_prompt(fields)
        if schema is None:
            system_prompt = provider.build_system_prompt()

        # Extract
        result = provider.extract(
            page_bytes=page_bytes,
            content_type=content_type,
            schema=schema,
            system_prompt=system_prompt,
        )

        # Persist extracted fields if template present
        if fields:
            for f in fields:
                val = result.get(f.name, "") if isinstance(result, dict) else ""
                rec = ExtractedField(
                    document_id=doc.id,
                    template_field_id=f.id,
                    user_id=None,
                    extracted_value=str(val),
                    value=str(val),
                )
                db.add(rec)

        # Mark success
        try:
            if hasattr(OcrJob, "Status"):
                job.status = OcrJob.Status.succeeded
            else:
                job.status = "succeeded"  # type: ignore
        except Exception:
            pass
        job.completed_at = datetime.now(UTC)
        db.add(job)
        db.commit()
    except Exception as e:
        # Mark failure
        try:
            job = db.query(OcrJob).filter(OcrJob.id == job_id).first()
            if job:
                try:
                    if hasattr(OcrJob, "Status"):
                        job.status = OcrJob.Status.failed
                    else:
                        job.status = "failed"  # type: ignore
                except Exception:
                    pass
                job.error_message = (str(e) or "error")[:2000]
                job.completed_at = datetime.now(UTC)
                db.add(job)
                db.commit()
        finally:
            pass
    finally:
        db.close()
