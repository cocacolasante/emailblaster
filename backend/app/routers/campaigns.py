from __future__ import annotations

import math
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Campaign,
    CampaignStatus,
    ComposeStatus,
    ConnectedAccount,
    EmailEvent,
    EmailEventType,
    Lead,
    LinkedInAccount,
    ResearchStatus,
    SendStatus,
)
from app.schemas.campaign import (
    CampaignCreate,
    CampaignResponse,
    CampaignStats,
    CampaignUpdate,
    ConnectedAccountInfo,
    FailedLeadInfo,
    LeadCounts,
    RetryFailedResponse,
    campaign_to_dict,
)
from app.schemas.lead import LeadSummary, PaginatedLeads
from app.services.sequence_service import ensure_default_sequence

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _get_or_404(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    c = await db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


def _rate(numer: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return round(numer / denom, 4)


async def _compute_stats_and_counts(
    db: AsyncSession, campaign: Campaign
) -> tuple[LeadCounts, CampaignStats]:
    """Aggregate lead counts and event-derived rates for one campaign."""
    # Lead counts by send_status
    counts_q = await db.execute(
        select(Lead.send_status, func.count())
        .where(Lead.campaign_id == campaign.id)
        .group_by(Lead.send_status)
    )
    by_status: dict[SendStatus, int] = {row[0]: row[1] for row in counts_q.all()}
    total = sum(by_status.values())
    sent_count = by_status.get(SendStatus.SENT, 0)

    # Distinct lead count per event type — opens and clicks may have many rows
    # per lead; we count unique leads to match how rates are usually reported.
    events_q = await db.execute(
        select(EmailEvent.event_type, func.count(func.distinct(EmailEvent.lead_id)))
        .where(EmailEvent.campaign_id == campaign.id)
        .group_by(EmailEvent.event_type)
    )
    by_event: dict[EmailEventType, int] = {row[0]: row[1] for row in events_q.all()}

    delivered = by_event.get(EmailEventType.DELIVERED, 0)
    opened = by_event.get(EmailEventType.OPENED, 0)
    clicked = by_event.get(EmailEventType.CLICKED, 0)
    bounced = by_event.get(EmailEventType.HARD_BOUNCE, 0) + by_event.get(EmailEventType.SOFT_BOUNCE, 0)
    replied = by_event.get(EmailEventType.REPLIED, 0)
    unsubscribed = by_event.get(EmailEventType.UNSUBSCRIBED, 0)

    reply_tracking = campaign.connected_account_id is not None

    stats = CampaignStats(
        sent_count=sent_count,
        delivered=delivered,
        opened=opened,
        clicked=clicked,
        bounced=bounced,
        replied=replied if reply_tracking else 0,
        unsubscribed=unsubscribed,
        open_rate=_rate(opened, sent_count),
        click_rate=_rate(clicked, sent_count),
        bounce_rate=_rate(bounced, sent_count),
        reply_rate=_rate(replied, sent_count) if reply_tracking else None,
        reply_tracking_note=None if reply_tracking else "reply tracking not configured",
    )

    counts = LeadCounts(
        total=total,
        pending=by_status.get(SendStatus.PENDING, 0),
        scheduled=by_status.get(SendStatus.SCHEDULED, 0),
        sent=sent_count,
        failed=by_status.get(SendStatus.FAILED, 0),
    )
    return counts, stats


async def _build_response(db: AsyncSession, campaign: Campaign) -> CampaignResponse:
    counts, stats = await _compute_stats_and_counts(db, campaign)

    account_info: ConnectedAccountInfo | None = None
    if campaign.connected_account_id is not None:
        acc = await db.get(ConnectedAccount, campaign.connected_account_id)
        if acc is not None:
            account_info = ConnectedAccountInfo.model_validate(acc)

    payload: dict[str, Any] = campaign_to_dict(campaign)
    payload["connected_account"] = account_info
    payload["connected_account_configured"] = campaign.connected_account_id is not None
    payload["linkedin_account_configured"] = campaign.linkedin_account_id is not None
    payload["lead_counts"] = counts
    payload["stats"] = stats
    return CampaignResponse.model_validate(payload)


async def _verify_account_exists(db: AsyncSession, account_id: uuid.UUID | None) -> None:
    if account_id is None:
        return
    if (await db.get(ConnectedAccount, account_id)) is None:
        raise HTTPException(status_code=422, detail="connected_account_id does not exist")


async def _verify_linkedin_account_exists(db: AsyncSession, account_id: uuid.UUID | None) -> None:
    if account_id is None:
        return
    if (await db.get(LinkedInAccount, account_id)) is None:
        raise HTTPException(status_code=422, detail="linkedin_account_id does not exist")


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate, db: AsyncSession = Depends(get_db)
) -> CampaignResponse:
    await _verify_account_exists(db, payload.connected_account_id)
    await _verify_linkedin_account_exists(db, payload.linkedin_account_id)
    campaign = Campaign(**payload.model_dump())
    db.add(campaign)
    await db.flush()
    await ensure_default_sequence(db, campaign)
    await db.commit()
    await db.refresh(campaign)
    return await _build_response(db, campaign)


@router.get("/", response_model=list[CampaignResponse])
async def list_campaigns(db: AsyncSession = Depends(get_db)) -> list[CampaignResponse]:
    rows = (await db.execute(select(Campaign).order_by(Campaign.created_at.desc()))).scalars().all()
    return [await _build_response(db, c) for c in rows]


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignResponse:
    c = await _get_or_404(db, campaign_id)
    return await _build_response(db, c)


@router.patch("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
) -> CampaignResponse:
    c = await _get_or_404(db, campaign_id)
    if c.status not in {CampaignStatus.DRAFT, CampaignStatus.PREVIEWING}:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit campaign in status '{c.status.value}' (only draft or previewing)",
        )

    updates = payload.model_dump(exclude_unset=True)
    if "connected_account_id" in updates:
        await _verify_account_exists(db, updates["connected_account_id"])
    if "linkedin_account_id" in updates:
        await _verify_linkedin_account_exists(db, updates["linkedin_account_id"])

    for key, value in updates.items():
        setattr(c, key, value)
    await db.commit()
    await db.refresh(c)
    return await _build_response(db, c)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    c = await _get_or_404(db, campaign_id)
    await db.delete(c)
    await db.commit()


