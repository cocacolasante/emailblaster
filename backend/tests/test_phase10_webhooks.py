"""Phase 10: Brevo webhook + one-click unsubscribe."""
import uuid
from datetime import time

from sqlalchemy import select

from app.models import (
    Campaign,
    EmailEvent,
    EmailEventType,
    Lead,
    Suppression,
    SuppressionReason,
)


async def _make_campaign_and_lead(db_session, brevo_id: str = "msg-xyz") -> tuple[Campaign, Lead]:
    c = Campaign(
        name="P10",
        goal="g", tone="t",
        sender_name="s", sender_email="s@x.com",
        sample_count=1,
        schedule_days=[0, 1, 2, 3, 4],
        schedule_time_start=time(9, 0),
        schedule_time_end=time(17, 0),
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    l = Lead(
        campaign_id=c.id, email="lead@x.com",
        brevo_message_id=brevo_id,
    )
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return c, l


# ---------- Brevo webhook ----------


async def test_webhook_records_delivered_event(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    resp = await client.post("/webhooks/brevo", json=[{
        "event": "delivered",
        "message-id": lead.brevo_message_id,
        "email": "lead@x.com",
    }])
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "processed": 1}

    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == EmailEventType.DELIVERED
    assert events[0].lead_id == lead.id


async def test_webhook_records_opened_and_clicked(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[
        {"event": "opened", "message-id": lead.brevo_message_id},
        {"event": "clicked", "message-id": lead.brevo_message_id},
    ])
    types = [e.event_type for e in (await db_session.execute(select(EmailEvent))).scalars().all()]
    assert EmailEventType.OPENED in types
    assert EmailEventType.CLICKED in types


async def test_hard_bounce_adds_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[{
        "event": "hard_bounce",
        "message-id": lead.brevo_message_id,
    }])
    sup = await db_session.scalar(select(Suppression).where(Suppression.email == "lead@x.com"))
    assert sup is not None
    assert sup.reason == SuppressionReason.HARD_BOUNCE


async def test_spam_complaint_adds_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[{
        "event": "spam", "message-id": lead.brevo_message_id,
    }])
    sup = await db_session.scalar(select(Suppression).where(Suppression.email == "lead@x.com"))
    assert sup is not None
    assert sup.reason == SuppressionReason.SPAM


async def test_unsubscribe_event_adds_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[{
        "event": "unsubscribed", "message-id": lead.brevo_message_id,
    }])
    sup = await db_session.scalar(select(Suppression).where(Suppression.email == "lead@x.com"))
    assert sup is not None
    assert sup.reason == SuppressionReason.UNSUBSCRIBED


async def test_soft_bounce_does_NOT_add_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[{
        "event": "soft_bounce", "message-id": lead.brevo_message_id,
    }])
    sup = await db_session.scalar(select(Suppression).where(Suppression.email == "lead@x.com"))
    assert sup is None


async def test_webhook_ignores_unknown_event_type(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    resp = await client.post("/webhooks/brevo", json=[{
        "event": "something_new", "message-id": lead.brevo_message_id,
    }])
    assert resp.status_code == 200
    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert events == []


async def test_webhook_ignores_unknown_message_id(client, db_session):
    await _make_campaign_and_lead(db_session)
    resp = await client.post("/webhooks/brevo", json=[{
        "event": "opened", "message-id": "not-in-db",
    }])
    assert resp.status_code == 200
    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert events == []


async def test_webhook_strips_angle_brackets_from_message_id(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session, brevo_id="abc-123")
    await client.post("/webhooks/brevo", json=[{
        "event": "opened", "message-id": "<abc-123>",
    }])
    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert len(events) == 1


async def test_webhook_handles_single_object_payload(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    resp = await client.post("/webhooks/brevo", json={
        "event": "delivered", "message-id": lead.brevo_message_id,
    })
    assert resp.status_code == 200
    assert resp.json()["processed"] == 1


async def test_duplicate_event_only_adds_one_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    await client.post("/webhooks/brevo", json=[
        {"event": "hard_bounce", "message-id": lead.brevo_message_id},
        {"event": "hard_bounce", "message-id": lead.brevo_message_id},
    ])
    sups = (await db_session.execute(select(Suppression).where(Suppression.email == "lead@x.com"))).scalars().all()
    assert len(sups) == 1


# ---------- Unsubscribe link ----------


async def test_unsubscribe_renders_confirmation_and_adds_suppression(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    resp = await client.get(f"/unsubscribe/{lead.id}")
    assert resp.status_code == 200
    assert "unsubscribed" in resp.text.lower()

    sup = await db_session.scalar(select(Suppression).where(Suppression.email == "lead@x.com"))
    assert sup is not None
    assert sup.reason == SuppressionReason.UNSUBSCRIBED

    # Event also recorded
    events = (await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))).scalars().all()
    assert any(e.event_type == EmailEventType.UNSUBSCRIBED for e in events)


async def test_unsubscribe_unknown_lead_returns_404_html(client):
    resp = await client.get(f"/unsubscribe/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert "invalid" in resp.text.lower()


async def test_unsubscribe_idempotent_when_already_suppressed(client, db_session):
    _, lead = await _make_campaign_and_lead(db_session)
    db_session.add(Suppression(email="lead@x.com", reason=SuppressionReason.MANUAL))
    await db_session.commit()

    resp = await client.get(f"/unsubscribe/{lead.id}")
    assert resp.status_code == 200
    # No duplicate suppression
    sups = (await db_session.execute(select(Suppression).where(Suppression.email == "lead@x.com"))).scalars().all()
    assert len(sups) == 1
    # First reason preserved
    assert sups[0].reason == SuppressionReason.MANUAL
