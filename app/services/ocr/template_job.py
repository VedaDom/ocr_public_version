from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.infrastructure.db import SessionLocal
from app.core.config import get_settings
from app.domain.models.template_gen_job import TemplateGenJob
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.services.ocr.template_gen import TemplateGenerator
from app.services.credits import debit_if_possible, refund, InsufficientCreditsError
from app.services.rustfs import get_rustfs_client

UTC = timezone.utc


def process_template_gen_job(job_id: uuid.UUID) -> None:
    db: Session = SessionLocal()
    try:
        job = db.query(TemplateGenJob).filter(TemplateGenJob.id == job_id).first()
        if not job:
            return

        if job.status not in ("queued",):
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        db.add(job)
        db.commit()

        debited = False
        amount = int(get_settings().template_gen_cost)
        try:
            res = debit_if_possible(
                db,
                job.org_id,
                amount,
                reason="template_gen",
                idempotency_key=f"template_gen:{job.id}",
            )
            debited = not res.already_processed
        except InsufficientCreditsError:
            job.status = "failed"
            job.error_message = "insufficient credits"
            job.completed_at = datetime.now(UTC)
            db.add(job)
            db.commit()
            return

        try:
            rustfs = get_rustfs_client()
            pdf_bytes = rustfs.download_by_url_sync(job.pdf_url)
            gen = TemplateGenerator()
            result = gen.generate(pdf_bytes=pdf_bytes, content_type="application/pdf")

            # Determine template name
            base_name = (job.name or "Generated Template").strip()[:200] or "Generated Template"
            tpl_name = base_name
            suffix = 1
            while (
                db.query(DocumentTemplate)
                .filter(DocumentTemplate.org_id == job.org_id, DocumentTemplate.name == tpl_name)
                .first()
            ):
                tpl_name = f"{base_name} ({suffix})"
                suffix += 1

            # Create template
            t = DocumentTemplate(org_id=job.org_id, name=tpl_name, description=(job.description or "")[:500], created_by_id=job.created_by_id)
            db.add(t)
            db.flush()

            # Create fields
            fields = result.get("fields") or []
            order = 1
            for f in fields:
                fname = str(f.get("name") or "").strip()[:100]
                flabel = str(f.get("label") or fname).strip()[:200]
                ftype = str(f.get("field_type") or "string").strip()[:50]
                freq = bool(f.get("required") or False)
                fdesc = str(f.get("description") or "").strip()[:500]
                if not fname:
                    continue
                rec = DocumentTemplateField(
                    template_id=t.id,
                    name=fname,
                    label=flabel,
                    field_type=ftype,
                    required=freq,
                    description=fdesc,
                    order_index=order,
                )
                db.add(rec)
                order += 1

            db.commit()
            job.template_id = t.id
            job.status = "succeeded"
            job.completed_at = datetime.now(UTC)
            db.add(job)
            db.commit()
        except Exception as e:
            job.status = "failed"
            job.error_message = (str(e) or "error")[:2000]
            job.completed_at = datetime.now(UTC)
            db.add(job)
            db.commit()
            # Refund if we charged earlier
            try:
                if debited:
                    refund(
                        db,
                        job.org_id,
                        amount,
                        reason="refund_template_gen",
                        idempotency_key=f"refund:template_gen:{job.id}",
                    )
            except Exception:
                pass
    finally:
        db.close()
