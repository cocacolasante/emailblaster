"""Phase 8: preview API + approval flow."""
import uuid
from datetime import time
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import (
    Campaign,
    CampaignStatus,
    ComposeStatus,
    Lead,
    ResearchStatus,
    SendStatus,
    StyleCorrection,
)


# --------------------------------------------------------------------------
# Setup helpers
# --------------------------------------------------------------------------


async def _make_campaign(
    db_session, *,
    status: CampaignStatus = CampaignStatus.PREVIEWING,
    sample_count: int = 2,
    min_delay_seconds: int = 0,
) -> Campaign:
    c = Campaign(
        name="Phase 8 test",
        goal="Demo",
        tone="Direct",
        sender_name="A",
        sender_email="a@x.com",
        sample_count=sample_count,
        schedule_days=[0, 1, 2, 3, 4],
        schedule_time_start=time(9, 0),
        schedule_time_end=time(17, 0),
        status=status,
        min_delay_seconds=min_delay_seconds,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


async def _make_lead(
    db_session, campaign: Campaign, *,
    email: str,
    is_sample: bool = False,
    compose_status: ComposeStatus = ComposeStatus.PENDING,
    composed_subject: str | None = None,
    composed_body: str | None = None,
    research_data: dict | None = None,
    research_status: ResearchStatus = ResearchStatus.PENDING,
    send_status: SendStatus = SendStatus.PENDING,
) -> Lead:
    lead = Lead(
        campaign_id=campaign.id,
        email=email,
        first_name=email.split("@")[0].title(),
        last_name="Doe",
        company="Acme",
        job_title="CEO",
        is_sample=is_sample,
        compose_status=compose_status,
        composed_subject=composed_subject,
        composed_body=composed_body,
        research_data=research_data,
        research_status=research_status,
        send_status=send_status,
    )
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)
    return lead


# --------------------------------------------------------------------------
# GET /preview
# --------------------------------------------------------------------------


async def test_get_preview_returns_only_samples(client, db_session):
    campaign = await _make_campaign(db_session)
    sample = await _make_lead(
        db_session, campaign, email="s1@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Body",
        research_data={"quality": "rich", "person_news": ["Series B"]},
    )
    # Non-sample lead — should NOT appear in /preview.
    await _make_lead(db_session, campaign, email="n1@x.com", is_sample=False)

    resp = await client.get(f"/campaigns/{campaign.id}/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == str(campaign.id)
    assert body["status"] == "previewing"
    assert len(body["samples"]) == 1
    assert body["samples"][0]["lead_id"] == str(sample.id)
    assert body["samples"][0]["composed_subject"] == "Hi"
    assert body["samples"][0]["research_quality"] == "rich"


async def test_get_preview_research_summary_built_from_data(client, db_session):
    campaign = await _make_campaign(db_session)
    await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        research_data={
            "quality": "rich",
            "person_news": ["Raised Series B", "Spoke at SaaStr"],
            "company_description": "AI for SMB sales teams",
            "industry": "SaaS",
            "linkedin_headline": "CEO at Acme",
        },
    )

    resp = await client.get(f"/campaigns/{campaign.id}/preview")
    summary = resp.json()["samples"][0]["research_summary"]
    assert "Raised Series B" in summary
    assert "AI for SMB" in summary
    assert "SaaS" in summary
    assert "CEO at Acme" in summary


async def test_get_preview_all_ready_when_every_sample_done(client, db_session):
    campaign = await _make_campaign(db_session)
    await _make_lead(db_session, campaign, email="s1@x.com", is_sample=True,
                     compose_status=ComposeStatus.DONE)
    await _make_lead(db_session, campaign, email="s2@x.com", is_sample=True,
                     compose_status=ComposeStatus.DONE)

    resp = await client.get(f"/campaigns/{campaign.id}/preview")
    assert resp.json()["all_ready"] is True


async def test_get_preview_all_ready_false_when_any_pending(client, db_session):
    campaign = await _make_campaign(db_session)
    await _make_lead(db_session, campaign, email="s1@x.com", is_sample=True,
                     compose_status=ComposeStatus.DONE)
    await _make_lead(db_session, campaign, email="s2@x.com", is_sample=True,
                     compose_status=ComposeStatus.RUNNING)

    resp = await client.get(f"/campaigns/{campaign.id}/preview")
    assert resp.json()["all_ready"] is False


