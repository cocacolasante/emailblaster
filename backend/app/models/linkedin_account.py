from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign


class LinkedInAccountStatus(str, enum.Enum):
    UNTESTED = "untested"
    OK = "ok"
    FAILED = "failed"          # bad creds or network failure
    CHALLENGED = "challenged"  # LinkedIn wants a captcha/PIN — needs user action
    RESTRICTED = "restricted"  # account is in LinkedIn's penalty box; stop all actions


class LinkedInAccount(Base):
    """A user-connected LinkedIn account.

    Credentials are Fernet-encrypted at rest. Decryption is confined to
    `app/services/linkedin/linkedin_api_impl.py` (analogous to imap_client).
    """
    __tablename__ = "linkedin_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    linkedin_email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Nullable since 0006: Unipile-managed accounts don't store a password
    # (Unipile owns the LinkedIn session).  DIY/Playwright accounts still
    # require this.
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated after a successful login; JSON-encoded li_at + JSESSIONID etc.
    # Only used by DIY/Playwright impls.  Unipile rows leave this empty.
    session_cookies_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-account proxy override; falls back to settings.LINKEDIN_PROXY_URL.
    # Only meaningful for DIY/Playwright; Unipile manages its own proxies.
    proxy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unipile's opaque account id, returned by the hosted-auth flow.
    # NULL for DIY/Playwright accounts.  Provider methods that need to
    # call Unipile read this off the model instance.
    unipile_account_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, unique=True,
    )
    # "diy" (legacy Playwright / linkedin-api) or "unipile".  Defaults to
    # "diy" so old rows behave as before; the connect-via-Unipile router
    # inserts new rows with "unipile".  Useful for branching in the router
    # / sequencer when behaviour diverges (e.g. password is required for
    # diy but not for unipile).
    provider_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="diy", server_default="diy",
    )
    status: Mapped[LinkedInAccountStatus] = mapped_column(
        Enum(
            LinkedInAccountStatus,
            name="linkedin_account_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LinkedInAccountStatus.UNTESTED,
        server_default=LinkedInAccountStatus.UNTESTED.value,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pending challenge URL surfaced after a failed action — user resolves
    # in their own browser then calls /resolve-challenge.
    pending_challenge_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="linkedin_account",
        passive_deletes=True,
    )
