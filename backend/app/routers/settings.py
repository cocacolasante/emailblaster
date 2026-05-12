"""Read-only settings endpoints (no secrets exposed)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

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
