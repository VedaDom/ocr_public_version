from __future__ import annotations

import httpx
from typing import Any

from app.core.config import get_settings


def send_analytics(payload: dict[str, Any]) -> None:
    settings = get_settings()
    url = settings.analytics_endpoint_url
    if not url:
        return
    headers = {
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(url, json=payload, headers=headers)
    except Exception:
        # Best-effort only
        pass
