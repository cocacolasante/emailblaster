from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    EmailEvent,
    EmailEventType,
    Lead,
    Suppression,
    SuppressionReason,
)

logger = logging.getLogger(__name__)

# No prefix — Brevo posts to /webhooks/brevo and the compose worker emits
# unsubscribe links at /unsubscribe/{lead_id} (both at root).
router = APIRouter(tags=["webhooks"])


# --------------------------------------------------------------------------
# Brevo event mapping
# --------------------------------------------------------------------------

_BREVO_EVENT_MAP: dict[str, EmailEventType] = {
    "delivered": EmailEventType.DELIVERED,
    "request": EmailEventType.DELIVERED,  # Brevo "request" precedes delivery
    "opened": EmailEventType.OPENED,
    "unique_opened": EmailEventType.OPENED,
    "click": EmailEventType.CLICKED,
    "clicked": EmailEventType.CLICKED,
    "unique_clicked": EmailEventType.CLICKED,
    "soft_bounce": EmailEventType.SOFT_BOUNCE,
    "hard_bounce": EmailEventType.HARD_BOUNCE,
    "spam": EmailEventType.SPAM,
    "unsubscribed": EmailEventType.UNSUBSCRIBED,
}

_SUPPRESSION_MAP: dict[EmailEventType, SuppressionReason] = {
    EmailEventType.HARD_BOUNCE: SuppressionReason.HARD_BOUNCE,
    EmailEventType.SPAM: SuppressionReason.SPAM,
    EmailEventType.UNSUBSCRIBED: SuppressionReason.UNSUBSCRIBED,
}


def _extract_message_id(event: dict[str, Any]) -> str | None:
    """Brevo's payload varies — try the documented field names."""
    for key in ("message-id", "messageId", "message_id"):
        v = event.get(key)
        if v:
            return str(v).strip("<>")
    return None


async def _process_event(db: AsyncSession, event: dict[str, Any]) -> None:
    brevo_event = str(event.get("event") or "").lower()
    event_type = _BREVO_EVENT_MAP.get(brevo_event)
    if event_type is None:
        return  # ignore unknown events

    msg_id = _extract_message_id(event)
    if not msg_id:
        return

    lead = await db.scalar(
        select(Lead).where(Lead.brevo_message_id == msg_id)
    )
    if lead is None:
        return

    db.add(EmailEvent(
        lead_id=lead.id,
        campaign_id=lead.campaign_id,
        event_type=event_type,
        event_data=event,
    ))

    suppression_reason = _SUPPRESSION_MAP.get(event_type)
    if suppression_reason is not None:
        existing = await db.scalar(
            select(Suppression).where(Suppression.email == lead.email)
        )
        if existing is None:
            db.add(Suppression(email=lead.email, reason=suppression_reason))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post("/webhooks/brevo")
async def brevo_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}")

    events = payload if isinstance(payload, list) else [payload]
    processed = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            await _process_event(db, ev)
            processed += 1
        except Exception:  # noqa: BLE001
            # Swallow per-event errors so one malformed entry doesn't block
            # the rest of the batch — Brevo will retry the whole webhook.
            logger.exception("brevo webhook event failed: %s", ev.get("event"))
    await db.commit()
    return {"status": "ok", "processed": processed}


_UNSUBSCRIBE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Unsubscribed</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 80px auto; padding: 20px; text-align: center; color: #333; line-height: 1.5;">
<h1 style="font-size: 24px;">You've been unsubscribed</h1>
<p>You will not receive further emails from us.</p>
</body>
</html>
"""

_INVALID_UNSUB_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Invalid link</title></head>
<body style="font-family: sans-serif; text-align: center; margin: 80px auto;">
<h1>Invalid unsubscribe link</h1>
<p>This link is not valid or has expired.</p>
</body>
</html>
"""


@router.get("/unsubscribe/{lead_id}", response_class=HTMLResponse)
async def unsubscribe(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        return HTMLResponse(_INVALID_UNSUB_HTML, status_code=404)

    existing = await db.scalar(
        select(Suppression).where(Suppression.email == lead.email)
    )
    if existing is None:
        db.add(Suppression(
            email=lead.email,
            reason=SuppressionReason.UNSUBSCRIBED,
        ))

    db.add(EmailEvent(
        lead_id=lead.id,
        campaign_id=lead.campaign_id,
        event_type=EmailEventType.UNSUBSCRIBED,
        event_data={"source": "one_click_link"},
    ))
    await db.commit()
    return HTMLResponse(_UNSUBSCRIBE_HTML)
