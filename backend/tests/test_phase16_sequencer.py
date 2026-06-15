"""Integration tests for the sequence scheduler (advance_sequences) +
send_email_step.

These directly drive ``_advance_sequences_async`` rather than going through
Celery, so we don't depend on a worker process. Brevo is monkeypatched.
"""
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Campaign,
    CampaignStatus,
    EmailEvent,
    EmailEventType,
    Lead,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
    SendStatus,
)
from app.services.sequence_service import (
    enroll_leads,
    ensure_default_sequence,
)
from app.workers import sequencer


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_campaign(db_session) -> Campaign:
    c = Campaign(
        name="seq-test",
        goal="Book a call",
        tone="Direct",
        sender_name="A",
        sender_email="a@example.com",
        schedule_days=[0, 1, 2, 3, 4, 5, 6],  # always-on
        schedule_time_start=time(0, 0),
        schedule_time_end=time(23, 59),
        schedule_timezone="UTC",
        status=CampaignStatus.RUNNING,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


async def _make_lead(db_session, campaign, email="lead@example.com") -> Lead:
    l = Lead(
        campaign_id=campaign.id,
        email=email,
        first_name="L",
        composed_subject="hi",
        composed_body="body",
        send_status=SendStatus.PENDING,
    )
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return l


# --------------------------------------------------------------------------
# Default-sequence-only campaign: entry node advances when lead already sent
# --------------------------------------------------------------------------


async def test_entry_node_advances_after_legacy_send_completes(db_session):
    campaign = await _make_campaign(db_session)
    await ensure_default_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    await enroll_leads(db_session, campaign.id, [lead.id])
    await db_session.commit()

    # Simulate the legacy compose→send path having finished.
    lead.send_status = SendStatus.SENT
    await db_session.commit()

    # Tick the scheduler — should advance past the entry node and complete.
    counts = await sequencer._advance_sequences_async()
    assert counts["advanced_entry_done"] >= 1

    await db_session.refresh(lead)
    state = await db_session.scalar(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id)
    )
    assert state.status == LeadSequenceStatus.COMPLETED
    assert state.current_node_id is None


async def test_entry_node_waits_when_legacy_send_pending(db_session):
    campaign = await _make_campaign(db_session)
    await ensure_default_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    await enroll_leads(db_session, campaign.id, [lead.id])
    await db_session.commit()
    # lead.send_status is still PENDING — scheduler should NOT advance.

    counts = await sequencer._advance_sequences_async()
    assert counts["advanced_entry_done"] == 0

    state = await db_session.scalar(
        select(LeadSequenceState).where(LeadSequenceState.lead_id == lead.id)
    )
    assert state.status == LeadSequenceStatus.ACTIVE
    # next_run_at pushed 5 minutes out (so we don't busy-spin)
    assert state.next_run_at > _now() + timedelta(minutes=4)


# --------------------------------------------------------------------------
# Wait node: scheduler advances when duration elapses
# --------------------------------------------------------------------------


async def _build_three_node_sequence(db_session, campaign) -> tuple[Sequence, SequenceNode, SequenceNode, SequenceNode]:
    """email entry --always--> wait --not replied--> followup-email --> end."""
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()

    entry = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"use_campaign_compose": True}, is_entry=True,
    )
    wait_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.WAIT,
        config={"duration_minutes": 60}, is_entry=False,
    )
    followup = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"subject_template": "Hi {{first_name}}", "body_template": "Body for {{first_name}}"},
        is_entry=False,
    )
    db_session.add_all([entry, wait_node, followup])
    await db_session.flush()

    db_session.add_all([
        SequenceEdge(sequence_id=seq.id, from_node_id=entry.id, to_node_id=wait_node.id, condition={"op": "always"}),
        SequenceEdge(
            sequence_id=seq.id, from_node_id=wait_node.id, to_node_id=followup.id,
            condition={"op": "not", "child": {"op": "replied"}},
        ),
        SequenceEdge(sequence_id=seq.id, from_node_id=followup.id, to_node_id=None, condition={"op": "always"}),
    ])
    await db_session.commit()
    return seq, entry, wait_node, followup


async def test_wait_node_advances_when_due(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT  # entry already done via legacy path
    await db_session.commit()

    # Park the lead on the wait node with a next_run_at in the past.
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=wait_node.sequence_id,
        current_node_id=wait_node.id,
        status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now() - timedelta(minutes=1),
        entered_current_at=_now() - timedelta(minutes=61),
    )
    db_session.add(state)
    await db_session.commit()

    # Capture dispatched email-step tasks so the scheduler doesn't enqueue
    # against a real Celery broker during the test.
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sequencer.send_email_step, "delay",
        lambda lead_id, node_id: sent.append((lead_id, node_id)),
    )

    counts = await sequencer._advance_sequences_async()
    assert counts["advanced_wait"] == 1

    await db_session.refresh(state)
    # After the wait, we're now on the followup node; next_run_at is "now"
    # so the next tick dispatches it.
    assert state.current_node_id == followup.id
    assert state.status == LeadSequenceStatus.ACTIVE

    # Another tick should dispatch the followup email step.
    counts2 = await sequencer._advance_sequences_async()
    assert counts2["dispatched_email"] == 1
    assert sent == [(str(lead.id), str(followup.id))]


