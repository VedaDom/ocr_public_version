from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.security import get_current_user, require_permission
from app.infrastructure.db import get_db
from app.domain.models.organization import Organization
from app.domain.models.membership import Membership
from app.domain.models.template import DocumentTemplate
from app.domain.models.document_template_field import DocumentTemplateField
from app.domain.models.user import User
from app.schemas.templates import (
    TemplateCreate,
    TemplateUpdate,
    TemplateOut,
    TemplateDetailOut,
    TemplateFieldCreate,
    TemplateFieldUpdate,
    TemplateFieldOut,
)
from app.services.ocr.template_gen import TemplateGenerator

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


@router.post("/{org_id}/ocr/templates/generate", response_model=TemplateDetailOut, status_code=201)
async def generate_template_from_pdf(
    org_id: str,
    pdf: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org_uuid = _parse_uuid(org_id, "org id")

    # Permission check
    require_permission(db, user, org_uuid, "org.manage")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not pdf or not pdf.filename:
        raise HTTPException(status_code=400, detail="PDF file is required")
    if (pdf.content_type or "").lower() != "application/pdf" and not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    data = await pdf.read()
    try:
        gen = TemplateGenerator()
        result = gen.generate(pdf_bytes=data, content_type=pdf.content_type or "application/pdf")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Template generation failed: {e}")

    # Determine template name
    tpl_name = (name or pdf.filename.rsplit(".", 1)[0]).strip()[:200]
    if not tpl_name:
        tpl_name = "Generated Template"

    # Ensure unique name per org
    base_name = tpl_name
    suffix = 1
    while db.query(DocumentTemplate).filter(DocumentTemplate.org_id == org_uuid, DocumentTemplate.name == tpl_name).first():
        tpl_name = f"{base_name} ({suffix})"
        suffix += 1

    # Create template
    t = DocumentTemplate(org_id=org_uuid, name=tpl_name, description=(description or "")[:500], created_by_id=user.id)
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
    db.refresh(t)

    # Load created fields ordered
    created_fields = (
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
            for f in created_fields
        ],
    )


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
