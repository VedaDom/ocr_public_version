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
from app.domain.models.user import User
from app.schemas.documents import (
    DocumentOut,
    OcrJobOut,
    DocumentUploadResponse,
    DocumentBatchOut,
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
