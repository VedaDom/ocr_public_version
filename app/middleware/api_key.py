from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.db import SessionLocal
from app.domain.models.api_key import OrganizationApiKey
from app.domain.models.api_call_log import ApiCallLog

UTC = timezone.utc


_rate_lock = threading.Lock()
_rate_state: dict[str, tuple[int, int]] = {}


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        db = SessionLocal()
        api_key_rec: OrganizationApiKey | None = None
        rate_headers: dict[str, str] = {}
        start = time.perf_counter()
        status_code = 500
        try:
            auth = request.headers.get("authorization") or request.headers.get("Authorization")
            if auth and auth.lower().startswith("bearer "):
                token = auth.split(" ", 1)[1].strip()
                if token.startswith("ak_"):
                    parts = token.split("_", 2)
                    if len(parts) == 3:
                        prefix = parts[1]
                        secret = parts[2]
                        api_key_rec = (
                            db.query(OrganizationApiKey)
                            .filter(OrganizationApiKey.prefix == prefix)
                            .first()
                        )
                        if not api_key_rec:
                            status_code = 401
                            return JSONResponse({"detail": "Invalid API key"}, status_code=status_code)
                        full_key = f"ak_{prefix}_{secret}"
                        hashed = hashlib.sha256(full_key.encode("utf-8")).hexdigest()
                        if hashed != api_key_rec.hashed_key:
                            status_code = 401
                            return JSONResponse({"detail": "Invalid API key"}, status_code=status_code)
                        now = datetime.now(UTC)
                        if api_key_rec.revoked:
                            status_code = 401
                            return JSONResponse({"detail": "API key revoked"}, status_code=status_code)
                        if api_key_rec.expires_at and now > api_key_rec.expires_at:
                            status_code = 401
                            return JSONResponse({"detail": "API key expired"}, status_code=status_code)
                        api_key_rec.last_used_at = now
                        db.add(api_key_rec)
                        db.commit()
                        request.state.api_key = api_key_rec
                        if api_key_rec.rate_limit_per_min is not None and api_key_rec.rate_limit_per_min > 0:
                            minute = int(time.time() // 60)
                            key = str(api_key_rec.id)
                            with _rate_lock:
                                entry_min, count = _rate_state.get(key, (minute, 0))
                                if entry_min != minute:
                                    entry_min = minute
                                    count = 0
                                if count + 1 > api_key_rec.rate_limit_per_min:
                                    retry_after = 60 - int(time.time() % 60)
                                    status_code = 429
                                    return JSONResponse(
                                        {"detail": "Rate limit exceeded"},
                                        status_code=status_code,
                                        headers={
                                            "Retry-After": str(retry_after),
                                            "X-RateLimit-Limit": str(api_key_rec.rate_limit_per_min),
                                            "X-RateLimit-Remaining": "0",
                                            "X-RateLimit-Reset": str(retry_after),
                                        },
                                    )
                                count += 1
                                _rate_state[key] = (entry_min, count)
                                remaining = max(api_key_rec.rate_limit_per_min - count, 0)
                                rate_headers = {
                                    "X-RateLimit-Limit": str(api_key_rec.rate_limit_per_min),
                                    "X-RateLimit-Remaining": str(remaining),
                                    "X-RateLimit-Reset": str(60 - int(time.time() % 60)),
                                }
            else:
                request.state.api_key = None

            response = await call_next(request)
            status_code = response.status_code
            for k, v in rate_headers.items():
                response.headers[k] = v
            return response
        finally:
            try:
                end = time.perf_counter()
                duration_ms = int((end - start) * 1000)
                url = request.url
                path = url.path
                if url.query:
                    path = f"{path}?{url.query}"
                ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
                ua = request.headers.get("user-agent")
                log = ApiCallLog(
                    org_id=(api_key_rec.org_id if api_key_rec else None),
                    api_key_id=(api_key_rec.id if api_key_rec else None),
                    user_id=None,
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    ip=ip,
                    user_agent=ua,
                )
                db.add(log)
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                db.close()
