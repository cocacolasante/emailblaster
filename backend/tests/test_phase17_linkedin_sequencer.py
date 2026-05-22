"""Integration tests for the sequencer's LinkedIn channel handler.

The LinkedIn provider is monkeypatched — no real HTTP. We verify:
  - sequencer dispatches LinkedIn nodes
  - the step handler writes execution rows + advances state
  - skip behavior when the campaign has no linkedin_account_id, the lead
    has no linkedin_url, or the account is in a bad state
  - rate-limiting (daily cap)
"""
import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Campaign,
    CampaignStatus,
    Lead,
    LeadSequenceState,
    LeadSequenceStatus,
    LeadStepExecution,
    LeadStepResult,
    LinkedInAccount,
    LinkedInAccountStatus,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
    SendStatus,
)
from app.services import encryption
from app.services.linkedin.base import ActionResult, ProfileRef
from app.workers import sequencer


@pytest.fixture(autouse=True)
def _reset_module_redis_clients(monkeypatch):
    """pytest-asyncio gives each test a fresh event loop, but our sequencer
    redis singleton is module-level. Reset so we don't reuse a client bound
    to a dead loop."""
    monkeypatch.setattr(sequencer, "_LI_REDIS_CLIENT", None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_campaign(db_session, *, linkedin_account_id=None) -> Campaign:
    c = Campaign(
        name="li-seq-test",
        goal="Book a call",
        tone="Direct",
        sender_name="A",
        sender_email="a@example.com",
        schedule_days=list(range(7)),
        schedule_time_start=time(0, 0),
        schedule_time_end=time(23, 59),
        schedule_timezone="UTC",
        status=CampaignStatus.RUNNING,
        linkedin_account_id=linkedin_account_id,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


async def _make_li_account(db_session, *, status=LinkedInAccountStatus.OK) -> LinkedInAccount:
    acc = LinkedInAccount(
        label="t",
        linkedin_email="t@example.com",
        password_encrypted=encryption.encrypt("pw"),
        status=status,
    )
    db_session.add(acc)
    await db_session.commit()
    await db_session.refresh(acc)
    return acc


async def _make_lead(db_session, campaign, *, linkedin_url="https://www.linkedin.com/in/sundarpichai/") -> Lead:
    l = Lead(
        campaign_id=campaign.id,
        email="lead@example.com",
        first_name="L",
        linkedin_url=linkedin_url,
        send_status=SendStatus.PENDING,
    )
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return l


async def _build_view_profile_sequence(db_session, campaign) -> tuple[Sequence, SequenceNode, SequenceNode]:
    return await _build_li_node_sequence(db_session, campaign, SequenceNodeKind.LINKEDIN_VIEW_PROFILE)


async def _build_li_node_sequence(
    db_session, campaign, kind: SequenceNodeKind,
) -> tuple[Sequence, SequenceNode, SequenceNode]:
    """Generic ``email entry -> <kind>`` sequence used by tests that need
    a specific LinkedIn action node — daily-cap tests in particular need
    a non-exempt kind (view_profile bypasses the cap)."""
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()
    entry = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"use_campaign_compose": True}, is_entry=True,
    )
    li_node = SequenceNode(
        sequence_id=seq.id, kind=kind,
        config={}, is_entry=False,
    )
    db_session.add_all([entry, li_node])
    await db_session.flush()
    db_session.add_all([
        SequenceEdge(sequence_id=seq.id, from_node_id=entry.id, to_node_id=li_node.id, condition={"op": "always"}),
        SequenceEdge(sequence_id=seq.id, from_node_id=li_node.id, to_node_id=None, condition={"op": "always"}),
    ])
    await db_session.commit()
    return seq, entry, li_node


