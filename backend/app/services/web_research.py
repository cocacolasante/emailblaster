"""Anthropic-backed web research for an individual lead.

Returns a stable shape regardless of failures or missing API key so callers
never need to special-case None.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT: dict[str, Any] = {
    "person_news": [],
    "company_news": [],
    "company_description": "",
    "found": False,
}

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts)


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Some models prepend prose — try to locate the outermost JSON object.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


async def research_person_web(
    first_name: str,
    last_name: str,
    company: str,
    job_title: str,
) -> dict[str, Any]:
    if not settings.ANTHROPIC_API_KEY:
        return dict(_DEFAULT)

    prompt = (
        "You are researching a person for a personalized cold email.\n"
        f"Subject: {first_name} {last_name}, {job_title} at {company}\n\n"
        "Use web search to find:\n"
        "1. Recent news, achievements, public quotes, or interviews from this person.\n"
        "2. Recent company news, product launches, or announcements.\n"
        "3. A short description of the company.\n\n"
        "Respond ONLY with one JSON object, no preamble, no markdown:\n"
        '{"person_news": ["item 1", "item 2"], "company_news": ["item 1"], '
        '"company_description": "short description", "found": true}\n\n'
        'If nothing useful is found, return found: false and empty lists.'
    )

    try:
        message = await _get_client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("research_person_web API call failed: %s", e)
        return dict(_DEFAULT)

    data = _parse_json(_extract_text(message))
    if not data:
        return dict(_DEFAULT)

    return {
        "person_news": list(data.get("person_news") or []),
        "company_news": list(data.get("company_news") or []),
        "company_description": str(data.get("company_description") or ""),
        "found": bool(data.get("found", False)),
    }
