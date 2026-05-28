"""Cross-campaign research cache helpers.

Cache is keyed by lowercased email.  ``lookup`` returns the cached
research_data when it's still inside ``RESEARCH_CACHE_TTL_DAYS``;
``upsert`` writes/refreshes the row.  All callers use lowercased,
stripped email — same canonicalisation as the rest of the lead pipeline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ResearchCache


def _canon(email: str) -> str:
    return (email or "").strip().lower()


async def lookup(session: AsyncSession, email: str) -> dict[str, Any] | None:
    """Return the cached ``research_data`` for ``email`` if it's still fresh
    (within ``RESEARCH_CACHE_TTL_DAYS``), else None."""
    e = _canon(email)
    if not e:
        return None
    row = await session.get(ResearchCache, e)
    if row is None:
        return None
    age = datetime.now(timezone.utc) - row.refreshed_at
    if age > timedelta(days=settings.RESEARCH_CACHE_TTL_DAYS):
        return None
    return dict(row.research_data or {})


async def upsert(session: AsyncSession, email: str, research_data: dict[str, Any]) -> None:
    """Write or refresh the cache row for ``email``.  No-op if email is blank
    or research_data is empty (no point caching a no-signal lookup)."""
    e = _canon(email)
    if not e or not research_data:
        return
    stmt = pg_insert(ResearchCache).values(
        email=e,
        research_data=research_data,
        refreshed_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        index_elements=["email"],
        set_={
            "research_data": research_data,
            "refreshed_at": datetime.now(timezone.utc),
        },
    )
    await session.execute(stmt)