def _stub_provider(monkeypatch, *, returns: ActionResult | None = None,
                   raises: Exception | None = None):
    """Patch get_linkedin_provider with a fake whose view_profile returns
    the given ActionResult or raises.
    """
    from app.workers import sequencer as seq_mod

    class _Stub:
        async def view_profile(self, account, profile):
            if raises:
                raise raises
            return returns or ActionResult(ok=True, external_id="urn:li:fsd_profile:xyz")
        async def follow_profile(self, *a, **kw):
            return ActionResult(ok=True)
        async def react_to_post(self, *a, **kw):
            return ActionResult(ok=True)
        async def latest_post_urn(self, *a, **kw):
            return None
        async def test_connection(self, *a, **kw):
            return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **kw):
            return []

    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: _Stub())


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


async def test_view_profile_step_succeeds(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    # Park the lead directly on the LI node, ready to fire.
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    _stub_provider(monkeypatch)

    # Run the handler directly (don't go through Celery).
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "sent", result

    await sequencer._record_execution_and_advance(lead.id, li_node.id, result)

    rows = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].result == LeadStepResult.SENT
    assert rows[0].external_id == "urn:li:fsd_profile:xyz"


# --------------------------------------------------------------------------
# Skip paths
# --------------------------------------------------------------------------


async def test_skips_when_no_linkedin_account_on_campaign(db_session, monkeypatch):
    campaign = await _make_campaign(db_session, linkedin_account_id=None)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    _stub_provider(monkeypatch)
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "misconfigured"


async def test_skips_when_lead_has_no_linkedin_url(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign, linkedin_url=None)

    _stub_provider(monkeypatch)
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "skipped"
    assert "no linkedin_url" in result["error"]


async def test_skips_when_account_challenged(db_session, monkeypatch):
    acc = await _make_li_account(db_session, status=LinkedInAccountStatus.CHALLENGED)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    _stub_provider(monkeypatch)
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    # "challenged" is its own status (transient — drives retry-on-node);
    # "skipped" is for permanent skips. See TRANSIENT_SKIP_STATUSES.
    assert result["status"] == "challenged"
    assert "challenged" in result["error"]


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


async def test_daily_cap_triggers_deferred_with_reset_eta(db_session, monkeypatch):
    """Force the daily cap to 1, then run two actions in a row.

    Cap-style skips return ``status=deferred`` with a ``retry_in`` that
    matches the Redis TTL of the day counter — NOT ``rate_limited``,
    because the transient-retry budget (10 × 5 min) is way too short to
    survive until the daily cap resets ~24h later.  Without this, leads
    that hit the cap mid-day silently advance past the action before
    midnight."""
    from app.config import settings

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    # Reset the cached redis singleton — it's bound to whatever event loop
    # first created it, and pytest-asyncio gives each test a fresh loop.
    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # follow_profile, not view_profile — views are exempt from the daily cap.
    _, entry, li_node = await _build_li_node_sequence(
        db_session, campaign, SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE,
    )
    lead = await _make_lead(db_session, campaign)
    _stub_provider(monkeypatch)

    # Clean redis counters from any prior test on this account id.
    rc = seq_mod._li_redis()
    await rc.delete(f"li-rate:{acc.id}:day", f"li-rate:{acc.id}:last")

    r1 = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert r1["status"] == "sent"

    r2 = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert r2["status"] == "deferred"
    assert r2["reason"] == "daily_cap"
    assert "daily cap" in r2["error"]
    # TTL should be close to 24h (just bumped on the first acquire).
    assert 86000 < r2["retry_in"] <= 86400


