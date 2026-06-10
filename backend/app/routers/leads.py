from __future__ import annotations

import json
import uuid

import math

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Campaign,
    CampaignStatus,
    EmailEvent,
    EmailEventType,
    Lead,
    LeadStepExecution,
    LeadStepResult,
    SendStatus,
    SequenceNode,
    SequenceNodeKind,
    Suppression,
)
from app.schemas.lead import (
    ConfirmUploadResponse,
    LeadDetail,
    LeadHistoryItem,
    LeadSummary,
    PaginatedLeads,
    UploadPreviewResponse,
)
from app.services.csv_parser import parse_csv_content, select_sample_indices, suggest_mapping
from app.services.sequence_service import (
    campaign_sends_legacy_first_email,
    enroll_leads,
    ensure_default_sequence,
)
from app.workers import ingest as ingest_tasks

router = APIRouter(tags=["leads"])


@router.get("/leads", response_model=PaginatedLeads)
async def list_all_leads(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    campaign_id: uuid.UUID | None = None,
    send_status: SendStatus | None = None,
    search: str | None = None,
    has_notes: bool | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaginatedLeads:
    """Global cross-campaign leads listing — the lite-CRM Leads tab.

    Each row includes the lead's notes preview + the campaign name, so the
    user can browse / filter / add notes across every campaign at once.
    """
    filters = []
    if campaign_id is not None:
        filters.append(Lead.campaign_id == campaign_id)
    if send_status is not None:
        filters.append(Lead.send_status == send_status)
    if search:
        s = f"%{search}%"
        filters.append(
            or_(Lead.email.ilike(s), Lead.first_name.ilike(s),
                Lead.last_name.ilike(s), Lead.company.ilike(s))
        )
    if has_notes is True:
        filters.append(Lead.notes.is_not(None))
    elif has_notes is False:
        filters.append(Lead.notes.is_(None))

    total = (await db.execute(
        select(func.count()).select_from(Lead).where(*filters)
    )).scalar_one()

    rows_q = (
        select(Lead, Campaign.name)
        .join(Campaign, Campaign.id == Lead.campaign_id)
        .where(*filters)
        .order_by(Lead.updated_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await db.execute(rows_q)).all()
    items: list[LeadSummary] = []
    for lead, campaign_name in rows:
        s = LeadSummary.model_validate(lead)
        s.campaign_name = campaign_name
        s.has_notes = bool(lead.notes)
        # Trim notes preview so the list payload doesn't ship full essays.
        if s.notes and len(s.notes) > 280:
            s.notes = s.notes[:277] + "…"
        items.append(s)

    return PaginatedLeads(
        items=items, total=total, page=page, page_size=page_size,
        total_pages=math.ceil(total / page_size) if total > 0 else 0,
    )


# ─── Lead detail (rich CRM-style view) ───────────────────────────────────

_NODE_KIND_LABEL_VERB = {
    SequenceNodeKind.EMAIL: ("Sent email", "📧"),
    SequenceNodeKind.LINKEDIN_VIEW_PROFILE: ("Viewed LinkedIn profile", "👁️"),
    SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE: ("Followed on LinkedIn", "➕"),
    SequenceNodeKind.LINKEDIN_CONNECT: ("Sent LinkedIn connection request", "🤝"),
    SequenceNodeKind.LINKEDIN_DM: ("Sent LinkedIn DM", "💬"),
    SequenceNodeKind.LINKEDIN_INMAIL: ("Sent InMail", "📨"),
    SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE: ("Invited to company page", "🏢"),
    SequenceNodeKind.LINKEDIN_REACT_POST: ("Reacted to LinkedIn post", "👍"),
    SequenceNodeKind.LINKEDIN_COMMENT_POST: ("Commented on LinkedIn post", "💭"),
    SequenceNodeKind.WAIT: ("Waited", "⏳"),
}

_EVENT_LABEL_VERB = {
    EmailEventType.DELIVERED: ("Email delivered", "✉️"),
    EmailEventType.OPENED: ("Email opened", "👀"),
    EmailEventType.CLICKED: ("Link clicked", "🖱️"),
    EmailEventType.REPLIED: ("Replied", "💬"),
    EmailEventType.SOFT_BOUNCE: ("Soft-bounced", "⚠️"),
    EmailEventType.HARD_BOUNCE: ("Hard-bounced", "⚠️"),
    EmailEventType.SPAM: ("Marked as spam", "🚫"),
    EmailEventType.UNSUBSCRIBED: ("Unsubscribed", "🚷"),
}

# Result enum → status string used by the UI for colour cues.
_RESULT_STATUS = {
    LeadStepResult.SENT: "success",
    LeadStepResult.SKIPPED: "warn",
    LeadStepResult.FAILED: "fail",
}

# Event type → status string.  Engagement (delivered/opened/clicked/replied)
# is success; bounces / spam / unsubscribe are warn/fail.
_EVENT_STATUS = {
    EmailEventType.DELIVERED: "success",
    EmailEventType.OPENED: "success",
    EmailEventType.CLICKED: "success",
    EmailEventType.REPLIED: "success",
    EmailEventType.SOFT_BOUNCE: "warn",
    EmailEventType.HARD_BOUNCE: "fail",
    EmailEventType.SPAM: "fail",
    EmailEventType.UNSUBSCRIBED: "warn",
}


def _research_summary(blob: dict | None) -> dict:
    """Pick the most useful fields out of the (potentially noisy)
    ``research_data`` JSONB so the UI doesn't have to render the whole
    raw blob.  Safe on missing / partial / None input."""
    if not isinstance(blob, dict):
        return {}
    return {
        "industry": str(blob.get("industry") or "") or None,
        "size_hint": str(blob.get("size_hint") or "") or None,
        "company_description": str(blob.get("company_description") or "") or None,
        "person_news": [x for x in (blob.get("person_news") or []) if x][:6],
        "company_news": [x for x in (blob.get("company_news") or []) if x][:6],
        "recent_updates": [x for x in (blob.get("recent_updates") or []) if x][:6],
        "quality": str(blob.get("quality") or "") or None,
        "from_cache": bool(blob.get("from_cache")),
    }


@router.get("/leads/{lead_id}", response_model=LeadDetail)
async def get_lead_detail(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LeadDetail:
    """Full per-lead view powering the lite-CRM detail modal.

    Returns the lead, every sequence step we attempted + every email
    event the recipient triggered, merged into a single chronologically-
    sorted ``history`` array.  Done server-side so the UI doesn't have
    to do two fetches and a JS sort on hundreds of rows.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Campaign name for the header context.
    campaign = await db.get(Campaign, lead.campaign_id)
    campaign_name = campaign.name if campaign is not None else None

    # Sequence executions, joined to the node so we can label by kind.
    exec_rows = (await db.execute(
        select(LeadStepExecution, SequenceNode.kind)
        .join(SequenceNode, SequenceNode.id == LeadStepExecution.node_id)
        .where(LeadStepExecution.lead_id == lead_id)
        .order_by(LeadStepExecution.attempted_at.desc())
    )).all()

    # Email events (passive engagement signal).
    event_rows = (await db.execute(
        select(EmailEvent)
        .where(EmailEvent.lead_id == lead_id)
        .order_by(EmailEvent.occurred_at.desc())
    )).scalars().all()

    history: list[LeadHistoryItem] = []
    counts: dict[str, int] = {}

    for execution, node_kind in exec_rows:
        label, icon = _NODE_KIND_LABEL_VERB.get(node_kind, (node_kind.value.replace("_", " ").title(), "•"))
        status = _RESULT_STATUS.get(execution.result, "warn")
        # Prefix the verb with the action result so the UI line reads
        # well even without the status pill: "Sent email" stays as-is on
        # success, but "Sent email (failed)" surfaces the fail-state.
        if execution.result == LeadStepResult.FAILED:
            label = f"{label} (failed)"
        elif execution.result == LeadStepResult.SKIPPED:
            label = f"{label} (skipped)"
        history.append(LeadHistoryItem(
            at=execution.attempted_at,
            kind="execution",
            action=label,
            status=status,
            icon=icon,
            detail=(execution.error or None),
            external_id=execution.external_id,
        ))
        # Roll-up: count successful sends per node kind for the header pills.
        if execution.result == LeadStepResult.SENT:
            counts[node_kind.value] = counts.get(node_kind.value, 0) + 1

    for event in event_rows:
        label, icon = _EVENT_LABEL_VERB.get(event.event_type, (event.event_type.value, "•"))
        history.append(LeadHistoryItem(
            at=event.occurred_at,
            kind="event",
            action=label,
            status=_EVENT_STATUS.get(event.event_type, "warn"),
            icon=icon,
            detail=None,
            external_id=None,
        ))
        counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1

    # Stable chronological order (newest first) regardless of which
    # source row landed first in the list.
    history.sort(key=lambda h: h.at, reverse=True)

    detail = LeadDetail(
        id=lead.id,
        campaign_id=lead.campaign_id,
        email=lead.email,
        first_name=lead.first_name,
        last_name=lead.last_name,
        company=lead.company,
        company_name=lead.company,
        company_website=lead.company_website,
        job_title=lead.job_title,
        phone=lead.phone,
        linkedin_url=lead.linkedin_url,
        linkedin_connection_status=lead.linkedin_connection_status,
        linkedin_last_reply_at=lead.linkedin_last_reply_at,
        research_status=lead.research_status,
        compose_status=lead.compose_status,
        send_status=lead.send_status,
        is_sample=lead.is_sample,
        sample_approved=lead.sample_approved,
        scheduled_send_at=lead.scheduled_send_at,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        notes=lead.notes,
        has_notes=bool(lead.notes),
        campaign_name=campaign_name,
        raw_csv_row=lead.raw_csv_row,
        research_data=lead.research_data,
        composed_subject=lead.composed_subject,
        composed_body=lead.composed_body,
        style_correction=lead.style_correction,
        brevo_message_id=lead.brevo_message_id,
        history=history,
        history_counts=counts,
        research_summary=_research_summary(lead.research_data),
    )
    return detail


# Lead-model fields the user is allowed to populate from a CSV column.
_ALLOWED_LEAD_FIELDS = {
    "email", "phone", "linkedin_url",
    "first_name", "last_name", "company", "company_website", "job_title",
}


async def _get_campaign_or_404(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    c = await db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.post(
    "/campaigns/{campaign_id}/upload",
    response_model=UploadPreviewResponse,
)
async def upload_preview(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadPreviewResponse:
    await _get_campaign_or_404(db, campaign_id)
    content = await file.read()
    try:
        columns, rows = parse_csv_content(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return UploadPreviewResponse(
        columns=columns,
        preview_rows=rows[:5],
        suggested_mapping=suggest_mapping(columns),
        total_rows=len(rows),
    )


@router.post(
    "/campaigns/{campaign_id}/leads/confirm-upload",
    response_model=ConfirmUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_upload(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    mapping: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> ConfirmUploadResponse:
    campaign = await _get_campaign_or_404(db, campaign_id)

    try:
        mapping_dict: dict[str, str] = json.loads(mapping)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"mapping is not valid JSON: {e}")
    if not isinstance(mapping_dict, dict):
        raise HTTPException(status_code=422, detail="mapping must be a JSON object")

    # Identify which CSV column maps to email — required.
    email_col = next(
        (csv_col for csv_col, field in mapping_dict.items() if field == "email"),
        None,
    )
    if email_col is None:
        raise HTTPException(status_code=422, detail="No CSV column is mapped to email")

    content = await file.read()
    try:
        _, rows = parse_csv_content(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Filter mapping to fields we actually persist on Lead (besides email).
    field_map = {
        col: f for col, f in mapping_dict.items()
        if f in _ALLOWED_LEAD_FIELDS and f != "email"
    }

    # Collect candidate emails to check against suppression list in one query.
    candidate_emails: set[str] = set()
    for row in rows:
        email = (row.get(email_col, "") or "").strip().lower()
        if email:
            candidate_emails.add(email)

    suppressed_set: set[str] = set()
    if candidate_emails:
        rows_q = await db.execute(
            select(Suppression.email).where(Suppression.email.in_(candidate_emails))
        )
        suppressed_set = {r[0] for r in rows_q.all()}

    leads_to_insert: list[Lead] = []
    seen: set[str] = set()
    duplicates = 0
    suppressed_count = 0

    for row in rows:
        email = (row.get(email_col, "") or "").strip().lower()
        if not email:
            continue
        if email in suppressed_set:
            suppressed_count += 1
            continue
        if email in seen:
            duplicates += 1
            continue
        seen.add(email)

        kwargs: dict[str, str | dict] = {
            "campaign_id": campaign_id,
            "email": email,
            "raw_csv_row": row,
        }
        for csv_col, field in field_map.items():
            value = (row.get(csv_col, "") or "").strip()
            if value:
                kwargs[field] = value

        leads_to_insert.append(Lead(**kwargs))

    # Sample selection — spread across the deduped list.
    sample_idx = set(select_sample_indices(len(leads_to_insert), campaign.sample_count))
    for i, lead in enumerate(leads_to_insert):
        if i in sample_idx:
            lead.is_sample = True

    db.add_all(leads_to_insert)
    await db.flush()
    # Make sure the campaign has a sequence + enroll the new leads onto its
    # entry node. Idempotent — ensure_default_sequence no-ops if a sequence
    # already exists (e.g. created via POST /campaigns/{id}/sequence).
    await ensure_default_sequence(db, campaign)
    await enroll_leads(db, campaign_id, [l.id for l in leads_to_insert])

    # Email-first campaigns go through sample review (PREVIEWING).  Any other
    # start node (LinkedIn / wait / ...) has no first email to preview, so
    # launch straight into RUNNING and let the sequencer drive the first
    # action — research still runs for DM personalization.
    auto_launched = not await campaign_sends_legacy_first_email(db, campaign_id)
    campaign.status = (
        CampaignStatus.RUNNING if auto_launched else CampaignStatus.PREVIEWING
    )
    await db.commit()

    if leads_to_insert:
        ingest_tasks.run_campaign_research.delay(str(campaign_id))

    return ConfirmUploadResponse(
        total=len(leads_to_insert),
        suppressed=suppressed_count,
        duplicates_removed=duplicates,
        samples_selected=len(sample_idx),
        auto_launched=auto_launched,
    )
