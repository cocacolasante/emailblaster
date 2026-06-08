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

import httpx
from anthropic import APIError, APIStatusError, AuthenticationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ConnectedAccount
from app.schemas.research_client import (
    ResearchClientRequest,
    ResearchClientResponse,
    ResearchedProfile,
    SendClientEmailRequest,
    SendClientEmailResponse,
)
from app.services import brevo, compose_client, research_client
from app.services.email_template import render_html, render_text
from app.services.signature import apply_signature

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
    if not settings.BREVO_API_KEY:
        raise HTTPException(
            status_code=502,
            detail=(
                "BREVO_API_KEY is not configured.  Add it to .env and "
                "recreate the backend container."
            ),
        )

    # From-address priority: explicit request override > DB default
    # sender (a ConnectedAccount the user marked as the workspace
    # default in Settings) > settings.BREVO_SENDER_EMAIL.  The DB lookup
    # returns the full row so we can both pick the email_address AND
    # apply that account's signature below.
    sender_email: str | None = req.sender_email
    sender_account: ConnectedAccount | None = None
    if sender_email:
        # Explicit override — see if it matches a ConnectedAccount so we
        # can still apply that account's signature.  Unmatched override
        # = no signature (the user picked an address we don't know
        # about).
        sender_account = await db.scalar(
            select(ConnectedAccount)
            .where(ConnectedAccount.email_address == sender_email)
            .limit(1)
        )
    else:
        sender_account = await db.scalar(
            select(ConnectedAccount)
            .where(ConnectedAccount.is_default_sender.is_(True))
            .limit(1)
        )
        sender_email = (
            sender_account.email_address if sender_account is not None
            else settings.BREVO_SENDER_EMAIL
        )

    # Apply the chosen inbox's signature.  ``apply_signature`` is
    # idempotent (a body already ending with the signature is returned
    # unchanged) AND it swaps the AI's sign-off block when present, so
    # we don't get duplicated "Best, Anthony" lines.  Empty / null
    # signature → body unchanged.
    final_body = apply_signature(
        req.body,
        sender_account.signature if sender_account else None,
    )

    # Synthetic identifiers so Brevo's event log can correlate replies +
    # opens to this one-off send if we ever wire that up.
    synthetic_lead_id = str(uuid.uuid4())

    try:
        message_id = await brevo.send_email(
            to_email=str(req.to_email),
            to_name=req.to_name,
            subject=req.subject,
            html_body=render_html(final_body),
            text_body=render_text(final_body),
            sender_name=req.sender_name,
            sender_email=str(sender_email),
            campaign_id="research-client",
            lead_id=synthetic_lead_id,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "research-client send: Brevo %s on send to %s — %s",
            exc.response.status_code, req.to_email, exc.response.text[:200],
        )
        # Surface Brevo's status + a short reason so the UI toast tells
        # the user what's wrong (auth, validation, etc.) instead of a
        # generic failure.
        raise HTTPException(
            status_code=502,
            detail=(
                f"Brevo rejected the send (HTTP {exc.response.status_code}). "
                "Check BREVO_API_KEY validity and the sender email is "
                "verified on your Brevo account."
            ),
        ) from exc
    except RuntimeError as exc:
        # send_email raises RuntimeError on missing API key (already
        # caught above) and on a successful 2xx with no messageId — the
        # latter is treated as a soft Brevo bug.
        logger.warning("research-client send: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning("research-client send: network error to Brevo: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Network error talking to Brevo: {exc}",
        ) from exc

    return SendClientEmailResponse(
        message_id=str(message_id),
        sent_at=datetime.now(timezone.utc),
        to_email=req.to_email,
    )