async def test_deferred_cap_skip_does_not_burn_retry_budget(db_session, monkeypatch):
    """The whole point of routing cap-skips through `deferred` is that
    the lead waits the full Redis TTL and DOESN'T consume
    MAX_TRANSIENT_RETRIES.  We assert: after a cap-deferred skip, the
    lead is parked on the same node with next_run_at ~24h out, NOT
    cleared in 5 min."""
    from app.config import settings
    from datetime import timedelta

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # follow_profile counts against the day cap; view_profile is exempt.
    _, entry, li_node = await _build_li_node_sequence(
        db_session, campaign, SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE,
    )
    # Two distinct leads: the first one consumes the only slot, the
    # second one hits the cap on the same node.  Two-lead set up
    # (rather than calling twice for the same lead) is required because
    # lifetime per-node idempotency now skips a re-dispatch for the
    # already-SENT (lead, node) pair before the cap check runs.
    lead1 = await _make_lead(db_session, campaign,
                             linkedin_url="https://www.linkedin.com/in/sundarpichai/")
    lead2 = Lead(
        campaign_id=campaign.id, email="other@example.com",
        first_name="Other",
        linkedin_url="https://www.linkedin.com/in/satyanadella/",
        send_status=SendStatus.PENDING,
    )
    db_session.add(lead2)
    await db_session.commit()
    await db_session.refresh(lead2)
    _stub_provider(monkeypatch)

    rc = seq_mod._li_redis()
    await rc.delete(f"li-rate:{acc.id}:day", f"li-rate:{acc.id}:last")

    state2 = LeadSequenceState(
        lead_id=lead2.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state2)
    await db_session.commit()

    # First call (lead1) claims the only slot.
    r1 = await sequencer._send_linkedin_step_async(str(lead1.id), str(li_node.id))
    assert r1["status"] == "sent"
    await sequencer._record_execution_and_advance(lead1.id, li_node.id, r1)

    # Second call (lead2) hits the daily cap.
    r2 = await sequencer._send_linkedin_step_async(str(lead2.id), str(li_node.id))
    assert r2["status"] == "deferred"
    await sequencer._record_execution_and_advance(lead2.id, li_node.id, r2)

    await db_session.refresh(state2)
    # Lead2 is parked on the same node, status ACTIVE, next_run_at ~24h.
    assert state2.current_node_id == li_node.id
    assert state2.status == LeadSequenceStatus.ACTIVE
    assert state2.next_run_at is not None
    delta = state2.next_run_at - _now()
    # Big enough to be clearly the TTL-based defer, not the 5-min retry.
    assert delta > timedelta(hours=1)


# --------------------------------------------------------------------------
# Transient-skip retry (M5-followup) — challenged/restricted/rate_limited
# skips keep the lead on the current node so the step retries once the
# underlying issue is fixed. Other skips still advance.
# --------------------------------------------------------------------------


async def test_challenged_skip_keeps_lead_on_node(db_session, monkeypatch):
    acc = await _make_li_account(db_session, status=LinkedInAccountStatus.CHALLENGED)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()
    _stub_provider(monkeypatch)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "challenged"

    await sequencer._record_execution_and_advance(lead.id, li_node.id, result)
    await db_session.refresh(state)

    # Cursor did NOT advance — still pointing at the LinkedIn node.
    assert state.current_node_id == li_node.id
    assert state.status == LeadSequenceStatus.ACTIVE
    # next_run_at pushed ~5 min into the future.
    assert state.next_run_at is not None
    assert state.next_run_at > _now() + timedelta(minutes=4)
    # Execution row still written for analytics + history.
    rows = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].result == LeadStepResult.SKIPPED


async def test_daily_cap_deferred_skip_keeps_lead_on_node(db_session, monkeypatch):
    """A daily_cap skip keeps the lead parked on the current node.

    (Previously asserted ``status == rate_limited``; now cap-style skips
    are routed to ``status == deferred`` instead so MAX_TRANSIENT_RETRIES
    doesn't burn through the lead's budget before the cap resets.  The
    parking semantics — lead stays on node, next_run_at pushed out — are
    identical from the user's standpoint.)
    """
    from app.config import settings

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 0)  # cap=0 → instant skip
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)
    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # follow_profile counts against the day cap; view_profile is exempt.
    _, entry, li_node = await _build_li_node_sequence(
        db_session, campaign, SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE,
    )
    lead = await _make_lead(db_session, campaign)
    _stub_provider(monkeypatch)

    rc = seq_mod._li_redis()
    await rc.delete(f"li-rate:{acc.id}:day", f"li-rate:{acc.id}:last")

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "deferred"
    assert result["reason"] == "daily_cap"

    await sequencer._record_execution_and_advance(lead.id, li_node.id, result)
    await db_session.refresh(state)
    assert state.current_node_id == li_node.id
    assert state.next_run_at > _now() + timedelta(minutes=4)


