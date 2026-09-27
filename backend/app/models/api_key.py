"""Workspace API keys — machine credentials for agents (Muse over MCP).

An agent on a phone has no browser session, so it presents a bearer key
instead.  The key acts as the member who created it, inside that
member's workspace.

Keys are HASHED, never encrypted: nothing ever needs to read one back,
only to check a presented one, so a database leak yields nothing usable.
Verification runs through the SECURITY DEFINER ``api_key_verify()``
(migration 0050) because the key is what identifies the workspace — the
lookup can't run inside the tenant boundary it establishes.

Only a signed-in person can mint or revoke keys (a key can't mint keys:
a credential that issues credentials can't be meaningfully revoked).
Revoked rows are kept — ``last_used_at`` is the evidence of what a
leaked key did.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.tenancy.mixin import TenantMixin

KEY_PREFIX = "eb_"
# Enough to find the row, not enough to guess the rest.
PREFIX_LENGTH = 11


class ApiKey(TenantMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