async def test_skip_on_reply_halts_followup(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    await db_session.commit()

    # Record a REPLIED event for this lead — the wait→followup edge has a
    # NOT replied condition, which now fails. Since there's no other edge,
    # the lead halts.
    db_session.add(EmailEvent(
        lead_id=lead.id, campaign_id=campaign.id,
        event_type=EmailEventType.REPLIED,
    ))
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=wait_node.sequence_id,
        current_node_id=wait_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now() - timedelta(minutes=1),
        entered_current_at=_now() - timedelta(minutes=61),
    )
    db_session.add(state)
    await db_session.commit()

    monkeypatch.setattr(sequencer.send_email_step, "delay", lambda *a, **k: None)
    await sequencer._advance_sequences_async()

    await db_session.refresh(state)
    assert state.status == LeadSequenceStatus.HALTED
    assert "no outgoing edge matched" in (state.halt_reason or "")


# --------------------------------------------------------------------------
# send_email_step writes an execution row + advances cursor
# --------------------------------------------------------------------------


async def test_send_email_step_records_execution_and_advances(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    await db_session.commit()

    # Park lead on the followup node, ready to fire.
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    # Stub brevo so we don't make a real API call.
    async def fake_send(**kwargs):
        return "fake-message-id-123"
    monkeypatch.setattr(sequencer.brevo, "send_email", fake_send)

    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result["status"] == "sent"
    assert result["message_id"] == "fake-message-id-123"

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)

    exec_rows = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(exec_rows) == 1
    assert exec_rows[0].result == LeadStepResult.SENT
    assert exec_rows[0].external_id == "fake-message-id-123"

    await db_session.refresh(state)
    # Followup terminates the branch (edge to_node_id=None).
    assert state.status == LeadSequenceStatus.COMPLETED
    assert state.current_node_id is None


# --------------------------------------------------------------------------
# Follow-up email step honours pre-send gates (paused / scheduled / rate-limited)
# --------------------------------------------------------------------------


async def test_followup_step_defers_when_campaign_paused(db_session, monkeypatch):
    """A paused campaign must NOT fire follow-up emails.  The step returns
    `deferred`, the cursor stays on the followup node, and next_run_at is
    pushed out by 5 minutes (default re-check interval for paused)."""
    campaign = await _make_campaign(db_session)
    campaign.status = CampaignStatus.PAUSED
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    await db_session.commit()

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    called = {"n": 0}
    async def explode(**kwargs):
        called["n"] += 1
        raise AssertionError("Brevo should NOT be called when paused")
    monkeypatch.setattr(sequencer.brevo, "send_email", explode)

    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result["status"] == "deferred"
    assert result["reason"] == "paused"
    assert called["n"] == 0

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    await db_session.refresh(state)
    assert state.current_node_id == followup.id  # parked, not advanced
    assert state.status == LeadSequenceStatus.ACTIVE
    assert state.next_run_at is not None and state.next_run_at > _now()


async def test_followup_step_defers_outside_schedule_window(db_session, monkeypatch):
    """Out-of-window step returns deferred with retry_at; cursor pinned and
    rescheduled to the window open time."""
    from datetime import time as _time
    campaign = await _make_campaign(db_session)
    # Only allow Monday (weekday 0); force-close the window for "now" by
    # giving a narrow time slot the test almost certainly isn't in.
    campaign.schedule_days = [0]
    campaign.schedule_time_start = _time(2, 0)
    campaign.schedule_time_end = _time(2, 30)
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    await db_session.commit()

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    async def explode(**kwargs):
        raise AssertionError("Brevo should NOT be called outside the window")
    monkeypatch.setattr(sequencer.brevo, "send_email", explode)

    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result["status"] == "deferred"
    assert result["reason"] == "scheduled"
    assert "retry_at" in result

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    await db_session.refresh(state)
    assert state.current_node_id == followup.id
    # Should be in the future (the next open window).
    assert state.next_run_at is not None and state.next_run_at > _now()


