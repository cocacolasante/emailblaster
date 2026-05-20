from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ResearchClientRequest(BaseModel):
    """Input for the one-off 'research a client' tool."""

    linkedin_url: str = Field(min_length=10, max_length=500)
    goal: str = Field(min_length=3, max_length=2000)
    tone: str = Field(default="professional", max_length=120)
    sender_name: str = Field(default="", max_length=120)
    research_mode: Literal["fast", "deep"] = "fast"
    output_kind: Literal["email", "linkedin_dm"] = "linkedin_dm"
    # Default 600 is a comfortable email; 300 is a comfortable DM.  We keep
    # one default and let the UI override it based on output_kind.
    char_limit: int = Field(default=600, ge=50, le=5000)


class ResearchedProfile(BaseModel):
    first_name: str
    last_name: str
    headline: str
    company: str
    company_website: str
    job_title: str
    industry: str
    found: bool
    quality: str  # "low" | "partial" | "rich"


class ResearchClientResponse(BaseModel):
    profile: ResearchedProfile
    research: dict[str, Any]
    subject: str
    body: str
    char_count: int
    duration_ms: int
