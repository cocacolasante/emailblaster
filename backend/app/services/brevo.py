"""Brevo (formerly Sendinblue) transactional email API client."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT_SECONDS = 30.0


async def send_email(
    *,
    to_email: str,
    to_name: str | None,
    subject: str,
    html_body: str,
    text_body: str,
    sender_name: str,
    sender_email: str,
    campaign_id: str,
    lead_id: str,
) -> str:
    """Send a transactional email via Brevo. Returns the Brevo messageId.

    Raises httpx.HTTPStatusError on non-2xx response, or RuntimeError when
    BREVO_API_KEY is not configured.
    """
    if not settings.BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY is not configured")

    recipient: dict[str, Any] = {"email": to_email}
    if to_name:
        recipient["name"] = to_name

    payload: dict[str, Any] = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [recipient],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
        "headers": {
            "X-Campaign-ID": str(campaign_id),
            "X-Lead-ID": str(lead_id),
        },
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            BREVO_URL,
            json=payload,
            headers={
                "api-key": settings.BREVO_API_KEY,
                "accept": "application/json",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json() or {}
        message_id = data.get("messageId")
        if not message_id:
            raise RuntimeError("Brevo response missing messageId")
        return str(message_id)
