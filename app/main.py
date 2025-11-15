from fastapi import FastAPI, Depends
from typing import Any
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.api.v1.router import api_router
from app.infrastructure.db import get_db
from app.domain.models.analytics_event import AnalyticsEvent

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



app.include_router(api_router, prefix=settings.api_v1_prefix)

@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/analytics")
async def receive_analytics(payload: dict[str, Any], db: Session = Depends(get_db)):
    # Public endpoint to receive analytics payloads; best-effort, no auth
    event_type = str(payload.get("type") or "")[:64]
    ev = AnalyticsEvent(event_type=event_type, payload=payload)
    db.add(ev)
    db.commit()
    return {"status": "received"}
