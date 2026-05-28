from __future__ import annotations

import json
import uuid

import math

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Campaign, CampaignStatus, Lead, SendStatus, Suppression
from app.schemas.lead import (
    ConfirmUploadResponse,
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
