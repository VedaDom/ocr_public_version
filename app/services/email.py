from __future__ import annotations

import httpx

from app.core.config import get_settings


async def send_login_email(*, to_email: str, verify_link: str, code: str) -> None:
    settings = get_settings()
    api_key = settings.resend_api_key
    from_email = settings.resend_from_email
    if not api_key or not from_email:
        # In dev, simply no-op if not configured
        return

    html = f"""
    <div>
      <p>Use the link below to sign in:</p>
      <p><a href=\"{verify_link}\">Sign in</a></p>
      <p>Or enter this one-time code: <strong>{code}</strong></p>
      <p>This code/link expires in 15 minutes.</p>
    </div>
    """

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": "Your sign-in link",
        "html": html,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10) as client:
        await client.post("https://api.resend.com/emails", json=payload, headers=headers)
