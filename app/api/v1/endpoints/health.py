import time
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "api", "version": "0.1.0", "ts": int(time.time())}