async def test_get_preview_all_ready_false_when_no_samples(client, db_session):
    campaign = await _make_campaign(db_session)
    resp = await client.get(f"/campaigns/{campaign.id}/preview")
    assert resp.json()["all_ready"] is False
    assert resp.json()["samples"] == []


async def test_get_preview_404_for_unknown_campaign(client):
    resp = await client.get(f"/campaigns/{uuid.uuid4()}/preview")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# PATCH /samples/{lead_id}
# --------------------------------------------------------------------------


async def test_patch_updates_subject(client, db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Original", composed_body="Body",
    )
    resp = await client.patch(
        f"/campaigns/{campaign.id}/preview/samples/{lead.id}",
        json={"composed_subject": "Revised"},
    )
    assert resp.status_code == 200
    assert resp.json()["composed_subject"] == "Revised"


async def test_patch_body_edit_creates_style_correction(client, db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Original body text",
    )
    resp = await client.patch(
        f"/campaigns/{campaign.id}/preview/samples/{lead.id}",
        json={"composed_body": "Revised punchy body"},
    )
    assert resp.status_code == 200

    sc = (await db_session.execute(
        select(StyleCorrection).where(StyleCorrection.campaign_id == campaign.id)
    )).scalars().all()
    assert len(sc) == 1
    assert sc[0].original_body == "Original body text"
    assert sc[0].corrected_body == "Revised punchy body"


async def test_patch_body_unchanged_does_not_create_style_correction(client, db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Same body",
    )
    # Patch with the SAME body — no correction should be recorded.
    await client.patch(
        f"/campaigns/{campaign.id}/preview/samples/{lead.id}",
        json={"composed_body": "Same body", "approved": True},
    )
    sc = (await db_session.execute(select(StyleCorrection))).scalars().all()
    assert sc == []


async def test_patch_sets_sample_approved(client, db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
    )
    resp = await client.patch(
        f"/campaigns/{campaign.id}/preview/samples/{lead.id}",
        json={"approved": True},
    )
    assert resp.status_code == 200
    assert resp.json()["sample_approved"] is True


async def test_patch_rejects_non_sample_lead(client, db_session):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(
        db_session, campaign, email="n@x.com", is_sample=False,
        compose_status=ComposeStatus.DONE,
    )
    resp = await client.patch(
        f"/campaigns/{campaign.id}/preview/samples/{lead.id}",
        json={"composed_subject": "X"},
    )
    assert resp.status_code == 400


