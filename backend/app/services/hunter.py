"""Hunter.io email verification.

When the key isn't configured we return a permissive default so the send
worker doesn't drop leads — verification is opt-in.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

HUNTER_URL = "https://api.hunter.io/v2/email-verifier"
_DELIVERABLE_STATUSES = {"valid", "accept_all", "webmail"}


async def verify_email_hunter(email: str) -> dict[str, Any]:
    if not settings.HUNTER_API_KEY:
        return {"deliverable": True, "score": 100}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                HUNTER_URL,
                params={"email": email, "api_key": settings.HUNTER_API_KEY},
            )
            resp.raise_for_status()
            payload = (resp.json() or {}).get("data") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("Hunter verify failed for %s: %s", email, e)
        # Permissive fallback — never block sending on a verifier outage.
        return {"deliverable": True, "score": 0}

    return {
        "deliverable": (payload.get("status") or "").lower() in _DELIVERABLE_STATUSES,
        "score": int(payload.get("score") or 0),
    }
