from __future__ import annotations

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.infrastructure.db import get_db
from app.domain.models.document import Document
from app.domain.models.document_batch import DocumentBatch
from app.domain.models.ocr_job import OcrJob
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.domain.models.extracted_field import ExtractedField
from app.schemas.documents import (
    DocumentCreate,
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
from app.services.ocr.pipeline import process_ocr_job

router = APIRouter(prefix="/ocr", tags=["ocr"])


def _parse_uuid(id_str: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


def _start_ocr_job(job_id: uuid.UUID) -> None:
    process_ocr_job(job_id)


@router.post("/documents", response_model=DocumentUploadResponse, status_code=201)
def register_document(
    payload: DocumentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):

    # Validate template if provided
    tpl_id = None
    if payload.template_id:
        try:
            tpl_uuid = uuid.UUID(payload.template_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid template_id")
        tpl = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        tpl_id = tpl.id

    # Create document (single)
    if payload.reference_id:
        existing = db.query(Document).filter(Document.reference_id == payload.reference_id).first()
        if existing:
            raise HTTPException(status_code=409, detail="reference_id already exists")
    doc = Document(
        url=payload.url,
        reference_id=(payload.reference_id or None),
        page_number=1,
    )
    db.add(doc)
    db.flush()

    job = OcrJob(document_id=doc.id, template_id=tpl_id)
    db.add(job)
    db.commit()
    db.refresh(doc)
    db.refresh(job)

    # Auto-start the OCR job in background
    background_tasks.add_task(_start_ocr_job, job.id)

    return DocumentUploadResponse(
        batch_id=None,
        documents=[
            DocumentOut(
                id=str(doc.id),
                url=doc.url,
                reference_id=doc.reference_id,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
        ],
        jobs=[
            OcrJobOut(
                id=str(job.id),
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
        ],
    )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    rows = db.query(Document).order_by(Document.created_at.desc()).all()
    return [
        DocumentOut(
            id=str(d.id),
            url=d.url,
            reference_id=d.reference_id,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in rows
    ]


@router.get("/documents/batches/{batch_id}", response_model=DocumentBatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    batch_uuid = _parse_uuid(batch_id, "batch id")
    batch = db.query(DocumentBatch).filter(DocumentBatch.id == batch_uuid).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    docs = db.query(Document).filter(Document.batch_id == batch.id).order_by(Document.created_at.asc()).all()
    doc_ids = [d.id for d in docs]
    jobs = []
    if doc_ids:
        jobs = db.query(OcrJob).filter(OcrJob.document_id.in_(doc_ids)).all()

    return DocumentBatchOut(
        id=str(batch.id),
        created_at=batch.created_at,
        documents=[
            DocumentOut(
                id=str(d.id),
                url=d.url,
                reference_id=d.reference_id,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ],
        jobs=[
            OcrJobOut(
                id=str(j.id),
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


@router.get("/ocr/jobs/{job_id}", response_model=OcrJobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job_uuid = _parse_uuid(job_id, "job id")

    job = db.query(OcrJob).filter(OcrJob.id == job_uuid).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return OcrJobOut(
        id=str(job.id),
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


@router.get("/documents/by_ref/{reference_id}", response_model=DocumentOut)
def get_document_by_reference(reference_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.reference_id == reference_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentOut(
        id=str(doc.id),
        url=doc.url,
        reference_id=doc.reference_id,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/documents/{document_id}/fields", response_model=list[ExtractedFieldOut])
def list_extracted_fields(document_id: str, db: Session = Depends(get_db)):
    doc_uuid = _parse_uuid(document_id, "document id")

    doc = db.query(Document).filter(Document.id == doc_uuid).first()
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
                extracted_value=ef.extracted_value,
                value=ef.value,
                confidence=ef.confidence,
                field_name=_fld.name,
                field_label=_fld.label,
                created_at=ef.created_at,
                updated_at=ef.updated_at,
            )
        )
    return out


@router.get("/documents/{document_id}/fields/{field_id}", response_model=ExtractedFieldOut)
def get_extracted_field(document_id: str, field_id: str, db: Session = Depends(get_db)):
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    fld = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        extracted_value=ef.extracted_value,
        value=ef.value,
        confidence=ef.confidence,
        field_name=(fld.name if fld else ""),
        field_label=(fld.label if fld else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.post("/documents/{document_id}/fields", response_model=ExtractedFieldOut, status_code=201)
def upsert_extracted_field(document_id: str, payload: ExtractedFieldCreate, db: Session = Depends(get_db)):
    doc_uuid = _parse_uuid(document_id, "document id")
    try:
        tf_uuid = uuid.UUID(payload.template_field_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid template_field_id")

    doc = db.query(Document).filter(Document.id == doc_uuid).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    fld = (
        db.query(DocumentTemplateField)
        .filter(DocumentTemplateField.id == tf_uuid)
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
        if payload.confidence is not None:
            existing.confidence = float(payload.confidence)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == tf_uuid).first()
        return ExtractedFieldOut(
            id=str(existing.id),
            document_id=str(existing.document_id),
            template_field_id=str(existing.template_field_id),
            extracted_value=existing.extracted_value,
            value=existing.value,
            confidence=existing.confidence,
            field_name=(fld_row.name if fld_row else ""),
            field_label=(fld_row.label if fld_row else ""),
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    ef = ExtractedField(
        document_id=doc.id,
        template_field_id=tf_uuid,
        extracted_value=str(payload.extracted_value or ""),
        value=str(payload.value or ""),
        confidence=(float(payload.confidence) if payload.confidence is not None else None),
    )
    db.add(ef)
    db.commit()
    db.refresh(ef)
    fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        extracted_value=ef.extracted_value,
        value=ef.value,
        confidence=ef.confidence,
        field_name=(fld_row.name if fld_row else ""),
        field_label=(fld_row.label if fld_row else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.patch("/documents/{document_id}/fields/{field_id}", response_model=ExtractedFieldOut)
def update_extracted_field(document_id: str, field_id: str, payload: ExtractedFieldUpdate, db: Session = Depends(get_db)):
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    if payload.value is not None:
        ef.value = str(payload.value)
    if payload.extracted_value is not None:
        ef.extracted_value = str(payload.extracted_value)
    if payload.confidence is not None:
        ef.confidence = float(payload.confidence)
    db.add(ef)
    db.commit()
    db.refresh(ef)
    fld_row = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == ef.template_field_id).first()
    return ExtractedFieldOut(
        id=str(ef.id),
        document_id=str(ef.document_id),
        template_field_id=str(ef.template_field_id),
        extracted_value=ef.extracted_value,
        value=ef.value,
        confidence=ef.confidence,
        field_name=(fld_row.name if fld_row else ""),
        field_label=(fld_row.label if fld_row else ""),
        created_at=ef.created_at,
        updated_at=ef.updated_at,
    )


@router.delete("/documents/{document_id}/fields/{field_id}", status_code=204)
def delete_extracted_field(document_id: str, field_id: str, db: Session = Depends(get_db)):
    doc_uuid = _parse_uuid(document_id, "document id")
    fld_uuid = _parse_uuid(field_id, "field id")

    ef = (
        db.query(ExtractedField)
        .join(Document, Document.id == ExtractedField.document_id)
        .filter(ExtractedField.id == fld_uuid, Document.id == doc_uuid)
        .first()
    )
    if not ef:
        raise HTTPException(status_code=404, detail="Field not found")

    db.delete(ef)
    db.commit()
    return None
