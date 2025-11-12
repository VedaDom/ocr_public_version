from fastapi import APIRouter
from app.api.v1.endpoints import health, auth, orgs, users, templates, documents, api_keys

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(orgs.router)
api_router.include_router(users.router)
api_router.include_router(templates.router)
api_router.include_router(documents.router)
api_router.include_router(api_keys.router)
