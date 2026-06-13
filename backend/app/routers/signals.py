"""Prospect-signal endpoints: watches CRUD, signal feed, action/dismiss.

Accepting/actioning a signal stays human — these endpoints toggle
status and tracking only; the worker applies the (bounded) autonomous
actions when a signal is first detected.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Lead,
    Opportunity,
    ProspectSignal,
    ProspectSignalStatus,
    SignalWatch,
    SignalWatchStatus,
    SignalWatchType,
    SocialSearchFrequency,
)

router = APIRouter(prefix="/signals", tags=["signals"])


def _blank_to_none(v: str | None) -> str | None:
    if v is None:
        return None
    return v.strip() or None


class WatchCreate(BaseModel):
    watch_type: SignalWatchType
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    person_name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    linkedin_url: str | None = Field(default=None, max_length=500)
    company_website: str | None = Field(default=None, max_length=500)
    frequency: SocialSearchFrequency = SocialSearchFrequency.DAILY

    _v = field_validator(
        "person_name", "company", "email", "linkedin_url", "company_website",
    )(_blank_to_none)


class WatchUpdate(BaseModel):
    frequency: SocialSearchFrequency | None = None
    status: SignalWatchStatus | None = None
    company: str | None = None
    email: str | None = None

    _v = field_validator("company", "email")(_blank_to_none)


def _watch_dict(w: SignalWatch) -> dict[str, Any]:
    return {
        "id": w.id,
        "watch_type": w.watch_type,
        "lead_id": w.lead_id,
        "opportunity_id": w.opportunity_id,
        "person_name": w.person_name,
        "company": w.company,
        "email": w.email,
        "linkedin_url": w.linkedin_url,
        "company_website": w.company_website,
        "frequency": w.frequency,
        "status": w.status,
        "next_run_at": w.next_run_at,
        "last_run_at": w.last_run_at,
        "last_run_status": w.last_run_status,
        "last_seen": w.last_seen,
        "created_at": w.created_at,
    }


@router.post("/watches", status_code=201)
async def create_watch(
    payload: WatchCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if payload.lead_id is not None:
        if await db.get(Lead, payload.lead_id) is None:
            raise HTTPException(status_code=404, detail="lead not found")
    if payload.opportunity_id is not None:
        if await db.get(Opportunity, payload.opportunity_id) is None:
            raise HTTPException(status_code=404, detail="opportunity not found")
    if (
        payload.lead_id is None
        and payload.opportunity_id is None
        and not (payload.company or payload.person_name)
    ):
        raise HTTPException(
            status_code=422,
            detail="a watch needs a lead, an opportunity, or a company/person target",
        )
    w = SignalWatch(
        watch_type=payload.watch_type,
        lead_id=payload.lead_id,
        opportunity_id=payload.opportunity_id,
        person_name=payload.person_name,
        company=payload.company,
        email=(payload.email or "").lower() or None,
        linkedin_url=payload.linkedin_url,
        company_website=payload.company_website,
        frequency=payload.frequency,
        # First run as soon as the beat ticks (manual stays manual).
        next_run_at=(
            datetime.now(timezone.utc)
            if payload.frequency != SocialSearchFrequency.MANUAL
            else None
        ),
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return _watch_dict(w)


@router.get("/watches")
async def list_watches(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(SignalWatch).order_by(SignalWatch.created_at.desc())
    )).scalars().all()
    return [_watch_dict(w) for w in rows]


@router.patch("/watches/{watch_id}")
async def update_watch(
    watch_id: uuid.UUID, payload: WatchUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    w = await db.get(SignalWatch, watch_id)
    if w is None:
        raise HTTPException(status_code=404, detail="watch not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(w, key, value)
    await db.commit()
    await db.refresh(w)
    return _watch_dict(w)


@router.delete("/watches/{watch_id}", status_code=204, response_model=None)
async def delete_watch(
    watch_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    w = await db.get(SignalWatch, watch_id)
    if w is None:
        raise HTTPException(status_code=404, detail="watch not found")
    await db.delete(w)
    await db.commit()


@router.post("/watches/{watch_id}/run-now")
async def run_watch_now(
    watch_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    w = await db.get(SignalWatch, watch_id)
    if w is None:
        raise HTTPException(status_code=404, detail="watch not found")
    if w.status != SignalWatchStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="watch is paused")
    from app.workers.signals import run_watch

    run_watch.delay(str(w.id))
    return {"enqueued": True}


# ---------------------------------------------------------------------------
# Signal feed
# ---------------------------------------------------------------------------


@router.get("")
async def list_signals(
    status: ProspectSignalStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    filters = []
    if status is not None:
        filters.append(ProspectSignal.status == status)
    total = (await db.execute(
        select(func.count()).select_from(ProspectSignal).where(*filters)
    )).scalar_one()
    rows = (await db.execute(
        select(ProspectSignal)
        .where(*filters)
        .order_by(ProspectSignal.detected_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )).scalars().all()
    items = [
        {
            "id": s.id,
            "watch_id": s.watch_id,
            "signal_type": s.signal_type,
            "summary": s.summary,
            "detail": s.detail,
            "status": s.status,
            "lead_id": s.lead_id,
            "opportunity_id": s.opportunity_id,
            "detected_at": s.detected_at,
        }
        for s in rows
    ]
    return {
        "items": items, "total": total, "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }


async def _set_signal_status(
    db: AsyncSession, signal_id: uuid.UUID, status: ProspectSignalStatus,
) -> dict[str, Any]:
    s = await db.get(ProspectSignal, signal_id)
    if s is None:
        raise HTTPException(status_code=404, detail="signal not found")
    s.status = status
    await db.commit()
    return {"id": str(s.id), "status": s.status.value}


@router.post("/{signal_id}/action")
async def action_signal(
    signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await _set_signal_status(db, signal_id, ProspectSignalStatus.ACTIONED)


@router.post("/{signal_id}/dismiss")
async def dismiss_signal(
    signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await _set_signal_status(db, signal_id, ProspectSignalStatus.DISMISSED)
