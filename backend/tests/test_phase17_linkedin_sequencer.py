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
    assert result["status"] == "skipped"
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