async def test_permanent_skip_advances_cursor(db_session, monkeypatch):
    """A non-transient skip (lead has no linkedin_url) still advances."""
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign, linkedin_url=None)

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()
    _stub_provider(monkeypatch)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "skipped"
    assert "no linkedin_url" in result["error"]

    await sequencer._record_execution_and_advance(lead.id, li_node.id, result)
    await db_session.refresh(state)
    # The sequence has only entry → li_node → terminate, so advancing past
    # li_node completes the sequence.
    assert state.status == LeadSequenceStatus.COMPLETED


async def test_transient_retry_cap_eventually_advances(db_session, monkeypatch):
    """After MAX_TRANSIENT_RETRIES skips on the same visit, give up + advance."""
    acc = await _make_li_account(db_session, status=LinkedInAccountStatus.CHALLENGED)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=li_node.sequence_id,
        current_node_id=li_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now() - timedelta(hours=1),
    )
    db_session.add(state)
    await db_session.commit()
    _stub_provider(monkeypatch)

    # Burn through MAX_TRANSIENT_RETRIES − 1 skips; still on the node.
    for _ in range(sequencer.MAX_TRANSIENT_RETRIES - 1):
        result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
        await sequencer._record_execution_and_advance(lead.id, li_node.id, result)
    await db_session.refresh(state)
    assert state.current_node_id == li_node.id  # still pinned

    # One more transient skip → exceeds the cap → advances past the node.
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    await sequencer._record_execution_and_advance(lead.id, li_node.id, result)
    await db_session.refresh(state)
    assert state.status == LeadSequenceStatus.COMPLETED


# --------------------------------------------------------------------------
# Connect → DM "wait for acceptance" parking semantics
# --------------------------------------------------------------------------


async def _build_connect_then_dm_sequence(db_session, campaign):
    """email entry -> linkedin_connect -- linkedin_connection=connected --> linkedin_dm"""
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()
    entry = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"use_campaign_compose": True}, is_entry=True,
    )
    connect_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.LINKEDIN_CONNECT,
        config={"note_template": "Hi {{first_name}}"}, is_entry=False,
    )
    dm_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.LINKEDIN_DM,
        config={"body_template": "Thanks for connecting!"}, is_entry=False,
    )
    db_session.add_all([entry, connect_node, dm_node])
    await db_session.flush()
    db_session.add_all([
        SequenceEdge(sequence_id=seq.id, from_node_id=entry.id,
                     to_node_id=connect_node.id, condition={"op": "always"}),
        # The gated edge: only traverse once the prospect accepts.
        SequenceEdge(sequence_id=seq.id, from_node_id=connect_node.id,
                     to_node_id=dm_node.id,
                     condition={"op": "linkedin_connection", "value": "connected"}),
        SequenceEdge(sequence_id=seq.id, from_node_id=dm_node.id,
                     to_node_id=None, condition={"op": "always"}),
    ])
    await db_session.commit()
    return seq, entry, connect_node, dm_node


