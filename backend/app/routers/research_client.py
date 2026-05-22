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

from anthropic import APIError, APIStatusError, AuthenticationError
from fastapi import APIRouter, HTTPException

from app.schemas.research_client import (
    ResearchClientRequest,
    ResearchClientResponse,
    ResearchedProfile,
)
from app.services import compose_client, research_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research-client", tags=["research-client"])


@router.post("", response_model=ResearchClientResponse)
async def research_client_endpoint(req: ResearchClientRequest) -> ResearchClientResponse:
    started = time.perf_counter()

    research = await research_client.research_from_linkedin_url(
        req.linkedin_url, req.research_mode,
    )
    if research.get("error") == "url_not_recognised_as_linkedin_profile":
        raise HTTPException(
            status_code=400,
            detail="URL must be a LinkedIn profile URL like https://www.linkedin.com/in/<slug>/",
        )

    try:
        composed = await compose_client.compose_for_client(
            output_kind=req.output_kind,
            goal=req.goal, tone=req.tone, sender_name=req.sender_name,
            char_limit=req.char_limit, research=research,
        )
    except ValueError as exc:
        logger.warning("research_client compose failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"compose_failed: {exc}") from exc
    except AuthenticationError as exc:
        # Bad/missing ANTHROPIC_API_KEY in .env.  Surface a clear 502
        # so the frontend toast tells the user what's wrong instead of
        # showing a generic 500.  Same shape applies to research stage,
        # but that one already swallows the error and returns empty
        # research; the failure mode here is unique because compose
        # MUST produce a body to be useful.
        logger.error("Anthropic auth failed in research_client: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=(
                "Anthropic API key is invalid or expired.  Rotate the key "
                "at console.anthropic.com, update ANTHROPIC_API_KEY in .env, "
                "and run: docker compose up -d --force-recreate backend "
                "worker beat"
            ),
        ) from exc
    except (APIError, APIStatusError) as exc:
        # Network / rate-limit / 5xx from Anthropic.  Same idea: clean
        # 502 with the upstream status so we don't masquerade as a
        # server bug.
        logger.warning("Anthropic API error in research_client: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"anthropic_api_error: {exc}",
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    return ResearchClientResponse(
        profile=ResearchedProfile(
            first_name=research.get("first_name", ""),
            last_name=research.get("last_name", ""),
            headline=research.get("headline", ""),
            company=research.get("company", ""),
            company_website=research.get("company_website", ""),
            job_title=research.get("job_title", ""),
            industry=research.get("industry", ""),
            found=bool(research.get("found", False)),
            quality=research.get("quality", "low"),
        ),
        research=research,
        subject=composed["subject"],
        body=composed["body"],
        char_count=len(composed["body"]),
        duration_ms=duration_ms,
    )
