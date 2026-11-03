"""A person who replies leaves every automated sequence — positive or negative,
in every campaign that holds their address, on every send path."""
from __future__ import annotations

import email.message
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.models import (
    EmailEvent,
    EmailEventType,
    LeadSequenceState,
    LeadSequenceStatus,
    LinkedInAccount,
    LinkedInAccountStatus,
    SendStatus,
)
from app.routers import webhooks
from app.services import imap_client, reply_stop
from app.services.linkedin.base import InboundEvent
from app.tenancy.context import tenant_scope
from app.workers import linkedin_poller, send as send_mod, sequencer
from app.workers.reply_poller import poll_account_for_replies
from tests import test_phase10_reply_poller as poll_t
from tests import test_phase16_sequencer as seq_t
from tests.conftest import create_test_user


def _msg(from_email="lead@example.com", subject="Re: hello", **extra) -> dict:
    return {
        "uid": "1", "message_id": f"mid-{subject}", "in_reply_to": "", "references": [],
        "subject": subject, "from_email": from_email, "body_text": "thanks", **extra,
    }


async def _enrolled(db_session, *, email="lead@example.com", sent=True, node_index=3):
    """A running campaign with one lead sitting on its follow-up node."""
    campaign = await seq_t._make_campaign(db_session)
    campaign.min_delay_seconds = 0
    nodes = await seq_t._build_three_node_sequence(db_session, campaign)
    followup = nodes[node_index]
    lead = await seq_t._make_lead(db_session, campaign, email=email)
    lead.send_status = SendStatus.SENT if sent else SendStatus.PENDING
    db_session.add(LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=seq_t._now(), entered_current_at=seq_t._now(),
    ))
    await db_session.commit()
    return campaign, lead, followup


async def _state(db_session, lead) -> LeadSequenceState:
    state = await db_session.scalar(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id))
    await db_session.refresh(state)
    return state


async def _poll(db_session, campaign, *messages):
    acc = await poll_t._make_account(db_session)
    with patch("app.workers.reply_poller.imap_client.fetch_recent_with_account",
               return_value=list(messages)), \
         patch("app.workers.reply_poller.settings.AGENT_ENABLED", False):
        result = await poll_account_for_replies(acc, [campaign.id], db_session)
    await db_session.commit()
    return result


# --- the moment a reply is recorded ------------------------------------------


@pytest.mark.parametrize("body", ["Yes, let's talk", "Not interested, stop emailing me"])
async def test_any_reply_halts_the_sequence(db_session, body):
    campaign, lead, _ = await _enrolled(db_session)
    result = await _poll(db_session, campaign, _msg(body_text=body))
    assert result["replies_found"] == 1
    state = await _state(db_session, lead)
    assert state.status == LeadSequenceStatus.HALTED
    assert state.halt_reason == "lead replied" and state.next_run_at is None


async def test_reply_stops_the_same_person_in_other_campaigns(db_session):
    """Regression: a retarget campaign re-contacted people who had answered."""
    campaign, lead, _ = await _enrolled(db_session)
    _, other_active, _ = await _enrolled(db_session, email="LEAD@example.com")
    _, other_unsent, _ = await _enrolled(db_session, email="lead@example.com", sent=False)

    await _poll(db_session, campaign, _msg())

    assert (await _state(db_session, other_active)).status == LeadSequenceStatus.HALTED
    assert (await _state(db_session, other_unsent)).status == LeadSequenceStatus.HALTED
    await db_session.refresh(other_unsent)
    assert other_unsent.send_status == SendStatus.SUPPRESSED
    await db_session.refresh(other_active)
    assert other_active.send_status == SendStatus.SENT  # history stays as it was


async def test_reply_to_a_follow_up_matches_by_sender(db_session):
    """A new-thread follow-up has its own Message-ID and subject."""
    campaign, lead, _ = await _enrolled(db_session)
    result = await _poll(db_session, campaign, _msg(from_email="lead@example.com",
                                                    subject="Re: a different follow-up"))
    assert result["replies_found"] == 1
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.HALTED


async def test_out_of_office_does_not_stop_or_count(db_session):
    campaign, lead, _ = await _enrolled(db_session)
    result = await _poll(db_session, campaign, _msg(subject="Automatic reply: hello", auto_reply=True))
    assert result["replies_found"] == 0
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.ACTIVE
    assert (await db_session.execute(select(EmailEvent))).scalars().all() == []


def _headers(**h):
    m = email.message.Message()
    for k, v in h.items():
        m[k.replace("_", "-")] = v
    return m


@pytest.mark.parametrize("headers,expected", [
    ({"Subject": "Automatic reply: Grant funding"}, True),
    ({"Subject": "Out of office"}, True),
    ({"Subject": "Re: hello", "Auto_Submitted": "auto-replied"}, True),
    ({"Subject": "Re: hello", "Auto_Submitted": "no"}, False),
    ({"Subject": "Re: I am out of office next week but interested"}, False),
    ({"Subject": "REMOVE"}, False),
])
def test_is_auto_reply(headers, expected):
    assert imap_client.is_auto_reply(_headers(**headers)) is expected


