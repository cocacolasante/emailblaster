"""CRM endpoints: manual leads, lead conversion, opportunities, activities.

Salesforce-style layer.  The CRM lead IS the existing ``Lead`` model
(nullable campaign_id since migration 0025), so manually-created leads
show up in the same global Leads list the user already works in.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    CLOSED_STAGES,
    STAGE_DEFAULT_PROBABILITY,
    CrmActivity,
    CrmActivityType,
    CrmLeadStatus,
    Lead,
    Opportunity,
    OpportunityStage,
)
from app.schemas.crm import (
    ActivityCreate,
    ActivityResponse,
    ActivityUpdate,
    ConvertLeadRequest,
    ConvertLeadResponse,
    LeadCreate,
    LeadCrmUpdate,
    OpportunityCreate,
    OpportunityResponse,
    OpportunityUpdate,
    PaginatedActivities,
    PaginatedOpportunities,
    PipelineSummary,
)

router = APIRouter(prefix="/crm", tags=["crm"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# Manual lead creation + CRM status
# ============================================================================

@router.post("/leads", status_code=201)
async def create_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a campaign-less CRM lead.  It appears in the global Leads
    list immediately; it does NOT enter any compose/send pipeline."""
    lead = Lead(
        campaign_id=None,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        company=payload.company,
        job_title=payload.job_title,
        phone=payload.phone,
        linkedin_url=payload.linkedin_url,
        company_website=payload.company_website,
        notes=payload.notes,
        crm_status=payload.crm_status.value,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return {"id": str(lead.id), "email": lead.email, "crm_status": lead.crm_status}


@router.patch("/leads/{lead_id}", status_code=200)
async def update_lead_crm(
    lead_id: uuid.UUID,
    payload: LeadCrmUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update the CRM-facing lead fields (currently just crm_status)."""
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if payload.crm_status is not None:
        if payload.crm_status == CrmLeadStatus.CONVERTED:
            raise HTTPException(
                status_code=400,
                detail="Use POST /crm/leads/{id}/convert to convert a lead",
            )
        lead.crm_status = payload.crm_status.value
    await db.commit()
    return {"id": str(lead.id), "crm_status": lead.crm_status}


# ============================================================================
# Lead conversion
# ============================================================================

def _opportunity_response(
    opp: Opportunity, activity_count: int = 0, open_task_count: int = 0,
) -> OpportunityResponse:
    resp = OpportunityResponse.model_validate(opp)
    resp.amount = float(opp.amount) if opp.amount is not None else None
    resp.activity_count = activity_count
    resp.open_task_count = open_task_count
    return resp


@router.post("/leads/{lead_id}/convert", response_model=ConvertLeadResponse)
async def convert_lead(
    lead_id: uuid.UUID,
    payload: ConvertLeadRequest,
    db: AsyncSession = Depends(get_db),
) -> ConvertLeadResponse:
    """Convert a lead into an opportunity (Salesforce-style).

    - Contact snapshot copied from the lead onto the opportunity so the
      deal record survives lead deletion.
    - Lead flips to ``crm_status=converted`` and gets
      ``converted_opportunity_id`` set.
    - Existing lead-scoped activities stay on the lead; they remain
      visible from the opportunity via the source-lead link.
    - Idempotent-ish: converting an already-converted lead 409s with the
      existing opportunity id so the UI can deep-link instead of
      double-creating.
    """
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.converted_opportunity_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Lead already converted (opportunity {lead.converted_opportunity_id})",
        )

    full_name = " ".join(filter(None, [lead.first_name, lead.last_name]))
    default_name = (
        f"{lead.company or full_name or lead.email} — {date.today().isoformat()}"
    )
    opp = Opportunity(
        name=payload.name or default_name,
        stage=payload.stage,
        amount=payload.amount,
        close_date=payload.close_date,
        probability=STAGE_DEFAULT_PROBABILITY.get(payload.stage),
        first_name=lead.first_name,
        last_name=lead.last_name,
        email=lead.email,
        phone=lead.phone,
        company=lead.company,
        job_title=lead.job_title,
        linkedin_url=lead.linkedin_url,
        source_lead_id=lead.id,
    )
    db.add(opp)
    await db.flush()

    lead.crm_status = CrmLeadStatus.CONVERTED.value
    lead.converted_opportunity_id = opp.id

    # Conversion is itself a logged activity so the timeline tells the
    # full story.
    db.add(CrmActivity(
        lead_id=lead.id,
        opportunity_id=opp.id,
        activity_type=CrmActivityType.NOTE,
        subject="Lead converted to opportunity",
        body=f"Opportunity: {opp.name}",
    ))

    await db.commit()
    await db.refresh(opp)
    return ConvertLeadResponse(
        opportunity=_opportunity_response(opp, activity_count=1),
        lead_id=lead.id,
        lead_crm_status=CrmLeadStatus.CONVERTED,
    )


# ============================================================================
# Opportunities
# ============================================================================

async def _get_opp_or_404(db: AsyncSession, opp_id: uuid.UUID) -> Opportunity:
    opp = await db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


async def _activity_counts(
    db: AsyncSession, opp_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[int, int]]:
    """{opp_id: (activity_count, open_task_count)}."""
    if not opp_ids:
        return {}
    rows = (await db.execute(
        select(
            CrmActivity.opportunity_id,
            func.count(),
            func.count().filter(and_(
                CrmActivity.activity_type == CrmActivityType.TASK,
                CrmActivity.completed_at.is_(None),
            )),
        )
        .where(CrmActivity.opportunity_id.in_(opp_ids))
        .group_by(CrmActivity.opportunity_id)
    )).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@router.post("/opportunities", response_model=OpportunityResponse, status_code=201)
async def create_opportunity(
    payload: OpportunityCreate,
    db: AsyncSession = Depends(get_db),
) -> OpportunityResponse:
    opp = Opportunity(
        name=payload.name,
        stage=payload.stage,
        amount=payload.amount,
        close_date=payload.close_date,
        probability=(
            payload.probability
            if payload.probability is not None
            else STAGE_DEFAULT_PROBABILITY.get(payload.stage)
        ),
        description=payload.description,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=(payload.email or "").strip().lower() or None,
        phone=payload.phone,
        company=payload.company,
        job_title=payload.job_title,
        linkedin_url=payload.linkedin_url,
    )
    if payload.stage in CLOSED_STAGES:
        opp.closed_at = _now()
    db.add(opp)
    await db.commit()
    await db.refresh(opp)
    return _opportunity_response(opp)


@router.get("/opportunities", response_model=PaginatedOpportunities)
async def list_opportunities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    stage: OpportunityStage | None = Query(default=None),
    open_only: bool = Query(default=False),
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaginatedOpportunities:
    filters = []
    if stage is not None:
        filters.append(Opportunity.stage == stage)
    if open_only:
        filters.append(Opportunity.stage.notin_(list(CLOSED_STAGES)))
    if search:
        s = f"%{search.strip()}%"
        filters.append(
            Opportunity.name.ilike(s)
            | Opportunity.company.ilike(s)
            | Opportunity.email.ilike(s)
        )

    total = (await db.execute(
        select(func.count()).select_from(Opportunity).where(*filters)
    )).scalar_one()

    rows = (await db.execute(
        select(Opportunity)
        .where(*filters)
        .order_by(Opportunity.updated_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )).scalars().all()

    counts = await _activity_counts(db, [o.id for o in rows])
    items = [
        _opportunity_response(o, *counts.get(o.id, (0, 0))) for o in rows
    ]
    return PaginatedOpportunities(
        items=items, total=total, page=page, page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/opportunities/pipeline", response_model=list[PipelineSummary])
async def pipeline_summary(db: AsyncSession = Depends(get_db)) -> list[PipelineSummary]:
    """Per-stage roll-up for the Kanban board header."""
    rows = (await db.execute(
        select(
            Opportunity.stage,
            func.count(),
            func.coalesce(func.sum(Opportunity.amount), 0),
        ).group_by(Opportunity.stage)
    )).all()
    by_stage = {r[0]: (r[1], float(r[2])) for r in rows}
    return [
        PipelineSummary(
            stage=stage,
            count=by_stage.get(stage, (0, 0.0))[0],
            total_amount=by_stage.get(stage, (0, 0.0))[1],
        )
        for stage in OpportunityStage
    ]


@router.get("/opportunities/{opp_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> OpportunityResponse:
    opp = await _get_opp_or_404(db, opp_id)
    counts = await _activity_counts(db, [opp.id])
    return _opportunity_response(opp, *counts.get(opp.id, (0, 0)))


@router.patch("/opportunities/{opp_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opp_id: uuid.UUID,
    payload: OpportunityUpdate,
    db: AsyncSession = Depends(get_db),
) -> OpportunityResponse:
    """Update fields / move stage.  Stage transitions into closed_won /
    closed_lost stamp ``closed_at``; moving back out clears it.  The
    probability auto-follows the stage default UNLESS the user has set
    an explicit probability in the same request."""
    opp = await _get_opp_or_404(db, opp_id)
    updates = payload.model_dump(exclude_unset=True)

    new_stage = updates.get("stage")
    if new_stage is not None and new_stage != opp.stage:
        opp.stage = new_stage
        if new_stage in CLOSED_STAGES:
            opp.closed_at = _now()
        else:
            opp.closed_at = None
            opp.loss_reason = None
        # Probability follows the stage default unless explicitly set.
        if "probability" not in updates:
            opp.probability = STAGE_DEFAULT_PROBABILITY.get(new_stage)
    updates.pop("stage", None)

    if "email" in updates and updates["email"]:
        updates["email"] = updates["email"].strip().lower()

    for key, value in updates.items():
        setattr(opp, key, value)
    await db.commit()
    await db.refresh(opp)
    counts = await _activity_counts(db, [opp.id])
    return _opportunity_response(opp, *counts.get(opp.id, (0, 0)))


@router.delete("/opportunities/{opp_id}", status_code=204, response_model=None)
async def delete_opportunity(
    opp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    opp = await _get_opp_or_404(db, opp_id)
    # Un-convert the source lead so it can be converted again later.
    if opp.source_lead_id is not None:
        lead = await db.get(Lead, opp.source_lead_id)
        if lead is not None and lead.converted_opportunity_id == opp.id:
            lead.converted_opportunity_id = None
            if lead.crm_status == CrmLeadStatus.CONVERTED.value:
                lead.crm_status = CrmLeadStatus.QUALIFIED.value
    await db.delete(opp)
    await db.commit()


# ============================================================================
# Activities
# ============================================================================

@router.post("/activities", response_model=ActivityResponse, status_code=201)
async def create_activity(
    payload: ActivityCreate,
    db: AsyncSession = Depends(get_db),
) -> ActivityResponse:
    if payload.lead_id is None and payload.opportunity_id is None:
        raise HTTPException(
            status_code=422,
            detail="Activity needs a lead_id and/or an opportunity_id",
        )
    if payload.lead_id is not None and await db.get(Lead, payload.lead_id) is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    if payload.opportunity_id is not None and await db.get(Opportunity, payload.opportunity_id) is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    activity = CrmActivity(
        lead_id=payload.lead_id,
        opportunity_id=payload.opportunity_id,
        activity_type=payload.activity_type,
        subject=payload.subject,
        body=payload.body,
        direction=payload.direction,
        due_at=payload.due_at,
        occurred_at=payload.occurred_at or _now(),
    )
    db.add(activity)
    await db.commit()
    await db.refresh(activity)
    return ActivityResponse.model_validate(activity)


@router.get("/activities", response_model=PaginatedActivities)
async def list_activities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    activity_type: CrmActivityType | None = Query(default=None),
    open_tasks: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> PaginatedActivities:
    """List activities; filter by parent / type.  ``open_tasks=true``
    returns incomplete tasks across ALL parents ordered by due date —
    the 'what's on my plate' view."""
    filters = []
    if lead_id is not None:
        filters.append(CrmActivity.lead_id == lead_id)
    if opportunity_id is not None:
        filters.append(CrmActivity.opportunity_id == opportunity_id)
    if activity_type is not None:
        filters.append(CrmActivity.activity_type == activity_type)
    if open_tasks:
        filters.append(CrmActivity.activity_type == CrmActivityType.TASK)
        filters.append(CrmActivity.completed_at.is_(None))

    total = (await db.execute(
        select(func.count()).select_from(CrmActivity).where(*filters)
    )).scalar_one()

    order = (
        CrmActivity.due_at.asc().nulls_last()
        if open_tasks else CrmActivity.occurred_at.desc()
    )
    rows = (await db.execute(
        select(CrmActivity)
        .where(*filters)
        .order_by(order)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )).scalars().all()

    return PaginatedActivities(
        items=[ActivityResponse.model_validate(a) for a in rows],
        total=total, page=page, page_size=page_size,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


@router.patch("/activities/{activity_id}", response_model=ActivityResponse)
async def update_activity(
    activity_id: uuid.UUID,
    payload: ActivityUpdate,
    db: AsyncSession = Depends(get_db),
) -> ActivityResponse:
    activity = await db.get(CrmActivity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    updates = payload.model_dump(exclude_unset=True)
    completed = updates.pop("completed", None)
    if completed is True and activity.completed_at is None:
        activity.completed_at = _now()
    elif completed is False:
        activity.completed_at = None

    for key, value in updates.items():
        setattr(activity, key, value)
    await db.commit()
    await db.refresh(activity)
    return ActivityResponse.model_validate(activity)


@router.delete("/activities/{activity_id}", status_code=204, response_model=None)
async def delete_activity(
    activity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    activity = await db.get(CrmActivity, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    await db.delete(activity)
    await db.commit()
