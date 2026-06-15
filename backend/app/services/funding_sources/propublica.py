"""ProPublica Nonprofit Explorer API (free, no auth) — by EIN.

  GET https://projects.propublica.org/nonprofits/api/v2/organizations/<ein>.json

Returns the org profile + a list of filings.  We pull a best-effort
officer name (from the most recent filing that exposes one) to seed a
Hunter email-finder lookup.

IMPORTANT (see the feature design): this only covers orgs that have
filed a 990 / 990-EZ / 990-PF — it EXCLUDES the smallest 990-N filers,
so it mostly helps the established USASpending cohort, NOT brand-new
501(c)(3)s.  Treat it as an optional assist, never the primary path.
Never raises — an outage / unknown EIN returns None.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API = "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
_TIMEOUT = 15.0
_EIN_RE = re.compile(r"\D")


def _normalize_ein(ein: str | None) -> str | None:
    """ProPublica wants the 9-digit EIN with no dash."""
    digits = _EIN_RE.sub("", ein or "")
    return digits or None


def _officer_name(filings: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Pull a (first, last) officer name from the newest filing that has one."""
    for f in sorted(filings, key=lambda x: x.get("tax_prd_yr") or 0, reverse=True):
        # Filing payloads vary across form types; read defensively.
        full = (
            f.get("officer_name")
            or f.get("formation_officer")
            or f.get("compnsatncurrofcr_name")
            or ""
        ).strip()
        if full:
            parts = full.split()
            if len(parts) >= 2:
                return parts[0], parts[-1]
            return full, None
    return None, None


async def lookup_org(ein: str | None) -> dict[str, Any] | None:
    """Return ``{name, website, first_name, last_name}`` for the EIN, or
    None when nothing usable is found.  Best-effort, never raises."""
    norm = _normalize_ein(ein)
    if not norm:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_API.format(ein=norm))
            resp.raise_for_status()
            data = resp.json() or {}
    except Exception as e:  # noqa: BLE001 — optional assist, never crash the feed
        logger.warning("ProPublica lookup failed for EIN %s: %s", norm, e)
        return None

    org = data.get("organization") or {}
    filings = (
        (data.get("filings_with_data") or [])
        + (data.get("filings_without_data") or [])
    )
    first, last = _officer_name(filings)
    website = (org.get("website") or "").strip() or None
    if not (first or website):
        return None
    return {
        "name": (org.get("name") or "").strip() or None,
        "website": website,
        "first_name": first,
        "last_name": last,
    }
