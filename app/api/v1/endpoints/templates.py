from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.security import get_current_user, require_permission
from app.infrastructure.db import get_db
from app.domain.models.organization import Organization
from app.domain.models.membership import Membership
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.domain.models.user import User
from app.domain.models.template_gen_job import TemplateGenJob
from app.schemas.templates import (
    TemplateCreate,
    TemplateUpdate,
    TemplateOut,
    TemplateDetailOut,
    TemplateFieldCreate,
    TemplateFieldUpdate,
    TemplateFieldOut,
    TemplateGenJobOut,
)
from app.services.ocr.template_gen import TemplateGenerator
from app.services.ocr.template_job import process_template_gen_job
from app.core.config import get_settings
from app.services.credits import debit_if_possible, refund, InsufficientCreditsError
from app.services.rustfs import get_rustfs_client

router = APIRouter(prefix="/orgs", tags=["ocr"])


def _parse_uuid(id_str: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


@router.get("/{org_id}/ocr/templates", response_model=list[TemplateOut])
def list_templates(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")

    # Must be a member to list
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    rows = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.org_id == org_uuid)
        .order_by(DocumentTemplate.name.asc())
        .all()
    )
    out: list[TemplateOut] = []
    for t in rows:
        out.append(
            TemplateOut(
                id=str(t.id),
                org_id=str(t.org_id),
                name=t.name,
                description=t.description,
                created_by_id=str(t.created_by_id),
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


@router.post("/{org_id}/ocr/templates", response_model=TemplateOut, status_code=201)
def create_template(org_id: str, payload: TemplateCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")

    # Permission check
    require_permission(db, user, org_uuid, "org.manage")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Unique name per org
    existing = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.org_id == org_uuid, DocumentTemplate.name == payload.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Template name already exists")

    t = DocumentTemplate(org_id=org_uuid, name=payload.name, description=payload.description, created_by_id=user.id)
    db.add(t)
    db.commit()
    db.refresh(t)

    return TemplateOut(
        id=str(t.id),
        org_id=str(t.org_id),
        name=t.name,
        description=t.description,
        created_by_id=str(t.created_by_id),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _job_out(job: TemplateGenJob) -> TemplateGenJobOut:
    return TemplateGenJobOut(
        id=str(job.id),
        org_id=str(job.org_id),
        created_by_id=str(job.created_by_id),
        pdf_url=job.pdf_url,
        name=job.name,
        description=job.description,
        status=job.status,
        error_message=job.error_message,
        template_id=(str(job.template_id) if job.template_id else None),
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.post("/{org_id}/ocr/templates/generate", response_model=TemplateGenJobOut, status_code=202)
async def generate_template_from_pdf(
    org_id: str,
    pdf: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=""),
    idempotency_key: str | None = Form(default=None),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org_uuid = _parse_uuid(org_id, "org id")

    # Allow any member to generate templates
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not pdf or not pdf.filename:
        raise HTTPException(status_code=400, detail="PDF file is required")
    if (pdf.content_type or "").lower() != "application/pdf" and not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Upload the PDF to storage for background processing
    data = await pdf.read()
    rustfs = get_rustfs_client()
    pdf_url = await rustfs.upload_file(data, pdf.filename, pdf.content_type or "application/pdf")

    # Idempotency: if caller provided a key, return existing job if present
    if idempotency_key:
        existing = (
            db.query(TemplateGenJob)
            .filter(TemplateGenJob.org_id == org_uuid, TemplateGenJob.idempotency_key == idempotency_key)
            .first()
        )
        if existing:
            # If queued, ensure background task is scheduled again (best-effort)
            if existing.status == "queued" and background_tasks is not None:
                background_tasks.add_task(process_template_gen_job, existing.id)
            return _job_out(existing)

    job = TemplateGenJob(
        org_id=org_uuid,
        created_by_id=user.id,
        pdf_url=pdf_url,
        name=(name or (pdf.filename.rsplit(".", 1)[0])).strip()[:200],
        description=(description or "")[:500],
        idempotency_key=(idempotency_key or None),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if background_tasks is not None:
        background_tasks.add_task(process_template_gen_job, job.id)

    return _job_out(job)


@router.get("/{org_id}/ocr/templates/{template_id}", response_model=TemplateDetailOut)
def get_template(org_id: str, template_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")

    # Must be a member to view
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    fields = (
        db.query(DocumentTemplateField)
        .filter(DocumentTemplateField.template_id == t.id)
        .order_by(DocumentTemplateField.order_index.asc(), DocumentTemplateField.created_at.asc())
        .all()
    )

    return TemplateDetailOut(
        id=str(t.id),
        org_id=str(t.org_id),
        name=t.name,
        description=t.description,
        created_by_id=str(t.created_by_id),
        created_at=t.created_at,
        updated_at=t.updated_at,
        fields=[
            TemplateFieldOut(
                id=str(f.id),
                template_id=str(f.template_id),
                name=f.name,
                label=f.label,
                field_type=f.field_type,
                required=f.required,
                description=f.description,
                order_index=f.order_index,
                created_at=f.created_at,
                updated_at=f.updated_at,
            )
            for f in fields
        ],
    )


@router.patch("/{org_id}/ocr/templates/{template_id}", response_model=TemplateOut)
def update_template(org_id: str, template_id: str, payload: TemplateUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")

    require_permission(db, user, org_uuid, "org.manage")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    if payload.name and payload.name != t.name:
        conflict = (
            db.query(DocumentTemplate)
            .filter(DocumentTemplate.org_id == org_uuid, DocumentTemplate.name == payload.name)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=409, detail="Template name already exists")
        t.name = payload.name
    if payload.description is not None:
        t.description = payload.description

    db.add(t)
    db.commit()
    db.refresh(t)

    return TemplateOut(
        id=str(t.id),
        org_id=str(t.org_id),
        name=t.name,
        description=t.description,
        created_by_id=str(t.created_by_id),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.delete("/{org_id}/ocr/templates/{template_id}", status_code=204)
def delete_template(org_id: str, template_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")

    require_permission(db, user, org_uuid, "org.manage")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    db.delete(t)
    db.commit()
    return None


@router.get("/{org_id}/ocr/templates/generate/{job_id}", response_model=TemplateGenJobOut)
def get_template_gen_job(org_id: str, job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    try:
        jid = uuid.UUID(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job id")

    # Must be a member to view
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    job = db.query(TemplateGenJob).filter(TemplateGenJob.id == jid, TemplateGenJob.org_id == org_uuid).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_out(job)


@router.post("/{org_id}/ocr/templates/{template_id}/fields", response_model=TemplateFieldOut, status_code=201)
def create_field(org_id: str, template_id: str, payload: TemplateFieldCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")

    require_permission(db, user, org_uuid, "org.manage")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    # Ensure unique name per template
    existing = (
        db.query(DocumentTemplateField)
        .filter(DocumentTemplateField.template_id == t.id, DocumentTemplateField.name == payload.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Field name already exists")

    # Determine default order as max+1 if not provided explicitly
    order_index = payload.order_index
    if order_index == 0:
        max_order = (
            db.query(func.coalesce(func.max(DocumentTemplateField.order_index), 0))
            .filter(DocumentTemplateField.template_id == t.id)
            .scalar()
        )
        order_index = int(max_order) + 1

    f = DocumentTemplateField(
        template_id=t.id,
        name=payload.name,
        label=payload.label,
        field_type=payload.field_type,
        required=bool(payload.required),
        description=payload.description or "",
        order_index=order_index,
    )
    db.add(f)
    db.commit()
    db.refresh(f)

    return TemplateFieldOut(
        id=str(f.id),
        template_id=str(f.template_id),
        name=f.name,
        label=f.label,
        field_type=f.field_type,
        required=f.required,
        description=f.description,
        order_index=f.order_index,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


@router.get("/{org_id}/ocr/templates/{template_id}/fields", response_model=list[TemplateFieldOut])
def list_fields(org_id: str, template_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")

    # Must be a member to view fields
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    fields = (
        db.query(DocumentTemplateField)
        .filter(DocumentTemplateField.template_id == t.id)
        .order_by(DocumentTemplateField.order_index.asc(), DocumentTemplateField.created_at.asc())
        .all()
    )

    out: list[TemplateFieldOut] = []
    for f in fields:
        out.append(
            TemplateFieldOut(
                id=str(f.id),
                template_id=str(f.template_id),
                name=f.name,
                label=f.label,
                field_type=f.field_type,
                required=f.required,
                description=f.description,
                order_index=f.order_index,
                created_at=f.created_at,
                updated_at=f.updated_at,
            )
        )
    return out


@router.patch("/{org_id}/ocr/templates/{template_id}/fields/{field_id}", response_model=TemplateFieldOut)
def update_field(org_id: str, template_id: str, field_id: str, payload: TemplateFieldUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")
    fld_uuid = _parse_uuid(field_id, "field id")

    require_permission(db, user, org_uuid, "org.manage")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    f = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == fld_uuid, DocumentTemplateField.template_id == t.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Field not found")

    if payload.label is not None:
        f.label = payload.label
    if payload.field_type is not None:
        f.field_type = payload.field_type
    if payload.required is not None:
        f.required = bool(payload.required)
    if payload.description is not None:
        f.description = payload.description
    if payload.order_index is not None:
        f.order_index = int(payload.order_index)

    db.add(f)
    db.commit()
    db.refresh(f)

    return TemplateFieldOut(
        id=str(f.id),
        template_id=str(f.template_id),
        name=f.name,
        label=f.label,
        field_type=f.field_type,
        required=f.required,
        description=f.description,
        order_index=f.order_index,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


@router.delete("/{org_id}/ocr/templates/{template_id}/fields/{field_id}", status_code=204)
def delete_field(org_id: str, template_id: str, field_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    tpl_uuid = _parse_uuid(template_id, "template id")
    fld_uuid = _parse_uuid(field_id, "field id")

    require_permission(db, user, org_uuid, "org.manage")

    t = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == tpl_uuid, DocumentTemplate.org_id == org_uuid)
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    f = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == fld_uuid, DocumentTemplateField.template_id == t.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Field not found")

    db.delete(f)
    db.commit()
    return None
