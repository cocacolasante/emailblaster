"""Endpoint for the one-off 'research a client' tool.

Synchronous flow (the user is waiting in the browser):

1. Parse the LinkedIn URL → derive a slug + name guess.
2. Single Anthropic call with web_search to extract identity +
   personalization signals (mode controls depth).
3. Single Anthropic call to compose the outreach message with a strict
   per-message character limit.

No DB writes — research and compose are returned to the user to copy
into LinkedIn / email manually.  Adding a saved-history table would be
a separate feature; out of scope here.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from anthropic import APIError, APIStatusError, AuthenticationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    ConnectedAccount,
    CrmActivity,
    CrmActivityDirection,
    CrmActivityType,
    Lead,
    Opportunity,
)
from sqlalchemy import func as sa_func
from app.schemas.research_client import (
    ResearchClientRequest,
    ResearchClientResponse,
    ResearchedProfile,
    SendClientEmailRequest,
    SendClientEmailResponse,
)
from app.services import brevo, compose_client, outreach, research_cache, research_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research-client", tags=["research-client"])


async def research_and_compose(
    db: AsyncSession,
    *,
    linkedin_url: str,
    goal: str,
    tone: str = "professional",
    sender_name: str = "",
    research_mode: str = "fast",
    output_kind: str = "linkedin_dm",
    char_limit: int = 600,
    research: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Research a LinkedIn profile (cached unless deep) and compose the
    outreach.  Returns ``(research, composed)``.  Shared by the GUI endpoint
    below and the Muse outreach-draft flow; pass ``research`` to recompose
    (redraft) without paying for research again.  Raises HTTPException with
    the same user-facing messages either way."""
    if research is None:
        # Cache key: ``linkedin:<slug>`` reuses the research_cache table
        # (namespaced so it can't collide with the bulk pipeline's email
        # keys).  Deep mode bypasses the cache — the user explicitly asked
        # for fresh, deep research.  TTL is RESEARCH_CACHE_TTL_DAYS.
        slug, _name_guess = research_client.parse_linkedin_url(linkedin_url)
        if not slug:
            raise HTTPException(
                status_code=400,
                detail="URL must be a LinkedIn profile URL like https://www.linkedin.com/in/<slug>/",
            )
        cache_key = f"linkedin:{slug}"
        is_deep = (research_mode or "").lower() == "deep"
        if not is_deep:
            cached = await research_cache.lookup(db, cache_key)
            if cached:
                # Informational — the UI shows why the call returned instantly.
                cached["from_cache"] = True
                research = cached
        if research is None:
            research = await research_client.research_from_linkedin_url(linkedin_url, research_mode)
            if research.get("error") == "url_not_recognised_as_linkedin_profile":
                raise HTTPException(
                    status_code=400,
                    detail="URL must be a LinkedIn profile URL like https://www.linkedin.com/in/<slug>/",
                )
            # Only cache successful research — a ``found=False`` row would
            # poison later calls that might find the prospect.
            if research.get("found"):
                try:
                    await research_cache.upsert(db, cache_key, research)
                    await db.commit()
                except Exception:  # noqa: BLE001 — cache is a perf optimisation
                    await db.rollback()

    try:
        composed = await compose_client.compose_for_client(
            output_kind=output_kind,
            goal=goal, tone=tone, sender_name=sender_name,
            char_limit=char_limit, research=research,
        )
    except ValueError as exc:
        logger.warning("research_client compose failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"compose_failed: {exc}") from exc
    except AuthenticationError as exc:
        logger.error("Anthropic auth failed in research_client: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=(
                "Anthropic rejected this workspace's API key.  Update it in "
                "Settings → Integrations → Anthropic."
            ),
        ) from exc
    except (APIError, APIStatusError) as exc:
        # Network / rate-limit / 5xx from Anthropic — a clean 502 with the
        # upstream status so it doesn't masquerade as a server bug.
        logger.warning("Anthropic API error in research_client: %s", exc)
        raise HTTPException(status_code=502, detail=f"anthropic_api_error: {exc}") from exc
    return research, composed


def profile_from_research(research: dict[str, Any]) -> ResearchedProfile:
    return ResearchedProfile(
        first_name=research.get("first_name", ""),
        last_name=research.get("last_name", ""),
        headline=research.get("headline", ""),
        company=research.get("company", ""),
        company_website=research.get("company_website", ""),
        job_title=research.get("job_title", ""),
        industry=research.get("industry", ""),
        found=bool(research.get("found", False)),
        quality=research.get("quality", "low"),
    )


@router.post("", response_model=ResearchClientResponse)
async def research_client_endpoint(
    req: ResearchClientRequest,
    db: AsyncSession = Depends(get_db),
) -> ResearchClientResponse:
    started = time.perf_counter()
    research, composed = await research_and_compose(
        db,
        linkedin_url=req.linkedin_url, goal=req.goal, tone=req.tone,
        sender_name=req.sender_name, research_mode=req.research_mode,
        output_kind=req.output_kind, char_limit=req.char_limit,
    )
    return ResearchClientResponse(
        profile=profile_from_research(research),
        research=research,
        subject=composed["subject"],
        body=composed["body"],
        char_count=len(composed["body"]),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


@router.post("/send", response_model=SendClientEmailResponse)
async def send_client_email(
    req: SendClientEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> SendClientEmailResponse:
    """One-off transactional send out of the 'Research a client' tool.

    Goes via Brevo's transactional API — no campaign / lead row backs
    this send.  Synthetic header IDs (``X-Campaign-ID: research-client``
    plus a UUID per send) let Brevo's event log distinguish these from
    campaign sends without inventing a real Campaign.

    The sender's from-address falls back to ``settings.BREVO_SENDER_EMAIL``
    when the request doesn't override it.  ``settings.BREVO_API_KEY``
    must be configured — same prereq as the bulk pipeline.  All
    Anthropic failures are remapped to 502 with the upstream message
    so the frontend toast surfaces something actionable instead of a
    generic 500.
    """
    # Send + CRM-track via the shared core (also used by signal outreach).
    try:
        result = await outreach.send_and_track(
            db,
            to_email=str(req.to_email),
            to_name=req.to_name,
            subject=req.subject,
            body=req.body,
            sender_name=req.sender_name,
            sender_email=req.sender_email,
            campaign_tag="research-client",
        )
    except outreach.OutreachSendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return SendClientEmailResponse(
        message_id=result.message_id,
        sent_at=datetime.now(timezone.utc),
        to_email=req.to_email,
        crm_lead_id=result.crm_lead_id,
        crm_lead_created=result.crm_lead_created,
        crm_activity_logged=result.crm_activity_logged,
    )
