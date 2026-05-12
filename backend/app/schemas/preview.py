from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.models import CampaignStatus, ComposeStatus


class SamplePreview(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    company: str | None
    research_quality: str
    research_summary: str
    composed_subject: str | None
    composed_body: str | None
    compose_status: ComposeStatus
    sample_approved: bool | None


class PreviewResponse(BaseModel):
    campaign_id: uuid.UUID
    status: CampaignStatus
    samples: list[SamplePreview]
    all_ready: bool


class SampleUpdateRequest(BaseModel):
    composed_subject: str | None = None
    composed_body: str | None = None
    approved: bool | None = None


class ApproveAllResponse(BaseModel):
    campaign_id: uuid.UUID
    status: CampaignStatus
    samples_approved: int
    leads_dispatched_to_send: int


class RejectResponse(BaseModel):
    campaign_id: uuid.UUID
    status: CampaignStatus
    leads_cleared: int


class PreviewProgress(BaseModel):
    total_leads: int
    researched: int
    composed: int
    sent: int
    failed: int
