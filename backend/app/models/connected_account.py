from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign


class ConnectedAccountTestStatus(str, enum.Enum):
    UNTESTED = "untested"
    OK = "ok"
    FAILED = "failed"


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    email_address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    imap_host: Mapped[str] = mapped_column(Text, nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993, server_default="993")
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_status: Mapped[ConnectedAccountTestStatus] = mapped_column(
        Enum(ConnectedAccountTestStatus, name="connected_account_test_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ConnectedAccountTestStatus.UNTESTED,
        server_default=ConnectedAccountTestStatus.UNTESTED.value,
    )
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="connected_account",
        passive_deletes=True,
    )
