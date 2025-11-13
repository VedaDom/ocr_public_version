import ipaddress
from fastapi import FastAPI, Request
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from app.middleware.api_key import ApiKeyAuthMiddleware

from app.core.config import get_settings
from app.infrastructure.db import SessionLocal
from app.domain.models.organization import Organization
from app.domain.models.role import Role
from app.domain.models.membership import Membership
from app.api.v1.router import api_router

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ApiKeyAuthMiddleware)


# @app.middleware("http")
# async def restrict_docs_to_ip(request: Request, call_next):
#     path = request.url.path
#     if path in ("/docs", "/redoc", "/openapi.json"):
#         host = request.url.hostname or ""
#         is_ip = False
#         try:
#             ipaddress.ip_address(host)
#             is_ip = True
#         except ValueError:
#             # allow localhost for local dev
#             if host == "localhost":
#                 is_ip = True
#         if not is_ip:
#             return Response(status_code=404)
#     return await call_next(request)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.on_event("startup")
def backfill_org_credits_on_startup() -> None:
    db = SessionLocal()
    try:
        db.execute(text(
            """
            INSERT INTO org_credits (org_id, balance, created_at, updated_at)
            SELECT org_id, COALESCE(SUM(delta), 0) AS balance, now(), now()
            FROM credits_ledger
            GROUP BY org_id
            ON CONFLICT (org_id) DO NOTHING
            """
        ))
        db.execute(text(
            """
            UPDATE org_credits oc
            SET balance = sub.balance, updated_at = now()
            FROM (
                SELECT org_id, COALESCE(SUM(delta), 0) AS balance
                FROM credits_ledger
                GROUP BY org_id
            ) sub
            WHERE oc.org_id = sub.org_id AND oc.balance <> sub.balance
            """
        ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@app.on_event("startup")
def backfill_owner_memberships_on_startup() -> None:
    db = SessionLocal()
    try:
        orgs = db.query(Organization).all()
        for org in orgs:
            if not getattr(org, "owner_id", None):
                continue
            owner_role = db.query(Role).filter(Role.org_id == org.id, Role.name == "OWNER").first()
            if not owner_role:
                owner_role = Role(org_id=org.id, name="OWNER", description="Organization owner")
                db.add(owner_role)
                db.flush()
            exists = (
                db.query(Membership)
                .filter(Membership.user_id == org.owner_id, Membership.org_id == org.id)
                .first()
            )
            if not exists:
                db.add(Membership(user_id=org.owner_id, org_id=org.id, role_id=owner_role.id))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

@app.get("/")
def root():
    return {"status": "ok"}
