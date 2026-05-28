"""Cross-campaign research cache, keyed by lowercased email.

When a lead with a known email is added to a NEW campaign, the research
worker reuses the cached research_data (if it's still within the
freshness window) instead of re-spending Anthropic web-search tokens.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ResearchCache(Base):
    __tablename__ = "research_cache"

    # Lowercased + stripped email — canonical key.  Multiple campaigns adding
    # the same email reuse the same row.
    email: Mapped[str] = mapped_column(Text, primary_key=True)
    research_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Last time the research was refreshed.  Combined with
    # ``RESEARCH_CACHE_TTL_DAYS`` from config to decide cache hits.
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
