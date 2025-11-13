from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.infrastructure.db import get_db, SessionLocal
from app.domain.models.organization import Organization
from app.domain.models.membership import Membership
from app.domain.models.document import Document
from app.domain.models.document_batch import DocumentBatch
from app.domain.models.ocr_job import OcrJob
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.domain.models.extracted_field import ExtractedField
from app.domain.models.user import User
from app.schemas.documents import (
    DocumentOut,
    OcrJobOut,
    DocumentUploadResponse,
    DocumentBatchOut,
)
from app.schemas.extracted_fields import (
    ExtractedFieldOut,
    ExtractedFieldCreate,
    ExtractedFieldUpdate,
)
from app.services.rustfs import get_rustfs_client
from app.services.ocr.pipeline import process_ocr_job
from pypdf import PdfReader, PdfWriter

router = APIRouter(prefix="/orgs", tags=["ocr"])


def _parse_uuid(id_str: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


def _start_ocr_job(job_id: uuid.UUID) -> None:
    process_ocr_job(job_id)


@router.post("/{org_id}/ocr/documents", response_model=DocumentUploadResponse, status_code=201)
async def register_document(
    org_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    template_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org_uuid = _parse_uuid(org_id, "org id")

    # Must be a member to register a document
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Validate template if provided
    tpl_id = None
    if template_id:
        try:
            tpl_uuid = uuid.UUID(template_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid template_id")
        tpl = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
            .first()
        )
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        tpl_id = tpl.id

    # Create batch if multiple files
    batch = None
    if len(files) > 1:
        batch = DocumentBatch(org_id=org_uuid, created_by_id=user.id)
        db.add(batch)
        db.flush()

    rustfs = get_rustfs_client()
    created_docs: list[Document] = []
    created_jobs: list[OcrJob] = []

    for f in files:
        data = await f.read()
        filename = f.filename or "upload"
        ct = f.content_type or "application/octet-stream"

        is_pdf = ct == "application/pdf" or filename.lower().endswith(".pdf")
        if is_pdf:
            reader = PdfReader(io.BytesIO(data))
            num_pages = len(reader.pages)
            if num_pages > 1:
                group_uuid = uuid.uuid4()
                for idx in range(num_pages):
                    writer = PdfWriter()
                    writer.add_page(reader.pages[idx])
                    buf = io.BytesIO()
                    writer.write(buf)
                    page_bytes = buf.getvalue()
                    page_name = filename.rsplit(".", 1)[0] + f"_page_{idx+1}.pdf"
                    url = await rustfs.upload_file(page_bytes, page_name, "application/pdf")
                    doc = Document(
                        org_id=org_uuid,
                        uploaded_by_id=user.id,
                        url=url,
                        batch_id=(batch.id if batch else None),
                        group_id=group_uuid,
                        page_number=idx + 1,
                    )
                    db.add(doc)
                    db.flush()
                    created_docs.append(doc)

                    job = OcrJob(org_id=org_uuid, document_id=doc.id, template_id=tpl_id, started_by_id=user.id)
                    db.add(job)
                    db.flush()
                    created_jobs.append(job)
                continue  # handled multi-page PDF

        # Single-page (PDF or image/other)
        url = await rustfs.upload_file(data, filename, ct)
        doc = Document(
            org_id=org_uuid,
            uploaded_by_id=user.id,
            url=url,
            batch_id=(batch.id if batch else None),
            page_number=1,
        )
        db.add(doc)
        db.flush()
        created_docs.append(doc)

        job = OcrJob(org_id=org_uuid, document_id=doc.id, template_id=tpl_id, started_by_id=user.id)
        db.add(job)
        db.flush()
        created_jobs.append(job)

    db.commit()

    # Auto-start the OCR jobs in background
    for j in created_jobs:
        background_tasks.add_task(_start_ocr_job, j.id)

    return DocumentUploadResponse(
        batch_id=(str(batch.id) if batch else None),
        documents=[
            DocumentOut(
                id=str(d.id),
                org_id=str(d.org_id),
                uploaded_by_id=str(d.uploaded_by_id),
                url=d.url,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in created_docs
        ],
        jobs=[
            OcrJobOut(
                id=str(j.id),
                org_id=str(j.org_id),
                document_id=str(j.document_id),
                template_id=str(j.template_id) if j.template_id else None,
                status=j.status.value if hasattr(j.status, "value") else str(j.status),
                provider=j.provider,
                error_message=j.error_message,
                created_at=j.created_at,
                updated_at=j.updated_at,
                started_at=j.started_at,
                completed_at=j.completed_at,
            )
            for j in created_jobs
        ],
    )


@router.get("/{org_id}/ocr/documents", response_model=list[DocumentOut])
def list_documents(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")

    # Must be a member to list
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    rows = db.query(Document).filter(Document.org_id == org_uuid).order_by(Document.created_at.desc()).all()
    return [
        DocumentOut(
            id=str(d.id),
            org_id=str(d.org_id),
            uploaded_by_id=str(d.uploaded_by_id),
            url=d.url,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in rows
    ]


@router.get("/{org_id}/ocr/documents/batches/{batch_id}", response_model=DocumentBatchOut)
def get_batch(org_id: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    batch_uuid = _parse_uuid(batch_id, "batch id")

    # Must be a member to view
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    batch = (
        db.query(DocumentBatch)
        .filter(DocumentBatch.id == batch_uuid, DocumentBatch.org_id == org_uuid)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    docs = db.query(Document).filter(Document.batch_id == batch.id).order_by(Document.created_at.asc()).all()
    doc_ids = [d.id for d in docs]
    jobs = []
    if doc_ids:
        jobs = db.query(OcrJob).filter(OcrJob.document_id.in_(doc_ids)).all()

    return DocumentBatchOut(
        id=str(batch.id),
        org_id=str(batch.org_id),
        created_by_id=str(batch.created_by_id),
        created_at=batch.created_at,
        documents=[
            DocumentOut(
                id=str(d.id),
                org_id=str(d.org_id),
                uploaded_by_id=str(d.uploaded_by_id),
                url=d.url,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ],
        jobs=[
            OcrJobOut(
                id=str(j.id),
                org_id=str(j.org_id),
                document_id=str(j.document_id),
                template_id=str(j.template_id) if j.template_id else None,
                status=j.status.value if hasattr(j.status, "value") else str(j.status),
                provider=j.provider,
                error_message=j.error_message,
                created_at=j.created_at,
                updated_at=j.updated_at,
                started_at=j.started_at,
                completed_at=j.completed_at,
            )
            for j in jobs
        ],
    )


@router.get("/{org_id}/ocr/ocr/jobs/{job_id}", response_model=OcrJobOut)
def get_job(org_id: str, job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    job_uuid = _parse_uuid(job_id, "job id")

    # Must be a member to view
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    job = db.query(OcrJob).filter(OcrJob.id == job_uuid, OcrJob.org_id == org_uuid).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return OcrJobOut(
        id=str(job.id),
        org_id=str(job.org_id),
        document_id=str(job.document_id),
        template_id=str(job.template_id) if job.template_id else None,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        provider=job.provider,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{org_id}/ocr/documents/{document_id}/fields", response_model=list[ExtractedFieldOut])
def list_extracted_fields(org_id: str, document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    doc_uuid = _parse_uuid(document_id, "document id")

    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    doc = db.query(Document).filter(Document.id == doc_uuid, Document.org_id == org_uuid).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    rows = (
        db.query(ExtractedField, DocumentTemplateField)
        .join(DocumentTemplateField, DocumentTemplateField.id == ExtractedField.template_field_id)
        .filter(ExtractedField.document_id == doc.id)
        .order_by(DocumentTemplateField.order_index.asc(), ExtractedField.created_at.asc())
        .all()
    )
    out: list[ExtractedFieldOut] = []
    for ef, _fld in rows:
        out.append(
            ExtractedFieldOut(
                id=str(ef.id),
                document_id=str(ef.document_id),
                template_field_id=str(ef.template_field_id),
                user_id=(str(ef.user_id) if ef.user_id else None),
                extracted_value=ef.extracted_value,
                value=ef.value,
                field_name=_fld.name,
                field_label=_fld.label,
                created_at=ef.created_at,
                updated_at=ef.updated_at,
            )
        )
    return out


@router.get("/{org_id}/ocr/documents/{document_id}/fields/{field_id}", response_model=ExtractedFieldOut)
def get_extracted_field(org_id: str, document_id: str, field_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid, Document.org_id == org_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    fld = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        user_id=(str(ef.user_id) if ef.user_id else None),
        extracted_value=ef.extracted_value,
        value=ef.value,
        field_name=(fld.name if fld else ""),
        field_label=(fld.label if fld else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.post("/{org_id}/ocr/documents/{document_id}/fields", response_model=ExtractedFieldOut, status_code=201)
def upsert_extracted_field(org_id: str, document_id: str, payload: ExtractedFieldCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    doc_uuid = _parse_uuid(document_id, "document id")
    try:
        tf_uuid = uuid.UUID(payload.template_field_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid template_field_id")

    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    doc = db.query(Document).filter(Document.id == doc_uuid, Document.org_id == org_uuid).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    fld = (
        db.query(DocumentTemplateField, DocumentTemplate)
        .join(DocumentTemplate, DocumentTemplate.id == DocumentTemplateField.template_id)
        .filter(DocumentTemplateField.id == tf_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not fld:
        raise HTTPException(status_code=404, detail="Template field not found")

    existing = (
        db.query(ExtractedField)
        .filter(ExtractedField.document_id == doc.id, ExtractedField.template_field_id == tf_uuid)
        .first()
    )
    if existing:
        if payload.value is not None:
            existing.value = str(payload.value)
        if payload.extracted_value is not None:
            existing.extracted_value = str(payload.extracted_value)
        existing.user_id = user.id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == tf_uuid).first()
        return ExtractedFieldOut(
            id=str(existing.id),
            document_id=str(existing.document_id),
            template_field_id=str(existing.template_field_id),
            user_id=(str(existing.user_id) if existing.user_id else None),
            extracted_value=existing.extracted_value,
            value=existing.value,
            field_name=(fld_row.name if fld_row else ""),
            field_label=(fld_row.label if fld_row else ""),
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    ef = ExtractedField(
        document_id=doc.id,
        template_field_id=tf_uuid,
        user_id=user.id,
        extracted_value=str(payload.extracted_value or ""),
        value=str(payload.value or ""),
    )
    db.add(ef)
    db.commit()
    db.refresh(ef)
    fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        user_id=(str(ef.user_id) if ef.user_id else None),
        extracted_value=ef.extracted_value,
        value=ef.value,
        field_name=(fld_row.name if fld_row else ""),
        field_label=(fld_row.label if fld_row else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.patch("/{org_id}/ocr/documents/{document_id}/fields/{field_id}", response_model=ExtractedFieldOut)
def update_extracted_field(org_id: str, document_id: str, field_id: str, payload: ExtractedFieldUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid, Document.org_id == org_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    if payload.value is not None:
        ef.value = str(payload.value)
    if payload.extracted_value is not None:
        ef.extracted_value = str(payload.extracted_value)
    ef.user_id = user.id
    db.add(ef)
    db.commit()
    db.refresh(ef)
    fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        user_id=(str(ef.user_id) if ef.user_id else None),
        extracted_value=ef.extracted_value,
        value=ef.value,
        field_name=(fld_row.name if fld_row else ""),
        field_label=(fld_row.label if fld_row else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.delete("/{org_id}/ocr/documents/{document_id}/fields/{field_id}", status_code=204)
def delete_extracted_field(org_id: str, document_id: str, field_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid, Document.org_id == org_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    db.delete(ef)
    db.commit()
    return None
