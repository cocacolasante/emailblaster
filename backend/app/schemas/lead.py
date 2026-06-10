from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import ComposeStatus, LinkedInConnectionStatus, ResearchStatus, SendStatus


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
    # CRM-lite extras (kept here so the global Leads list can show them).
    notes: str | None = None
    has_notes: bool = False
    campaign_name: str | None = None


class LeadEmailUpdate(BaseModel):
    """Edit a lead's composed email and/or notes."""
    composed_subject: str | None = None
    composed_body: str | None = None
    notes: str | None = None


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


class LeadHistoryItem(BaseModel):
    """One row on the lead's activity timeline.

    Two source tables feed this:
    - ``LeadStepExecution`` rows (every sequence step we attempted —
      email sends, LinkedIn views/connects/DMs, etc.).  Carries the
      result enum (``sent``, ``skipped``, ``failed``...) and any
      ``external_id`` (Brevo message_id, LinkedIn invitation_id).
    - ``EmailEvent`` rows (delivered / opened / clicked / replied /
      bounced / spam / unsubscribed — what the recipient did).

    The endpoint merges both into one chronologically-sorted list so
    the UI can render a single timeline without doing the merge
    client-side.
    """
    at: datetime
    kind: str           # "execution" | "event" — discriminator for the UI
    action: str         # human-readable label ("Sent email", "Opened email")
    status: str         # success-y / warn-y / fail-y — UI colour cue
    icon: str           # emoji hint so the UI has a default visual
    detail: str | None = None  # error message, sequence node label, etc.
    external_id: str | None = None  # Brevo message_id or LinkedIn invitation_id


class LeadDetail(LeadResponse):
    """Full per-lead view with all the fields the CRM modal renders.

    Adds the LinkedIn outreach state, the activity timeline, and a
    distilled ``research_summary`` so the UI doesn't have to dig
    through the raw ``research_data`` JSONB."""
    linkedin_connection_status: LinkedInConnectionStatus | None = None
    linkedin_last_reply_at: datetime | None = None
    company_website: str | None = None
    company_name: str | None = None  # alias of ``company`` for symmetry with research blobs

    history: list[LeadHistoryItem]
    # Lightweight roll-ups for the UI header pills.
    history_counts: dict[str, int]  # {"sent": 3, "opened": 1, "replied": 0, ...}
    research_summary: dict[str, Any]  # {industry, person_news, company_news, ...}


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
    # True when the campaign launched straight into RUNNING (its start node
    # isn't an email, so there are no sample emails to preview/approve).
    auto_launched: bool = False
