"""Workspace suppression (the "ignore list") — one place to add an email to
the list AND pull it out of every campaign.

Used by the manual "ignore" button, the real-time Brevo event poller (hard
bounce / spam / unsubscribe / blocked), and the Brevo blocklist sync, so the
three effects always happen together:

  1. Add the email to the suppression list  → blocks ALL future campaigns
     (the send gates + bulk pipeline reject suppressed emails, even on a fresh
     CSV upload of the same address).
  2. Halt every ACTIVE sequence state for any lead with that email  → stops
     in-flight follow-ups / replies in current campaigns.
  3. Mark every not-yet-sent lead (PENDING/SCHEDULED) with that email
     SUPPRESSED  → removes them from the current send queue and shows them as
     suppressed in the UI.

Idempotent and email-keyed (an email can span several leads/campaigns).  The
caller owns the transaction — this adds/updates rows but does NOT commit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Lead,
    LeadSequenceState,
    LeadSequenceStatus,
    SendStatus,
    Suppression,
    SuppressionReason,
    canonical_email,
)

# Human-readable halt reasons per suppression reason (shown on the halted
# sequence state + the Leads timeline).  The manual one keeps the word
# "ignored" the existing ignore-button contract depends on.
_HALT_REASONS: dict[SuppressionReason, str] = {
    SuppressionReason.MANUAL: "ignored by user (manual suppression)",
    SuppressionReason.HARD_BOUNCE: "suppressed — email hard-bounced (Brevo)",
    SuppressionReason.SPAM: "suppressed — marked as spam (Brevo)",
    SuppressionReason.UNSUBSCRIBED: "suppressed — recipient unsubscribed (Brevo)",
    SuppressionReason.BLOCKED: "suppressed — recipient blocklisted (Brevo)",
    SuppressionReason.SOFT_BOUNCE: "suppressed — soft bounce (protecting sender reputation)",
}


@dataclass
class SuppressionResult:
    email: str
    suppressed: bool = False           # False only when the email was blank/invalid
    already_suppressed: bool = False
    leads_halted: int = 0              # ACTIVE sequence states flipped to HALTED
    leads_marked_suppressed: int = 0   # PENDING/SCHEDULED leads → SUPPRESSED
    campaigns_affected: list[UUID] = field(default_factory=list)


async def halt_address(
    db: AsyncSession,
    canonical: str,
    reason_text: str,
    *,
    lead_id: UUID | None = None,
) -> tuple[int, set[UUID], int]:
    """Halt every ACTIVE enrollment for this address and pull its not-yet-sent
    first emails out of the send queue (SENT/FAILED untouched).  ``lead_id`` also
    covers that lead when it has no email (LinkedIn-only).  Caller commits.
    Returns ``(enrollments halted, campaigns affected, leads marked)``."""
    # func.lower so mixed-case legacy rows are caught too.
    conditions = []
    if canonical:
        conditions.append(func.lower(Lead.email) == canonical)
    if lead_id is not None:
        conditions.append(Lead.id == lead_id)
    if not conditions:
        return 0, set(), 0
    who = or_(*conditions)

    state_rows = (await db.execute(
        select(LeadSequenceState, Lead.campaign_id)
        .join(Lead, Lead.id == LeadSequenceState.lead_id)
        .where(who)
        .where(LeadSequenceState.status == LeadSequenceStatus.ACTIVE)
    )).all()
    campaigns: set[UUID] = set()
    for state, campaign_id in state_rows:
        state.status = LeadSequenceStatus.HALTED
        state.halt_reason = reason_text
        state.next_run_at = None
        if campaign_id is not None:
            campaigns.add(campaign_id)

    marked = await db.execute(
        update(Lead)
        .where(who, Lead.send_status.in_((SendStatus.PENDING, SendStatus.SCHEDULED)))
        .values(send_status=SendStatus.SUPPRESSED)
    )
    return len(state_rows), campaigns, marked.rowcount or 0


async def suppress_email(
    db: AsyncSession,
    email: str | None,
    reason: SuppressionReason,
    *,
    halt_reason: str | None = None,
) -> SuppressionResult:
    """Add ``email`` to the suppression list and pull it out of every campaign.

    See module docstring for the three combined effects.  Caller commits.
    """
    canonical = canonical_email(email)
    if not canonical:
        return SuppressionResult(email="", suppressed=False)

    result = SuppressionResult(email=canonical, suppressed=True)
    reason_text = halt_reason or _HALT_REASONS.get(reason, f"suppressed ({reason.value})")

    # 1) Upsert the suppression row (unique on email).
    existing = await db.scalar(
        select(Suppression).where(Suppression.email == canonical)
    )
    result.already_suppressed = existing is not None
    if existing is None:
        db.add(Suppression(email=canonical, reason=reason))

    # 2) + 3) Halt every enrollment for this email; pull unsent first emails.
    result.leads_halted, campaigns, result.leads_marked_suppressed = await halt_address(
        db, canonical, reason_text,
    )

    result.campaigns_affected = sorted(campaigns)
    return result