async def test_patch_404_when_lead_belongs_to_other_campaign(client, db_session):
    c1 = await _make_campaign(db_session)
    c2 = await _make_campaign(db_session)
    lead = await _make_lead(db_session, c2, email="s@x.com", is_sample=True)

    resp = await client.patch(
        f"/campaigns/{c1.id}/preview/samples/{lead.id}",
        json={"composed_subject": "X"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Approve-all
# --------------------------------------------------------------------------


async def test_approve_all_transitions_to_running_and_dispatches_composed(
    client, db_session
):
    campaign = await _make_campaign(db_session)
    sample = await _make_lead(
        db_session, campaign, email="s1@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Body",
    )
    composed_non_sample = await _make_lead(
        db_session, campaign, email="n1@x.com", is_sample=False,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Body",
    )
    # Pending non-sample — should NOT be enqueued for send here.
    await _make_lead(
        db_session, campaign, email="n2@x.com", is_sample=False,
        compose_status=ComposeStatus.PENDING,
    )

    with patch("app.routers.preview.send_lead.delay") as enqueue:
        resp = await client.post(f"/campaigns/{campaign.id}/preview/approve-all")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["samples_approved"] == 1
    assert body["leads_dispatched_to_send"] == 2  # 1 sample + 1 composed non-sample

    dispatched = {call.args[0] for call in enqueue.call_args_list}
    assert dispatched == {str(sample.id), str(composed_non_sample.id)}

    # Sample is now marked approved.
    refreshed = await db_session.scalar(select(Lead).where(Lead.id == sample.id))
    await db_session.refresh(refreshed)
    assert refreshed.sample_approved is True

    refreshed_c = await db_session.get(Campaign, campaign.id)
    await db_session.refresh(refreshed_c)
    assert refreshed_c.status == CampaignStatus.RUNNING


async def test_approve_all_staggers_dispatch_by_min_delay(client, db_session):
    """With a min_delay, approve-all schedules sends spaced by min_delay (via
    apply_async eta) instead of firing the whole batch at once."""
    campaign = await _make_campaign(db_session, min_delay_seconds=120)
    for i in range(3):
        await _make_lead(
            db_session, campaign, email=f"s{i}@x.com", is_sample=False,
            compose_status=ComposeStatus.DONE,
            composed_subject="Hi", composed_body="Body",
        )

    with patch("app.routers.preview.send_lead.apply_async") as enqueue, \
         patch("app.routers.preview.send_lead.delay") as delay_mock:
        resp = await client.post(f"/campaigns/{campaign.id}/preview/approve-all")

    assert resp.status_code == 200
    # All sends went through apply_async (staggered), none through bare delay.
    delay_mock.assert_not_called()
    assert enqueue.call_count == 3
    etas = sorted(call.kwargs["eta"] for call in enqueue.call_args_list)
    # Consecutive etas are exactly min_delay apart.
    gaps = [(etas[i + 1] - etas[i]).total_seconds() for i in range(len(etas) - 1)]
    assert all(g == 120 for g in gaps), gaps


async def test_approve_all_only_allowed_when_previewing(client, db_session):
    campaign = await _make_campaign(db_session, status=CampaignStatus.DRAFT)
    with patch("app.routers.preview.send_lead.delay"):
        resp = await client.post(f"/campaigns/{campaign.id}/preview/approve-all")
    assert resp.status_code == 409


async def test_approve_all_404_unknown_campaign(client):
    resp = await client.post(f"/campaigns/{uuid.uuid4()}/preview/approve-all")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Reject
# --------------------------------------------------------------------------


async def test_reject_resets_state(client, db_session):
    campaign = await _make_campaign(db_session)
    sample = await _make_lead(
        db_session, campaign, email="s@x.com", is_sample=True,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Body",
    )
    non_sample = await _make_lead(
        db_session, campaign, email="n@x.com", is_sample=False,
        compose_status=ComposeStatus.DONE,
        composed_subject="Hi", composed_body="Body",
    )
    sample.sample_approved = True
    await db_session.commit()

    resp = await client.post(f"/campaigns/{campaign.id}/preview/reject")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["leads_cleared"] == 2

    refreshed_s = await db_session.scalar(select(Lead).where(Lead.id == sample.id))
    await db_session.refresh(refreshed_s)
    assert refreshed_s.compose_status == ComposeStatus.PENDING
    assert refreshed_s.composed_subject is None
    assert refreshed_s.composed_body is None
    assert refreshed_s.sample_approved is None

    refreshed_n = await db_session.scalar(select(Lead).where(Lead.id == non_sample.id))
    await db_session.refresh(refreshed_n)
    assert refreshed_n.compose_status == ComposeStatus.PENDING
    assert refreshed_n.composed_body is None


async def test_reject_only_allowed_when_previewing(client, db_session):
    campaign = await _make_campaign(db_session, status=CampaignStatus.RUNNING)
    resp = await client.post(f"/campaigns/{campaign.id}/preview/reject")
    assert resp.status_code == 409


async def test_reject_404_unknown_campaign(client):
    resp = await client.post(f"/campaigns/{uuid.uuid4()}/preview/reject")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


async def test_progress_counts_each_status(client, db_session):
    campaign = await _make_campaign(db_session)
    # 5 leads in various states
    await _make_lead(db_session, campaign, email="a@x.com",
                     research_status=ResearchStatus.DONE,
                     compose_status=ComposeStatus.DONE,
                     send_status=SendStatus.SENT)
    await _make_lead(db_session, campaign, email="b@x.com",
                     research_status=ResearchStatus.DONE,
                     compose_status=ComposeStatus.DONE)
    await _make_lead(db_session, campaign, email="c@x.com",
                     research_status=ResearchStatus.DONE)
    await _make_lead(db_session, campaign, email="d@x.com")  # all pending
    await _make_lead(db_session, campaign, email="e@x.com",
                     research_status=ResearchStatus.FAILED)

    resp = await client.get(f"/campaigns/{campaign.id}/preview/progress")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "total_leads": 5,
        "researched": 3,
        "composed": 2,
        "sent": 1,
        "failed": 1,
    }


