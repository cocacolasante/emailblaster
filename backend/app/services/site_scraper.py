"""Anthropic-backed company website summary.

Uses the same web-search tool to read an organization's About / News pages and
return a structured snapshot.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.services import web_research

logger = logging.getLogger(__name__)

_DEFAULT: dict[str, Any] = {
    "about": "",
    "recent_updates": [],
    "industry": "",
    "size_hint": "",
}


async def scrape_company_site(company_name: str) -> dict[str, Any]:
    if not settings.ANTHROPIC_API_KEY or not company_name:
        return dict(_DEFAULT)

    prompt = (
        f'Research the company "{company_name}" for context to use in an outreach email.\n'
        "Use web search on their official website (About page, Blog, News/Press) to gather:\n"
        "1. A short description of what the company does (1-2 sentences).\n"
        "2. Recent product launches, announcements, or news (1-3 items).\n"
        "3. Their industry / sector.\n"
        '4. A size hint — one of: "startup", "growth", "mid-market", "enterprise", or "" if unsure.\n\n'
        "Respond ONLY with one JSON object, no preamble, no markdown:\n"
        '{"about": "short description", "recent_updates": ["update 1"], '
        '"industry": "SaaS", "size_hint": "startup"}'
    )

    try:
        message = await web_research._get_client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("scrape_company_site API call failed: %s", e)
        return dict(_DEFAULT)

    data = web_research._parse_json(web_research._extract_text(message))
    if not data:
        return dict(_DEFAULT)

    return {
        "about": str(data.get("about") or ""),
        "recent_updates": list(data.get("recent_updates") or []),
        "industry": str(data.get("industry") or ""),
        "size_hint": str(data.get("size_hint") or ""),
    }