# --- at send time ------------------------------------------------------------


async def _record_reply(db_session, lead):
    db_session.add(EmailEvent(lead_id=lead.id, campaign_id=lead.campaign_id,
                              event_type=EmailEventType.REPLIED))
    await db_session.commit()


async def test_first_email_is_not_sent_to_someone_who_replied_elsewhere(db_session, set_creds):
    """Enrolled after the reply, so nothing halted them: the gate must."""
    set_creds("brevo", api_key="k")
    _, earlier, _ = await _enrolled(db_session)
    await _record_reply(db_session, earlier)
    campaign, lead, _ = await _enrolled(db_session, sent=False)
    lead.composed_subject, lead.composed_body = "Hi", "Hello there"
    await db_session.commit()

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    send = AsyncMock(return_value="mid")
    with patch.object(send_mod, "_new_redis", return_value=redis_client), \
         patch.object(send_mod.brevo, "send_email", send):
        result = await send_mod.send_lead_async(str(lead.id))

    assert result == {"status": "replied"}
    send.assert_not_called()
    await db_session.refresh(lead)
    assert lead.send_status == SendStatus.SUPPRESSED
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.HALTED


async def test_queued_follow_up_stops_if_a_reply_landed_after_dispatch(db_session, monkeypatch):
    campaign, lead, followup = await _enrolled(db_session)
    await _record_reply(db_session, lead)  # state still ACTIVE: dispatched just before

    send = AsyncMock(return_value="mid")
    monkeypatch.setattr(sequencer.brevo, "send_email", send)
    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result == {"status": "replied"}
    send.assert_not_called()

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    state = await _state(db_session, lead)
    assert state.status == LeadSequenceStatus.HALTED and state.halt_reason == "lead replied"
    assert state.current_node_id == followup.id


async def test_linkedin_step_stops_after_an_email_reply(db_session):
    _, lead, followup = await _enrolled(db_session)
    await _record_reply(db_session, lead)
    assert await sequencer._send_linkedin_step_async(str(lead.id), str(followup.id)) == {"status": "replied"}


async def test_linkedin_reply_counts_even_without_an_email(db_session):
    _, lead, _ = await _enrolled(db_session)
    lead.email = None
    lead.linkedin_last_reply_at = seq_t._now()
    await db_session.commit()
    assert await reply_stop.has_replied(db_session, lead) is True
    assert await reply_stop.stop_on_reply(db_session, lead) == 1
    await db_session.commit()
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.HALTED


async def test_no_reply_means_no_stop(db_session):
    _, lead, _ = await _enrolled(db_session)
    _, other, _ = await _enrolled(db_session, email="someone-else@example.com")
    await _record_reply(db_session, other)
    assert await reply_stop.has_replied(db_session, lead) is False


async def _linkedin_account(db_session, campaign, lead, unipile_id="uni-1"):
    acc = LinkedInAccount(label="t", linkedin_email="t@x.com", provider_kind="unipile",
                          unipile_account_id=unipile_id, status=LinkedInAccountStatus.OK)
    db_session.add(acc)
    await db_session.commit()
    campaign.linkedin_account_id = acc.id
    lead.linkedin_url = "https://www.linkedin.com/in/lead-slug"
    await db_session.commit()
    return acc


async def test_linkedin_poller_reply_stops_the_sequence(db_session, monkeypatch):
    campaign, lead, _ = await _enrolled(db_session)
    acc = await _linkedin_account(db_session, campaign, lead)
    provider = SimpleNamespace(inbox_recent_events=AsyncMock(return_value=[InboundEvent(
        kind="message_received", from_public_id="lead-slug", from_urn=None,
        occurred_at=seq_t._now(), message_text="no thanks",
    )]))
    monkeypatch.setattr(linkedin_poller, "get_provider", lambda: provider)

    counts = await linkedin_poller._poll_account(db_session, acc)
    await db_session.commit()

    assert counts["messages_matched"] == 1
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.HALTED


async def test_linkedin_webhook_reply_stops_the_sequence(db_session, monkeypatch):
    campaign, lead, _ = await _enrolled(db_session)
    await _linkedin_account(db_session, campaign, lead)

    async def found(*_):
        return lead

    monkeypatch.setattr(webhooks, "_find_lead_for_event", found)
    await webhooks._handle_message_received(db_session, {"account_id": "uni-1"})
    await db_session.commit()

    assert (await _state(db_session, lead)).status == LeadSequenceStatus.HALTED


async def test_a_reply_in_one_workspace_does_not_touch_another(db_session, _engine):
    _, lead, _ = await _enrolled(db_session)
    _, other_tenant, _ = await create_test_user(_engine, role="owner", new_tenant_name="Other")
    with tenant_scope(other_tenant):
        _, theirs, _ = await _enrolled(db_session)
        await _record_reply(db_session, theirs)

    assert await reply_stop.has_replied(db_session, lead) is False
    assert await reply_stop.stop_on_reply(db_session, lead) == 1  # only our own row
    await db_session.commit()
    with tenant_scope(other_tenant):
        assert (await _state(db_session, theirs)).status == LeadSequenceStatus.ACTIVE