async def test_connect_then_dm_parks_when_invitation_pending(db_session, monkeypatch):
    """After linkedin_connect fires the lead is INVITED, not CONNECTED.  The
    gated DM edge does not match yet — the lead must stay on the connect
    node and re-evaluate later, not halt."""
    from app.workers import sequencer as seq_mod
    from app.models import LinkedInConnectionStatus

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, connect_node, dm_node = await _build_connect_then_dm_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)

    class _StubConnectOk:
        async def send_connect_request(self, account, profile, note=None):
            return ActionResult(ok=True, external_id="ACoAA-invite-1")
        async def view_profile(self, *a, **k): return ActionResult(ok=True)
        async def follow_profile(self, *a, **k): return ActionResult(ok=True)
        async def react_to_post(self, *a, **k): return ActionResult(ok=True)
        async def latest_post_urn(self, *a, **k): return None
        async def test_connection(self, *a, **k): return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **k): return []
    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: _StubConnectOk())

    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=connect_node.sequence_id,
        current_node_id=connect_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    # Fire the connect step.
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(connect_node.id))
    assert result["status"] == "sent"

    # Flip the lead to INVITED — the real handler does this via
    # _persist_invite_optimistic; we mimic it for the test.
    lead.linkedin_connection_status = LinkedInConnectionStatus.INVITED
    await db_session.commit()

    await sequencer._record_execution_and_advance(lead.id, connect_node.id, result)
    await db_session.refresh(state)

    # Lead is parked on the connect node, NOT advanced to DM, NOT halted.
    assert state.status == LeadSequenceStatus.ACTIVE
    assert state.current_node_id == connect_node.id
    assert state.next_run_at is not None
    assert state.halt_reason is None

    # The next scheduler tick (still pending) must NOT re-fire the connect step
    # — the prior SENT execution row means we're parked, just re-eval edges.
    dispatched: list[tuple[str, str]] = []
    def fake_apply_async(*, args, **kw):
        dispatched.append((args[0], args[1]))
    monkeypatch.setattr(sequencer.send_linkedin_step, "apply_async", fake_apply_async)

    # Move next_run_at into the past so the scheduler picks the row up.
    state.next_run_at = _now() - timedelta(seconds=1)
    await db_session.commit()

    await sequencer._advance_sequences_async()
    await db_session.refresh(state)
    assert dispatched == []  # action did NOT re-fire
    assert state.current_node_id == connect_node.id  # still parked


async def test_connect_then_dm_advances_after_acceptance(db_session, monkeypatch):
    """Once linkedin_connection flips to CONNECTED (Unipile webhook for
    invitation.accepted) the parked lead advances to the DM node on the
    next scheduler tick."""
    from app.workers import sequencer as seq_mod
    from app.models import LinkedInConnectionStatus

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, connect_node, dm_node = await _build_connect_then_dm_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.linkedin_connection_status = LinkedInConnectionStatus.INVITED
    await db_session.commit()

    # Simulate the lead already parked on connect_node with a SENT exec row.
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=connect_node.sequence_id,
        current_node_id=connect_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now() - timedelta(seconds=1),
        entered_current_at=_now() - timedelta(minutes=45),
    )
    db_session.add(state)
    db_session.add(LeadStepExecution(
        lead_id=lead.id, node_id=connect_node.id,
        result=LeadStepResult.SENT, external_id="ACoAA-invite-1",
    ))
    await db_session.commit()

    # Acceptance lands (webhook would normally do this).
    lead.linkedin_connection_status = LinkedInConnectionStatus.CONNECTED
    await db_session.commit()

    dispatched: list[tuple[str, str]] = []
    def fake_apply_async(*, args, **kw):
        dispatched.append((args[0], args[1]))
    monkeypatch.setattr(sequencer.send_linkedin_step, "apply_async", fake_apply_async)

    # Stub the provider so any dispatched step would succeed (we're only
    # checking the cursor advances; the actual dispatch happens later).
    _stub_provider(monkeypatch)

    await sequencer._advance_sequences_async()
    await db_session.refresh(state)

    # Cursor walked from connect_node to dm_node; DM is queued for dispatch
    # on the NEXT tick (this tick only advanced the cursor + re-eval).
    assert state.current_node_id == dm_node.id
    assert state.status == LeadSequenceStatus.ACTIVE


