"""Integration tests for M3 LinkedIn write actions:
  - linkedin_connect (with + without note, 300-char cap)
  - linkedin_dm (skip when not 1st-degree)
  - linkedin_invite_to_page (skip when not connected, page_id required)
  - per-kind rate caps (connect, DM, page-invite-per-page)

Real LinkedIn HTTP is monkeypatched at the provider level; we never hit
network.
"""
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
    LinkedInConnectionStatus,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
    SendStatus,
)
from app.services import encryption
from app.services.linkedin.base import ActionResult
from app.workers import sequencer


@pytest.fixture(autouse=True)
def _reset_li_redis_singleton(monkeypatch):
    """The redis client is module-cached; pytest-asyncio gives each test a
    fresh event loop, so the cached client from the previous test points at
    a dead loop. Reset it before every test in this file."""
    monkeypatch.setattr(sequencer, "_LI_REDIS_CLIENT", None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


async def _make_campaign(db_session, *, linkedin_account_id) -> Campaign:
    c = Campaign(
        name="m3-test",
        goal="Connect with leads",
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


async def _make_lead(db_session, campaign, *, connection=LinkedInConnectionStatus.UNKNOWN) -> Lead:
    l = Lead(
        campaign_id=campaign.id,
        email="lead@example.com",
        first_name="Cole",
        linkedin_url="https://www.linkedin.com/in/coleburnham/",
        linkedin_connection_status=connection,
        send_status=SendStatus.PENDING,
    )
    db_session.add(l)
    await db_session.commit()
    await db_session.refresh(l)
    return l


async def _build_seq_with_node(db_session, campaign, kind, config) -> SequenceNode:
    """Add `kind` as a non-entry node attached to the campaign's sequence,
    creating the sequence (with an email entry node) only on the first call.
    """
    seq = (await db_session.execute(
        select(Sequence).where(Sequence.campaign_id == campaign.id)
    )).scalar_one_or_none()
    if seq is None:
        seq = Sequence(campaign_id=campaign.id, is_published=True)
        db_session.add(seq)
        await db_session.flush()
        entry = SequenceNode(
            sequence_id=seq.id, kind=SequenceNodeKind.EMAIL,
            config={"use_campaign_compose": True}, is_entry=True,
        )
        db_session.add(entry)
        await db_session.flush()
    else:
        entry = (await db_session.execute(
            select(SequenceNode).where(
                SequenceNode.sequence_id == seq.id,
                SequenceNode.is_entry.is_(True),
            )
        )).scalar_one()

    node = SequenceNode(
        sequence_id=seq.id, kind=kind, config=config, is_entry=False,
    )
    db_session.add(node)
    await db_session.flush()
    db_session.add_all([
        SequenceEdge(sequence_id=seq.id, from_node_id=entry.id, to_node_id=node.id, condition={"op": "always"}),
        SequenceEdge(sequence_id=seq.id, from_node_id=node.id, to_node_id=None, condition={"op": "always"}),
    ])
    await db_session.commit()
    return node


def _stub_provider(monkeypatch, **overrides):
    """Patch get_linkedin_provider with a fake. Provide overrides like
    send_connect_request=AsyncMock or just rely on defaults that return OK.
    """
    from app.workers import sequencer as seq_mod

    class _Stub:
        async def view_profile(self, account, profile):
            return ActionResult(ok=True, external_id="urn:li:fsd_profile:x")
        async def follow_profile(self, account, profile):
            return ActionResult(ok=True)
        async def react_to_post(self, account, post_urn, reaction="LIKE"):
            return ActionResult(ok=True)
        async def latest_post_urn(self, account, profile):
            return None
        async def send_connect_request(self, account, profile, note=None):
            return ActionResult(ok=True, external_id=profile.public_id, meta={"note": note})
        async def send_dm(self, account, profile, text):
            return ActionResult(ok=True, external_id="urn:li:fsd_profile:x", meta={"len": len(text)})
        async def invite_to_page(self, account, profile, page_id):
            return ActionResult(ok=True, external_id="urn:li:fsd_profile:x", meta={"page_id": page_id})
        async def test_connection(self, *a, **k):
            return ActionResult(ok=True)
        async def inbox_recent_events(self, *a, **k):
            return []

    instance = _Stub()
    for k, v in overrides.items():
        setattr(instance, k, v)
    monkeypatch.setattr(seq_mod, "get_linkedin_provider", lambda: instance)
    return instance


# --------------------------------------------------------------------------
# linkedin_connect
# --------------------------------------------------------------------------


async def test_connect_with_note_substitutes_and_marks_invited(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(db_session, campaign)
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
        {"note_template": "Hi {{first_name}}, would love to connect."},
    )

    captured = {}
    async def fake_connect(account, profile, note=None):
        captured["note"] = note
        return ActionResult(ok=True, external_id=profile.public_id)
    _stub_provider(monkeypatch, send_connect_request=fake_connect)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent", result
    assert captured["note"] == "Hi Cole, would love to connect."

    # Lead promoted to INVITED optimistically.
    await db_session.refresh(lead)
    assert lead.linkedin_connection_status == LinkedInConnectionStatus.INVITED


async def test_connect_no_note_flag_skips_template(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(db_session, campaign)
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
        {"no_note": True, "note_template": "ignored"},
    )

    captured = {}
    async def fake_connect(account, profile, note=None):
        captured["note"] = note
        return ActionResult(ok=True, external_id=profile.public_id)
    _stub_provider(monkeypatch, send_connect_request=fake_connect)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent"
    assert captured["note"] is None


async def test_connect_note_truncated_to_300_chars(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(db_session, campaign)
    long_note = "x" * 400
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
        {"note_template": long_note},
    )

    captured = {}
    async def fake_connect(account, profile, note=None):
        captured["note"] = note
        return ActionResult(ok=True)
    _stub_provider(monkeypatch, send_connect_request=fake_connect)

    await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert len(captured["note"]) == 300


# --------------------------------------------------------------------------
# linkedin_dm
# --------------------------------------------------------------------------


async def test_dm_skips_when_not_connected(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    # Lead is INVITED, not CONNECTED → DM must skip.
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.INVITED,
    )
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_DM,
        {"text_template": "Thanks for connecting!"},
    )
    _stub_provider(monkeypatch)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "skipped"
    assert "1st degree" in result["error"]


async def test_dm_sends_when_connected(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.CONNECTED,
    )
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_DM,
        {"text_template": "Hi {{first_name}}, thanks for connecting!"},
    )

    captured = {}
    async def fake_dm(account, profile, text):
        captured["text"] = text
        return ActionResult(ok=True)
    _stub_provider(monkeypatch, send_dm=fake_dm)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent"
    assert captured["text"] == "Hi Cole, thanks for connecting!"


