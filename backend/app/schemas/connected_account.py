from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ConnectedAccountTestStatus


class ConnectedAccountCreate(BaseModel):
    label: str = Field(min_length=1)
    email_address: str = Field(min_length=1)
    imap_host: str = Field(min_length=1)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_use_ssl: bool = True
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ConnectedAccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    email_address: str | None = Field(default=None, min_length=1)
    imap_host: str | None = Field(default=None, min_length=1)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_use_ssl: bool | None = None
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)


class ConnectedAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    email_address: str
    imap_host: str
    imap_port: int
    imap_use_ssl: bool
    username: str
    last_tested_at: datetime | None
    last_test_status: ConnectedAccountTestStatus
    last_test_error: str | None
    last_polled_at: datetime | None
    created_at: datetime


class ConnectedAccountStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    email_address: str
    last_tested_at: datetime | None
    last_test_status: ConnectedAccountTestStatus
    last_test_error: str | None
    last_polled_at: datetime | None


class ImapTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    message_count: int | None = None