async def test_view_profile_is_exempt_from_daily_cap(db_session, monkeypatch):
    """view_profile is a read action with negligible bot-detection risk
    and a doubling effect on throughput when sequences are
    ``view → connect → DM``.  It deliberately doesn't count against
    ``LINKEDIN_DAILY_ACTION_CAP``: even with day_cap=1 (effectively
    zero remaining capacity) the acquire still succeeds for view_profile."""
    from app.config import settings
    from app.workers import sequencer as seq_mod

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    rc = seq_mod._li_redis()
    aid = str(acc.id)
    await rc.delete(f"li-rate:{aid}:last", f"li-rate:{aid}:day")

    # Burn the (already minimal) day cap with one CONNECT.
    r1 = await seq_mod._li_rate_acquire(acc, kind=SequenceNodeKind.LINKEDIN_CONNECT)
    assert r1["ok"] is True

    # A regular kind should now be rejected.
    r2 = await seq_mod._li_rate_acquire(acc, kind=SequenceNodeKind.LINKEDIN_FOLLOW_PROFILE)
    assert r2["ok"] is False
    assert r2["reason"] == "daily_cap"

    # But view_profile is exempt from the day cap.
    r3 = await seq_mod._li_rate_acquire(acc, kind=SequenceNodeKind.LINKEDIN_VIEW_PROFILE)
    assert r3["ok"] is True

    # And the day counter STAYED at 1 — view_profile didn't bump it.
    day_count = int(await rc.get(f"li-rate:{aid}:day") or 0)
    assert day_count == 1


async def test_view_profile_still_respects_min_delay(db_session, monkeypatch):
    """Exempting from the cap doesn't mean we burst-view 100 profiles in
    a minute.  Min-delay still applies to every LinkedIn action."""
    from app.config import settings
    from app.workers import sequencer as seq_mod

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 1000)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 60)
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    rc = seq_mod._li_redis()
    aid = str(acc.id)
    await rc.delete(f"li-rate:{aid}:last", f"li-rate:{aid}:day")

    r1 = await seq_mod._li_rate_acquire(acc, kind=SequenceNodeKind.LINKEDIN_VIEW_PROFILE)
    assert r1["ok"] is True
    r2 = await seq_mod._li_rate_acquire(acc, kind=SequenceNodeKind.LINKEDIN_VIEW_PROFILE)
    assert r2["ok"] is False
    assert r2["reason"] == "min_delay"


async def test_rate_acquire_is_atomic_under_concurrency(db_session, monkeypatch):
    """Two concurrent _li_rate_acquire calls for the same account must not
    both pass when only one slot remains under the daily cap.  The Lua
    script runs server-side as a single atomic operation."""
    import asyncio
    from app.workers import sequencer as seq_mod

    acc = await _make_li_account(db_session)
    # Clear any leftover Redis counters for this account from a prior
    # test (the fakeredis instance is shared across the file).
    rc = seq_mod._li_redis()
    aid = str(acc.id)
    await rc.delete(f"li-rate:{aid}:last", f"li-rate:{aid}:day")
    # Make the cap exactly 1 so the race is observable.
    monkeypatch.setattr(seq_mod.settings, "LINKEDIN_DAILY_ACTION_CAP", 1)
    monkeypatch.setattr(seq_mod.settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    # Two coroutines try to claim simultaneously.
    a, b = await asyncio.gather(
        seq_mod._li_rate_acquire(acc),
        seq_mod._li_rate_acquire(acc),
    )
    # Exactly one wins; the other gets daily_cap.
    winners = [r for r in (a, b) if r.get("ok")]
    losers = [r for r in (a, b) if not r.get("ok")]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].get("reason") == "daily_cap"


