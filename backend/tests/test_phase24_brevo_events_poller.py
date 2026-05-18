"""Tests for the Brevo events poller (replaces the inbound webhook).

We swap ``brevo.fetch_events`` for a stub so no real network call is made;
the poller is otherwise exercised end-to-end against the test DB and a
real (fakeredis) Redis.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.models import (
    Campaign,
    EmailEvent,
    EmailEventType,
    Lead,
    Suppression,
    SuppressionReason,
)
from app.workers import brevo_events_poller as poller


@pytest.fixture(autouse=True)
async def _clear_watermark():
    """Each test sets up its own scenario from a clean watermark.  Redis
    is shared across tests so we explicitly delete the key before each."""
    client = poller._new_redis()
    try:
        await client.delete(poller.WATERMARK_KEY)
        yield
    finally:
        await client.delete(poller.WATERMARK_KEY)
        await client.aclose()


async def _make_lead_with_message(db_session, msg_id: str) -> Lead:
    c = Campaign(
        name="P24", goal="g", tone="t",
        sender_name="s", sender_email="s@x.com",
        sample_count=1,
        schedule_days=[0, 1, 2, 3, 4],
        schedule_time_start=time(9, 0),
        schedule_time_end=time(17, 0),
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    lead = Lead(campaign_id=c.id, email=f"{msg_id}@x.com", brevo_message_id=msg_id)
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)
    return lead


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def test_poller_processes_new_events(db_session, monkeypatch):
    lead = await _make_lead_with_message(db_session, "msg-aaa")
    now = datetime.now(timezone.utc)
    fake_events = [
        {"event": "delivered", "messageId": "msg-aaa", "date": _iso(now - timedelta(minutes=2))},
        {"event": "opened",    "messageId": "msg-aaa", "date": _iso(now - timedelta(minutes=1))},
    ]
    async def fake_fetch(**kw):
        return fake_events
    monkeypatch.setattr(poller.brevo, "fetch_events", fake_fetch)

    counts = await poller._poll_async()
    assert counts["fetched"] == 2
    assert counts["processed"] == 2

    types = {e.event_type for e in (await db_session.execute(select(EmailEvent))).scalars().all()}
    assert EmailEventType.DELIVERED in types
    assert EmailEventType.OPENED in types


async def test_poller_skips_events_older_than_watermark(db_session, monkeypatch):
    """A second poll on the same window must not re-record events the
    first poll already processed.  The poller maintains a Redis watermark
    and skips events with date <= watermark."""
    lead = await _make_lead_with_message(db_session, "msg-bbb")
    now = datetime.now(timezone.utc)
    fake_events = [
        {"event": "delivered", "messageId": "msg-bbb", "date": _iso(now - timedelta(minutes=2))},
    ]
    async def fake_fetch(**kw):
        return fake_events
    monkeypatch.setattr(poller.brevo, "fetch_events", fake_fetch)

    counts1 = await poller._poll_async()
    assert counts1["processed"] == 1

    # Second tick — no new events.
    counts2 = await poller._poll_async()
    assert counts2["processed"] == 0
    assert counts2["skipped_old"] >= 1

    # Exactly one event row persists.
    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert len(events) == 1


async def test_poller_adds_suppression_on_hard_bounce(db_session, monkeypatch):
    lead = await _make_lead_with_message(db_session, "msg-ccc")
    now = datetime.now(timezone.utc)
    async def fake_fetch(**kw):
        return [{"event": "hardBounce", "messageId": "msg-ccc", "date": _iso(now)}]
    monkeypatch.setattr(poller.brevo, "fetch_events", fake_fetch)

    await poller._poll_async()

    sup = await db_session.scalar(select(Suppression).where(Suppression.email == lead.email))
    assert sup is not None
    assert sup.reason == SuppressionReason.HARD_BOUNCE


async def test_poller_handles_fetch_failure_gracefully(db_session, monkeypatch):
    """A Brevo API failure must not crash the worker or move the watermark
    forward (so the next tick retries the same window)."""
    await _make_lead_with_message(db_session, "msg-ddd")
    async def boom(**kw):
        raise RuntimeError("brevo is down")
    monkeypatch.setattr(poller.brevo, "fetch_events", boom)

    counts = await poller._poll_async()
    assert counts == {"fetched": 0, "processed": 0, "skipped_old": 0}


async def test_poller_normalises_brevo_api_event_names(db_session, monkeypatch):
    """The events API returns ``hardBounce`` (camelCase); the webhook used
    ``hard_bounce``.  Both map to HARD_BOUNCE."""
    lead = await _make_lead_with_message(db_session, "msg-eee")
    now = datetime.now(timezone.utc)
    async def fake_fetch(**kw):
        return [
            {"event": "hardBounce", "messageId": "msg-eee", "date": _iso(now)},
        ]
    monkeypatch.setattr(poller.brevo, "fetch_events", fake_fetch)

    counts = await poller._poll_async()
    assert counts["processed"] == 1
    events = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert any(e.event_type == EmailEventType.HARD_BOUNCE for e in events)
