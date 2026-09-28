"""One-off prospect outreach drafts (research a LinkedIn URL → draft →
confirm recipient + sender → human approval → send).

Created by the Muse flow (``/outreach-drafts``).  Nothing is sent from a
draft without two explicit steps: ``confirm`` pins the exact recipient,
sender and message version and issues a one-time code, and ``send``
requires that code plus ``user_approved``.  Any redraft or edit after
confirming drops the draft back to ``draft`` and voids the code.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.tenancy.mixin import TenantMixin


class OutreachChannel(str, enum.Enum):
    EMAIL = "email"
    LINKEDIN_DM = "linkedin_dm"            # message a 1st-degree connection
    LINKEDIN_CONNECT = "linkedin_connect"  # connection request with a note (≤200 chars)


class OutreachDraftStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"        # confirmed — awaiting approval to send
    SENT = "sent"
    FAILED = "failed"      # send attempted and failed; can be fixed + re-confirmed
    DISCARDED = "discarded"


def _enum(cls, name):
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class OutreachDraft(TenantMixin, Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    linkedin_url: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[OutreachChannel] = mapped_column(_enum(OutreachChannel, "outreach_channel"), nullable=False)
    status: Mapped[OutreachDraftStatus] = mapped_column(
        _enum(OutreachDraftStatus, "outreach_draft_status"), nullable=False,
        default=OutreachDraftStatus.DRAFT, server_default="draft",
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False, default="professional")
    research_mode: Mapped[str] = mapped_column(Text, nullable=False, default="fast")
    char_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    research: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Bumped on every redraft/edit; a confirmation is only valid for the
    # version it was issued against.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    to_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("linkedin_accounts.id", ondelete="SET NULL"), nullable=True,
    )
    confirm_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    send_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    crm_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
