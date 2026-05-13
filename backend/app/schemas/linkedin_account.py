from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import LinkedInAccountStatus


class LinkedInAccountCreate(BaseModel):
    label: str = Field(min_length=1)
    linkedin_email: str = Field(min_length=1)
    password: str = Field(min_length=1)
    proxy_url: str | None = None


class LinkedInAccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    linkedin_email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    proxy_url: str | None = None


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
