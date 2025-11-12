from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.domain.models.user import User
from app.infrastructure.db import get_db
from app.schemas.users import UserUpdate
from app.schemas.auth import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    u = db.query(User).filter(User.id == user.id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    u.names = payload.names
    db.add(u)
    db.commit()
    db.refresh(u)
    return UserOut(id=str(u.id), email=u.email, names=u.names)