# --------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
async def pause_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignResponse:
    c = await _get_or_404(db, campaign_id)
    if c.status != CampaignStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot pause campaign in status '{c.status.value}' (only running)",
        )
    c.status = CampaignStatus.PAUSED
    await db.commit()
    await db.refresh(c)
    return await _build_response(db, c)


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
async def resume_campaign(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CampaignResponse:
    c = await _get_or_404(db, campaign_id)
    if c.status != CampaignStatus.PAUSED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume campaign in status '{c.status.value}' (only paused)",
        )
    c.status = CampaignStatus.RUNNING
    await db.commit()
    await db.refresh(c)
    return await _build_response(db, c)


# --------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------


@router.get("/{campaign_id}/leads", response_model=PaginatedLeads)
async def list_campaign_leads(
    campaign_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    send_status: SendStatus | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaginatedLeads:
    await _get_or_404(db, campaign_id)

    filters = [Lead.campaign_id == campaign_id]
    if send_status is not None:
        filters.append(Lead.send_status == send_status)
    if search:
        s = f"%{search}%"
        filters.append(
            or_(Lead.email.ilike(s), Lead.first_name.ilike(s), Lead.last_name.ilike(s))
        )

    count_q = select(func.count()).select_from(Lead).where(*filters)
    total = (await db.execute(count_q)).scalar_one()

    rows_q = (
        select(Lead)
        .where(*filters)
        .order_by(Lead.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(rows_q)).scalars().all()

    return PaginatedLeads(
        items=[LeadSummary.model_validate(l) for l in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total > 0 else 0,
    )


# --------------------------------------------------------------------------
# Failed leads + retry
# --------------------------------------------------------------------------


def _failed_stage(lead: Lead) -> str:
    if lead.send_status == SendStatus.FAILED:
        return "send"
    if lead.compose_status == ComposeStatus.FAILED:
        return "compose"
    return "research"


@router.get("/{campaign_id}/errors", response_model=list[FailedLeadInfo])
async def list_campaign_errors(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[FailedLeadInfo]:
    """List leads whose research, compose, or send pipeline ended in FAILED."""
    await _get_or_404(db, campaign_id)
    rows = (await db.execute(
        select(Lead)
        .where(
            Lead.campaign_id == campaign_id,
            or_(
                Lead.research_status == ResearchStatus.FAILED,
                Lead.compose_status == ComposeStatus.FAILED,
                Lead.send_status == SendStatus.FAILED,
            ),
        )
        .order_by(Lead.created_at.asc())
    )).scalars().all()

    return [
        FailedLeadInfo(
            lead_id=l.id,
            email=l.email,
            first_name=l.first_name,
            last_name=l.last_name,
            research_status=l.research_status.value,
            compose_status=l.compose_status.value,
            send_status=l.send_status.value,
            failed_stage=_failed_stage(l),
        )
        for l in rows
    ]


@router.post(
    "/{campaign_id}/retry-failed",
    response_model=RetryFailedResponse,
)
async def retry_failed_leads(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RetryFailedResponse:
    """Reset failed leads to pending and re-queue the appropriate worker.

    For each failed lead, we re-queue only the earliest failed stage:
      - research_status=FAILED  → reset and enqueue research_lead
      - compose_status=FAILED   → reset and enqueue compose_lead
      - send_status=FAILED      → reset and enqueue send_lead
    """
    await _get_or_404(db, campaign_id)

    rows = (await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            or_(
                Lead.research_status == ResearchStatus.FAILED,
                Lead.compose_status == ComposeStatus.FAILED,
                Lead.send_status == SendStatus.FAILED,
            ),
        )
    )).scalars().all()

    # Local imports so this router stays importable even when the worker
    # modules are mocked out (e.g. in dependency-graph tests).
    from app.workers.compose import compose_lead
    from app.workers.research import research_lead
    from app.workers.send import send_lead

    research_retried = compose_retried = send_retried = 0
    for lead in rows:
        if lead.research_status == ResearchStatus.FAILED:
            lead.research_status = ResearchStatus.PENDING
            research_lead.delay(str(lead.id))
            research_retried += 1
        elif lead.compose_status == ComposeStatus.FAILED:
            lead.compose_status = ComposeStatus.PENDING
            compose_lead.delay(str(lead.id))
            compose_retried += 1
        elif lead.send_status == SendStatus.FAILED:
            lead.send_status = SendStatus.PENDING
            send_lead.delay(str(lead.id))
            send_retried += 1

    await db.commit()
    return RetryFailedResponse(
        research_retried=research_retried,
        compose_retried=compose_retried,
        send_retried=send_retried,
    )
