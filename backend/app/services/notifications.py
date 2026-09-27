"""Workspace notifications: persisted feed rows + alert emails to members.

The ``Notification`` row is the source of truth (it powers the UI bell);
email is a best-effort side-channel.  Routing:

- ``user_id`` (recipient) — an explicit ``recipient_user_id``, else the
  owner of the linked activity / opportunity / lead, else NULL = the
  whole workspace.
- Email goes to the recipient (or, for workspace-wide rows, every owner /
  admin) whose membership has ``notify_email_enabled``, sent with the
  WORKSPACE's Brevo credentials and sender (``agent_settings.
  notify_from_*`` overrides the Brevo default sender).

``create_notification`` is idempotent per workspace via the unique
``(tenant_id, dedup_key)``.  Quiet hours, a disabled preference, or a
workspace without Brevo defer/skip the email but the row persists;
``emailed_at`` stays NULL so the daily digest can sweep it up.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentSettings,
    CrmActivity,
    Lead,
    Notification,
    NotificationKind,
    Opportunity,
)
from app.models.identity import Membership, MembershipRole, User
from app.services import brevo, credentials

logger = logging.getLogger(__name__)


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


async def resolve_recipient(
    session: AsyncSession,
    *,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    activity_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """The member a record-linked alert belongs to: the most specific
    linked record's owner (task → deal → lead), or None (whole workspace)."""
    for model, rid in ((CrmActivity, activity_id), (Opportunity, opportunity_id), (Lead, lead_id)):
        if rid is None:
            continue
        owner = await session.scalar(select(model.owner_id).where(model.id == rid))
        if owner is not None:
            return owner
    return None


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
    recipient_user_id: uuid.UUID | None = None,
    route_to_owner: bool = True,
) -> Notification | None:
    """Insert a Notification unless ``dedup_key`` already exists in this
    workspace.  Returns the new row (flushed, not committed) or None when
    deduped.  Caller owns the transaction."""
    existing = await session.scalar(
        select(Notification.id).where(Notification.dedup_key == dedup_key)
    )
    if existing is not None:
        return None
    if recipient_user_id is None and route_to_owner:
        recipient_user_id = await resolve_recipient(
            session, lead_id=lead_id, opportunity_id=opportunity_id, activity_id=activity_id,
        )
    row = Notification(
        kind=kind,
        title=title,
        body=body,
        dedup_key=dedup_key,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        activity_id=activity_id,
        user_id=recipient_user_id,
    )
    session.add(row)
    await session.flush()
    return row


def _render_email(notification: Notification) -> tuple[str, str]:
    """Tiny HTML + text rendering for the alert."""
    title = notification.title
    body = notification.body or ""
    text = f"{title}\n\n{body}\n\n— Email Blaster"
    html_body = body.replace("\n", "<br>")
    html = (
        f"<div style='font-family:sans-serif;max-width:560px'>"
        f"<h3 style='margin:0 0 12px'>{title}</h3>"
        f"<p style='color:#374151'>{html_body}</p>"
        f"<p style='color:#9ca3af;font-size:12px;margin-top:24px'>"
        f"— Email Blaster</p></div>"
    )
    return html, text


async def email_recipients(
    session: AsyncSession, notification: Notification, *, digest: bool = False,
) -> list[User]:
    """Members who should get this alert by email."""
    pref = Membership.digest_enabled if digest else Membership.notify_email_enabled
    q = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == notification.tenant_id, pref.is_(True))
    )
    if notification.user_id is not None:
        q = q.where(User.id == notification.user_id)
    else:
        q = q.where(Membership.role.in_([MembershipRole.OWNER, MembershipRole.ADMIN]))
    return list((await session.execute(q.order_by(User.email))).scalars().all())


async def send_notification_email(
    session: AsyncSession,
    notification: Notification,
    agent_settings: AgentSettings | None = None,
    *,
    digest: bool = False,
) -> bool:
    """Email one notification to its recipient(s).  Returns True and stamps
    ``emailed_at`` when at least one email went out; False (row untouched)
    on skip/failure.  Never raises."""
    creds = credentials.get("brevo")
    if creds is None:
        logger.info("notification %s not emailed: workspace has no Brevo", notification.kind)
        return False
    recipients = await email_recipients(session, notification, digest=digest)
    if not recipients:
        return False
    from_email = (agent_settings.notify_from_email if agent_settings else None) or creds.sender_email
    from_name = (agent_settings.notify_from_name if agent_settings else None) or creds.sender_name
    html, text = _render_email(notification)
    sent = 0
    for user in recipients:
        try:
            await brevo.send_email(
                to_email=user.email,
                to_name=user.name,
                subject=f"[Email Blaster] {notification.title}"[:200],
                html_body=html,
                text_body=text,
                sender_name=from_name,
                sender_email=from_email,
                campaign_id="notification",
                lead_id=str(notification.id),
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — alert email must never break the caller
            logger.warning("notification email to %s failed (%s): %s",
                           user.email, notification.kind, exc)
    if not sent:
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
    recipient_user_id: uuid.UUID | None = None,
    route_to_owner: bool = True,
) -> dict[str, Any]:
    """Create-and-maybe-email in one call.  Quiet hours defer the email
    (row persists with ``emailed_at`` NULL); dedup skips both.  Returns
    ``{"notification": row | None, "deduped": bool, "emailed": bool}``.

    ``route_to_owner=False`` makes a record-linked alert workspace-wide
    (e.g. a campaign auto-pause everyone should see)."""
    row = await create_notification(
        session,
        kind=kind,
        title=title,
        dedup_key=dedup_key,
        body=body,
        lead_id=lead_id,
        opportunity_id=opportunity_id,
        activity_id=activity_id,
        recipient_user_id=recipient_user_id,
        route_to_owner=route_to_owner,
    )
    if row is None:
        return {"notification": None, "deduped": True, "emailed": False}

    if in_quiet_hours(
        datetime.now(timezone.utc),
        agent_settings.quiet_hours_start_utc,
        agent_settings.quiet_hours_end_utc,
    ):
        return {"notification": row, "deduped": False, "emailed": False}

    emailed = await send_notification_email(session, row, agent_settings)
    return {"notification": row, "deduped": False, "emailed": emailed}
