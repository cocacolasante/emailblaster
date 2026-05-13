"""Per-lead research worker.

The async core function does all the IO and DB writes. The Celery wrapper
calls it via asyncio.run, retries transient failures with exponential
backoff, and on the final failure marks the lead's research_status as
FAILED so the UI can surface it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import Campaign, Lead, ResearchMode, ResearchStatus
from app.services import apollo, hunter, site_scraper, web_research
from app.workers.celery_app import celery_app
from app.workers.compose import compose_lead

logger = logging.getLogger(__name__)


def _assess_quality(
    web_data: dict[str, Any],
    site_data: dict[str, Any],
    apollo_data: dict[str, Any],
) -> str:
    """3-tier quality signal driving the compose worker's prompt selection."""
    has_person = bool(web_data.get("person_news"))
    has_company = bool(site_data.get("about")) or bool(web_data.get("company_description"))
    has_enrichment = bool(apollo_data)

    signals = sum([has_person, has_company, has_enrichment])
    if signals == 0:
        return "low"
    if signals >= 2:
        return "rich"
    return "partial"


def _safe(result: Any, default: Any) -> Any:
    """Convert an asyncio.gather exception into a safe default."""
    return default if isinstance(result, BaseException) else result


async def research_lead_async(lead_id: str) -> dict[str, Any]:
    lid = uuid.UUID(str(lead_id))
    engine = create_async_engine(settings.DATABASE_URL)
    enqueued = False

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            if lead is None:
                logger.warning("research_lead: lead %s not found", lead_id)
                return {"status": "not_found"}

            campaign = await session.get(Campaign, lead.campaign_id)
            if campaign is None:
                logger.warning("research_lead: campaign for lead %s missing", lead_id)
                return {"status": "not_found"}

            lead.research_status = ResearchStatus.RUNNING
            await session.commit()

            # Snapshot the inputs while session is open — we'll use them
            # outside the transaction window for the parallel research calls.
            first_name = lead.first_name or ""
            last_name = lead.last_name or ""
            company = lead.company or ""
            company_website = lead.company_website or ""
            job_title = lead.job_title or ""
            email = lead.email
            mode = campaign.research_mode

        tasks: list[asyncio.Future[Any]] = [
            asyncio.ensure_future(
                web_research.research_person_web(first_name, last_name, company, job_title)
            ),
            asyncio.ensure_future(
                site_scraper.scrape_company_site(company, company_website or None)
            ),
            asyncio.ensure_future(hunter.verify_email_hunter(email)),
        ]
        run_apollo = mode == ResearchMode.DEEP and bool(settings.APOLLO_API_KEY)
        if run_apollo:
            tasks.append(
                asyncio.ensure_future(
                    apollo.enrich_lead_apollo(email, first_name, last_name, company)
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        web_data = _safe(results[0], {})
        site_data = _safe(results[1], {})
        hunter_data = _safe(results[2], {"deliverable": True, "score": 0})
        apollo_data: dict[str, Any] = _safe(results[3], {}) if run_apollo else {}

        quality = _assess_quality(web_data, site_data, apollo_data)
        research_data = {
            "quality": quality,
            "person_news": web_data.get("person_news", []),
            "company_news": web_data.get("company_news", []),
            "company_description": (
                site_data.get("about") or web_data.get("company_description") or ""
            ),
            "recent_updates": site_data.get("recent_updates", []),
            "industry": site_data.get("industry") or apollo_data.get("company_industry") or "",
            "linkedin_headline": apollo_data.get("linkedin_headline") or "",
            "job_title": apollo_data.get("job_title") or job_title,
            "seniority": apollo_data.get("seniority") or "",
            "email_deliverable": bool(hunter_data.get("deliverable", True)),
        }

        async with AsyncSession(engine, expire_on_commit=False) as session:
            lead = await session.get(Lead, lid)
            if lead is None:
                logger.warning("research_lead: lead %s vanished mid-flight", lead_id)
                return {"status": "not_found"}
            lead.research_data = research_data
            lead.research_status = ResearchStatus.DONE
            await session.commit()
    finally:
        await engine.dispose()

    # Enqueue compose outside the DB scope so a Celery broker hiccup doesn't
    # roll back the research write.
    compose_lead.delay(str(lid))
    enqueued = True
    return {"status": "done", "quality": quality, "compose_enqueued": enqueued}


async def _mark_lead_failed(lead_id: str) -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            lead = await session.get(Lead, uuid.UUID(lead_id))
            if lead is not None:
                lead.research_status = ResearchStatus.FAILED
                await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(bind=True, name="research.research_lead", max_retries=2)
def research_lead(self, lead_id: str) -> dict[str, Any]:  # noqa: D401
    try:
        return asyncio.run(research_lead_async(lead_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("research_lead failed for %s", lead_id)
        try:
            raise self.retry(exc=exc, countdown=60 * (2**self.request.retries))
        except self.MaxRetriesExceededError:
            asyncio.run(_mark_lead_failed(lead_id))
            return {"status": "failed", "error": str(exc)}
