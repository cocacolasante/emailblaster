"""On-demand contact enrichment for a notification-only signal.

The funding feeds already try ``enrichment.resolve_contact`` once at
discovery time; orgs with no resolvable contact land in the queue as
notification-only (no ``lead_id``).  This is the user-triggered "Find
contact" retry: rebuild the org from the signal and run the SAME
low-cost resolver (Hunter-first; at most ONE Haiku web-lookup, and only
when no domain is already known).  On a hit we stage a campaign-less
Lead and link it to the signal, so the existing Draft / Send /
Add-to-campaign flows light up.

Stays inside the autonomy boundary: this never sets ``campaign_id`` or
enrolls a sequence — it only resolves a contact and stages a CRM lead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, ProspectSignal, canonical_email
from app.services.funding_sources import enrichment
from app.services.funding_sources.base import DiscoveredOrg

logger = logging.getLogger(__name__)

# IRS summary shape: "New 501(c)(3): {name} ({STATE}) — IRS ruling …"
_IRS_SUMMARY_RE = re.compile(r"^New 501\(c\)\(3\):\s*(?P<name>.+?)\s*\(", re.IGNORECASE)
# USAspending summary shape: "{name} won a federal grant …"
_GRANT_SUMMARY_RE = re.compile(r"^(?P<name>.+?)\s+won a federal grant", re.IGNORECASE)


@dataclass
class EnrichResult:
    found: bool = False
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    generic: bool = False
    lead_id: Any = None
    lead_created: bool = False
    already_had_contact: bool = False


def _org_name_from_signal(signal: ProspectSignal) -> str | None:
    """Best org name: stored ``detail.org_name`` else parsed summary."""
    detail = signal.detail or {}
    name = (detail.get("org_name") or detail.get("company") or "").strip()
    if name:
        return name
    summary = signal.summary or ""
    for rx in (_IRS_SUMMARY_RE, _GRANT_SUMMARY_RE):
        m = rx.match(summary)
        if m:
            return m.group("name").strip()
    return None


def _org_from_signal(signal: ProspectSignal) -> DiscoveredOrg | None:
    detail = signal.detail or {}
    org_name = _org_name_from_signal(signal)
    if not org_name:
        return None
    return DiscoveredOrg(
        signal_type=signal.signal_type,
        summary=signal.summary,
        dedup_key=signal.dedup_key,
        org_name=org_name,
        state=detail.get("state") or detail.get("recipient_state"),
        ein=detail.get("ein"),
        ntee_code=detail.get("ntee_code") or detail.get("ntee"),
        website=detail.get("website"),
        detail=detail,
    )


async def enrich_signal_contact(
    db: AsyncSession, signal: ProspectSignal,
) -> EnrichResult:
    """Resolve a contact for ``signal`` and stage+link a campaign-less Lead.

    Idempotent on a signal that already has a contactable lead — returns
    that contact untouched (no API spend).  Commits the session on a hit.
    """
    # Already contactable → no spend, report the existing contact.
    if signal.lead_id is not None:
        lead = await db.get(Lead, signal.lead_id)
        if lead is not None and lead.email:
            return EnrichResult(
                found=True, already_had_contact=True,
                email=lead.email, first_name=lead.first_name,
                last_name=lead.last_name, title=lead.job_title,
                lead_id=lead.id,
            )

    org = _org_from_signal(signal)
    if org is None:
        return EnrichResult(found=False)

    contact = await enrichment.resolve_contact(org)
    if not contact or not contact.get("email"):
        return EnrichResult(found=False)

    email = canonical_email(contact["email"])
    # Find-or-create a campaign-less lead by email (mirrors the worker's
    # discovery-staging branch).
    lead = await db.scalar(select(Lead).where(Lead.email == email).limit(1))
    lead_created = False
    if lead is None:
        lead = Lead(
            campaign_id=None,                      # NEVER a campaign
            email=email,
            first_name=contact.get("first_name"),
            last_name=contact.get("last_name"),
            company=org.org_name,
            company_website=org.website,
            job_title=contact.get("title"),
            research_data={
                "ein": org.ein,
                "ntee": org.ntee_code,
                "source": signal.source,
                **(org.detail or {}),
            },
        )
        db.add(lead)
        await db.flush()
        lead_created = True

    signal.lead_id = lead.id
    await db.commit()

    return EnrichResult(
        found=True,
        email=email,
        first_name=contact.get("first_name"),
        last_name=contact.get("last_name"),
        title=contact.get("title"),
        generic=bool(contact.get("generic")),
        lead_id=lead.id,
        lead_created=lead_created,
    )
