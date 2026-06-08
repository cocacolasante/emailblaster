"""Shared Anthropic helpers used by Social Listening services.

Kept tiny on purpose — it just bundles the lazy-init client, the text
extractor, and two JSON parsers (object + array) so the three social-
listening services don't each carry their own copy.

``services/web_research.py`` and ``services/research_client.py`` still
have their own inline copies; this module is opt-in for new services.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    """Lazy-singleton AsyncAnthropic client."""
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def extract_text(message: Any) -> str:
    """Pull the concatenated text content from an Anthropic message."""
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts)


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Find and parse the outermost ``{...}`` in ``text``.  Returns None if
    nothing parses."""
    if not text:
        return None
    cleaned = _strip_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def parse_json_array(text: str) -> list[Any] | None:
    """Find and parse the outermost ``[...]`` in ``text``.  Returns None if
    nothing parses."""
    if not text:
        return None
    cleaned = _strip_fences(text)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None
