"""Phase 6: research_lead task (services mocked, real DB)."""
import uuid
from datetime import time
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import Campaign, Lead, ResearchMode, ResearchStatus
from app.workers.research import _assess_quality, research_lead_async


async def _make_campaign(db_session, *, mode: ResearchMode = ResearchMode.FAST) -> Campaign:
    c = Campaign(
        name="Test",
        goal="Test",
        tone="Friendly",
        sender_name="A",
        sender_email="a@x.com",
        research_mode=mode,
        sample_count=1,
        schedule_days=[0, 1, 2, 3, 4],
        schedule_time_start=time(9, 0),
        schedule_time_end=time(17, 0),
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


async def _make_lead(db_session, campaign: Campaign, **overrides) -> Lead:
    defaults = {
        "email": "lead@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "company": "Acme",
        "job_title": "CEO",
    }
    defaults.update(overrides)
    l = Lead(campaign_id=campaign.id, **defaults)
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return l


# ---------- quality assessment ----------


def test_assess_quality_low_with_no_signals():
    assert _assess_quality({}, {}) == "low"


def test_assess_quality_partial_with_one_signal():
    assert _assess_quality({"person_news": ["x"]}, {}) == "partial"
    assert _assess_quality({"company_description": "x"}, {}) == "partial"
    assert _assess_quality({}, {"linkedin_url": "x"}) == "partial"


def test_assess_quality_rich_with_multiple_signals():
    assert _assess_quality({"person_news": ["x"], "company_description": "y"}, {}) == "rich"
    assert _assess_quality({"person_news": ["x"]}, {"linkedin_url": "y"}) == "rich"
    assert _assess_quality(
        {"person_news": ["x"], "company_description": "y"}, {"linkedin_url": "z"}
    ) == "rich"


# ---------- happy path ----------


async def test_fast_mode_runs_merged_research_and_hunter_only(db_session):
    campaign = await _make_campaign(db_session, mode=ResearchMode.FAST)
    lead = await _make_lead(db_session, campaign)

    # One merged research call returns person + company fields (no site_scraper).
    web_mock = AsyncMock(return_value={
        "person_news": ["raised Series B"], "company_news": [],
        "company_description": "AI for SMB", "recent_updates": ["new feature"],
        "industry": "SaaS", "size_hint": "startup", "found": True,
    })
    hunter_mock = AsyncMock(return_value={"deliverable": True, "score": 90})
    apollo_mock = AsyncMock(return_value={"linkedin_url": "should-not-be-called"})

    with patch("app.workers.research.web_research.research_person_web", web_mock), \
         patch("app.workers.research.hunter.verify_email_hunter", hunter_mock), \
         patch("app.workers.research.apollo.enrich_lead_apollo", apollo_mock), \
         patch("app.workers.research.compose_lead.delay") as enqueue:

        result = await research_lead_async(str(lead.id))

    # Apollo not invoked in FAST mode.
    apollo_mock.assert_not_called()
    enqueue.assert_called_once_with(str(lead.id))

    assert result["status"] == "done"
    assert result["quality"] == "rich"  # person_news + company description → 2 signals

    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_status == ResearchStatus.DONE
    rd = refreshed.research_data
    assert rd["quality"] == "rich"
    assert rd["person_news"] == ["raised Series B"]
    assert rd["company_description"] == "AI for SMB"
    assert rd["recent_updates"] == ["new feature"]
    assert rd["industry"] == "SaaS"
    assert rd["email_deliverable"] is True


async def test_deep_mode_invokes_apollo_when_key_set(db_session, monkeypatch):
    monkeypatch.setattr("app.workers.research.settings.APOLLO_API_KEY", "set-key")
    campaign = await _make_campaign(db_session, mode=ResearchMode.DEEP)
    lead = await _make_lead(db_session, campaign)

    apollo_mock = AsyncMock(return_value={
        "linkedin_url": "https://li/x",
        "linkedin_headline": "CEO at Acme",
        "job_title": "Chief Executive",
        "seniority": "founder",
        "company_industry": "SaaS",
    })

    with patch("app.workers.research.web_research.research_person_web", AsyncMock(return_value={"person_news": [], "company_news": [], "company_description": "", "found": False})), \
         patch("app.workers.research.hunter.verify_email_hunter", AsyncMock(return_value={"deliverable": True, "score": 0})), \
         patch("app.workers.research.apollo.enrich_lead_apollo", apollo_mock), \
         patch("app.workers.research.compose_lead.delay"):
        result = await research_lead_async(str(lead.id))

    apollo_mock.assert_called_once()
    assert result["status"] == "done"
    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    rd = refreshed.research_data
    assert rd["linkedin_headline"] == "CEO at Acme"
    assert rd["job_title"] == "Chief Executive"
    assert rd["seniority"] == "founder"
    assert rd["industry"] == "SaaS"
    # Only apollo signal → partial.
    assert rd["quality"] == "partial"


async def test_deep_mode_skips_apollo_when_no_key(db_session, monkeypatch):
    monkeypatch.setattr("app.workers.research.settings.APOLLO_API_KEY", "")
    campaign = await _make_campaign(db_session, mode=ResearchMode.DEEP)
    lead = await _make_lead(db_session, campaign)

    apollo_mock = AsyncMock(return_value={"never": "called"})
    with patch("app.workers.research.web_research.research_person_web", AsyncMock(return_value={"person_news": [], "company_news": [], "company_description": "", "found": False})), \
         patch("app.workers.research.hunter.verify_email_hunter", AsyncMock(return_value={"deliverable": True, "score": 0})), \
         patch("app.workers.research.apollo.enrich_lead_apollo", apollo_mock), \
         patch("app.workers.research.compose_lead.delay"):
        await research_lead_async(str(lead.id))

    apollo_mock.assert_not_called()


async def test_status_transitions_to_running_then_done(db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(db_session, campaign)
    assert lead.research_status == ResearchStatus.PENDING

    with patch("app.workers.research.web_research.research_person_web", AsyncMock(return_value={"person_news": [], "company_news": [], "company_description": "", "found": False})), \
         patch("app.workers.research.hunter.verify_email_hunter", AsyncMock(return_value={"deliverable": True, "score": 0})), \
         patch("app.workers.research.compose_lead.delay"):
        await research_lead_async(str(lead.id))

    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_status == ResearchStatus.DONE


async def test_web_research_failure_still_completes(db_session):
    """If the (now single) web-research call blows up, the task still finishes
    with empty research + hunter's deliverability, and compose is enqueued."""
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(db_session, campaign)

    with patch("app.workers.research.web_research.research_person_web", AsyncMock(side_effect=RuntimeError("API down"))), \
         patch("app.workers.research.hunter.verify_email_hunter", AsyncMock(return_value={"deliverable": True, "score": 0})), \
         patch("app.workers.research.compose_lead.delay") as enqueue:
        result = await research_lead_async(str(lead.id))

    assert result["status"] == "done"
    enqueue.assert_called_once_with(str(lead.id))
    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_status == ResearchStatus.DONE
    # web failed → empty research, but hunter still ran and the lead completes.
    assert refreshed.research_data["company_description"] == ""
    assert refreshed.research_data["person_news"] == []
    assert refreshed.research_data["email_deliverable"] is True
    assert refreshed.research_data["quality"] == "low"


async def test_none_mode_skips_all_research_but_enqueues_compose(db_session):
    """'No research' mode makes zero external calls yet still composes."""
    campaign = await _make_campaign(db_session, mode=ResearchMode.NONE)
    lead = await _make_lead(db_session, campaign)

    web_mock = AsyncMock(return_value={})
    hunter_mock = AsyncMock(return_value={"deliverable": True, "score": 0})
    apollo_mock = AsyncMock(return_value={})

    with patch("app.workers.research.web_research.research_person_web", web_mock), \
         patch("app.workers.research.hunter.verify_email_hunter", hunter_mock), \
         patch("app.workers.research.apollo.enrich_lead_apollo", apollo_mock), \
         patch("app.workers.research.compose_lead.delay") as enqueue:

        result = await research_lead_async(str(lead.id))

    # No research provider is touched at all.
    web_mock.assert_not_called()
    hunter_mock.assert_not_called()
    apollo_mock.assert_not_called()
    # Compose still runs (it uses the generic name+company-only prompt).
    enqueue.assert_called_once_with(str(lead.id))

    assert result["status"] == "done"
    assert result["quality"] == "low"

    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_status == ResearchStatus.DONE
    assert refreshed.research_data["quality"] == "low"
    assert refreshed.research_data["skipped"] is True


async def test_template_mode_skips_all_research_but_enqueues_compose(db_session):
    """'Template' mode (no AI) also makes zero research calls; compose then
    renders the campaign template instead of calling Anthropic."""
    campaign = await _make_campaign(db_session, mode=ResearchMode.TEMPLATE)
    lead = await _make_lead(db_session, campaign)

    web_mock = AsyncMock(return_value={})
    hunter_mock = AsyncMock(return_value={"deliverable": True, "score": 0})
    apollo_mock = AsyncMock(return_value={})

    with patch("app.workers.research.web_research.research_person_web", web_mock), \
         patch("app.workers.research.hunter.verify_email_hunter", hunter_mock), \
         patch("app.workers.research.apollo.enrich_lead_apollo", apollo_mock), \
         patch("app.workers.research.compose_lead.delay") as enqueue:

        result = await research_lead_async(str(lead.id))

    web_mock.assert_not_called()
    hunter_mock.assert_not_called()
    apollo_mock.assert_not_called()
    enqueue.assert_called_once_with(str(lead.id))
    assert result["status"] == "done"

    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_status == ResearchStatus.DONE


async def test_missing_lead_returns_not_found(db_session):
    with patch("app.workers.research.compose_lead.delay") as enqueue:
        result = await research_lead_async(str(uuid.uuid4()))
    assert result == {"status": "not_found"}
    enqueue.assert_not_called()


async def test_email_deliverable_flag_from_hunter(db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(db_session, campaign)

    with patch("app.workers.research.web_research.research_person_web", AsyncMock(return_value={"person_news": [], "company_news": [], "company_description": "", "found": False})), \
         patch("app.workers.research.hunter.verify_email_hunter", AsyncMock(return_value={"deliverable": False, "score": 5})), \
         patch("app.workers.research.compose_lead.delay"):
        await research_lead_async(str(lead.id))

    refreshed = await db_session.scalar(select(Lead).where(Lead.id == lead.id))
    await db_session.refresh(refreshed)
    assert refreshed.research_data["email_deliverable"] is False
