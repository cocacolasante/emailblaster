"""A provider rejecting a workspace's key is detected, surfaced and held.

Regression for 2026-10: Brevo's "authorised IPs" lock started 401-ing the
bootstrap workspace after its public IP changed.  The 401 was retried as a
transient error forever, nothing was recorded, and the workspace silently
stopped sending follow-ups and pulling opens/clicks for ten days.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
from sqlalchemy import select

from app.models import (
    LeadSequenceState,
    LeadSequenceStatus,
    Notification,
    SendStatus,
    TenantProviderKey,
)
from app.services import brevo, credentials, integration_health
from app.tenancy.keys import integration_broken_key
from app.workers import brevo_events_poller as poller
from app.workers import send as send_mod
from app.workers import sequencer

from tests.test_phase9_send_task import _make_campaign, _make_lead
from tests import test_phase16_sequencer as seq_t

IP_MESSAGE = (
    "We have detected you are using an unrecognised IP address 72.78.98.32. "
    "If you performed this action make sure to add the new IP address in this link: "
    "https://app.brevo.com/security/authorised_ips"
)


def _auth_error(status: int = 401) -> brevo.BrevoAuthError:
    req = httpx.Request("POST", brevo.BREVO_URL)
    resp = httpx.Response(status, json={"message": IP_MESSAGE, "code": "unauthorized"}, request=req)
    return brevo.BrevoAuthError("Brevo rejected the API key", request=req, response=resp)


@pytest.fixture
def redis_client(fake_redis_server):
    return fakeredis.aioredis.FakeRedis(server=fake_redis_server, decode_responses=True)


@pytest.fixture(autouse=True)
def _brevo_configured(set_creds):
    set_creds("brevo", api_key="test-key")


@pytest.fixture
async def brevo_row(db_session):
    row = TenantProviderKey(
        provider="brevo",
        encrypted_credentials=credentials.encrypt_fields(
            {"api_key": "test-key", "sender_email": "a@b.co", "sender_name": "A"}),
        preview="••••-key",
        last_test_status="ok",
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _row_status(db_session) -> tuple[str | None, str | None]:
    row = await db_session.scalar(
        select(TenantProviderKey).where(TenantProviderKey.provider == "brevo"))
    await db_session.refresh(row)
    return row.last_test_status, row.last_test_error


# --------------------------------------------------------------------------
# Brevo client
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_brevo_auth_statuses_raise_brevo_auth_error(status):
    req = httpx.Request("GET", brevo.BREVO_EVENTS_URL)
    resp = httpx.Response(status, json={"message": IP_MESSAGE}, request=req)
    with pytest.raises(brevo.BrevoAuthError) as exc:
        brevo._raise_for_status(resp)
    assert exc.value.brevo_message == IP_MESSAGE
    # Still an HTTPStatusError, so existing broad handlers keep working.
    assert isinstance(exc.value, httpx.HTTPStatusError)


def test_brevo_other_errors_stay_plain_http_errors():
    req = httpx.Request("GET", brevo.BREVO_EVENTS_URL)
    resp = httpx.Response(500, text="boom", request=req)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        brevo._raise_for_status(resp)
    assert not isinstance(exc.value, brevo.BrevoAuthError)


# --------------------------------------------------------------------------
# First email (send_lead)
# --------------------------------------------------------------------------


async def test_send_lead_rejected_key_flags_workspace_and_keeps_lead_pending(
    db_session, brevo_row, redis_client,
):
    campaign = await _make_campaign(db_session)
    lead = await _make_lead(db_session, campaign)

    with patch.object(send_mod, "_new_redis", return_value=redis_client), \
         patch.object(send_mod.brevo, "send_email", AsyncMock(side_effect=_auth_error())):
        result = await send_mod.send_lead_async(str(lead.id))

    # Not raised (no Celery retry loop), not FAILED — the lead just waits.
    assert result["status"] == "integration_error"
    await db_session.refresh(lead)
    assert lead.send_status == SendStatus.PENDING

    assert await _row_status(db_session) == ("failed", IP_MESSAGE)
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert len(notes) == 1
    assert "Brevo" in notes[0].title and IP_MESSAGE in (notes[0].body or "")
    assert notes[0].user_id is None  # workspace-wide
    assert await redis_client.ttl(integration_broken_key("brevo")) > 0


async def test_send_gate_holds_sends_while_flagged_without_claiming_a_slot(
    db_session, redis_client,
):
    campaign = await _make_campaign(db_session, min_delay_seconds=120)
    lead = await _make_lead(db_session, campaign)
    await redis_client.set(integration_broken_key("brevo"), "x", ex=300)

    send = AsyncMock(return_value="mid")
    with patch.object(send_mod, "_new_redis", return_value=redis_client), \
         patch.object(send_mod.brevo, "send_email", send):
        result = await send_mod.send_lead_async(str(lead.id))

    assert result["status"] == "rate_limited"
    assert result["reason"] == "integration_error"
    assert 0 < result["retry_in"] <= 300
    send.assert_not_called()
    # The hold sits before the min-delay claim, so no window is burned.
    assert await redis_client.get(f"rate:{campaign.id}:min_gate") is None


async def test_repeat_rejections_notify_once_a_day(db_session, brevo_row):
    await integration_health.mark_broken("brevo", "nope")
    await integration_health.mark_broken("brevo", "nope again")
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert len(notes) == 1
    assert await _row_status(db_session) == ("failed", "nope again")


# --------------------------------------------------------------------------
# Events poller = health probe
# --------------------------------------------------------------------------


async def test_poller_flags_rejected_key_then_recovers(
    db_session, brevo_row, redis_client, monkeypatch,
):
    async def rejected(**kw):
        raise _auth_error()

    monkeypatch.setattr(poller.brevo, "fetch_events", rejected)
    monkeypatch.setattr(poller, "_new_redis", lambda: fakeredis.aioredis.FakeRedis(
        decode_responses=True))
    counts = await poller._poll_async()
    assert counts["auth_failed"] == 1
    assert await _row_status(db_session) == ("failed", IP_MESSAGE)
    assert await redis_client.exists(integration_broken_key("brevo"))

    async def ok(**kw):
        return []

    monkeypatch.setattr(poller.brevo, "fetch_events", ok)
    await poller._poll_async()
    assert await _row_status(db_session) == ("ok", None)
    assert not await redis_client.exists(integration_broken_key("brevo"))


# --------------------------------------------------------------------------
# Follow-up email step (sequencer)
# --------------------------------------------------------------------------


async def test_followup_rejected_key_defers_without_burning_retries(db_session, monkeypatch):
    campaign = await seq_t._make_campaign(db_session)
    campaign.min_delay_seconds = 0
    _, _, _, followup = await seq_t._build_three_node_sequence(db_session, campaign)
    lead = await seq_t._make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    db_session.add(LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=seq_t._now(), entered_current_at=seq_t._now(),
    ))
    await db_session.commit()

    async def rejected(**kwargs):
        raise _auth_error()

    monkeypatch.setattr(sequencer.brevo, "send_email", rejected)
    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result == {
        "status": "deferred",
        "reason": "integration_error",
        "retry_in": integration_health.BROKEN_TTL_SECONDS,
    }

    # The deferral parks the lead on the follow-up instead of advancing it.
    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    state = await db_session.scalar(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id))
    await db_session.refresh(state)
    assert state.current_node_id == followup.id
    assert state.status == LeadSequenceStatus.ACTIVE
    assert state.next_run_at > seq_t._now()
