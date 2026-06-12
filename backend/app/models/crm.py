"""CRM models: opportunities + manually-logged activities.

Salesforce-style layer on top of the outreach engine.  The existing
``Lead`` model doubles as the CRM lead (it gained ``crm_status`` +
``converted_opportunity_id`` and a nullable ``campaign_id`` in
migration 0025).  This module adds:

  Opportunity   — a deal moving through a stage pipeline.
  CrmActivity   — a manually-logged touch (call / email / meeting /
                  note / task) attached to a lead OR an opportunity.

These are deliberately separate from the AUTOMATED history
(``lead_step_executions`` + ``email_events``): those record what the
machine did; CrmActivity records what the human did.  The lead-detail
endpoint merges all three into one timeline.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, Text, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CrmLeadStatus(str, enum.Enum):
    NEW = "new"
    WORKING = "working"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    UNQUALIFIED = "unqualified"


class OpportunityStage(str, enum.Enum):
    PROSPECTING = "prospecting"
    QUALIFICATION = "qualification"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


# Default win probability per stage — Salesforce-style heuristics.  Used
# to seed ``probability`` on create / stage change when the user hasn't
# set their own number.
STAGE_DEFAULT_PROBABILITY: dict[OpportunityStage, int] = {
    OpportunityStage.PROSPECTING: 10,
    OpportunityStage.QUALIFICATION: 25,
    OpportunityStage.PROPOSAL: 50,
    OpportunityStage.NEGOTIATION: 75,
    OpportunityStage.CLOSED_WON: 100,
    OpportunityStage.CLOSED_LOST: 0,
}

CLOSED_STAGES = {OpportunityStage.CLOSED_WON, OpportunityStage.CLOSED_LOST}


class CrmActivityType(str, enum.Enum):
    CALL = "call"
    EMAIL = "email"
    MEETING = "meeting"
    NOTE = "note"
    TASK = "task"


class CrmActivityDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Opportunity(Base):
    __tablename__ = "crm_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[OpportunityStage] = mapped_column(
        Enum(OpportunityStage, name="opportunity_stage",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=OpportunityStage.PROSPECTING,
        server_default=OpportunityStage.PROSPECTING.value,
        index=True,
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    probability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    loss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Contact snapshot — copied at conversion so the deal record stays
    # complete even if the source lead row is later deleted.
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    activities: Mapped[list["CrmActivity"]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CrmActivity(Base):
    __tablename__ = "crm_activities"
    __table_args__ = (
        CheckConstraint(
            "lead_id IS NOT NULL OR opportunity_id IS NOT NULL",
            name="ck_crm_activities_has_parent",
        ),
        # Upcoming-tasks hot query: open tasks ordered by due date.
        Index(
            "ix_crm_activities_open_tasks",
            "due_at",
            postgresql_where=text(
                "activity_type = 'task' AND completed_at IS NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crm_opportunities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    activity_type: Mapped[CrmActivityType] = mapped_column(
        Enum(CrmActivityType, name="crm_activity_type",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[CrmActivityDirection | None] = mapped_column(
        Enum(CrmActivityDirection, name="crm_activity_direction",
             values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the touch actually happened (a call logged after the fact can
    # be backdated); defaults to now.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    opportunity: Mapped["Opportunity | None"] = relationship(back_populates="activities")
