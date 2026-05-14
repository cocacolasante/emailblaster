from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import LinkedInAccountStatus


class LinkedInAccountCreate(BaseModel):
    label: str = Field(min_length=1)
    # For DIY accounts these are required.  For Unipile-managed accounts
    # the user goes through the hosted-auth flow instead and a separate
    # endpoint creates the row — so here they're optional to allow both
    # paths through the same schema.
    linkedin_email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    proxy_url: str | None = None
    li_at_cookie: str | None = None


class LinkedInAccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    linkedin_email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    proxy_url: str | None = None
    li_at_cookie: str | None = None


class LinkedInAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str
    linkedin_email: str
    proxy_url: str | None
    status: LinkedInAccountStatus
    last_error: str | None
    last_tested_at: datetime | None
    last_polled_at: datetime | None
    pending_challenge_url: str | None
    created_at: datetime
    updated_at: datetime
    # Unipile-specific fields.  NULL on legacy DIY rows.
    unipile_account_id: str | None = None
    provider_kind: str = "diy"


class ConnectViaUnipileRequest(BaseModel):
    """Body for POST /linkedin-accounts/connect-via-unipile.

    Caller supplies a label and the front-end URL the user should land on
    once Unipile's hosted flow completes (success/failure variants).
    """
    label: str = Field(min_length=1)
    success_redirect_url: str = Field(min_length=1)
    failure_redirect_url: str | None = None


class ConnectViaUnipileResponse(BaseModel):
    """Returned by POST /linkedin-accounts/connect-via-unipile.

    ``account_id`` is our local LinkedInAccount.id — we create the row
    eagerly so the frontend can poll its status.  ``hosted_url`` is the
    Unipile URL the user opens to complete login.
    """
    account_id: uuid.UUID
    hosted_url: str


class LinkedInTestResponse(BaseModel):
    ok: bool
    status: LinkedInAccountStatus
    error: str | None = None
    challenge_url: str | None = None
    meta: dict[str, Any] | None = None


class ResolveChallengeRequest(BaseModel):
    """Body posted after the user has manually resolved the challenge in
    their own browser. We just flip status back to UNTESTED and clear the
    challenge URL — the next /test call will re-validate.
    """
    note: str | None = None
