from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_permission
from app.domain.models.credit import CreditsLedger
from app.domain.models.membership import Membership
from app.domain.models.organization import Organization
from app.domain.models.user import User
from app.domain.models.role import Role
from app.domain.models.permission import Permission
from app.domain.models.role_permission import RolePermission
from app.infrastructure.db import get_db
from app.schemas.orgs import OrgCreate, OrgOut
from app.schemas.credits import TopUpRequest, BalanceResponse
from app.schemas.members import MemberOut, MemberAddRequest, MemberUpdateRequest

router = APIRouter(prefix="/orgs", tags=["orgs"])

UTC = timezone.utc


@router.get("/")
def list_orgs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    orgs = (
        db.query(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return [OrgOut(id=str(o.id), name=o.name) for o in orgs]


@router.post("/", response_model=OrgOut)
def create_org(payload: OrgCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Determine if this is user's first owned org
    existing_owned = db.query(Organization).filter(Organization.owner_id == user.id).count()

    org = Organization(name=payload.name, owner_id=user.id)
    db.add(org)
    db.flush()

    # Seed default roles for this org
    owner_role = Role(org_id=org.id, name="OWNER", description="Organization owner")
    admin_role = Role(org_id=org.id, name="ADMIN", description="Organization admin")
    member_role = Role(org_id=org.id, name="MEMBER", description="Standard member")
    viewer_role = Role(org_id=org.id, name="VIEWER", description="Read-only")
    db.add_all([owner_role, admin_role, member_role, viewer_role])
    db.flush()

    # Ensure permissions exist (global unique)
    def ensure_perm(name: str, desc: str | None = None) -> Permission:
        p = db.query(Permission).filter(Permission.name == name).first()
        if not p:
            p = Permission(name=name, description=desc)
            db.add(p)
            db.flush()
        return p

    perm_org_manage = ensure_perm("org.manage", "Manage organization settings and roles")
    perm_credits_topup = ensure_perm("credits.topup", "Top up organization credits")

    # Assign permissions to roles
    db.add_all([
        RolePermission(role_id=owner_role.id, permission_id=perm_org_manage.id),
        RolePermission(role_id=owner_role.id, permission_id=perm_credits_topup.id),
        RolePermission(role_id=admin_role.id, permission_id=perm_credits_topup.id),
        RolePermission(role_id=admin_role.id, permission_id=perm_org_manage.id),
    ])

    member = Membership(user_id=user.id, org_id=org.id, role_id=owner_role.id)
    db.add(member)

    if existing_owned == 0:
        grant = CreditsLedger(org_id=org.id, delta=30, reason="trial_grant")
        db.add(grant)

    db.commit()

    return OrgOut(id=str(org.id), name=org.name)


@router.get("/{org_id}/credits/balance", response_model=BalanceResponse)
def credits_balance(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid org id")

    # User must be a member to view balance
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    balance = (
        db.query(func.coalesce(func.sum(CreditsLedger.delta), 0)).filter(CreditsLedger.org_id == org_uuid).scalar()
    )
    return BalanceResponse(org_id=str(org_uuid), balance=int(balance or 0))


# Members


@router.get("/{org_id}/members", response_model=list[MemberOut])
def list_members(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid org id")

    # Only org members can view
    member = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_uuid).first()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")

    rows = (
        db.query(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .filter(Membership.org_id == org_uuid)
        .order_by(User.email.asc())
        .all()
    )

    out: list[MemberOut] = []
    for m, u, r in rows:
        out.append(
            MemberOut(
                id=str(m.id),
                user_id=str(u.id),
                email=u.email,
                names=u.names or None,
                role_id=str(r.id),
                role_name=r.name,
                created_at=m.created_at,
            )
        )
    return out


@router.post("/{org_id}/members", response_model=MemberOut, status_code=201)
def add_member(org_id: str, payload: MemberAddRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid org id")

    # Permission check
    require_permission(db, user, org_uuid, "org.manage")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    email = payload.email.lower()
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, names=(payload.names or ""))
        db.add(u)
        db.flush()
    elif payload.names and not (u.names and u.names.strip()):
        u.names = payload.names
        db.add(u)
        db.flush()

    existing = db.query(Membership).filter(Membership.user_id == u.id, Membership.org_id == org_uuid).first()
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member")

    role: Role | None = None
    if payload.role_id:
        try:
            rid = uuid.UUID(payload.role_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid role_id")
        role = db.query(Role).filter(Role.id == rid, Role.org_id == org_uuid).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
    else:
        role = db.query(Role).filter(Role.name == "MEMBER", Role.org_id == org_uuid).first()
        if not role:
            raise HTTPException(status_code=500, detail="Default role not found")

    m = Membership(user_id=u.id, org_id=org_uuid, role_id=role.id)
    db.add(m)
    db.commit()
    db.refresh(m)

    return MemberOut(
        id=str(m.id),
        user_id=str(u.id),
        email=u.email,
        names=u.names or None,
        role_id=str(role.id),
        role_name=role.name,
        created_at=m.created_at,
    )


@router.patch("/{org_id}/members/{member_id}", response_model=MemberOut)
def update_member(org_id: str, member_id: str, payload: MemberUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
        mem_uuid = uuid.UUID(member_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

    require_permission(db, user, org_uuid, "org.manage")

    m = db.query(Membership).filter(Membership.id == mem_uuid, Membership.org_id == org_uuid).first()
    if not m:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Resolve role
    role: Role | None = None
    if payload.role_id:
        try:
            rid = uuid.UUID(payload.role_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid role_id")
        role = db.query(Role).filter(Role.id == rid, Role.org_id == org_uuid).first()
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

    if role:
        # Prevent demoting last OWNER
        old_role = db.query(Role).filter(Role.id == m.role_id).first()
        if old_role and old_role.name == "OWNER" and role.name != "OWNER":
            owners = (
                db.query(Membership)
                .join(Role, Role.id == Membership.role_id)
                .filter(Membership.org_id == org_uuid, Role.name == "OWNER")
                .count()
            )
            if owners <= 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last OWNER")
        m.role_id = role.id

    db.add(m)
    db.commit()
    db.refresh(m)
    u = db.query(User).filter(User.id == m.user_id).first()
    r = db.query(Role).filter(Role.id == m.role_id).first()

    return MemberOut(
        id=str(m.id),
        user_id=str(u.id),
        email=u.email,
        names=u.names or None,
        role_id=str(r.id),
        role_name=r.name,
        created_at=m.created_at,
    )


@router.delete("/{org_id}/members/{member_id}", status_code=204)
def remove_member(org_id: str, member_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
        mem_uuid = uuid.UUID(member_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

    require_permission(db, user, org_uuid, "org.manage")

    m = db.query(Membership).filter(Membership.id == mem_uuid, Membership.org_id == org_uuid).first()
    if not m:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Prevent removing last OWNER
    role = db.query(Role).filter(Role.id == m.role_id).first()
    if role and role.name == "OWNER":
        owners = (
            db.query(Membership)
            .join(Role, Role.id == Membership.role_id)
            .filter(Membership.org_id == org_uuid, Role.name == "OWNER")
            .count()
        )
        if owners <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last OWNER")

    db.delete(m)
    db.commit()
    return None


@router.post("/{org_id}/credits/topup", response_model=BalanceResponse)
def credits_topup(org_id: str, payload: TopUpRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        org_uuid = uuid.UUID(org_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid org id")

    # Permission check
    require_permission(db, user, org_uuid, "credits.topup")

    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    entry = CreditsLedger(org_id=org_uuid, delta=int(payload.amount), reason=payload.reason or "topup")
    db.add(entry)
    db.commit()

    balance = (
        db.query(func.coalesce(func.sum(CreditsLedger.delta), 0)).filter(CreditsLedger.org_id == org_uuid).scalar()
    )
    return BalanceResponse(org_id=str(org_uuid), balance=int(balance or 0))
