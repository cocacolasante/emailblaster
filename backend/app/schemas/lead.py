from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import ComposeStatus, ResearchStatus, SendStatus


class LeadSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    company: str | None
    job_title: str | None
    research_status: ResearchStatus
    compose_status: ComposeStatus
    send_status: SendStatus
    is_sample: bool
    sample_approved: bool | None
    scheduled_send_at: datetime | None
    created_at: datetime


class LeadResponse(LeadSummary):
    """Per-lead detail, including the composed email and research blob."""
    phone: str | None
    linkedin_url: str | None
    raw_csv_row: dict[str, Any] | None
    research_data: dict[str, Any] | None
    composed_subject: str | None
    composed_body: str | None
    style_correction: str | None
    brevo_message_id: str | None
    updated_at: datetime


class PaginatedLeads(BaseModel):
    items: list[LeadSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class UploadPreviewResponse(BaseModel):
    columns: list[str]
    preview_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str]
    total_rows: int


class ConfirmUploadResponse(BaseModel):
    total: int
    suppressed: int
    duplicates_removed: int
    samples_selected: int
