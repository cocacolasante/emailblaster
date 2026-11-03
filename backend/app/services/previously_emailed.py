"""New campaigns skip anyone this workspace has already emailed.

``Campaign.allow_previously_emailed`` is the per-campaign override, for
deliberately going back to people: follow-ups, an existing customer base,
retargeting.  Retarget campaigns turn it on themselves.

The skip is scoped to THIS campaign (unlike a reply, which ends every sequence
for that address) and only ever applies before the campaign's first email to
the person: once a lead has been emailed here, its follow-ups carry on.
LinkedIn steps aren't touched; this rule is about email.
"""
from __future__ import annotations

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Campaign,
    Lead,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    SendStatus,
    SequenceNode,
    SequenceNodeKind,
)
from app.services.suppression import canonical_email

HALT_REASON = "already emailed by another campaign"

_EMAIL_KINDS = (SequenceNodeKind.EMAIL, SequenceNodeKind.EMAIL_REPLY)


def _was_emailed():
    """SQL: this ``Lead`` row has had an email go out."""
    sent_step = exists(
        select(LeadStepExecution.id)
        .join(SequenceNode, SequenceNode.id == LeadStepExecution.node_id)
        .where(
            LeadStepExecution.lead_id == Lead.id,
            LeadStepExecution.result == LeadStepResult.SENT,
            SequenceNode.kind.in_(_EMAIL_KINDS),
        )
    )
    return or_(Lead.send_status == SendStatus.SENT, sent_step)


async def should_skip(db: AsyncSession, lead: Lead, campaign: Campaign) -> bool:
    """True when this campaign must not start emailing this person."""
    if campaign.allow_previously_emailed:
        return False
    canonical = canonical_email(lead.email)
    if not canonical:
        return False
    if lead.send_status == SendStatus.SENT:
        return False
    if await db.scalar(select(Lead.id).where(Lead.id == lead.id, _was_emailed())):
        return False
    return await db.scalar(
        select(Lead.id)
        .where(
            func.lower(Lead.email) == canonical,
            Lead.id != lead.id,
            _was_emailed(),
        )
        .limit(1)
    ) is not None


async def skip_lead(db: AsyncSession, lead: Lead) -> None:
    """Take this one lead out of its campaign.  Caller commits."""
    lead.send_status = SendStatus.SUPPRESSED
    await db.execute(
        update(LeadSequenceState)
        .where(
            LeadSequenceState.lead_id == lead.id,
            LeadSequenceState.status == LeadSequenceStatus.ACTIVE,
        )
        .values(status=LeadSequenceStatus.HALTED, halt_reason=HALT_REASON, next_run_at=None)
    )
