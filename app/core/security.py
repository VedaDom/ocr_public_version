from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import uuid

from app.core.config import get_settings
from app.infrastructure.db import Session, get_db
from app.domain.models.user import User
from app.domain.models.membership import Membership
from app.domain.models.role import Role
from app.domain.models.role_permission import RolePermission
from app.domain.models.permission import Permission


UTC = timezone.utc

_settings = get_settings()
_jwt_secret = _settings.jwt_secret
_algorithm = _settings.jwt_algorithm
_expires_minutes = _settings.jwt_expires_minutes

auth_scheme = HTTPBearer(auto_error=False)


def create_access_token(*, subject: str, expires_minutes: int | None = None) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=expires_minutes or _expires_minutes)
    to_encode = {"sub": subject, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    return jwt.encode(to_encode, _jwt_secret, algorithm=_algorithm)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret, algorithms=[_algorithm])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(auth_scheme)],
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    try:
        user_id = uuid.UUID(sub)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_permission(db: Session, user: User, org_id: uuid.UUID, perm_name: str) -> None:
    rec = (
        db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(Membership, Membership.role_id == Role.id)
        .filter(
            Membership.user_id == user.id,
            Membership.org_id == org_id,
            Permission.name == perm_name,
        )
        .first()
    )
    if rec is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
