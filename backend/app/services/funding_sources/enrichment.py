"""Resolve a decision-maker contact for a discovered nonprofit.

  1. Ensure a domain — use the org's website if known, else ONE capped
     Haiku web-lookup (reusing signal_detection._web_lookup).
  2. Find a decision-maker email via Hunter, role priority
     Executive Director -> Development Director -> Grants Manager.
  3. Verify deliverability; a generic ``info@`` is kept but flagged
     low-priority.

Returns ``{email, first_name, last_name, title, generic}`` or None when
no contact can be resolved (the org then becomes notification-only).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services import hunter
from app.services.funding_sources.base import DiscoveredOrg
# Reuse the existing capped web-lookup pattern (Haiku, cost-wrapped).
from app.services.signal_detection import _web_lookup

logger = logging.getLogger(__name__)

# Decision-maker roles in outreach priority order.
_ROLE_PRIORITY = ["Executive Director", "Development Director", "Grants Manager"]
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    host = _SCHEME_RE.sub("", website.strip()).split("/", 1)[0].strip().lower()
    host = host[4:] if host.startswith("www.") else host
    return host or None


async def _find_domain(org: DiscoveredOrg) -> str | None:
    """One capped Haiku web-lookup for the org's official site."""
    web = await _web_lookup(
        f'"{org.org_name}" {org.state or ""} official website nonprofit'.strip(),
        'What is the official website domain of this nonprofit organization? '
        'Respond ONLY with JSON: {"domain": "example.org or empty string"}.',
    )
    return _domain_from_website((web or {}).get("domain"))


async def resolve_contact(org: DiscoveredOrg) -> dict[str, Any] | None:
    domain = _domain_from_website(org.website) or await _find_domain(org)
    if not domain:
        return None

    # Role-priority decision-maker search; first hit wins.
    contact: dict[str, Any] | None = None
    for role in _ROLE_PRIORITY:
        found = await hunter.find_email_hunter(domain, role=role)
        if found and found.get("email"):
            contact = found
            break
    # Fallback: any top email at the domain (may be a generic info@).
    if contact is None:
        contact = await hunter.find_email_hunter(domain)
    if not contact or not contact.get("email"):
        return None

    verdict = await hunter.verify_email_hunter(contact["email"])
    if not verdict.get("deliverable"):
        return None

    email = contact["email"].strip().lower()
    return {
        "email": email,
        "first_name": contact.get("first_name"),
        "last_name": contact.get("last_name"),
        "title": contact.get("title"),
        # info@/generic mailboxes are deliverable but low-priority.
        "generic": bool(contact.get("generic")) or email.split("@", 1)[0] in {
            "info", "contact", "hello", "admin", "office",
        },
    }
