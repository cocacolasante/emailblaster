"""Retargeting — build a follow-up campaign from leads who engaged (clicked a
link) with a previous campaign, with strict no-duplicate semantics + AI context.
"""
from __future__ import annotations

from datetime import time

import pytest
from sqlalchemy import func, select

from app.models import (
    Campaign, CampaignStatus, EmailEvent, EmailEventType, Lead,
    LinkedInConnectionStatus, ResearchMode,
)
from app.services import retarget
from app.workers.compose import _build_retarget_prompt

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def research_kicks(monkeypatch):
    """Capture (instead of enqueue) the research kick the retarget launch fires."""
    kicked: list[str] = []
    monkeypatch.setattr(
        "app.workers.ingest.run_campaign_research.delay",
        lambda cid: kicked.append(cid),
    )
    return kicked


def _payload(**ov):
    base = {
        "name": "Source", "goal": "book a call", "tone": "warm",
        "sender_name": "Op", "sender_email": "op@org.com", "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4], "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00", "schedule_timezone": "UTC",
    }
    base.update(ov)
    return base


async def _source_with_clickers(db_session):
    c = Campaign(name="Spring", goal="g", tone="warm", sender_name="Op",
                 sender_email="op@org.com", schedule_time_start=time(9, 0),
                 schedule_time_end=time(17, 0), status=CampaignStatus.RUNNING)
    db_session.add(c)
    await db_session.flush()
    leads = []
    for i in range(3):
        l = Lead(campaign_id=c.id, email=f"clicker{i}@org.com", first_name=f"F{i}",
                 company="Org", brevo_message_id=f"<m{i}@x>",
                 composed_subject=f"Subj {i}", composed_body=f"Body {i}")
        db_session.add(l)
        leads.append(l)
    await db_session.flush()
    # leads 0 and 1 clicked; lead 2 did not.
    for i in (0, 1):
        db_session.add(EmailEvent(
            lead_id=leads[i].id, campaign_id=c.id, event_type=EmailEventType.CLICKED,
            event_data={"link": f"https://grantmind.pro/x{i}", "event": "clicks"},
        ))
    await db_session.commit()
    return c, leads


# ---- engaged_leads + context ----------------------------------------------

async def test_engaged_leads_picks_clickers_with_context(db_session):
    c, leads = await _source_with_clickers(db_session)
    pairs = await retarget.engaged_leads(db_session, c.id)
    assert len(pairs) == 2
    by_email = {l.email: ctx for l, ctx in pairs}
    assert by_email["clicker0@org.com"]["clicked_url"] == "https://grantmind.pro/x0"
    assert by_email["clicker0@org.com"]["original_subject"] == "Subj 0"
    assert by_email["clicker0@org.com"]["engaged_via"] == "email_click"
    assert "clicker2@org.com" not in by_email   # didn't click


async def test_engaged_leads_includes_linkedin_connected(db_session):
    c, leads = await _source_with_clickers(db_session)
    leads[2].linkedin_connection_status = LinkedInConnectionStatus.CONNECTED
    await db_session.commit()
    pairs = await retarget.engaged_leads(db_session, c.id)
    via = {l.email: ctx["engaged_via"] for l, ctx in pairs}
    assert via.get("clicker2@org.com") == "linkedin_connection"
    assert len(pairs) == 3


# ---- retarget prompt -------------------------------------------------------

async def test_retarget_prompt_references_click_not_tracking():
    p = _build_retarget_prompt(
        "book a call", "warm", "Op", "Jane", "Doe", "Org",
        {"engaged_via": "email_click", "clicked_url": "https://grantmind.pro/signup",
         "original_subject": "Cut your grant-writing time", "original_body": "..."},
    )
    assert "https://grantmind.pro/signup" in p
    assert "Cut your grant-writing time" in p
    assert "do NOT explicitly say" in p   # the no-creepy-tracking guard


# ---- endpoint: create + dedup ----------------------------------------------

