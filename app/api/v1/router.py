from fastapi import APIRouter
from app.api.v1.endpoints import templates, documents

api_router = APIRouter()
api_router.include_router(templates.router)
api_router.include_router(documents.router)
