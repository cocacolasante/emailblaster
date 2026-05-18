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
    seq = Sequence(campaign_id=campaign.id, is_published=True)
    db_session.add(seq)
    await db_session.flush()
    entry = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
        config={"use_campaign_compose": True}, is_entry=True,
    )
    li_node = SequenceNode(
        sequence_id=seq.id, kind=SequenceNodeKind.LINKEDIN_VIEW_PROFILE,
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


async def test_daily_cap_triggers_rate_limited(db_session, monkeypatch):
    """Force the daily cap to 1, then run two actions in a row."""
    from app.config import settings

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    # Reset the cached redis singleton — it's bound to whatever event loop
    # first created it, and pytest-asyncio gives each test a fresh loop.
    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
    lead = await _make_lead(db_session, campaign)
    _stub_provider(monkeypatch)

    # Clean redis counters from any prior test on this account id.
    rc = seq_mod._li_redis()
    await rc.delete(f"li-rate:{acc.id}:day", f"li-rate:{acc.id}:last")

    r1 = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert r1["status"] == "sent"

    r2 = await sequencer._send_linkedin_step_async(str(lead.id), str(li_node.id))
    assert r2["status"] == "rate_limited"
    assert "daily cap" in r2["error"]


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


async def test_rate_limited_skip_keeps_lead_on_node(db_session, monkeypatch):
    """rate_limited is also a transient skip — same retry behavior."""
    from app.config import settings

    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 0)  # cap=0 → instant skip
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)
    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    _, entry, li_node = await _build_view_profile_sequence(db_session, campaign)
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
    assert result["status"] == "rate_limited"

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


async def test_rate_acquire_is_atomic_under_concurrency(db_session, monkeypatch):
    """Two concurrent _li_rate_acquire calls for the same account must not
    both pass when only one slot remains under the daily cap.  The Lua
    script runs server-side as a single atomic operation."""
    import asyncio
    from app.workers import sequencer as seq_mod

    acc = await _make_li_account(db_session)
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

