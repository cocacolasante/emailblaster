"""Nonprofit funding discovery — per-source state (migration 0032).

``FundingSourceState`` is the cursor / diff baseline for each external
feed, the same idea ``SignalWatch.last_seen`` plays for watches.  One
row per source ('usaspending' | 'irs_bmf').
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FundingSourceState(Base):
    __tablename__ = "funding_source_state"

    # 'usaspending' | 'irs_bmf'
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    # Runtime on/off, editable from Settings → Discovery (migration 0033).
    # NULL = not yet seeded; the worker/API seed it from the env default.
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Runtime config (migration 0033), seeded from env when NULL:
    #   usaspending: {"lookback_days": 7}
    #   irs_bmf:     {"ruling_lookback_months": 2, "states": ["PA", "NJ"]}
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Per-source high-water mark, e.g.
    #   usaspending: {"last_action_date": "2026-06-14"}
    #   irs_bmf:     {"last_file_month": "202606"}
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_run_status: Mapped[str | None] = mapped_column(Text, nullable=True)
