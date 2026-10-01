from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    Campaign,
    EmailEvent,
    EmailEventType,
    Lead,
    LeadStepExecution,
    LeadStepResult,
    SendStatus,
    SequenceNode,
    SequenceNodeKind,
)
from app.schemas.analytics import (
    AnalyticsOverview,
    AnalyticsRates,
    AnalyticsResponse,
    BestSubject,
    QualityBreakdownItem,
    SendCohort,
    TimelinePoint,
)

router = APIRouter(tags=["analytics"])

MIN_SENDS_FOR_SUBJECT_RANKING = 5
TIMELINE_DAYS = 30


# Canonical ratio metric — shared across all reporting surfaces.
from app.services.metrics import rate as _rate  # noqa: E402


def _send_time_from_message_id(brevo_message_id: str | None) -> datetime | None:
    """First-email send time, from the ``YYYYMMDDHHMM`` timestamp Brevo
    embeds in its Message-ID (``<202607291325.…@smtp-relay.mailin.fr>``,
    UTC).  The legacy first-email path writes no ``lead_step_executions``
    row, so this is the only durable per-lead send timestamp."""
    m = re.search(r"\d{12}", brevo_message_id or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(), "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _norm_message_id(message_id: str | None) -> str | None:
    """``<x@y>`` / ``x@y`` / padded → ``x@y`` so Brevo event ids match ours."""
    mid = (message_id or "").strip().strip("<>").strip()
    return mid or None


async def _send_cohorts(
    db: AsyncSession, campaign_id: uuid.UUID, now: datetime,
) -> list[SendCohort]:
    """Every email the campaign sent — the first email AND sequence
    follow-ups — bucketed by the week it went out, with the share of those
    emails that were opened.

    Opens are attributed to the exact email via Brevo's ``messageId`` on
    the event, so a follow-up's opens land in the follow-up's week (not
    the first email's).  Older events stored without a ``messageId`` fall
    back to the old lead-level rule: credited to the lead's first email.
    """
    # message id → {"sent_at", "lead_id", "first"}
    emails: dict[str, dict[str, Any]] = {}

    first_rows = (await db.execute(
        select(Lead.id, Lead.brevo_message_id).where(
            Lead.campaign_id == campaign_id,
            Lead.send_status == SendStatus.SENT,
            Lead.brevo_message_id.is_not(None),
        )
    )).all()
    for lead_id, message_id in first_rows:
        mid = _norm_message_id(message_id)
        sent_at = _send_time_from_message_id(message_id)
        if mid and sent_at:
            emails[mid] = {"sent_at": sent_at, "lead_id": lead_id, "first": True}

    step_rows = (await db.execute(
        select(LeadStepExecution.lead_id, LeadStepExecution.external_id,
               LeadStepExecution.attempted_at)
        .join(Lead, Lead.id == LeadStepExecution.lead_id)
        .join(SequenceNode, SequenceNode.id == LeadStepExecution.node_id)
        .where(
            Lead.campaign_id == campaign_id,
            LeadStepExecution.result == LeadStepResult.SENT,
            LeadStepExecution.external_id.is_not(None),
            SequenceNode.kind.in_([SequenceNodeKind.EMAIL, SequenceNodeKind.EMAIL_REPLY]),
        )
    )).all()
    for lead_id, message_id, attempted_at in step_rows:
        mid = _norm_message_id(message_id)
        if not mid or mid in emails:  # same message as the first email → count once
            continue
        sent_at = _send_time_from_message_id(message_id) or attempted_at
        emails[mid] = {"sent_at": sent_at, "lead_id": lead_id, "first": False}

    opened_mids: set[str] = set()
    legacy_opened_leads: set[uuid.UUID] = set()
    open_rows = (await db.execute(
        select(EmailEvent.lead_id, EmailEvent.event_data["messageId"].astext)
        .where(
            EmailEvent.campaign_id == campaign_id,
            EmailEvent.event_type == EmailEventType.OPENED,
        )
        .distinct()
    )).all()
    for lead_id, message_id in open_rows:
        mid = _norm_message_id(message_id)
        if mid:
            opened_mids.add(mid)
        else:
            legacy_opened_leads.add(lead_id)

    buckets: dict[Any, dict[str, Any]] = {}
    for mid, e in emails.items():
        sent_at = e["sent_at"]
        week_start = (sent_at - timedelta(days=sent_at.weekday())).date()
        b = buckets.setdefault(week_start, {
            "sent": 0, "opened": 0, "first": 0, "follow_ups": 0, "newest": sent_at,
        })
        b["sent"] += 1
        b["first" if e["first"] else "follow_ups"] += 1
        if mid in opened_mids or (e["first"] and e["lead_id"] in legacy_opened_leads):
            b["opened"] += 1
        b["newest"] = max(b["newest"], sent_at)

    return [
        SendCohort(
            week_start=week_start,
            sent=b["sent"],
            opened=b["opened"],
            open_rate=_rate(b["opened"], b["sent"]),
            first_emails=b["first"],
            follow_ups=b["follow_ups"],
            accumulating=(now - b["newest"]) < timedelta(days=7),
        )
        for week_start, b in sorted(buckets.items())
    ]


def _reputation(sent: int, delivered: int, bounced: int, spam: int) -> int | None:
    """Sender reputation score 0–100.

    delivery_rate × 50 + (1 − spam_rate) × 30 + (1 − bounce_rate) × 20.
    Returns None when there are no sends yet.
    """
    if sent <= 0:
        return None
    d = delivered / sent
    s = spam / sent
    b = bounced / sent
    score = d * 50 + (1 - s) * 30 + (1 - b) * 20
    return max(0, min(100, round(score)))


@router.get(
    "/campaigns/{campaign_id}/analytics",
    response_model=AnalyticsResponse,
)
async def get_campaign_analytics(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # ----- Lead counts -----
    total_leads = (await db.execute(
        select(func.count()).select_from(Lead).where(Lead.campaign_id == campaign_id)
    )).scalar_one()
    sent_count = (await db.execute(
        select(func.count()).select_from(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.send_status == SendStatus.SENT,
        )
    )).scalar_one()

    # ----- Event counts (distinct leads per type) -----
    events_rows = (await db.execute(
        select(EmailEvent.event_type, func.count(func.distinct(EmailEvent.lead_id)))
        .where(EmailEvent.campaign_id == campaign_id)
        .group_by(EmailEvent.event_type)
    )).all()
    by_event: dict[EmailEventType, int] = {row[0]: row[1] for row in events_rows}

    delivered = by_event.get(EmailEventType.DELIVERED, 0)
    opened = by_event.get(EmailEventType.OPENED, 0)
    clicked = by_event.get(EmailEventType.CLICKED, 0)
    replied = by_event.get(EmailEventType.REPLIED, 0)
    soft = by_event.get(EmailEventType.SOFT_BOUNCE, 0)
    hard = by_event.get(EmailEventType.HARD_BOUNCE, 0)
    bounced = soft + hard
    spam = by_event.get(EmailEventType.SPAM, 0)
    unsubbed = by_event.get(EmailEventType.UNSUBSCRIBED, 0)

    reply_tracking_enabled = campaign.connected_account_id is not None
    click_tracking_enabled = settings.EMAIL_CLICK_TRACKING_ENABLED

    overview = AnalyticsOverview(
        total_leads=total_leads,
        sent=sent_count,
        delivered=delivered,
        opened=opened,
        clicked=clicked if click_tracking_enabled else 0,
        replied=replied if reply_tracking_enabled else 0,
        bounced=bounced,
        spam_complaints=spam,
        unsubscribed=unsubbed,
    )

    rates = AnalyticsRates(
        open_rate=_rate(opened, sent_count),
        # Click tracking off in Brevo → no CLICKED events; "not tracked" (None)
        # rather than a misleading 0%.
        click_rate=_rate(clicked, sent_count) if click_tracking_enabled else None,
        reply_rate=_rate(replied, sent_count) if reply_tracking_enabled else None,
        bounce_rate=_rate(bounced, sent_count),
        spam_rate=_rate(spam, sent_count),
        unsub_rate=_rate(unsubbed, sent_count),
        delivery_rate=_rate(delivered, sent_count),
    )

    # ----- Timeline -----
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(days=TIMELINE_DAYS)
    if campaign.created_at and campaign.created_at > earliest:
        earliest = campaign.created_at

    timeline_rows = (await db.execute(
        select(
            cast(EmailEvent.occurred_at, Date).label("day"),
            EmailEvent.event_type,
            func.count(),
        )
        .where(
            EmailEvent.campaign_id == campaign_id,
            EmailEvent.occurred_at >= earliest,
            EmailEvent.event_type.in_([
                EmailEventType.OPENED,
                EmailEventType.CLICKED,
                EmailEventType.REPLIED,
            ]),
        )
        .group_by("day", EmailEvent.event_type)
        .order_by("day")
    )).all()

    daily: dict[Any, dict[str, int]] = {}
    for day, event_type, count in timeline_rows:
        bucket = daily.setdefault(day, {"opens": 0, "clicks": 0, "replies": 0})
        if event_type == EmailEventType.OPENED:
            bucket["opens"] += count
        elif event_type == EmailEventType.CLICKED:
            bucket["clicks"] += count
        elif event_type == EmailEventType.REPLIED:
            bucket["replies"] += count

    timeline = [
        TimelinePoint(date=d, opens=v["opens"], clicks=v["clicks"], replies=v["replies"])
        for d, v in sorted(daily.items())
    ]

    # ----- Quality breakdown -----
    sent_rows = (await db.execute(
        select(
            Lead.id, Lead.research_data, Lead.composed_subject,
        ).where(
            Lead.campaign_id == campaign_id,
            Lead.send_status == SendStatus.SENT,
        )
    )).all()

    opened_ids = {row[0] for row in (await db.execute(
        select(func.distinct(EmailEvent.lead_id))
        .where(
            EmailEvent.campaign_id == campaign_id,
            EmailEvent.event_type == EmailEventType.OPENED,
        )
    )).all()}

    quality_buckets: dict[str, dict[str, int]] = {}
    subject_buckets: dict[str, dict[str, int]] = {}
    for lead_id, research_data, subject in sent_rows:
        quality = str((research_data or {}).get("quality", "low"))
        qb = quality_buckets.setdefault(quality, {"count": 0, "opened": 0})
        qb["count"] += 1
        if lead_id in opened_ids:
            qb["opened"] += 1
        if subject:
            sb = subject_buckets.setdefault(subject, {"sent": 0, "opened": 0})
            sb["sent"] += 1
            if lead_id in opened_ids:
                sb["opened"] += 1

    quality_breakdown = [
        QualityBreakdownItem(
            quality=q,
            count=data["count"],
            open_rate=_rate(data["opened"], data["count"]),
        )
        for q, data in sorted(quality_buckets.items())
    ]

    # ----- Best subjects (min sends for ranking) -----
    qualifying_subjects = [
        BestSubject(
            subject=s,
            sent=data["sent"],
            open_rate=round(data["opened"] / data["sent"], 4),
        )
        for s, data in subject_buckets.items()
        if data["sent"] >= MIN_SENDS_FOR_SUBJECT_RANKING
    ]
    qualifying_subjects.sort(key=lambda x: (x.open_rate, x.sent), reverse=True)
    best_subjects = qualifying_subjects[:10]

    # ----- Send-week cohorts -----
    send_cohorts = await _send_cohorts(db, campaign_id, now)

    # ----- Reputation score -----
    reputation = _reputation(sent_count, delivered, bounced, spam)

    return AnalyticsResponse(
        campaign_id=campaign.id,
        overview=overview,
        rates=rates,
        reply_tracking_enabled=reply_tracking_enabled,
        click_tracking_enabled=click_tracking_enabled,
        timeline=timeline,
        research_quality_breakdown=quality_breakdown,
        sender_reputation_score=reputation,
        best_subject_lines=best_subjects,
        send_cohorts=send_cohorts,
    )