async def test_followup_step_halts_on_suppression(db_session, monkeypatch):
    """Suppression is permanent — the lead's WHOLE sequence is moot, so
    the state HALTS outright with a clear reason (it used to advance
    node-by-node, writing one skip row per remaining step)."""
    from app.models import Suppression, SuppressionReason
    campaign = await _make_campaign(db_session)
    _, entry, wait_node, followup = await _build_three_node_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    db_session.add(Suppression(email=lead.email, reason=SuppressionReason.UNSUBSCRIBED))
    await db_session.commit()

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=followup.sequence_id,
        current_node_id=followup.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    async def explode(**kwargs):
        raise AssertionError("Brevo should NOT be called for suppressed lead")
    monkeypatch.setattr(sequencer.brevo, "send_email", explode)

    result = await sequencer._send_email_step_async(str(lead.id), str(followup.id))
    assert result["status"] == "suppressed"

    await sequencer._record_execution_and_advance(lead.id, followup.id, result)
    await db_session.refresh(state)
    # Suppression halts the sequence outright with a clear reason.
    assert state.status == LeadSequenceStatus.HALTED
    assert "suppression" in (state.halt_reason or "").lower()
    assert state.next_run_at is None


# --------------------------------------------------------------------------
# Reply-in-thread node (email_reply)
# --------------------------------------------------------------------------


async def _make_reply_node(db_session, campaign, **cfg):
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()
    node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL_REPLY,
        config=cfg, is_entry=False,
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def _reply_lead(db_session, campaign, *, message_id="<orig@mail>", subject="Quick question"):
    lead = await _make_lead(db_session, campaign)
    lead.send_status = SendStatus.SENT
    lead.brevo_message_id = message_id
    lead.composed_subject = subject
    lead.composed_body = "Original pitch body."
    await db_session.commit()
    await db_session.refresh(lead)
    return lead


async def test_email_reply_manual_threads_to_original(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    node = await _make_reply_node(db_session, campaign, body_template="Just following up, {{first_name}}.")
    lead = await _reply_lead(db_session, campaign)

    captured = {}
    async def fake_send(**kwargs):
        captured.update(kwargs)
        return "<reply@mail>"
    monkeypatch.setattr(sequencer.brevo, "send_email", fake_send)

    result = await sequencer._send_email_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent"
    assert captured["subject"] == "Re: Quick question"
    assert captured["in_reply_to"] == "<orig@mail>"
    assert "Just following up, L." in captured["html_body"]


async def test_email_reply_does_not_double_prefix_re(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    node = await _make_reply_node(db_session, campaign, body_template="Ping")
    lead = await _reply_lead(db_session, campaign, subject="Re: Already a reply")

    captured = {}
    async def fake_send(**kwargs):
        captured.update(kwargs)
        return "<r@mail>"
    monkeypatch.setattr(sequencer.brevo, "send_email", fake_send)

    await sequencer._send_email_step_async(str(lead.id), str(node.id))
    assert captured["subject"] == "Re: Already a reply"


async def test_email_reply_ai_uses_composer_and_threads(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    node = await _make_reply_node(
        db_session, campaign, ai_compose=True, ai_prompt="mention the Q3 deadline",
    )
    lead = await _reply_lead(db_session, campaign)

    seen = {}
    async def fake_reply(**kwargs):
        seen.update(kwargs)
        return "AI-written nudge."
    monkeypatch.setattr(sequencer, "generate_followup_reply", fake_reply)

    captured = {}
    async def fake_send(**kwargs):
        captured.update(kwargs)
        return "<reply@mail>"
    monkeypatch.setattr(sequencer.brevo, "send_email", fake_send)

    result = await sequencer._send_email_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent"
    assert seen["idea"] == "mention the Q3 deadline"
    assert seen["original_subject"] == "Quick question"
    assert "AI-written nudge." in captured["html_body"]
    assert captured["in_reply_to"] == "<orig@mail>"


async def test_email_reply_skips_when_no_prior_email(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    node = await _make_reply_node(db_session, campaign, body_template="Ping")
    lead = await _make_lead(db_session, campaign)
    lead.brevo_message_id = None          # no original email was sent
    await db_session.commit()

    async def explode(**kwargs):
        raise AssertionError("must not send a reply with no prior email")
    monkeypatch.setattr(sequencer.brevo, "send_email", explode)

    result = await sequencer._send_email_step_async(str(lead.id), str(node.id))
    assert result["status"] == "skipped"
    assert "no previous email" in result["error"]


async def test_email_reply_manual_without_body_is_misconfigured(db_session, monkeypatch):
    campaign = await _make_campaign(db_session)
    node = await _make_reply_node(db_session, campaign)   # no body_template, no ai_compose
    lead = await _reply_lead(db_session, campaign)

    async def explode(**kwargs):
        raise AssertionError("must not send a misconfigured reply")
    monkeypatch.setattr(sequencer.brevo, "send_email", explode)

    result = await sequencer._send_email_step_async(str(lead.id), str(node.id))
    assert result["status"] == "misconfigured"