async def test_dm_misconfigured_when_no_template(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.CONNECTED,
    )
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_DM, {},
    )
    _stub_provider(monkeypatch)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "misconfigured"


# --------------------------------------------------------------------------
# linkedin_invite_to_page
# --------------------------------------------------------------------------


async def test_invite_to_page_skips_when_not_connected(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(db_session, campaign)  # UNKNOWN connection
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE,
        {"page_id": "12345678"},
    )
    _stub_provider(monkeypatch)
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "skipped"


async def test_invite_to_page_requires_page_id(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.CONNECTED,
    )
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE, {},
    )
    _stub_provider(monkeypatch)
    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "misconfigured"


async def test_invite_to_page_succeeds(db_session, monkeypatch):
    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.CONNECTED,
    )
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE,
        {"page_id": "99999"},
    )

    captured = {}
    async def fake_invite(account, profile, page_id):
        captured["page_id"] = page_id
        return ActionResult(ok=True)
    _stub_provider(monkeypatch, invite_to_page=fake_invite)

    result = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert result["status"] == "sent"
    assert captured["page_id"] == "99999"


# --------------------------------------------------------------------------
# Per-kind rate caps
# --------------------------------------------------------------------------


async def test_connect_cap_blocks_after_subcap_hit(db_session, monkeypatch):
    """Set the connect cap to 1, then run two connects in a row."""
    from app.config import settings
    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 100)
    monkeypatch.setattr(settings, "LINKEDIN_DAILY_CONNECT_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(db_session, campaign)
    node = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_CONNECT,
        {"note_template": "Hi {{first_name}}"},
    )
    _stub_provider(monkeypatch)

    rc = seq_mod._li_redis()
    await rc.delete(
        f"li-rate:{acc.id}:day",
        f"li-rate:{acc.id}:day:connect",
        f"li-rate:{acc.id}:last",
    )

    r1 = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert r1["status"] == "sent", r1

    r2 = await sequencer._send_linkedin_step_async(str(lead.id), str(node.id))
    assert r2["status"] == "rate_limited"
    assert "connect" in r2["error"]