async def test_rate_acquire_enforces_min_delay(db_session, monkeypatch):
    """min_delay denies the second action when it's too close to the first."""
    from app.workers import sequencer as seq_mod

    acc = await _make_li_account(db_session)
    rc = seq_mod._li_redis()
    aid = str(acc.id)
    await rc.delete(f"li-rate:{aid}:last", f"li-rate:{aid}:day")
    monkeypatch.setattr(seq_mod.settings, "LINKEDIN_DAILY_ACTION_CAP", 100)
    monkeypatch.setattr(seq_mod.settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 60)

    first = await seq_mod._li_rate_acquire(acc)
    assert first.get("ok") is True

    second = await seq_mod._li_rate_acquire(acc)
    assert second.get("ok") is False
    assert second.get("reason") == "min_delay"
    assert isinstance(second.get("remaining"), int)
    assert second["remaining"] > 0


async def test_connect_then_dm_halts_after_max_wait(db_session, monkeypatch):
    """When the prospect never accepts, the lead halts after MAX_EDGE_WAIT_DAYS
    with a clear reason so it surfaces in the halted-leads panel."""
    from app.workers import sequencer as seq_mod
    from app.models import LinkedInConnectionStatus

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, connect_node, dm_node = await _build_connect_then_dm_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    lead.linkedin_connection_status = LinkedInConnectionStatus.INVITED
    await db_session.commit()

    long_ago = _now() - timedelta(days=sequencer.MAX_EDGE_WAIT_DAYS + 1)
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=connect_node.sequence_id,
        current_node_id=connect_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now(), entered_current_at=long_ago,
    )
    db_session.add(state)
    db_session.add(LeadStepExecution(
        lead_id=lead.id, node_id=connect_node.id,
        result=LeadStepResult.SENT, external_id="ACoAA-invite-1",
        attempted_at=long_ago,
    ))
    await db_session.commit()

    _stub_provider(monkeypatch)
    await sequencer._advance_sequences_async()
    await db_session.refresh(state)

    assert state.status == LeadSequenceStatus.HALTED
    assert state.halt_reason is not None
    assert f"{sequencer.MAX_EDGE_WAIT_DAYS}d" in state.halt_reason


# --------------------------------------------------------------------------
# Schedule window + paused gates apply to LinkedIn steps too
# --------------------------------------------------------------------------


async def test_linkedin_step_defers_outside_schedule_window(db_session, monkeypatch):
    """LinkedIn steps used to run 24/7; that meant a connect cap-reset
    landing at 3 AM ET would fire 40 connects in the middle of the
    night.  Now the same window gate the email path uses applies to
    LinkedIn too — outside the window we defer to the next window open.
    """
    from datetime import time as _time
    from app.workers import sequencer as seq_mod

    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # Force a window the test is almost certainly not inside (Mon only,
    # 02:00–02:30 UTC).  Whatever the test clock says, we'll be out of
    # this window unless we're running at the precise 30-minute
    # Monday-night slot.
    campaign.schedule_days = [0]
    campaign.schedule_time_start = _time(2, 0)
    campaign.schedule_time_end = _time(2, 30)
    await db_session.commit()

    _, entry, li_node = await _build_li_node_sequence(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
    )
    lead = await _make_lead(db_session, campaign)

    # Provider must NOT be invoked.
    class _Explode:
        async def send_connect_request(self, *a, **kw):
            raise AssertionError("LinkedIn provider called outside window")
        async def view_profile(self, *a, **kw): return ActionResult(ok=True)
        async def follow_profile(self, *a, **kw): return ActionResult(ok=True)
        async def react_to_post(self, *a, **kw): return ActionResult(ok=True)
        async def latest_post_urn(self, *a, **kw): return None
        async def test_connection(self, *a, **kw): return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **kw): return []
    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: _Explode())

    result = await seq_mod._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "deferred"
    assert result["reason"] == "scheduled"
    assert "retry_at" in result

    # The rate-limit counter should NOT have been bumped — we deferred
    # before _li_rate_acquire ran.
    rc = seq_mod._li_redis()
    aid = str(acc.id)
    day_count = await rc.get(f"li-rate:{aid}:day")
    assert day_count is None or int(day_count) == 0


