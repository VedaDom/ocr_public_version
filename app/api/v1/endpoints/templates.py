from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.infrastructure.db import get_db
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.domain.models.template_gen_job import TemplateGenJob
from app.schemas.templates import (
    TemplateCreate,
    TemplateUpdate,
    TemplateOut,
    TemplateDetailOut,
    TemplateFieldCreate,
    TemplateFieldUpdate,
    TemplateFieldOut,
    TemplateGenJobCreate,
    TemplateGenJobOut,
)
from app.services.ocr.template_job import process_template_gen_job

router = APIRouter(prefix="/ocr", tags=["ocr"])


def _parse_uuid(id_str: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    rows = db.query(DocumentTemplate).order_by(DocumentTemplate.name.asc()).all()
    out: list[TemplateOut] = []
    for t in rows:
        out.append(
            TemplateOut(
                id=str(t.id),
                name=t.name,
                description=t.description,
                callback_url=t.callback_url,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return out


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    # Unique name globally
    existing = db.query(DocumentTemplate).filter(DocumentTemplate.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Template name already exists")

    t = DocumentTemplate(name=payload.name, description=payload.description, callback_url=payload.callback_url)
    db.add(t)
    db.commit()
    db.refresh(t)

    return TemplateOut(
        id=str(t.id),
        name=t.name,
        description=t.description,
        callback_url=t.callback_url,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _job_out(job: TemplateGenJob) -> TemplateGenJobOut:
    return TemplateGenJobOut(
        id=str(job.id),
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


@router.post("/templates/generate", response_model=TemplateGenJobOut, status_code=202)
def generate_template_from_pdf(
    payload: TemplateGenJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Idempotency: if caller provided a key, return existing job if present
    if payload.idempotency_key:
        existing = db.query(TemplateGenJob).filter(TemplateGenJob.idempotency_key == payload.idempotency_key).first()
        if existing:
            if existing.status == "queued":
                background_tasks.add_task(process_template_gen_job, existing.id)
            return _job_out(existing)

    job = TemplateGenJob(
        pdf_url=payload.pdf_url,
        name=(payload.name or None),
        description=(payload.description or "")[:500],
        idempotency_key=(payload.idempotency_key or None),
        callback_url=(payload.callback_url or None),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(process_template_gen_job, job.id)

    return _job_out(job)


@router.get("/templates/{template_id}", response_model=TemplateDetailOut)
def get_template(template_id: str, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
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
        name=t.name,
        description=t.description,
        callback_url=t.callback_url,
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


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: str, payload: TemplateUpdate, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    if payload.name and payload.name != t.name:
        conflict = db.query(DocumentTemplate).filter(DocumentTemplate.name == payload.name).first()
        if conflict:
            raise HTTPException(status_code=409, detail="Template name already exists")
        t.name = payload.name
    if payload.description is not None:
        t.description = payload.description
    if payload.callback_url is not None:
        t.callback_url = payload.callback_url

    db.add(t)
    db.commit()
    db.refresh(t)

    return TemplateOut(
        id=str(t.id),
        name=t.name,
        description=t.description,
        callback_url=t.callback_url,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: str, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    db.delete(t)
    db.commit()
    return None


@router.get("/templates/generate/{job_id}", response_model=TemplateGenJobOut)
def get_template_gen_job(job_id: str, db: Session = Depends(get_db)):
    try:
        jid = uuid.UUID(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job id")

    job = db.query(TemplateGenJob).filter(TemplateGenJob.id == jid).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _job_out(job)


@router.post("/templates/{template_id}/fields", response_model=TemplateFieldOut, status_code=201)
def create_field(template_id: str, payload: TemplateFieldCreate, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
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


@router.get("/templates/{template_id}/fields", response_model=list[TemplateFieldOut])
def list_fields(template_id: str, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
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


@router.patch("/templates/{template_id}/fields/{field_id}", response_model=TemplateFieldOut)
def update_field(template_id: str, field_id: str, payload: TemplateFieldUpdate, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")
    fld_uuid = _parse_uuid(field_id, "field id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
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


@router.delete("/templates/{template_id}/fields/{field_id}", status_code=204)
def delete_field(template_id: str, field_id: str, db: Session = Depends(get_db)):
    tpl_uuid = _parse_uuid(template_id, "template id")
    fld_uuid = _parse_uuid(field_id, "field id")

    t = db.query(DocumentTemplate).filter(DocumentTemplate.id == tpl_uuid).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    f = db.query(DocumentTemplateField).filter(DocumentTemplateField.id == fld_uuid, DocumentTemplateField.template_id == t.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Field not found")

    db.delete(f)
    db.commit()
    return None