async def test_page_invite_cap_is_per_page(db_session, monkeypatch):
    """Cap=1 for page A; reaching it doesn't block page B."""
    from app.config import settings
    monkeypatch.setattr(settings, "LINKEDIN_DAILY_ACTION_CAP", 100)
    monkeypatch.setattr(settings, "LINKEDIN_MONTHLY_PAGE_INVITE_CAP", 1)
    monkeypatch.setattr(settings, "LINKEDIN_MIN_ACTION_DELAY_SECONDS", 0)

    import app.workers.sequencer as seq_mod
    monkeypatch.setattr(seq_mod, "_LI_REDIS_CLIENT", None)

    acc = await _make_li_account(db_session)
    campaign = await _make_campaign(db_session, linkedin_account_id=acc.id)
    lead = await _make_lead(
        db_session, campaign, connection=LinkedInConnectionStatus.CONNECTED,
    )
    node_a = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE,
        {"page_id": "PAGE_A"},
    )
    node_b = await _build_seq_with_node(
        db_session, campaign, SequenceNodeKind.LINKEDIN_INVITE_TO_PAGE,
        {"page_id": "PAGE_B"},
    )
    _stub_provider(monkeypatch)

    rc = seq_mod._li_redis()
    await rc.delete(
        f"li-rate:page:PAGE_A:month",
        f"li-rate:page:PAGE_B:month",
        f"li-rate:{acc.id}:day",
        f"li-rate:{acc.id}:last",
    )

    r_a1 = await sequencer._send_linkedin_step_async(str(lead.id), str(node_a.id))
    assert r_a1["status"] == "sent"

    r_a2 = await sequencer._send_linkedin_step_async(str(lead.id), str(node_a.id))
    assert r_a2["status"] == "rate_limited"
    assert "page invite" in r_a2["error"]

    r_b1 = await sequencer._send_linkedin_step_async(str(lead.id), str(node_b.id))
    assert r_b1["status"] == "sent"  # different page, cap not yet hit


# --------------------------------------------------------------------------
# Publish-time config validation
# --------------------------------------------------------------------------


async def test_publish_rejects_overlong_connect_note(client):
    cid_resp = await client.post("/campaigns/", json={
        "name": "x", "goal": "g", "tone": "Direct",
        "sender_name": "A", "sender_email": "a@example.com",
        "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
    })
    assert cid_resp.status_code == 201
    cid = cid_resp.json()["id"]

    payload = {
        "nodes": [
            {"client_id": "e", "kind": "email", "is_entry": True, "config": {}},
            {
                "client_id": "c", "kind": "linkedin_connect", "is_entry": False,
                "config": {"note_template": "x" * 350},
            },
        ],
        "edges": [
            {"from_client_id": "e", "to_client_id": "c", "condition": {"op": "always"}},
        ],
    }
    await client.put(f"/campaigns/{cid}/sequence", json=payload)
    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    body = pub.json()
    assert body["ok"] is False
    assert any("note_template" in e and "300" in e for e in body["errors"])


async def test_publish_rejects_linkedin_invite_to_page(client):
    """linkedin_invite_to_page is currently gated out of PUBLISHABLE_KINDS
    because Unipile's /api/v1/linkedin passthrough doesn't allowlist
    voyagerRelationshipsDashInvitations.  Publishing a sequence that uses
    the kind should fail with a clear error pointing at the gated kind."""
    cid_resp = await client.post("/campaigns/", json={
        "name": "x", "goal": "g", "tone": "Direct",
        "sender_name": "A", "sender_email": "a@example.com",
        "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
    })
    cid = cid_resp.json()["id"]

    payload = {
        "nodes": [
            {"client_id": "e", "kind": "email", "is_entry": True, "config": {}},
            {
                "client_id": "i", "kind": "linkedin_invite_to_page", "is_entry": False,
                "config": {"page_id": "112935410"},
            },
        ],
        "edges": [
            {"from_client_id": "e", "to_client_id": "i", "condition": {"op": "always"}},
        ],
    }
    await client.put(f"/campaigns/{cid}/sequence", json=payload)
    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    body = pub.json()
    assert body["ok"] is False
    assert any("linkedin_invite_to_page" in e for e in body["errors"])


async def test_publish_rejects_linkedin_inmail(client):
    """linkedin_inmail is gated out of PUBLISHABLE_KINDS until Unipile
    Sales Nav API access is enabled on the workspace — otherwise the
    step would silently skip on every lead with `resource_access_restricted`.
    Better to refuse to publish a sequence the user can't actually run."""
    cid_resp = await client.post("/campaigns/", json={
        "name": "x", "goal": "g", "tone": "Direct",
        "sender_name": "A", "sender_email": "a@example.com",
        "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
    })
    cid = cid_resp.json()["id"]

    payload = {
        "nodes": [
            {"client_id": "e", "kind": "email", "is_entry": True, "config": {}},
            {
                "client_id": "im", "kind": "linkedin_inmail", "is_entry": False,
                "config": {"subject_template": "Hey", "body_template": "Hi {{first_name}}"},
            },
        ],
        "edges": [
            {"from_client_id": "e", "to_client_id": "im", "condition": {"op": "always"}},
        ],
    }
    await client.put(f"/campaigns/{cid}/sequence", json=payload)
    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    body = pub.json()
    assert body["ok"] is False
    assert any("linkedin_inmail" in e for e in body["errors"])


# test_publish_rejects_non_numeric_page_id removed — linkedin_invite_to_page
# is currently gated out of PUBLISHABLE_KINDS, so the numeric-page_id check
# is unreachable from the publish path.  Restore alongside re-enabling the
# kind if Unipile allowlists voyagerRelationshipsDashInvitations.
