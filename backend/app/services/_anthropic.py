"""Shared Anthropic helpers used by Social Listening services.

Kept tiny on purpose — it just bundles the lazy-init client, the text
extractor, and two JSON parsers (object + array) so the three social-
listening services don't each carry their own copy.

This is the ONLY module that constructs ``AsyncAnthropic`` (enforced by
a hardening test) — the key always comes from the workspace creds.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.services import credentials

logger = logging.getLogger(__name__)

# One client per API key.  Keys are per-workspace, so a process-wide
# singleton would send every tenant's calls on whichever key built it
# first.  Keyed by the key string itself, never by tenant id.
_clients: dict[str, AsyncAnthropic] = {}
_client = None  # legacy attribute; some tests still reset it


def client_for(creds: credentials.AnthropicCreds) -> AsyncAnthropic:
    client = _clients.get(creds.api_key)
    if client is None:
        client = AsyncAnthropic(api_key=creds.api_key)
        _clients[creds.api_key] = client
    return client


def get_client() -> AsyncAnthropic:
    """AsyncAnthropic client for the current workspace.

    Raises ``credentials.MissingCredential`` when the workspace has no
    Anthropic key.
    """
    return client_for(credentials.require("anthropic"))  # type: ignore[arg-type]


def is_configured() -> bool:
    return credentials.is_configured("anthropic")


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
