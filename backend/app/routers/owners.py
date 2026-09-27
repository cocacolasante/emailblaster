"""Bulk owner assignment across record types.

    POST /owners/assign {"record_type": "lead", "ids": [...], "owner_id": "<user>"|null}

Any workspace member may assign (ownership is accountability, not access
control).  Ids from other workspaces simply don't match; the new owner
must be a member of this workspace (422 otherwise) and is notified once
per batch.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Account, Campaign, Contact, CrmActivity, Lead, Opportunity
from app.services import ownership

router = APIRouter(prefix="/owners", tags=["owners"])

RecordType = Literal["lead", "opportunity", "activity", "campaign", "account", "contact"]
_MODELS: dict[str, type] = {
    "lead": Lead,
    "opportunity": Opportunity,
    "activity": CrmActivity,
    "campaign": Campaign,
    "account": Account,
    "contact": Contact,
}


class AssignRequest(BaseModel):
    record_type: RecordType
    ids: list[uuid.UUID] = Field(min_length=1, max_length=ownership.MAX_BULK_ASSIGN)
    owner_id: uuid.UUID | None


class AssignResponse(BaseModel):
    updated: int


@router.post("/assign", response_model=AssignResponse)
async def assign_owner(body: AssignRequest, db: AsyncSession = Depends(get_db)) -> AssignResponse:
    updated = await ownership.assign(db, _MODELS[body.record_type], body.ids, body.owner_id)
    await db.commit()
    return AssignResponse(updated=updated)
