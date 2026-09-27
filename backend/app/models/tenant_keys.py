"""Per-workspace third-party credentials (Anthropic, Brevo, Hunter,
Apollo, Unipile, Adzuna).

One row per (workspace, provider).  ``encrypted_credentials`` is a Fernet
token over a JSON object of the provider's fields (e.g. Brevo:
``{api_key, sender_email, sender_name, webhook_secret}``).  Only
``services/credentials.py`` decrypts it (enforced by a hardening test).
``preview`` holds a masked hint (``••••ab12``) written at save time so
listing integrations never needs to decrypt.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.tenancy.mixin import TenantMixin


class TenantProviderKey(TenantMixin, Base):
    __tablename__ = "tenant_provider_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_provider_keys_tenant_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # anthropic | brevo | hunter | apollo | unipile | adzuna
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_status: Mapped[str | None] = mapped_column(Text, nullable=True)  # ok | failed
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
