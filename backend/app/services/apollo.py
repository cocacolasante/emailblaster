"""Apollo.io person enrichment.

Returns {} when the key is missing, no match is found, or the API hard-fails
after retries. 429 responses get exponential backoff (1s, 2s, 4s).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

APOLLO_URL = "https://api.apollo.io/v1/people/match"
_MAX_ATTEMPTS = 3
_TIMEOUT_SECONDS = 30.0


async def enrich_lead_apollo(
    email: str,
    first_name: str,
    last_name: str,
    company: str,
) -> dict[str, Any]:
    if not settings.APOLLO_API_KEY:
        return {}

    payload = {
        "api_key": settings.APOLLO_API_KEY,
        "email": email,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "organization_name": company or None,
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.post(APOLLO_URL, json=payload)
            except httpx.HTTPError as e:
                logger.warning("Apollo request error (attempt %s): %s", attempt + 1, e)
                if attempt == _MAX_ATTEMPTS - 1:
                    return {}
                await asyncio.sleep(2**attempt)
                continue

            if resp.status_code == 429:
                logger.info("Apollo rate-limited, retrying after %ss", 2**attempt)
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code >= 500:
                logger.warning("Apollo %s on attempt %s", resp.status_code, attempt + 1)
                if attempt == _MAX_ATTEMPTS - 1:
                    return {}
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                # 401/403/404 — no point retrying.
                logger.info("Apollo returned %s for %s", resp.status_code, email)
                return {}

            try:
                data = resp.json() or {}
            except ValueError:
                return {}

            person = data.get("person") or {}
            if not person:
                return {}
            org = person.get("organization") or {}
            return {
                "linkedin_url": person.get("linkedin_url"),
                "linkedin_headline": person.get("headline"),
                "job_title": person.get("title"),
                "seniority": person.get("seniority"),
                "department": person.get("department"),
                "company_employee_count": org.get("estimated_num_employees"),
                "company_industry": org.get("industry"),
                "company_funding_stage": org.get("latest_funding_stage"),
            }
    return {}
