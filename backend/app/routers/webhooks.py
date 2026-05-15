from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    Campaign,
    EmailEvent,
    EmailEventType,
    Lead,
    LinkedInAccount,
    LinkedInAccountStatus,
    LinkedInConnectionStatus,
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


# --------------------------------------------------------------------------
# Unipile webhooks
# --------------------------------------------------------------------------
#
# Unipile pushes events to us instead of us polling.  Events we care about:
#
#   account.connected     — user finished the hosted-auth flow; payload
#                           includes the new account_id + the "name" we
#                           supplied at link-creation time (our local
#                           LinkedInAccount.id), letting us correlate.
#   account.disconnected  — user revoked / cookies expired; flip status to
#                           FAILED so the sequencer stops dispatching.
#   account.checkpoint    — Unipile is waiting on captcha/2FA.  Flip to
#                           CHALLENGED and surface the URL.
#   message.received      — inbound DM on LinkedIn; update Lead's
#                           linkedin_last_reply_at + connection_status.
#   invitation.accepted   — prospect accepted our connect request; flip
#                           Lead.linkedin_connection_status -> CONNECTED.
#
# Auth: Unipile signs the raw body with HMAC-SHA256 using the shared
# secret configured in their dashboard.  Signature lives in the
# ``X-Unipile-Signature`` header (or ``Unipile-Signature`` depending on
# their delivery flavour).  We constant-time compare.  Missing secret or
# missing/invalid signature → 401 (no work done).


def _verify_unipile_auth(header_value: str | None) -> bool:
    """Compare the inbound auth header against the configured shared secret.

    Unipile doesn't HMAC-sign request bodies — when creating a webhook
    you specify a custom header + value (e.g. ``X-Unipile-Auth: <secret>``),
    Unipile echoes that exact header back on every delivery, and we
    constant-time compare the value here.  See the Unipile docs section
    "Authentication" on the Webhooks page.
    """
    expected = settings.UNIPILE_WEBHOOK_SECRET
    if not expected:
        # No secret configured → refuse everything to avoid running on
        # forged payloads.  Set UNIPILE_WEBHOOK_SECRET in .env and add the
        # same value to each Unipile webhook's headers list.
        return False
    if not header_value:
        return False
    return hmac.compare_digest(header_value.strip(), expected)


async def _handle_account_connected(
    db: AsyncSession, payload: dict[str, Any]
) -> None:
    """Bind the Unipile account_id we just learned about to the local row.

    Correlation:
    - The placeholder row was created by POST /linkedin-accounts/connect-via-unipile
      with our local id passed to Unipile as ``name``.  Unipile echoes
      that back in the webhook payload under ``name`` (or ``account.name``).
    - We look up the row by id and stamp ``unipile_account_id`` + email +
      status from the webhook body.
    """
    body = payload.get("data") or payload.get("account") or payload
    local_id_raw = (
        body.get("name")
        or (body.get("account") or {}).get("name")
        or payload.get("name")
    )
    unipile_id = (
        body.get("account_id")
        or body.get("id")
        or (body.get("account") or {}).get("id")
    )
    if not local_id_raw or not unipile_id:
        logger.warning(
            "Unipile account.connected missing name/account_id: %s",
            payload,
        )
        return
    try:
        local_id = uuid.UUID(str(local_id_raw))
    except (ValueError, TypeError):
        logger.warning(
            "Unipile account.connected name not a UUID: %r",
            local_id_raw,
        )
        return
    acc = await db.get(LinkedInAccount, local_id)
    if acc is None:
        logger.warning(
            "Unipile account.connected references unknown local id %s",
            local_id,
        )
        return
    acc.unipile_account_id = str(unipile_id)
    acc.provider_kind = "unipile"
    acc.status = LinkedInAccountStatus.OK
    acc.pending_challenge_url = None
    acc.last_error = None
    email = (
        body.get("user_email")
        or body.get("linkedin_email")
        or (body.get("user") or {}).get("email")
    )
    if email and not acc.linkedin_email.startswith("(pending"):
        # Keep the manually edited label/email if user set one; otherwise
        # take what Unipile gives us.
        pass
    elif email:
        acc.linkedin_email = email


async def _handle_account_status_change(
    db: AsyncSession, payload: dict[str, Any], new_status: LinkedInAccountStatus,
    challenge: bool = False,
) -> None:
    body = payload.get("data") or payload.get("account") or payload
    unipile_id = (
        body.get("account_id")
        or body.get("id")
        or (body.get("account") or {}).get("id")
    )
    if not unipile_id:
        return
    acc = await db.scalar(
        select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == str(unipile_id))
    )
    if acc is None:
        return
    acc.status = new_status
    if challenge:
        acc.pending_challenge_url = "https://www.linkedin.com"
    acc.last_error = (
        body.get("detail")
        or body.get("message")
        or body.get("title")
        or new_status.value
    )


