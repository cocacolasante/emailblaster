from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SuppressionReason(str, enum.Enum):
    UNSUBSCRIBED = "unsubscribed"
    HARD_BOUNCE = "hard_bounce"
    SPAM = "spam"
    MANUAL = "manual"


class Suppression(Base):
    __tablename__ = "suppression_list"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    reason: Mapped[SuppressionReason] = mapped_column(
        Enum(SuppressionReason, name="suppression_reason", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
