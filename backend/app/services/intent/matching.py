"""Match external funding events (RFPs, peer awards) to monitored orgs.

The Grants.gov + USASpending collectors are *fan-out* collectors: one external
event (a new RFP, a peer's federal award) becomes one signal per matching
monitored org.  Matching is by:

- **cause** — an ICP cause code is a PREFIX of the org's NTEE code (cause "T"
  matches "T31"); empty cause list matches any.
- **geo** — the org's state is in the ICP geographies; empty matches any.

Selecting candidate monitored orgs ONCE per run (not per event) keeps the
fan-out O(events · matched_orgs) without a DB round-trip per event.
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Org

_NORM_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str | None) -> str:
    """Lowercased, punctuation-stripped org name for self-match detection."""
    return _NORM_RE.sub(" ", (name or "").lower()).strip()


def org_matches_cause(ntee_code: str | None, cause_prefixes: list[str]) -> bool:
    if not cause_prefixes:
        return True
    code = (ntee_code or "").upper()
    return any(code.startswith(p.upper()) for p in cause_prefixes if p)


async def candidate_orgs(
    session: AsyncSession, *,
    cause_prefixes: list[str] | None = None,
    geographies: list[str] | None = None,
    limit: int = 5000,
) -> list[Org]:
    """Monitored orgs matching the ICP cause (NTEE prefix) + geo (state)."""
    q = select(Org)
    if geographies:
        q = q.where(Org.state.in_([s.upper() for s in geographies if s]))
    if cause_prefixes:
        q = q.where(or_(*[Org.ntee_code.ilike(f"{p}%") for p in cause_prefixes if p]))
    q = q.limit(limit)
    return list((await session.execute(q)).scalars().all())


def group_by_state(orgs: list[Org]) -> dict[str, list[Org]]:
    by_state: dict[str, list[Org]] = defaultdict(list)
    for o in orgs:
        if o.state:
            by_state[o.state.upper()].append(o)
    return dict(by_state)
