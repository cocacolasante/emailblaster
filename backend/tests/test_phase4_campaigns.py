"""Phase 4: Campaign CRUD, status transitions, stats, paginated leads."""
import uuid
from datetime import time

import pytest

from app.models import (
    Campaign,
    CampaignStatus,
    ConnectedAccount,
    EmailEvent,
    EmailEventType,
    Lead,
    SendStatus,
)


def _campaign_payload(**overrides) -> dict:
    base = {
        "name": "Q2 outreach",
        "goal": "Book a 30-minute discovery call",
        "tone": "Professional",
        "sender_name": "Anthony",
        "sender_email": "anthony@example.com",
        "research_mode": "fast",
        "sample_count": 5,
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "America/New_York",
        "max_per_hour": 50,
        "max_per_day": 400,
        "min_delay_seconds": 60,
    }
    base.update(overrides)
    return base


async def _make_account(db_session, email="me@gmail.com") -> ConnectedAccount:
    acc = ConnectedAccount(
        label="Work Gmail",
        email_address=email,
        imap_host="imap.gmail.com",
        username=email,
        password_encrypted="ciphertext",
    )
    db_session.add(acc)
    await db_session.commit()
    await db_session.refresh(acc)
    return acc


# ---------- Create + validation ----------


async def test_create_campaign_minimum_fields(client):
    resp = await client.post("/campaigns/", json=_campaign_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["connected_account_configured"] is False
    assert body["connected_account"] is None
    assert body["lead_counts"]["total"] == 0
    assert body["stats"]["sent_count"] == 0
    assert body["stats"]["reply_rate"] is None
    assert body["stats"]["reply_tracking_note"] == "reply tracking not configured"


async def test_create_with_connected_account_links_and_enables_reply_tracking(client, db_session):
    acc = await _make_account(db_session)
    resp = await client.post(
        "/campaigns/", json=_campaign_payload(connected_account_id=str(acc.id))
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["connected_account_configured"] is True
    assert body["connected_account"]["label"] == "Work Gmail"
    assert body["connected_account"]["email_address"] == "me@gmail.com"
    assert body["stats"]["reply_tracking_note"] is None
    assert body["stats"]["reply_rate"] is None  # no sends yet, so still None


async def test_create_rejects_bad_day_values(client):
    resp = await client.post("/campaigns/", json=_campaign_payload(schedule_days=[0, 7]))
    assert resp.status_code == 422
    assert "0-6" in resp.text


async def test_create_rejects_inverted_time_window(client):
    resp = await client.post(
        "/campaigns/",
        json=_campaign_payload(schedule_time_start="17:00:00", schedule_time_end="09:00:00"),
    )
    assert resp.status_code == 422


async def test_create_rejects_sample_count_zero(client):
    resp = await client.post("/campaigns/", json=_campaign_payload(sample_count=0))
    assert resp.status_code == 422


async def test_create_rejects_unknown_connected_account(client):
    resp = await client.post(
        "/campaigns/",
        json=_campaign_payload(connected_account_id=str(uuid.uuid4())),
    )
    assert resp.status_code == 422


# ---------- Read ----------


async def test_list_returns_campaigns_with_summary(client):
    await client.post("/campaigns/", json=_campaign_payload(name="A"))
    await client.post("/campaigns/", json=_campaign_payload(name="B"))

    resp = await client.get("/campaigns/")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    for c in body:
        assert "lead_counts" in c
        assert "stats" in c
        assert "status" in c


async def test_get_single_includes_stats(client):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    resp = await client.get(f"/campaigns/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["lead_counts"]["total"] == 0


async def test_get_not_found(client):
    resp = await client.get(f"/campaigns/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------- Update gating ----------


async def test_patch_updates_fields_in_draft(client):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    resp = await client.patch(
        f"/campaigns/{created['id']}", json={"name": "Renamed", "tone": "Friendly"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["tone"] == "Friendly"


@pytest.mark.parametrize("blocked_status", ["running", "paused", "complete", "approved"])
async def test_patch_rejected_outside_draft_or_previewing(
    client, db_session, blocked_status
):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    # Bump the status via direct DB write (no API for that yet).
    c = await db_session.get(Campaign, uuid.UUID(created["id"]))
    c.status = CampaignStatus(blocked_status)
    await db_session.commit()

    resp = await client.patch(f"/campaigns/{created['id']}", json={"name": "X"})
    assert resp.status_code == 409


async def test_patch_validates_time_order_when_both_provided(client):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    resp = await client.patch(
        f"/campaigns/{created['id']}",
        json={"schedule_time_start": "18:00:00", "schedule_time_end": "08:00:00"},
    )
    assert resp.status_code == 422


# ---------- Delete + cascade ----------


async def test_delete_lead_removes_lead_and_cascades_child_rows(client, db_session):
    """DELETE /campaigns/{id}/leads/{lid} drops the lead row and every
    cascading child (sequence state, step executions, email events).
    Future sequencer dispatches for this lead get short-circuited by the
    existing ``lead is None`` guard in the workers."""
    from app.models import LeadSequenceState, LeadStepExecution, LeadStepResult
    from sqlalchemy import select

    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    cid = uuid.UUID(created["id"])

    lead = Lead(campaign_id=cid, email="bye@y.com")
    other = Lead(campaign_id=cid, email="stay@y.com")
    db_session.add_all([lead, other])
    await db_session.flush()
    db_session.add_all([
        EmailEvent(lead_id=lead.id, campaign_id=cid, event_type=EmailEventType.DELIVERED),
        EmailEvent(lead_id=other.id, campaign_id=cid, event_type=EmailEventType.DELIVERED),
    ])
    await db_session.commit()
    lead_id, other_id = lead.id, other.id

    resp = await client.delete(f"/campaigns/{created['id']}/leads/{lead_id}")
    assert resp.status_code == 204

    # Target lead + its events: gone.
    assert (await db_session.scalar(select(Lead).where(Lead.id == lead_id))) is None
    assert (await db_session.scalar(
        select(EmailEvent).where(EmailEvent.lead_id == lead_id)
    )) is None
    # Sibling lead is untouched.
    assert (await db_session.scalar(select(Lead).where(Lead.id == other_id))) is not None


async def test_delete_lead_404_when_lead_belongs_to_different_campaign(client, db_session):
    """A request to delete a lead through a campaign URL that doesn't
    own it must 404 — defends against cross-campaign lead-id guessing."""
    c1 = (await client.post("/campaigns/", json=_campaign_payload(name="C1"))).json()
    c2 = (await client.post("/campaigns/", json=_campaign_payload(name="C2"))).json()

    lead = Lead(campaign_id=uuid.UUID(c1["id"]), email="lead@c1.com")
    db_session.add(lead)
    await db_session.commit()

    # Try to delete c1's lead through c2's URL.
    resp = await client.delete(f"/campaigns/{c2['id']}/leads/{lead.id}")
    assert resp.status_code == 404

    # Lead is still there.
    from sqlalchemy import select
    assert (await db_session.scalar(select(Lead).where(Lead.id == lead.id))) is not None


async def test_delete_lead_404_when_lead_missing(client):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    resp = await client.delete(f"/campaigns/{created['id']}/leads/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_campaign_cascades_to_leads_and_events(client, db_session):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    cid = uuid.UUID(created["id"])

    lead = Lead(campaign_id=cid, email="x@y.com")
    db_session.add(lead)
    await db_session.flush()
    ev = EmailEvent(lead_id=lead.id, campaign_id=cid, event_type=EmailEventType.OPENED)
    db_session.add(ev)
    await db_session.commit()
    lead_id, ev_id = lead.id, ev.id

    resp = await client.delete(f"/campaigns/{created['id']}")
    assert resp.status_code == 204

    from sqlalchemy import select

    assert (await db_session.scalar(select(Lead).where(Lead.id == lead_id))) is None
    assert (await db_session.scalar(select(EmailEvent).where(EmailEvent.id == ev_id))) is None


# ---------- Pause / resume ----------


async def test_pause_only_works_when_running(client, db_session):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    # draft → pause should 409
    resp = await client.post(f"/campaigns/{created['id']}/pause")
    assert resp.status_code == 409

    c = await db_session.get(Campaign, uuid.UUID(created["id"]))
    c.status = CampaignStatus.RUNNING
    await db_session.commit()

    resp = await client.post(f"/campaigns/{created['id']}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


async def test_resume_only_works_when_paused(client, db_session):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    # draft → resume should 409
    resp = await client.post(f"/campaigns/{created['id']}/resume")
    assert resp.status_code == 409

    c = await db_session.get(Campaign, uuid.UUID(created["id"]))
    c.status = CampaignStatus.PAUSED
    await db_session.commit()

    resp = await client.post(f"/campaigns/{created['id']}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# ---------- Stats wiring ----------


async def test_stats_aggregate_lead_counts_and_event_rates(client, db_session):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    cid = uuid.UUID(created["id"])

    # 10 leads: 5 sent, 3 pending, 2 failed
    leads_sent = []
    for i in range(10):
        if i < 5:
            s = SendStatus.SENT
        elif i < 8:
            s = SendStatus.PENDING
        else:
            s = SendStatus.FAILED
        l = Lead(campaign_id=cid, email=f"l{i}@x.com", send_status=s)
        db_session.add(l)
        if s == SendStatus.SENT:
            leads_sent.append(l)
    await db_session.flush()

    # 3 opens, 2 clicks, 1 hard bounce (counted distinct per lead)
    for l in leads_sent[:3]:
        db_session.add(EmailEvent(lead_id=l.id, campaign_id=cid, event_type=EmailEventType.OPENED))
        # Duplicate open should still count as 1 lead
        db_session.add(EmailEvent(lead_id=l.id, campaign_id=cid, event_type=EmailEventType.OPENED))
    for l in leads_sent[:2]:
        db_session.add(EmailEvent(lead_id=l.id, campaign_id=cid, event_type=EmailEventType.CLICKED))
    db_session.add(
        EmailEvent(lead_id=leads_sent[4].id, campaign_id=cid, event_type=EmailEventType.HARD_BOUNCE)
    )
    await db_session.commit()

    body = (await client.get(f"/campaigns/{created['id']}")).json()
    counts = body["lead_counts"]
    stats = body["stats"]

    assert counts == {"total": 10, "pending": 3, "scheduled": 0, "sent": 5, "failed": 2}
    assert stats["sent_count"] == 5
    assert stats["opened"] == 3
    assert stats["clicked"] == 2
    assert stats["bounced"] == 1
    assert stats["open_rate"] == 0.6
    assert stats["click_rate"] == 0.4
    assert stats["bounce_rate"] == 0.2
    # No connected account → reply tracking disabled
    assert stats["reply_rate"] is None
    assert stats["reply_tracking_note"] == "reply tracking not configured"


async def test_reply_rate_computed_when_connected_account_present(client, db_session):
    acc = await _make_account(db_session)
    created = (
        await client.post("/campaigns/", json=_campaign_payload(connected_account_id=str(acc.id)))
    ).json()
    cid = uuid.UUID(created["id"])

    leads = []
    for i in range(4):
        l = Lead(campaign_id=cid, email=f"l{i}@x.com", send_status=SendStatus.SENT)
        db_session.add(l)
        leads.append(l)
    await db_session.flush()
    db_session.add(EmailEvent(lead_id=leads[0].id, campaign_id=cid, event_type=EmailEventType.REPLIED))
    await db_session.commit()

    body = (await client.get(f"/campaigns/{created['id']}")).json()
    assert body["stats"]["reply_rate"] == 0.25
    assert body["stats"]["replied"] == 1
    assert body["stats"]["reply_tracking_note"] is None


# ---------- Paginated leads ----------


async def test_leads_pagination_and_filter_and_search(client, db_session):
    created = (await client.post("/campaigns/", json=_campaign_payload())).json()
    cid = uuid.UUID(created["id"])

    # 12 leads with mixed statuses + names
    for i in range(12):
        send_st = SendStatus.SENT if i % 2 == 0 else SendStatus.PENDING
        db_session.add(Lead(
            campaign_id=cid,
            email=f"lead{i:02d}@example.com",
            first_name=f"First{i}",
            last_name="Smith" if i < 6 else "Jones",
            send_status=send_st,
        ))
    await db_session.commit()

    # Page 1, default page_size=50 — returns all 12
    r = (await client.get(f"/campaigns/{created['id']}/leads")).json()
    assert r["total"] == 12
    assert r["page"] == 1
    assert len(r["items"]) == 12
    assert r["total_pages"] == 1

    # Page 1 with page_size=5
    r = (await client.get(f"/campaigns/{created['id']}/leads?page=1&page_size=5")).json()
    assert r["total"] == 12
    assert len(r["items"]) == 5
    assert r["total_pages"] == 3

    # Filter by send_status=sent → 6 results
    r = (await client.get(f"/campaigns/{created['id']}/leads?send_status=sent")).json()
    assert r["total"] == 6
    assert all(item["send_status"] == "sent" for item in r["items"])

    # Search by last name
    r = (await client.get(f"/campaigns/{created['id']}/leads?search=Smith")).json()
    assert r["total"] == 6
    assert all(item["last_name"] == "Smith" for item in r["items"])

    # Search by email substring
    r = (await client.get(f"/campaigns/{created['id']}/leads?search=lead01")).json()
    assert r["total"] == 1


async def test_leads_endpoint_404_for_unknown_campaign(client):
    resp = await client.get(f"/campaigns/{uuid.uuid4()}/leads")
    assert resp.status_code == 404
