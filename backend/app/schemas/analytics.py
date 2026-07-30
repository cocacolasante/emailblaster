from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel


class AnalyticsOverview(BaseModel):
    total_leads: int
    sent: int
    delivered: int
    opened: int
    clicked: int
    replied: int
    bounced: int
    spam_complaints: int
    unsubscribed: int


class AnalyticsRates(BaseModel):
    open_rate: float | None
    click_rate: float | None
    reply_rate: float | None
    bounce_rate: float | None
    spam_rate: float | None
    unsub_rate: float | None
    delivery_rate: float | None


class TimelinePoint(BaseModel):
    date: date
    opens: int
    clicks: int
    replies: int


class QualityBreakdownItem(BaseModel):
    quality: str
    count: int
    open_rate: float | None


class BestSubject(BaseModel):
    subject: str
    sent: int
    open_rate: float


class SendCohort(BaseModel):
    """Leads grouped by the week their FIRST email was sent, with the share
    that has ever opened.  Separates "we sent less" from "people stopped
    opening" — the daily timeline can't tell those apart."""
    week_start: date
    sent: int
    opened: int
    open_rate: float | None
    # True while the cohort's newest send is < 7 days old — opens are
    # still arriving, so the rate reads low and will climb.
    accumulating: bool


class AnalyticsResponse(BaseModel):
    campaign_id: uuid.UUID
    overview: AnalyticsOverview
    rates: AnalyticsRates
    reply_tracking_enabled: bool
    click_tracking_enabled: bool = True
    timeline: list[TimelinePoint]
    research_quality_breakdown: list[QualityBreakdownItem]
    sender_reputation_score: int | None
    best_subject_lines: list[BestSubject]
    send_cohorts: list[SendCohort] = []
