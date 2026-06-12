"""Operator notifications: persisted feed rows + owner-alert emails.

The ``Notification`` row is the source of truth (it powers the UI
bell); the email to ``settings.OWNER_NOTIFY_EMAIL`` is a side-channel.
Consequences of that ordering:

- ``create_notification`` is idempotent via the UNIQUE ``dedup_key``
  (pre-checked with a SELECT — fine for a single-operator deployment;
  the DB constraint is the backstop).
- Email delivery is best-effort.  Quiet hours and a missing
  ``OWNER_NOTIFY_EMAIL`` defer/skip the email but the row persists
  either way; ``emailed_at`` stays NULL so the daily digest can sweep
  up anything that never went out.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AgentSettings, Notification, NotificationKind
from app.services import brevo

logger = logging.getLogger(__name__)

_warned_no_owner_email = False


def in_quiet_hours(
    now_utc: datetime,
    start_hour: int | None,
    end_hour: int | None,
) -> bool:
    """True when ``now_utc`` falls inside the [start, end) quiet window.
    A start > end window wraps midnight (e.g. 22 → 6).  Any None
    disables quiet hours."""
    if start_hour is None or end_hour is None:
        return False
    hour = now_utc.hour
    if start_hour == end_hour:
        return False  # degenerate zero-length window
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


async def create_notification(
    session: AsyncSession,
    *,
    kind: NotificationKind,
    title: str,
    dedup_key: str,
    body: str | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    activity_id: uuid.UUID | None = None,
) -> Notification | None:
    """Insert a Notification unless ``dedup_key`` already exists.
    Returns the new row (flushed, not committed) or None when deduped.
    Caller owns the transaction."""
    existing = await session.scalar(
        select(Notification.id).where(Notification.dedup_key == dedup_key)
    )
    if existing is not None:
        return None
    row = Notification(
        kind=kind,
        title=title,
        body=body,
        dedup_key=dedup_key,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        activity_id=activity_id,
    )
    session.add(row)
    await session.flush()
    return row


def _render_email(notification: Notification) -> tuple[str, str]:
    """Tiny HTML + text rendering for the owner alert."""
    title = notification.title
    body = notification.body or ""
    text = f"{title}\n\n{body}\n\n— Email Blaster agent"
    html_body = body.replace("\n", "<br>")
    html = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<h3 style='margin:0 0 12px'>{title}</h3>"
        f"<p style='color:#374151'>{html_body}</p>"
        f"<p style='color:#9ca3af;font-size:12px;margin-top:24px'>"
        f"— Email Blaster agent</p></div>"
    )
    return html, text


async def send_notification_email(notification: Notification) -> bool:
    """Email one notification to the owner.  Returns True and stamps
    ``emailed_at`` on success; False (row untouched) on skip/failure.
    Never raises."""
    global _warned_no_owner_email
    if not settings.OWNER_NOTIFY_EMAIL:
        if not _warned_no_owner_email:
            logger.warning(
                "OWNER_NOTIFY_EMAIL is not set — agent notifications will "
                "persist in the UI but no alert emails will be sent."
            )
            _warned_no_owner_email = True
        return False

    html, text = _render_email(notification)
    try:
        await brevo.send_email(
            to_email=settings.OWNER_NOTIFY_EMAIL,
            to_name=settings.OWNER_NOTIFY_NAME,
            subject=f"[Agent] {notification.title}"[:200],
            html_body=html,
            text_body=text,
            sender_name=settings.BREVO_SENDER_NAME,
            sender_email=settings.BREVO_SENDER_EMAIL,
            # Synthetic header IDs — owner alerts have no campaign/lead.
            campaign_id="agent-notification",
            lead_id=str(notification.id),
        )
    except Exception as exc:  # noqa: BLE001 — alert email must never break the caller
        logger.warning("notification email failed (%s): %s", notification.kind, exc)
        return False
    notification.emailed_at = datetime.now(timezone.utc)
    return True


async def notify(
    session: AsyncSession,
    agent_settings: AgentSettings,
    *,
    kind: NotificationKind,
    title: str,
    dedup_key: str,
    body: str | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    activity_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create-and-maybe-email in one call.  Quiet hours defer the email
    (row persists with ``emailed_at`` NULL); dedup skips both.  Returns
    ``{"notification": row | None, "deduped": bool, "emailed": bool}``."""
    row = await create_notification(
        session,
        kind=kind,
        title=title,
        dedup_key=dedup_key,
        body=body,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        activity_id=activity_id,
    )
    if row is None:
        return {"notification": None, "deduped": True, "emailed": False}

    if in_quiet_hours(
        datetime.now(timezone.utc),
        agent_settings.quiet_hours_start_utc,
        agent_settings.quiet_hours_end_utc,
    ):
        return {"notification": row, "deduped": False, "emailed": False}

    emailed = await send_notification_email(row)
    return {"notification": row, "deduped": False, "emailed": emailed}
