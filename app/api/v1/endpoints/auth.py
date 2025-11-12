from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, get_current_user
from app.domain.models.email_login_token import EmailLoginToken
from app.domain.models.user import User
from app.domain.models.refresh_token import RefreshToken
from app.infrastructure.db import get_db
from app.schemas.auth import LoginRequest, TokenResponse, UserOut, VerifyRequest, RefreshRequest
from app.services.email import send_login_email
from passlib.hash import bcrypt

router = APIRouter(prefix="/auth", tags=["auth"])

UTC = timezone.utc


@router.post("/request", status_code=204)
async def request_login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email)
        db.add(user)
        db.flush()

    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(10**6):06d}"
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    login_token = EmailLoginToken(
        email=email,
        user_id=user.id,
        token=token,
        code=code,
        expires_at=expires_at,
    )
    db.add(login_token)
    db.commit()

    settings = get_settings()
    app_url = getattr(settings, "app_url", "http://localhost:3000")
    verify_link = f"{app_url}/auth/callback?token={token}"

    await send_login_email(to_email=email, verify_link=verify_link, code=code)


@router.post("/verify", response_model=TokenResponse)
def verify_token(payload: VerifyRequest, request: Request, db: Session = Depends(get_db)):
    if not payload.token and not payload.code:
        raise HTTPException(status_code=400, detail="token or code is required")

    q = db.query(EmailLoginToken).filter(EmailLoginToken.purpose == "login", EmailLoginToken.consumed_at.is_(None))
    if payload.token:
        q = q.filter(EmailLoginToken.token == payload.token)
    if payload.code:
        q = q.filter(EmailLoginToken.code == payload.code)

    login_token = q.order_by(EmailLoginToken.created_at.desc()).first()
    if not login_token:
        raise HTTPException(status_code=400, detail="Invalid or already used token/code")

    if login_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Token expired")

    user = db.query(User).filter(User.id == login_token.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="User not active")

    login_token.consumed_at = datetime.now(UTC)
    db.add(login_token)
    db.commit()

    access = create_access_token(subject=str(user.id))

    # Issue refresh token
    settings = get_settings()
    refresh_raw = secrets.token_urlsafe(32)
    refresh_hash = bcrypt.hash(refresh_raw)
    rt = RefreshToken(
        user_id=user.id,
        token_hash=refresh_hash,
        user_agent=request.headers.get("user-agent"),
        ip_address=(request.client.host if request.client else None),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_expires_days),
    )
    db.add(rt)
    db.commit()

    return TokenResponse(
        access_token=access,
        refresh_token=refresh_raw,
        user=UserOut(id=str(user.id), email=user.email, names=user.names or None),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=str(user.id), email=user.email, names=user.names or None)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    if not payload.refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    now = datetime.now(UTC)
    # Find matching refresh token (scan and verify)
    candidates = db.query(RefreshToken).filter(RefreshToken.revoked_at.is_(None), RefreshToken.expires_at > now).all()
    matched: RefreshToken | None = None
    for t in candidates:
        if bcrypt.verify(payload.refresh_token, t.token_hash):
            matched = t
            break
    if not matched:
        raise HTTPException(status_code=400, detail="Invalid or expired refresh token")

    user = db.query(User).filter(User.id == matched.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="User not active")

    # Rotate refresh token
    matched.revoked_at = now
    db.add(matched)

    settings = get_settings()
    new_refresh_raw = secrets.token_urlsafe(32)
    new_refresh_hash = bcrypt.hash(new_refresh_raw)
    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=new_refresh_hash,
        user_agent=request.headers.get("user-agent"),
        ip_address=(request.client.host if request.client else None),
        expires_at=now + timedelta(days=settings.refresh_expires_days),
    )
    db.add(new_rt)
    db.commit()

    access = create_access_token(subject=str(user.id))
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh_raw,
        user=UserOut(id=str(user.id), email=user.email, names=user.names or None),
    )
