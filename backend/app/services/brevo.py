"""Brevo (formerly Sendinblue) transactional email API client."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_EVENTS_URL = "https://api.brevo.com/v3/smtp/statistics/events"
_TIMEOUT_SECONDS = 30.0
# Brevo caps a single events page at 5000.  For a single-operator inbox
# doing < 500 emails/day this always fits in one page; the worker still
# paginates in case of a backlog catch-up after worker downtime.
_EVENTS_PAGE_LIMIT = 5000


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


async def fetch_events(
    *,
    start_date: str,
    end_date: str,
    limit: int = _EVENTS_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """Pull transactional events from Brevo's statistics endpoint.

    Date strings are ``YYYY-MM-DD`` — that's the granularity the API
    supports.  The poller still filters in-memory by ISO timestamp so the
    day-resolution query doesn't double-process events; see
    ``brevo_events.process_event`` for the per-event dedup guard.

    Paginates via ``offset`` until the response is short of ``limit``.
    """
    if not settings.BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY is not configured")

    out: list[dict[str, Any]] = []
    offset = 0
    headers = {
        "api-key": settings.BREVO_API_KEY,
        "accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        while True:
            params = {
                "startDate": start_date,
                "endDate": end_date,
                "limit": limit,
                "offset": offset,
                "sort": "asc",
            }
            resp = await client.get(BREVO_EVENTS_URL, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json() or {}
            page = payload.get("events") or []
            if not isinstance(page, list):
                break
            out.extend(page)
            if len(page) < limit:
                break
            offset += limit
    return out
