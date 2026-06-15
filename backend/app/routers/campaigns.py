from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
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
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LinkedInAccount,
    ResearchStatus,
    SendStatus,
    Sequence,
    SequenceNode,
    Suppression,
)
from app.workers.send import compute_next_send_window
from app.schemas.campaign import (
    ApplySignatureResponse,
    CampaignActivity,
    CampaignCreate,
    CampaignResponse,
    CampaignStats,
    CampaignUpdate,
    ConnectedAccountInfo,
    FailedLeadInfo,
    LeadCounts,
    RecentLeadEvent,
    RetryFailedResponse,
    SequenceLeadStateInfo,
    SequenceStepEvent,
    campaign_to_dict,
)
from app.schemas.lead import LeadEmailUpdate, LeadResponse, LeadSummary, PaginatedLeads
from app.services.sequence_service import ensure_default_sequence
from app.services.signature import apply_signature, resolve_campaign_signature

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
    account_signature: str | None = None
    if campaign.connected_account_id is not None:
        acc = await db.get(ConnectedAccount, campaign.connected_account_id)
        if acc is not None:
            account_info = ConnectedAccountInfo.model_validate(acc)
            account_signature = acc.signature

    payload: dict[str, Any] = campaign_to_dict(campaign)
    payload["connected_account"] = account_info
    # The inherited signature from Settings (the bound account); the UI shows
    # this when the campaign has no per-campaign override.
    payload["account_signature"] = account_signature
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

    updates = payload.model_dump(exclude_unset=True)

    # Account assignments + signature + the schedule/throughput family are
    # safe to change on any status:
    #   - signature only affects emails on "Apply to all" or future composes
    #   - schedule (days / start / end / timezone) and throughput (min_delay,
    #     hour cap, day cap) take effect on the next send_lead invocation;
    #     existing eta-deferred tasks re-check the window when they fire.
    # ``goal`` is editable on any non-complete status: changing it rewrites
    # every not-yet-sent email (re-compose, NO new research — the existing
    # research_data is reused), so the "split voice" risk is resolved by
    # rewriting the unsent batch rather than by blocking the edit.  The
    # remaining content fields (tone, sender_*, research_mode, templates,
    # ...) stay gated to draft/previewing.
    _SCHEDULE_FIELDS = {
        "schedule_days",
        "schedule_time_start",
        "schedule_time_end",
        "schedule_timezone",
    }
    _THROUGHPUT_FIELDS = {
        "min_delay_seconds", "max_per_hour", "max_per_day",
        # Send-time optimization only affects FUTURE sends, so it's safe to
        # toggle mid-flight (no re-enqueue needed — the next gate pass
        # applies it).
        "send_time_optimization",
    }
    _status_exempt_fields = (
        {"linkedin_account_id", "connected_account_id", "signature", "goal"}
        | _SCHEDULE_FIELDS
        | _THROUGHPUT_FIELDS
    )
    if updates.keys() - _status_exempt_fields:
        if c.status not in {CampaignStatus.DRAFT, CampaignStatus.PREVIEWING}:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot edit campaign in status '{c.status.value}' (only draft or previewing)",
            )

    # A completed campaign is frozen — no goal rewrite (nothing left to send).
    if "goal" in updates and c.status == CampaignStatus.COMPLETE:
        raise HTTPException(
            status_code=409, detail="Cannot edit a completed campaign",
        )

    if "connected_account_id" in updates:
        await _verify_account_exists(db, updates["connected_account_id"])
    if "linkedin_account_id" in updates:
        await _verify_linkedin_account_exists(db, updates["linkedin_account_id"])

    schedule_touched = bool(updates.keys() & _SCHEDULE_FIELDS)
    goal_changed = "goal" in updates and updates["goal"] != c.goal

    for key, value in updates.items():
        setattr(c, key, value)

    # When the schedule shifts on a live campaign, re-enqueue every composed
    # PENDING+SCHEDULED lead so they pick up the new window.  Required
    # because nothing reads `lead.scheduled_send_at` — a SCHEDULED orphan
    # parked at the OLD eta would otherwise stay parked even after you
    # widen the window.  Throughput-only edits don't need this: the new
    # `min_delay` takes effect on the next gate claim naturally.  Snapshot
    # IDs before commit so the rowset is captured.
    requeue_ids: list[uuid.UUID] = []
    if schedule_touched and c.status in {
        CampaignStatus.RUNNING, CampaignStatus.PAUSED,
    }:
        requeue_ids = list((await db.execute(
            select(Lead.id).where(
                Lead.campaign_id == c.id,
                Lead.compose_status == ComposeStatus.DONE,
                Lead.send_status.in_((SendStatus.PENDING, SendStatus.SCHEDULED)),
            )
        )).scalars().all())

    # Goal changed → rewrite every already-composed, not-yet-sent email.
    # Re-compose reuses the lead's existing research_data, so NO new research
    # runs (research is a separate task).  Sent emails are left alone.
    recompose_ids: list[uuid.UUID] = []
    if goal_changed and c.status != CampaignStatus.DRAFT:
        recompose_ids = list((await db.execute(
            select(Lead.id).where(
                Lead.campaign_id == c.id,
                Lead.compose_status == ComposeStatus.DONE,
                Lead.send_status != SendStatus.SENT,
            )
        )).scalars().all())

    await db.commit()
    await db.refresh(c)

    if recompose_ids:
        from app.workers.compose import compose_lead

        for lid in recompose_ids:
            compose_lead.delay(str(lid))

    if requeue_ids:
        from app.workers.send import send_lead

        min_delay = max(c.min_delay_seconds or 0, 0)
        base = datetime.now(timezone.utc)
        for i, lid in enumerate(requeue_ids):
            if min_delay:
                send_lead.apply_async(
                    args=[str(lid)], eta=base + timedelta(seconds=i * min_delay)
                )
            else:
                send_lead.delay(str(lid))

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
    # Manual pause: clear any cap auto-pause marker so the sequencer doesn't
    # auto-resume it — the user wants it to stay paused.
    c.auto_paused_until = None
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
    c.auto_paused_until = None
    # A circuit-breaker pause requires THIS explicit human resume — clear
    # the marker + reason so the breaker can re-trip on fresh data.
    c.auto_paused_at = None
    c.auto_pause_reason = None

    # Re-enqueue every composed lead that hasn't sent yet.  Without this,
    # leads that hit the paused / out-of-window gate during the pause window
    # are orphaned in send_status=SCHEDULED — their send tasks were acked
    # when they returned `{status: paused}` and nothing else dispatches them
    # (the sequencer beat defers on entry-email nodes waiting for the legacy
    # pipeline; nothing reads `scheduled_send_at`).  Snapshot the IDs before
    # commit so the rowset is captured even after the transaction closes.
    pending_ids = list((await db.execute(
        select(Lead.id).where(
            Lead.campaign_id == c.id,
            Lead.compose_status == ComposeStatus.DONE,
            Lead.send_status.in_((SendStatus.PENDING, SendStatus.SCHEDULED)),
        )
    )).scalars().all())

    await db.commit()
    await db.refresh(c)

    # Stagger by min_delay so we don't fire the whole batch at once and
    # rely on the rate gate to bounce the losers — same pattern as
    # _kick_off_full_campaign in routers/preview.py.
    if pending_ids:
        from app.workers.send import send_lead

        min_delay = max(c.min_delay_seconds or 0, 0)
        base = datetime.now(timezone.utc)
        for i, lid in enumerate(pending_ids):
            if min_delay:
                send_lead.apply_async(
                    args=[str(lid)], eta=base + timedelta(seconds=i * min_delay)
                )
            else:
                send_lead.delay(str(lid))

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
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
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

    order_col = Lead.updated_at if sort_by == "updated_at" else Lead.created_at
    count_q = select(func.count()).select_from(Lead).where(*filters)
    total = (await db.execute(count_q)).scalar_one()

    rows_q = (
        select(Lead)
        .where(*filters)
        .order_by(order_col.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(rows_q)).scalars().all()
    items: list[LeadSummary] = []
    for l in rows:
        s = LeadSummary.model_validate(l)
        s.has_notes = bool(l.notes)
        if s.notes and len(s.notes) > 280:
            s.notes = s.notes[:277] + "…"
        items.append(s)

    return PaginatedLeads(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get("/{campaign_id}/leads/{lead_id}", response_model=LeadResponse)
async def get_campaign_lead(
    campaign_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    await _get_or_404(db, campaign_id)
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadResponse.model_validate(lead)


@router.patch("/{campaign_id}/leads/{lead_id}", response_model=LeadResponse)
async def update_campaign_lead(
    campaign_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: LeadEmailUpdate,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    """Edit a lead's composed email (subject/body) and/or notes.  Notes are
    always editable (CRM-lite); the composed email is locked once the email
    has been sent."""
    await _get_or_404(db, campaign_id)
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    updates = payload.model_dump(exclude_unset=True)
    composed_edits = {k: v for k, v in updates.items() if k in {"composed_subject", "composed_body"}}
    if composed_edits and lead.send_status == SendStatus.SENT:
        raise HTTPException(
            status_code=409, detail="Cannot edit an email that has already been sent"
        )
    if "composed_subject" in composed_edits and composed_edits["composed_subject"] is not None:
        lead.composed_subject = composed_edits["composed_subject"]
    if "composed_body" in composed_edits and composed_edits["composed_body"] is not None:
        lead.composed_body = composed_edits["composed_body"]
    if "notes" in updates:
        # Accept None / "" — both clear notes (treat all-whitespace as None).
        v = updates["notes"]
        lead.notes = v.strip() if (v and v.strip()) else None
    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post("/{campaign_id}/apply-signature", response_model=ApplySignatureResponse)
async def apply_campaign_signature(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ApplySignatureResponse:
    """Apply the campaign's signature to every composed, not-yet-sent email —
    swapping each one's AI sign-off for the signature block.  Idempotent: an
    email that already ends with the signature is left unchanged, so it's safe
    to run more than once.  Sent emails are skipped (can't be unsent).

    Uses the campaign's own signature when set, otherwise the connected
    account's Settings signature (the inherited default)."""
    c = await _get_or_404(db, campaign_id)
    effective_sig = await resolve_campaign_signature(db, c)
    if not (effective_sig or "").strip():
        raise HTTPException(
            status_code=400,
            detail="No signature set on this campaign or its connected account",
        )

    leads = (await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.compose_status == ComposeStatus.DONE,
            Lead.send_status != SendStatus.SENT,
        )
    )).scalars().all()

    updated = 0
    for lead in leads:
        new_body = apply_signature(lead.composed_body, effective_sig)
        if new_body != (lead.composed_body or ""):
            lead.composed_body = new_body
            updated += 1
    await db.commit()
    return ApplySignatureResponse(updated=updated)


@router.delete("/{campaign_id}/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_campaign_lead(
    campaign_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a lead from the campaign and halt all future sequence steps.

    The Lead row, its ``lead_sequence_states`` row, every
    ``lead_step_executions`` row, and every ``email_events`` row all
    have ``ondelete=CASCADE`` on their ``lead_id`` FK, so a single
    ``DELETE FROM leads`` cleans up every child row in one transaction.

    Any Celery task already in flight for this lead (compose, send,
    or a send_linkedin_step pulled from the queue) will read
    ``lead is None`` on its next session load and short-circuit with
    ``{"status": "not_found"}`` — the existing safety guards.  No
    additional "stop the queue" call is needed.
    """
    await _get_or_404(db, campaign_id)
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    await db.delete(lead)
    await db.commit()


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


@router.get("/{campaign_id}/activity", response_model=CampaignActivity)
async def get_campaign_activity(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CampaignActivity:
    from datetime import timezone
    campaign = await _get_or_404(db, campaign_id)

    # Status counts across the full pipeline
    pipeline_q = await db.execute(
        select(
            Lead.research_status,
            Lead.compose_status,
            Lead.send_status,
            func.count(),
        )
        .where(Lead.campaign_id == campaign_id)
        .group_by(Lead.research_status, Lead.compose_status, Lead.send_status)
    )
    rows = pipeline_q.all()

    researching = composing = pending_send = scheduled_send = sent = failed = 0
    for r_status, c_status, s_status, cnt in rows:
        if r_status == ResearchStatus.RUNNING:
            researching += cnt
        if c_status == ComposeStatus.RUNNING:
            composing += cnt
        if s_status == SendStatus.PENDING:
            pending_send += cnt
        if s_status == SendStatus.SCHEDULED:
            scheduled_send += cnt
        if s_status == SendStatus.SENT:
            sent += cnt
        if s_status == SendStatus.FAILED or r_status == ResearchStatus.FAILED or c_status == ComposeStatus.FAILED:
            failed += cnt

    # Next send window
    next_window_at = compute_next_send_window(campaign)

    # Estimated minutes: pending leads / throughput (1 per min_delay_seconds within window)
    queue_depth = pending_send + scheduled_send
    if queue_depth > 0 and campaign.min_delay_seconds > 0:
        raw_minutes = math.ceil((queue_depth * campaign.min_delay_seconds) / 60)
        if next_window_at is not None:
            gap_minutes = max(0, int((next_window_at - datetime.now(timezone.utc)).total_seconds() / 60))
            estimated_minutes_remaining: int | None = raw_minutes + gap_minutes
        else:
            estimated_minutes_remaining = raw_minutes
    else:
        estimated_minutes_remaining = None

    # 20 most recently updated leads for the email pipeline activity feed
    recent_rows = (await db.execute(
        select(Lead)
        .where(Lead.campaign_id == campaign_id)
        .order_by(Lead.updated_at.desc())
        .limit(20)
    )).scalars().all()

    recent_events = [
        RecentLeadEvent(
            lead_id=lead.id,
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            company=lead.company,
            research_status=lead.research_status.value,
            compose_status=lead.compose_status.value,
            send_status=lead.send_status.value,
            scheduled_send_at=lead.scheduled_send_at,
            updated_at=lead.updated_at,
        )
        for lead in recent_rows
    ]

    # Sequence state counts
    seq_state_q = await db.execute(
        select(LeadSequenceState.status, func.count())
        .join(Lead, Lead.id == LeadSequenceState.lead_id)
        .where(Lead.campaign_id == campaign_id)
        .group_by(LeadSequenceState.status)
    )
    seq_by_status: dict[LeadSequenceStatus, int] = {row[0]: row[1] for row in seq_state_q.all()}
    sequence_active = seq_by_status.get(LeadSequenceStatus.ACTIVE, 0)
    sequence_halted = seq_by_status.get(LeadSequenceStatus.HALTED, 0)
    sequence_completed = seq_by_status.get(LeadSequenceStatus.COMPLETED, 0)
    sequence_pending = seq_by_status.get(LeadSequenceStatus.PENDING, 0)

    # Recent sequence step executions (includes LinkedIn + follow-up emails).
    # Pull a wider window than we display so we can dedupe consecutive
    # same-(lead, node) retry rows into a single cluster — typical case
    # is a LinkedIn step that hit a transient rate-limit and retried 4
    # times before sending; the UI surfaces only the latest row + an "Nx"
    # badge instead of cluttering the list with the retry chain.
    raw_step_rows = (await db.execute(
        select(LeadStepExecution, Lead, SequenceNode)
        .join(Lead, Lead.id == LeadStepExecution.lead_id)
        .join(SequenceNode, SequenceNode.id == LeadStepExecution.node_id)
        .where(Lead.campaign_id == campaign_id)
        .order_by(LeadStepExecution.attempted_at.desc())
        .limit(100)
    )).all()

    # Cluster by (lead_id, node_id), preserving the desc-by-time
    # iteration order.  The FIRST time we see a key we keep its row as
    # the cluster's canonical "latest"; subsequent rows with the same
    # key just bump the count + slide the earliest timestamp back.
    clusters: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
    cluster_order: list[tuple[uuid.UUID, uuid.UUID]] = []
    for exec_row, lead_row, node_row in raw_step_rows:
        key = (lead_row.id, node_row.id)
        c = clusters.get(key)
        if c is None:
            clusters[key] = {
                "exec": exec_row,
                "lead": lead_row,
                "node": node_row,
                "attempt_count": 1,
                "earliest_attempted_at": exec_row.attempted_at,
            }
            cluster_order.append(key)
        else:
            c["attempt_count"] += 1
            # iterating DESC, so each subsequent row is older.
            c["earliest_attempted_at"] = exec_row.attempted_at

    recent_sequence_steps = [
        SequenceStepEvent(
            lead_id=clusters[key]["lead"].id,
            email=clusters[key]["lead"].email,
            first_name=clusters[key]["lead"].first_name,
            last_name=clusters[key]["lead"].last_name,
            company=clusters[key]["lead"].company,
            node_kind=clusters[key]["node"].kind.value,
            result=clusters[key]["exec"].result.value,
            error=clusters[key]["exec"].error,
            attempted_at=clusters[key]["exec"].attempted_at,
            attempt_count=clusters[key]["attempt_count"],
            earliest_attempted_at=(
                clusters[key]["earliest_attempted_at"]
                if clusters[key]["attempt_count"] > 1
                else None
            ),
        )
        for key in cluster_order[:30]
    ]

    # Halted leads (leads whose sequence cursor points at a deleted/blocked node)
    halted_rows = (await db.execute(
        select(LeadSequenceState, Lead, SequenceNode)
        .join(Lead, Lead.id == LeadSequenceState.lead_id)
        .outerjoin(SequenceNode, SequenceNode.id == LeadSequenceState.current_node_id)
        .where(
            Lead.campaign_id == campaign_id,
            LeadSequenceState.status == LeadSequenceStatus.HALTED,
        )
        .order_by(LeadSequenceState.updated_at.desc())
        .limit(20)
    )).all()

    halted_leads = [
        SequenceLeadStateInfo(
            lead_id=row[1].id,
            email=row[1].email,
            first_name=row[1].first_name,
            last_name=row[1].last_name,
            company=row[1].company,
            status=row[0].status.value,
            current_node_kind=row[2].kind.value if row[2] else None,
            next_run_at=row[0].next_run_at,
            halt_reason=row[0].halt_reason,
        )
        for row in halted_rows
    ]

    # Upcoming scheduled sequence steps (active leads sorted by next_run_at)
    upcoming_rows = (await db.execute(
        select(LeadSequenceState, Lead, SequenceNode)
        .join(Lead, Lead.id == LeadSequenceState.lead_id)
        .outerjoin(SequenceNode, SequenceNode.id == LeadSequenceState.current_node_id)
        .where(
            Lead.campaign_id == campaign_id,
            LeadSequenceState.status == LeadSequenceStatus.ACTIVE,
            LeadSequenceState.next_run_at.is_not(None),
        )
        .order_by(LeadSequenceState.next_run_at.asc())
        .limit(10)
    )).all()

    upcoming_steps = [
        SequenceLeadStateInfo(
            lead_id=row[1].id,
            email=row[1].email,
            first_name=row[1].first_name,
            last_name=row[1].last_name,
            company=row[1].company,
            status=row[0].status.value,
            current_node_kind=row[2].kind.value if row[2] else None,
            next_run_at=row[0].next_run_at,
            halt_reason=row[0].halt_reason,
        )
        for row in upcoming_rows
    ]

    return CampaignActivity(
        researching=researching,
        composing=composing,
        pending_send=pending_send,
        scheduled_send=scheduled_send,
        sent=sent,
        failed=failed,
        sequence_active=sequence_active,
        sequence_halted=sequence_halted,
        sequence_completed=sequence_completed,
        sequence_pending=sequence_pending,
        next_window_at=next_window_at,
        estimated_minutes_remaining=estimated_minutes_remaining,
        recent_events=recent_events,
        recent_sequence_steps=recent_sequence_steps,
        halted_leads=halted_leads,
        upcoming_steps=upcoming_steps,
    )


# --------------------------------------------------------------------------
# Re-enroll halted leads
# --------------------------------------------------------------------------


@router.post("/{campaign_id}/re-enroll-halted")
async def re_enroll_halted_leads(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Reset all halted sequence states back to the entry node so the
    sequencer picks them up on the next beat tick."""
    from datetime import timezone
    await _get_or_404(db, campaign_id)

    # Exclude leads whose email is on the workspace suppression list —
    # re-enrolling them would resume LinkedIn outreach to deliberately
    # ignored / unsubscribed prospects (the email step would still gate,
    # but LinkedIn steps fire from the sequencer).  func.lower keeps the
    # comparison robust against any legacy mixed-case lead rows.
    suppressed_emails = select(Suppression.email)
    halted_states = (await db.execute(
        select(LeadSequenceState)
        .join(Lead, Lead.id == LeadSequenceState.lead_id)
        .where(
            Lead.campaign_id == campaign_id,
            LeadSequenceState.status == LeadSequenceStatus.HALTED,
            func.lower(Lead.email).notin_(suppressed_emails),
        )
    )).scalars().all()

    if not halted_states:
        return {"re_enrolled": 0}

    seq = (await db.execute(
        select(Sequence).where(Sequence.campaign_id == campaign_id)
    )).scalar_one_or_none()
    if seq is None:
        raise HTTPException(status_code=404, detail="No sequence found for campaign")

    entry_node = (await db.execute(
        select(SequenceNode).where(
            and_(
                SequenceNode.sequence_id == seq.id,
                SequenceNode.is_entry.is_(True),
                SequenceNode.deleted_at.is_(None),
            )
        )
    )).scalar_one_or_none()
    if entry_node is None:
        raise HTTPException(status_code=409, detail="No live entry node found in sequence")

    now = datetime.now(timezone.utc)
    for state in halted_states:
        state.status = LeadSequenceStatus.ACTIVE
        state.current_node_id = entry_node.id
        state.halt_reason = None
        state.next_run_at = now
        state.entered_current_at = now

    await db.commit()
    return {"re_enrolled": len(halted_states)}


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


@router.get("/{campaign_id}/deliverability")
async def get_deliverability(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Deliverability strip data: window bounce/spam/open rates, the
    sending domain's remaining hourly/daily headroom, and the breaker
    state."""
    import redis.asyncio as aioredis

    from app.config import settings as app_settings
    from app.services import deliverability as deliv
    from app.workers.send import resolve_sending_domain

    c = await _get_or_404(db, campaign_id)
    stats = await deliv.deliverability_stats(db, c.id)

    domain = await resolve_sending_domain(db, c)
    domain_block: dict[str, Any] | None = None
    if domain:
        r = aioredis.from_url(app_settings.REDIS_URL, decode_responses=True)
        try:
            hour_used = int(await r.get(f"rate:domain:{domain}:hour") or 0)
            day_used = int(await r.get(f"rate:domain:{domain}:day") or 0)
        except Exception:  # noqa: BLE001 — Redis down ≠ 500 the strip
            hour_used = day_used = 0
        finally:
            await r.aclose()
        domain_block = {
            "domain": domain,
            "hour_used": hour_used,
            "hour_cap": app_settings.DOMAIN_MAX_PER_HOUR,
            "hour_remaining": max(0, app_settings.DOMAIN_MAX_PER_HOUR - hour_used),
            "day_used": day_used,
            "day_cap": app_settings.DOMAIN_MAX_PER_DAY,
            "day_remaining": max(0, app_settings.DOMAIN_MAX_PER_DAY - day_used),
        }

    return {
        **stats,
        "domain": domain_block,
        "auto_paused_at": c.auto_paused_at.isoformat() if c.auto_paused_at else None,
        "auto_pause_reason": c.auto_pause_reason,
        "send_time_optimization": c.send_time_optimization,
    }


@router.get("/{campaign_id}/copy-insights")
async def get_copy_insights(
    campaign_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """"What's working" panel data: reply-outcome counts by sentiment,
    the cached winning-angle summary, and example winning messages."""
    from app.models import ReplyOutcome
    from app.services import copy_insights as ci

    c = await _get_or_404(db, campaign_id)
    sentiment_rows = (await db.execute(
        select(ReplyOutcome.sentiment, func.count())
        .where(ReplyOutcome.campaign_id == c.id)
        .group_by(ReplyOutcome.sentiment)
    )).all()
    counts = {s: n for s, n in sentiment_rows}

    cached = await ci.get_cached_insights(db, c.id)
    examples = await ci.winning_examples(db, c.id, limit=3)
    insights = None
    if cached is not None:
        insights = {k: v for k, v in cached.insights.items() if k != "_meta"}

    return {
        "outcome_counts": {
            "positive": counts.get("positive", 0),
            "neutral": counts.get("neutral", 0),
            "negative": counts.get("negative", 0),
        },
        "insights": insights,
        "refreshed_at": cached.refreshed_at.isoformat() if cached else None,
        "winning_examples": examples,
    }
