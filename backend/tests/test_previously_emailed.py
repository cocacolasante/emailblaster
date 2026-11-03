"""New campaigns skip anyone the workspace has already emailed, unless the
campaign's "include people already emailed" toggle is on."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.models import (
    Campaign,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    SendStatus,
)
from app.services import previously_emailed
from app.tenancy.context import tenant_scope
from app.workers import research as research_mod
from app.workers import send as send_mod
from app.workers import sequencer
from tests import test_phase16_sequencer as seq_t
from tests.conftest import create_test_user

EMAIL = "person@example.com"


async def _lead_in_new_campaign(db_session, *, email=EMAIL, sent=False, allow=False):
    campaign = await seq_t._make_campaign(db_session)
    campaign.min_delay_seconds = 0
    campaign.allow_previously_emailed = allow
    nodes = await seq_t._build_three_node_sequence(db_session, campaign)
    followup = nodes[3]
    lead = await seq_t._make_lead(db_session, campaign, email=email)
    lead.send_status = SendStatus.SENT if sent else SendStatus.PENDING
    lead.composed_subject, lead.composed_body = "Hi", "Hello there"
    db_session.add(LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=seq_t._now(), entered_current_at=seq_t._now(),
    ))
    await db_session.commit()
    return campaign, lead, followup


async def _state(db_session, lead):
    state = await db_session.scalar(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id))
    await db_session.refresh(state)
    return state


async def _send_first(lead, set_creds):
    set_creds("brevo", api_key="k")
    send = AsyncMock(return_value="mid")
    with patch.object(send_mod, "_new_redis",
                      return_value=fakeredis.aioredis.FakeRedis(decode_responses=True)), \
         patch.object(send_mod.brevo, "send_email", send):
        return await send_mod.send_lead_async(str(lead.id)), send


async def test_campaign_skips_someone_another_campaign_emailed(db_session, set_creds):
    await _lead_in_new_campaign(db_session, sent=True)
    _, lead, _ = await _lead_in_new_campaign(db_session)

    result, send = await _send_first(lead, set_creds)

    assert result == {"status": "previously_emailed"}
    send.assert_not_called()
    await db_session.refresh(lead)
    assert lead.send_status == SendStatus.SUPPRESSED
    state = await _state(db_session, lead)
    assert state.status == LeadSequenceStatus.HALTED
    assert state.halt_reason == previously_emailed.HALT_REASON


async def test_toggle_on_sends_anyway(db_session, set_creds):
    await _lead_in_new_campaign(db_session, sent=True)
    _, lead, _ = await _lead_in_new_campaign(db_session, allow=True)
    result, send = await _send_first(lead, set_creds)
    assert result["status"] == "sent"
    send.assert_awaited_once()


async def test_never_emailed_person_is_sent(db_session, set_creds):
    # In another campaign too, but nothing has gone out to them yet.
    await _lead_in_new_campaign(db_session)
    _, lead, _ = await _lead_in_new_campaign(db_session)
    result, _ = await _send_first(lead, set_creds)
    assert result["status"] == "sent"


async def test_emailed_via_a_follow_up_step_counts(db_session):
    _, earlier, node = await _lead_in_new_campaign(db_session)
    db_session.add(LeadStepExecution(lead_id=earlier.id, node_id=node.id, result=LeadStepResult.SENT))
    await db_session.commit()
    campaign, lead, _ = await _lead_in_new_campaign(db_session)
    assert await previously_emailed.should_skip(db_session, lead, campaign) is True


async def test_address_match_ignores_case(db_session):
    await _lead_in_new_campaign(db_session, email="PERSON@example.com", sent=True)
    campaign, lead, _ = await _lead_in_new_campaign(db_session)
    assert await previously_emailed.should_skip(db_session, lead, campaign) is True


async def test_follow_ups_continue_once_this_campaign_has_emailed_them(db_session):
    """Both campaigns already emailed them: stopping now wouldn't undo that, and
    the rule is about starting."""
    await _lead_in_new_campaign(db_session, sent=True)
    campaign, lead, _ = await _lead_in_new_campaign(db_session, sent=True)
    assert await previously_emailed.should_skip(db_session, lead, campaign) is False


async def test_email_step_skips_a_lead_this_campaign_never_emailed(db_session, monkeypatch):
    await _lead_in_new_campaign(db_session, sent=True)
    _, lead, followup = await _lead_in_new_campaign(db_session)
    lead.brevo_message_id = "<first@x>"
    await db_session.commit()

    send = AsyncMock(return_value="mid")
    monkeypatch.setattr(sequencer.brevo, "send_email", send)
    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result == {"status": "previously_emailed"}
    send.assert_not_called()

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    state = await _state(db_session, lead)
    assert state.status == LeadSequenceStatus.HALTED
    assert state.halt_reason == previously_emailed.HALT_REASON


async def test_skip_leaves_the_other_campaign_running(db_session, set_creds):
    _, earlier, _ = await _lead_in_new_campaign(db_session, sent=True)
    _, lead, _ = await _lead_in_new_campaign(db_session)
    await _send_first(lead, set_creds)
    assert (await _state(db_session, earlier)).status == LeadSequenceStatus.ACTIVE


async def test_other_workspaces_sends_do_not_count(db_session, _engine):
    _, other_tenant, _ = await create_test_user(_engine, role="owner", new_tenant_name="Other")
    with tenant_scope(other_tenant):
        await _lead_in_new_campaign(db_session, sent=True)
    campaign, lead, _ = await _lead_in_new_campaign(db_session)
    assert await previously_emailed.should_skip(db_session, lead, campaign) is False


async def test_research_is_not_paid_for_on_a_skipped_lead(db_session):
    await _lead_in_new_campaign(db_session, sent=True)
    _, lead, _ = await _lead_in_new_campaign(db_session)

    result = await research_mod.research_lead_async(str(lead.id))

    assert result == {"status": "skipped_previously_emailed"}
    await db_session.refresh(lead)
    assert lead.send_status == SendStatus.SUPPRESSED
    assert lead.research_data["previously_emailed"] is True


async def test_linkedin_first_campaign_keeps_the_lead(db_session, monkeypatch):
    """The rule is about email: research must not take them out of LinkedIn."""
    await _lead_in_new_campaign(db_session, sent=True)
    _, lead, _ = await _lead_in_new_campaign(db_session)
    monkeypatch.setattr(research_mod.sequence_service, "campaign_sends_legacy_first_email",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(research_mod, "stop_requested", lambda *_: True)  # stop before paid calls

    result = await research_mod.research_lead_async(str(lead.id))

    assert result != {"status": "skipped_previously_emailed"}
    await db_session.refresh(lead)
    assert lead.send_status == SendStatus.PENDING
    assert (await _state(db_session, lead)).status == LeadSequenceStatus.ACTIVE


# --- API ---------------------------------------------------------------------


def _create_payload(**extra):
    return {
        "name": "New", "goal": "g", "tone": "t", "sender_name": "s", "sender_email": "s@x.com",
        "schedule_days": [0, 1, 2, 3, 4], "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00", **extra,
    }


async def test_default_is_off_and_toggle_saves(client):
    made = await client.post("/campaigns/", json=_create_payload())
    assert made.status_code == 201, made.text
    assert made.json()["allow_previously_emailed"] is False

    on = await client.post("/campaigns/", json=_create_payload(allow_previously_emailed=True))
    assert on.json()["allow_previously_emailed"] is True

    patched = await client.patch(f"/campaigns/{made.json()['id']}", json={"allow_previously_emailed": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["allow_previously_emailed"] is True


async def test_retarget_campaigns_turn_it_on(client, db_session):
    source = (await client.post("/campaigns/", json=_create_payload())).json()
    made = await client.post("/campaigns/", json=_create_payload(retarget_source_campaign_id=source["id"]))
    assert made.status_code == 201, made.text
    row = await db_session.get(Campaign, made.json()["id"])
    assert row.allow_previously_emailed is True
