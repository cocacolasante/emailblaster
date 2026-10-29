"""OAuth clients registered by agent platforms (Claude, ChatGPT, ...).

MCP clients that can't take a pasted bearer key connect with OAuth 2.1:
they register themselves here (RFC 7591 dynamic client registration),
send the user through our login + consent page, and swap the resulting
code for an access token.  The access token IS a workspace API key
(``api_keys.oauth_client_id`` points back here), so everything after the
token exchange — verification, revocation, "acts as its creator" — is the
API-key path unchanged.

Global, not workspace-scoped: a client registers before any user signs in,
and one registration (e.g. claude.ai) serves every workspace.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # SHA-256 of the secret for confidential clients; NULL for public
    # (PKCE-only) clients, which is what Claude and ChatGPT register as.
    client_secret_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_name: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    token_endpoint_auth_method: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
