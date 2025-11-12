from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_permission
from app.infrastructure.db import get_db
from app.domain.models.membership import Membership
from app.domain.models.organization import Organization
from app.domain.models.api_key import OrganizationApiKey
from app.domain.models.user import User
from app.schemas.api_keys import ApiKeyCreate, ApiKeyOut, ApiKeyCreateResponse

router = APIRouter(prefix="/orgs", tags=["api-keys"])


def _parse_uuid(id_str: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


@router.post("/{org_id}/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
def create_api_key(org_id: str, payload: ApiKeyCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")

    # Only org managers can create keys
    require_permission(db, user, org_uuid, "org.manage")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Enforce unique name per org
    existing = (
        db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.org_id == org_uuid, OrganizationApiKey.name == payload.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="API key name already exists")

    # Generate unique prefix
    prefix = None
    for _ in range(10):
        candidate = secrets.token_hex(6)  # 12 hex chars
        if not db.query(OrganizationApiKey).filter(OrganizationApiKey.prefix == candidate).first():
            prefix = candidate
            break
    if not prefix:
        raise HTTPException(status_code=500, detail="Failed to generate API key prefix")

    # Generate full key and hash
    secret_part = secrets.token_urlsafe(32)
    full_key = f"ak_{prefix}_{secret_part}"
    hashed = hashlib.sha256(full_key.encode("utf-8")).hexdigest()

    rec = OrganizationApiKey(
        org_id=org_uuid,
        name=payload.name,
        prefix=prefix,
        hashed_key=hashed,
        scopes=payload.scopes or [],
        rate_limit_per_min=payload.rate_limit_per_min,
        expires_at=payload.expires_at,
        created_by_id=user.id,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return ApiKeyCreateResponse(
        id=str(rec.id),
        org_id=str(rec.org_id),
        name=rec.name,
        prefix=rec.prefix,
        scopes=rec.scopes,
        rate_limit_per_min=rec.rate_limit_per_min,
        revoked=rec.revoked,
        expires_at=rec.expires_at,
        last_used_at=rec.last_used_at,
        created_by_id=str(rec.created_by_id),
        created_at=rec.created_at,
        updated_at=rec.updated_at,
        key=full_key,
    )


@router.get("/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")

    # Only managers can list keys
    require_permission(db, user, org_uuid, "org.manage")

    rows = (
        db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.org_id == org_uuid)
        .order_by(OrganizationApiKey.created_at.desc())
        .all()
    )
    return [
        ApiKeyOut(
            id=str(r.id),
            org_id=str(r.org_id),
            name=r.name,
            prefix=r.prefix,
            scopes=r.scopes,
            rate_limit_per_min=r.rate_limit_per_min,
            revoked=r.revoked,
            expires_at=r.expires_at,
            last_used_at=r.last_used_at,
            created_by_id=str(r.created_by_id),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.get("/{org_id}/api-keys/{key_id}", response_model=ApiKeyOut)
def get_api_key(org_id: str, key_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    key_uuid = _parse_uuid(key_id, "api key id")

    require_permission(db, user, org_uuid, "org.manage")

    rec = (
        db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.id == key_uuid, OrganizationApiKey.org_id == org_uuid)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="API key not found")

    return ApiKeyOut(
        id=str(rec.id),
        org_id=str(rec.org_id),
        name=rec.name,
        prefix=rec.prefix,
        scopes=rec.scopes,
        rate_limit_per_min=rec.rate_limit_per_min,
        revoked=rec.revoked,
        expires_at=rec.expires_at,
        last_used_at=rec.last_used_at,
        created_by_id=str(rec.created_by_id),
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


@router.post("/{org_id}/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(org_id: str, key_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_uuid = _parse_uuid(org_id, "org id")
    key_uuid = _parse_uuid(key_id, "api key id")

    require_permission(db, user, org_uuid, "org.manage")

    rec = (
        db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.id == key_uuid, OrganizationApiKey.org_id == org_uuid)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="API key not found")

    if rec.revoked:
        return ApiKeyOut(
            id=str(rec.id),
            org_id=str(rec.org_id),
            name=rec.name,
            prefix=rec.prefix,
            scopes=rec.scopes,
            rate_limit_per_min=rec.rate_limit_per_min,
            revoked=rec.revoked,
            expires_at=rec.expires_at,
            last_used_at=rec.last_used_at,
            created_by_id=str(rec.created_by_id),
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    rec.revoked = True
    rec.updated_at = datetime.now(datetime.now().astimezone().tzinfo)
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return ApiKeyOut(
        id=str(rec.id),
        org_id=str(rec.org_id),
        name=rec.name,
        prefix=rec.prefix,
        scopes=rec.scopes,
        rate_limit_per_min=rec.rate_limit_per_min,
        revoked=rec.revoked,
        expires_at=rec.expires_at,
        last_used_at=rec.last_used_at,
        created_by_id=str(rec.created_by_id),
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )
