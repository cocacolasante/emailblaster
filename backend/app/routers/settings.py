"""Read-only settings endpoints (no secrets exposed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services import brevo_blocklist

router = APIRouter(prefix="/settings", tags=["settings"])


class ApiStatusResponse(BaseModel):
    anthropic: bool
    brevo: bool
    apollo: bool
    hunter: bool


@router.get("/api-status", response_model=ApiStatusResponse)
async def get_api_status() -> ApiStatusResponse:
    """Returns a bool per integration: True if the key is configured (non-empty)."""
    return ApiStatusResponse(
        anthropic=bool(settings.ANTHROPIC_API_KEY),
        brevo=bool(settings.BREVO_API_KEY),
        apollo=bool(settings.APOLLO_API_KEY),
        hunter=bool(settings.HUNTER_API_KEY),
    )


class BlocklistSyncResponse(BaseModel):
    fetched: int
    newly_suppressed: int
    already_suppressed: int
    leads_halted: int
    leads_removed: int


@router.post("/brevo/sync-blocklist", response_model=BlocklistSyncResponse)
async def sync_brevo_blocklist(db: AsyncSession = Depends(get_db)) -> BlocklistSyncResponse:
    """Pull Brevo's blocked-contacts list (hard bounces / unsubscribes / spam /
    admin-blocked) and suppress each: add to the ignore list, halt them in
    current campaigns, and block them from future ones.  Idempotent — safe to
    re-run.  Runs the same logic as the scheduled backstop, on demand."""
    if not settings.BREVO_API_KEY:
        raise HTTPException(status_code=400, detail="BREVO_API_KEY is not configured")
    result = await brevo_blocklist.sync_blocklist(db)
    return BlocklistSyncResponse(
        fetched=result.fetched,
        newly_suppressed=result.newly_suppressed,
        already_suppressed=result.already_suppressed,
        leads_halted=result.leads_halted,
        leads_removed=result.leads_removed,
    )