async def _find_lead_for_event(
    db: AsyncSession, account: LinkedInAccount, body: dict[str, Any],
) -> Lead | None:
    """Match a Unipile event payload to one of our leads by public_identifier."""
    sender = body.get("sender") or body.get("from") or body.get("attendee") or {}
    public_id = (
        sender.get("provider_id")
        or sender.get("public_identifier")
        or sender.get("public_id")
        or body.get("provider_id")
    )
    if not public_id:
        return None
    slug = str(public_id).strip("/").split("/")[-1].lower()
    lead_rows = (await db.execute(
        select(Lead)
        .join(Campaign, Campaign.id == Lead.campaign_id)
        .where(Campaign.linkedin_account_id == account.id)
    )).scalars().all()
    for l in lead_rows:
        if not l.linkedin_url:
            continue
        their_slug = l.linkedin_url.rstrip("/").split("/")[-1].split("?")[0].lower()
        if their_slug == slug:
            return l
    return None


async def _handle_message_received(
    db: AsyncSession, payload: dict[str, Any],
) -> None:
    body = payload.get("data") or payload.get("message") or payload
    unipile_id = (
        body.get("account_id")
        or (body.get("account") or {}).get("id")
        or payload.get("account_id")
    )
    if not unipile_id:
        return
    acc = await db.scalar(
        select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == str(unipile_id))
    )
    if acc is None:
        return
    lead = await _find_lead_for_event(db, acc, body)
    if lead is None:
        return
    ts_raw = body.get("timestamp") or body.get("created_at")
    lead.linkedin_last_reply_at = _parse_iso(ts_raw) or datetime.now(timezone.utc)
    # First inbound message implies 1st-degree connection.
    if lead.linkedin_connection_status != LinkedInConnectionStatus.CONNECTED:
        lead.linkedin_connection_status = LinkedInConnectionStatus.CONNECTED


async def _handle_invitation_accepted(
    db: AsyncSession, payload: dict[str, Any],
) -> None:
    body = payload.get("data") or payload.get("invitation") or payload
    unipile_id = (
        body.get("account_id")
        or (body.get("account") or {}).get("id")
        or payload.get("account_id")
    )
    if not unipile_id:
        return
    acc = await db.scalar(
        select(LinkedInAccount).where(LinkedInAccount.unipile_account_id == str(unipile_id))
    )
    if acc is None:
        return
    lead = await _find_lead_for_event(db, acc, body)
    if lead is None:
        return
    lead.linkedin_connection_status = LinkedInConnectionStatus.CONNECTED


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


@router.post("/webhooks/unipile")
async def unipile_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Receive Unipile push events.

    Auth: Unipile uses a static shared-secret-in-a-header pattern — when
    creating the webhook you add a custom header (default
    ``X-Unipile-Auth``) and Unipile echoes it on every delivery.  We
    constant-time compare against ``settings.UNIPILE_WEBHOOK_SECRET``.
    Missing or wrong value → 401, no DB writes.

    Idempotency: every event Unipile sends has an ``id``; if we've seen it
    before we no-op.  (We don't currently persist seen ids; if duplicate
    delivery becomes a real issue add a ``webhook_events`` table.)
    """
    raw = await request.body()
    # Header name is configurable so it can match whatever the user
    # configured in Unipile's webhook dashboard.  Starlette lowercases
    # header keys; we look up case-insensitively via .get().
    auth_value = request.headers.get(settings.UNIPILE_WEBHOOK_AUTH_HEADER.lower())
    if not _verify_unipile_auth(auth_value):
        raise HTTPException(status_code=401, detail="invalid auth header")
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    event_type = (
        payload.get("type")
        or payload.get("event")
        or payload.get("event_name")
        or ""
    ).lower()
    logger.info("Unipile webhook event=%r", event_type)

    # Route by event name.  Unipile occasionally varies the casing /
    # punctuation between versions (account.connected vs ACCOUNT_CONNECTED),
    # so we normalise to lowercase + dot.
    normalised = event_type.replace("_", ".").replace(":", ".")

    if normalised in {"account.connected", "account.created", "creation.success"}:
        await _handle_account_connected(db, payload)
    elif normalised in {"account.disconnected", "account.deleted", "creation.fail"}:
        await _handle_account_status_change(db, payload, LinkedInAccountStatus.FAILED)
    elif normalised in {"account.checkpoint", "account.error.checkpoint"}:
        await _handle_account_status_change(
            db, payload, LinkedInAccountStatus.CHALLENGED, challenge=True,
        )
    elif normalised in {"message.received", "messaging.message.received", "new.message"}:
        await _handle_message_received(db, payload)
    elif normalised in {"invitation.accepted", "user.relation.created", "new.relation"}:
        await _handle_invitation_accepted(db, payload)
    else:
        logger.info("Unipile webhook: ignoring event_type=%r", event_type)
        return {"ok": True, "ignored": True}

    await db.commit()
    return {"ok": True}