async def test_retarget_endpoint_creates_and_dedupes(client, db_session):
    c, leads = await _source_with_clickers(db_session)

    r1 = await client.post(f"/campaigns/{c.id}/retarget", json={})
    assert r1.status_code == 200
    body = r1.json()
    assert body["created"] is True and body["engaged"] == 2 and body["added"] == 2
    target_id = body["target_campaign_id"]

    target = await db_session.get(Campaign, target_id)
    assert target.name.startswith(retarget.RETARGET_PREFIX)
    assert target.research_mode is ResearchMode.NONE
    # Copied leads carry the retarget context for compose.
    new_leads = (await db_session.execute(select(Lead).where(Lead.campaign_id == target.id))).scalars().all()
    assert len(new_leads) == 2
    ctx = (new_leads[0].research_data or {}).get("retarget_context")
    assert ctx and ctx["source_campaign_name"] == "Spring" and ctx.get("clicked_url")

    # Re-run into the SAME target → no duplicates.
    r2 = await client.post(f"/campaigns/{c.id}/retarget", json={"target_campaign_id": target_id})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["added"] == 0 and b2["skipped_duplicate"] == 2
    total = await db_session.scalar(select(func.count()).select_from(Lead).where(Lead.campaign_id == target.id))
    assert total == 2   # still 2, no dupes


async def test_retarget_endpoint_launches_draft_target(client, db_session, research_kicks):
    """A fresh retarget target must not be stranded in DRAFT — no draft-exit
    exists for copied-in leads (confirm-upload is CSV-only, and publishing a
    sequence never touches campaign status)."""
    kicked = research_kicks
    c, leads = await _source_with_clickers(db_session)

    r = await client.post(f"/campaigns/{c.id}/retarget", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["launched"] is True
    assert body["target_status"] == "previewing"   # default sequence = email entry
    assert kicked == [body["target_campaign_id"]]

    target = await db_session.get(Campaign, body["target_campaign_id"])
    assert target.status is CampaignStatus.PREVIEWING
    # Preview samples marked so the review screen isn't empty.
    samples = (await db_session.execute(
        select(Lead).where(Lead.campaign_id == target.id, Lead.is_sample.is_(True))
    )).scalars().all()
    assert len(samples) == 2   # sample_count (5) >= added (2) → all are samples


async def test_retarget_launch_rescues_stuck_draft_target(client, db_session, research_kicks):
    """Re-running retarget into a pre-fix DRAFT target (leads already copied,
    nothing new to add) still launches it."""
    kicked = research_kicks
    c, leads = await _source_with_clickers(db_session)
    # Build the stuck state directly: draft target with leads, never launched.
    target = await retarget.create_retarget_campaign(db_session, c)
    await retarget.retarget_into_campaign(db_session, c, target)
    assert target.status is CampaignStatus.DRAFT

    r = await client.post(
        f"/campaigns/{c.id}/retarget", json={"target_campaign_id": str(target.id)}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 0 and body["skipped_duplicate"] == 2
    assert body["launched"] is True and body["target_status"] == "previewing"
    assert kicked == [str(target.id)]


async def test_retarget_launch_noops_on_live_target(client, db_session):
    """Adding into an already-launched target must not touch its status."""
    c, leads = await _source_with_clickers(db_session)
    r1 = await client.post(f"/campaigns/{c.id}/retarget", json={})
    target_id = r1.json()["target_campaign_id"]
    target = await db_session.get(Campaign, target_id)
    target.status = CampaignStatus.RUNNING
    await db_session.commit()

    r2 = await client.post(
        f"/campaigns/{c.id}/retarget", json={"target_campaign_id": target_id}
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["launched"] is False and body["target_status"] == "running"


async def test_retarget_preview(client, db_session):
    c, leads = await _source_with_clickers(db_session)
    r = await client.get(f"/campaigns/{c.id}/retarget/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["engaged"] == 2 and body["by_email_click"] == 2


async def test_create_campaign_retarget_type_pulls_engaged(client, db_session, research_kicks):
    kicked = research_kicks
    c, leads = await _source_with_clickers(db_session)
    r = await client.post("/campaigns/", json=_payload(
        name="Retarget — Spring", research_mode="fast",
        retarget_source_campaign_id=str(c.id),
    ))
    assert r.status_code == 201
    body = r.json()
    assert body["is_retarget"] is True
    assert body["research_mode"] == "none"   # forced
    assert body["status"] == "previewing"    # launched, not stranded in draft
    new_id = body["id"]
    assert kicked == [new_id]
    n = await db_session.scalar(select(func.count()).select_from(Lead).where(Lead.campaign_id == new_id))
    assert n == 2
