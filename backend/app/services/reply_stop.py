"""A person who replied leaves every automated sequence — positive or negative.

The stop is by ADDRESS within the workspace, not by lead row: the same person
can sit in several campaigns, and a reply to one must not leave the others
running (a retarget campaign re-contacted people who had already answered).

Two layers, so neither a late poll nor a queued task can slip through:

- ``stop_on_reply`` runs the moment a reply is recorded (IMAP poller, LinkedIn
  poller + webhook): halts their enrollments and pulls unsent first emails.
- ``has_replied`` runs at send time (``send.check_send_gates`` and the LinkedIn
  step), so a lead enrolled later, resumed, or already dispatched still stops.

One-off outreach a person approves is deliberately NOT gated: answering someone
who replied is the point.  Auto-replies never reach here — the poller drops
them (see ``imap_client.is_auto_reply``).
"""
from __future__ import annotations

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailEvent, EmailEventType, Lead
from app.services.suppression import canonical_email, halt_address

HALT_REASON = "lead replied"


def _same_person(lead: Lead):
    canonical = canonical_email(lead.email)
    if not canonical:
        return Lead.id == lead.id
    return or_(Lead.id == lead.id, func.lower(Lead.email) == canonical)


async def has_replied(db: AsyncSession, lead: Lead) -> bool:
    """Has this person replied, by email or LinkedIn, in any campaign?"""
    replied_by_email = exists(
        select(EmailEvent.id).where(
            EmailEvent.lead_id == Lead.id,
            EmailEvent.event_type == EmailEventType.REPLIED,
        )
    )
    return await db.scalar(
        select(Lead.id)
        .where(
            _same_person(lead),
            or_(Lead.linkedin_last_reply_at.is_not(None), replied_by_email),
        )
        .limit(1)
    ) is not None


async def stop_on_reply(db: AsyncSession, lead: Lead) -> int:
    """Take this person out of every sequence; returns enrollments halted.
    Caller commits.  Idempotent."""
    halted, _, _ = await halt_address(
        db, canonical_email(lead.email), HALT_REASON, lead_id=lead.id,
    )
    return halted