async def test_stale_dispatch_skips_api_call_when_cursor_moved(db_session, monkeypatch):
    """A Celery task that orphaned during a worker restart re-delivers
    after visibility_timeout carrying its original node_id.  If the
    lead's cursor has advanced past that node in the meantime, firing
    the task would double-execute the prior step (e.g. a second ghost-
    view, a second connect invite).  Bail at the top of
    _send_linkedin_step_async with status=stale_dispatch — NO provider
    call, NO execution row, NO cursor change."""
    from app.workers import sequencer as seq_mod

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # Two-step sequence so we have two distinct LI node ids.
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()
    entry = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"use_campaign_compose": True}, is_entry=True,
    )
    view_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.LINKEDIN_VIEW_PROFILE,
        config={}, is_entry=False,
    )
    connect_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.LINKEDIN_CONNECT,
        config={}, is_entry=False,
    )
    db_session.add_all([entry, view_node, connect_node])
    await db_session.flush()
    db_session.add_all([
        SequenceEdge(sequence_id=seq.id, from_node_id=entry.id,
                     to_node_id=view_node.id, condition={"op": "always"}),
        SequenceEdge(sequence_id=seq.id, from_node_id=view_node.id,
                     to_node_id=connect_node.id, condition={"op": "always"}),
        SequenceEdge(sequence_id=seq.id, from_node_id=connect_node.id,
                     to_node_id=None, condition={"op": "always"}),
    ])
    await db_session.commit()

    lead = await _make_lead(db_session, campaign)
    # Cursor is already PAST view_node (on connect_node).  A stale Celery
    # task targeting view_node should be a no-op.
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=seq.id,
        current_node_id=connect_node.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=_now() + timedelta(minutes=10),
        entered_current_at=_now(),
    )
    db_session.add(state)
    await db_session.commit()

    class _Explode:
        async def view_profile(self, *a, **kw):
            raise AssertionError("provider called for stale-dispatch view_profile")
        async def follow_profile(self, *a, **kw): return ActionResult(ok=True)
        async def react_to_post(self, *a, **kw): return ActionResult(ok=True)
        async def latest_post_urn(self, *a, **kw): return None
        async def test_connection(self, *a, **kw): return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **kw): return []
        async def send_connect_request(self, *a, **kw): return ActionResult(ok=True)
    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: _Explode())

    result = await seq_mod._send_linkedin_step_async(str(lead.id), str(view_node.id))
    assert result == {"status": "stale_dispatch"}

    # And running it through _record_execution_and_advance must NOT
    # write a row (audit log stays clean).
    before = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(before) == 0

    await seq_mod._record_execution_and_advance(lead.id, view_node.id, result)

    after = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(after) == 0, "stale_dispatch must not write an execution row"

    # Cursor unchanged.
    await db_session.refresh(state)
    assert state.current_node_id == connect_node.id


async def test_linkedin_step_defers_when_campaign_paused(db_session, monkeypatch):
    from app.workers import sequencer as seq_mod
    from app.models import CampaignStatus

    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    campaign.status = CampaignStatus.PAUSED
    await db_session.commit()

    _, entry, li_node = await _build_li_node_sequence(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
    )
    lead = await _make_lead(db_session, campaign)

    class _Explode:
        async def send_connect_request(self, *a, **kw):
            raise AssertionError("LinkedIn provider called while paused")
        async def view_profile(self, *a, **kw): return ActionResult(ok=True)
        async def follow_profile(self, *a, **kw): return ActionResult(ok=True)
        async def react_to_post(self, *a, **kw): return ActionResult(ok=True)
        async def latest_post_urn(self, *a, **kw): return None
        async def test_connection(self, *a, **kw): return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **kw): return []
    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: _Explode())

    result = await seq_mod._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert result["status"] == "deferred"
    assert result["reason"] == "paused"
    assert "retry_at" not in result  # paused has no eta — recheck in 5 min

