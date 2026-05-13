"""Tests for M5: soft-delete + per-node analytics endpoint."""
import uuid
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
    SendStatus,
    Sequence,
    SequenceEdge,
    SequenceNode,
    SequenceNodeKind,
)
from app.services.sequence_service import (
    ensure_default_sequence,
    enroll_leads,
    replace_graph,
)
from app.workers import sequencer


@pytest.fixture(autouse=True)
def _reset_li_redis_singleton(monkeypatch):
    monkeypatch.setattr(sequencer, "_LI_REDIS_CLIENT", None)


async def _make_campaign(db_session) -> Campaign:
    c = Campaign(
        name="m5-test", goal="g", tone="Direct",
        sender_name="A", sender_email="a@example.com",
        schedule_days=list(range(7)),
        schedule_time_start=time(0, 0), schedule_time_end=time(23, 59),
        schedule_timezone="UTC", status=CampaignStatus.RUNNING,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


# --------------------------------------------------------------------------
# Soft-delete
# --------------------------------------------------------------------------


async def test_replace_graph_soft_deletes_existing_nodes(db_session):
    campaign = await _make_campaign(db_session)
    seq = await ensure_default_sequence(db_session, campaign)
    await db_session.commit()

    # Capture the original entry node id.
    original_entry = await db_session.scalar(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id, SequenceNode.is_entry.is_(True),
        )
    )

    await replace_graph(db_session, seq, [
        {"client_id": "e", "kind": "email", "is_entry": True, "config": {}, "position_x": 0, "position_y": 0},
    ], [])
    await db_session.commit()

    # Original entry node still exists in the table, just soft-deleted.
    await db_session.refresh(original_entry)
    assert original_entry.deleted_at is not None

    # A new live entry node was created.
    live_entries = (await db_session.execute(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id,
            SequenceNode.is_entry.is_(True),
            SequenceNode.deleted_at.is_(None),
        )
    )).scalars().all()
    assert len(live_entries) == 1
    assert live_entries[0].id != original_entry.id


async def test_soft_deleted_nodes_preserve_step_executions(db_session):
    """Step-execution rows pointing at retired nodes survive the rewrite."""
    campaign = await _make_campaign(db_session)
    seq = await ensure_default_sequence(db_session, campaign)
    await db_session.commit()
    entry = await db_session.scalar(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id, SequenceNode.is_entry.is_(True),
        )
    )
    lead = Lead(
        campaign_id=campaign.id, email="x@x.com",
        send_status=SendStatus.PENDING,
    )
    db_session.add(lead)
    await db_session.commit()

    db_session.add(LeadStepExecution(
        lead_id=lead.id, node_id=entry.id,
        result=LeadStepResult.SENT, external_id="msg-1",
    ))
    await db_session.commit()

    # Rewrite the graph.
    await replace_graph(db_session, seq, [
        {"client_id": "new", "kind": "email", "is_entry": True, "config": {}, "position_x": 0, "position_y": 0},
    ], [])
    await db_session.commit()

    # Execution row still exists (FK didn't cascade-delete it).
    rows = (await db_session.execute(
        select(LeadStepExecution).where(LeadStepExecution.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].node_id == entry.id  # points at the now-retired node


async def test_scheduler_halts_lead_on_deleted_node(db_session):
    campaign = await _make_campaign(db_session)
    seq = await ensure_default_sequence(db_session, campaign)
    entry = await db_session.scalar(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id, SequenceNode.is_entry.is_(True),
        )
    )
    lead = Lead(
        campaign_id=campaign.id, email="x@x.com",
        send_status=SendStatus.SENT,
    )
    db_session.add(lead)
    await db_session.commit()
    state = LeadSequenceState(
        lead_id=lead.id, sequence_id=seq.id,
        current_node_id=entry.id, status=LeadSequenceStatus.ACTIVE,
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        entered_current_at=datetime.now(timezone.utc),
    )
    db_session.add(state)
    await db_session.commit()

    # Retire the entry node.
    entry.deleted_at = datetime.now(timezone.utc)
    await db_session.commit()

    await sequencer._advance_sequences_async()

    await db_session.refresh(state)
    assert state.status == LeadSequenceStatus.HALTED
    assert "deleted" in (state.halt_reason or "")


# --------------------------------------------------------------------------
# Analytics endpoint
# --------------------------------------------------------------------------


def _payload(**overrides):
    base = {
        "name": "x", "goal": "g", "tone": "Direct",
        "sender_name": "A", "sender_email": "a@example.com",
        "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
    }
    base.update(overrides)
    return base


async def test_analytics_empty_campaign_returns_zeros(client):
    r = await client.post("/campaigns/", json=_payload())
    cid = r.json()["id"]
    a = await client.get(f"/campaigns/{cid}/sequence/analytics")
    assert a.status_code == 200
    body = a.json()
    assert body["total_leads"] == 0
    assert body["active"] == 0
    assert len(body["per_node"]) == 1  # the auto-created entry node
    assert body["per_node"][0]["attempted"] == 0


async def test_analytics_counts_step_executions(client, db_session):
    r = await client.post("/campaigns/", json=_payload())
    cid = uuid.UUID(r.json()["id"])

    campaign = await db_session.get(Campaign, cid)
    seq = (await db_session.execute(
        select(Sequence).where(Sequence.campaign_id == cid)
    )).scalar_one()
    entry = (await db_session.execute(
        select(SequenceNode).where(
            SequenceNode.sequence_id == seq.id, SequenceNode.is_entry.is_(True),
        )
    )).scalar_one()

    # Three leads with various step outcomes on the entry node.
    leads = []
    for i in range(3):
        l = Lead(campaign_id=cid, email=f"l{i}@x.com", send_status=SendStatus.SENT)
        db_session.add(l)
        leads.append(l)
    await db_session.commit()
    for l in leads:
        await db_session.refresh(l)

    db_session.add_all([
        LeadStepExecution(lead_id=leads[0].id, node_id=entry.id, result=LeadStepResult.SENT),
        LeadStepExecution(lead_id=leads[1].id, node_id=entry.id, result=LeadStepResult.SENT),
        LeadStepExecution(lead_id=leads[2].id, node_id=entry.id, result=LeadStepResult.SKIPPED),
    ])
    await db_session.commit()

    a = await client.get(f"/campaigns/{cid}/sequence/analytics")
    body = a.json()
    assert len(body["per_node"]) == 1
    node = body["per_node"][0]
    assert node["attempted"] == 3
    assert node["sent"] == 2
    assert node["skipped"] == 1
    assert node["failed"] == 0


async def test_analytics_counts_active_leads(client, db_session):
    r = await client.post("/campaigns/", json=_payload())
    cid = uuid.UUID(r.json()["id"])
    campaign = await db_session.get(Campaign, cid)
    await enroll_leads(db_session, cid, [])  # no-op (no leads yet)

    # Add two leads, enroll them on the entry node.
    leads = []
    for i in range(2):
        l = Lead(campaign_id=cid, email=f"a{i}@x.com")
        db_session.add(l)
        leads.append(l)
    await db_session.commit()
    for l in leads:
        await db_session.refresh(l)
    await enroll_leads(db_session, cid, [l.id for l in leads])
    await db_session.commit()

    a = await client.get(f"/campaigns/{cid}/sequence/analytics")
    body = a.json()
    assert body["total_leads"] == 2
    assert body["active"] == 2
    assert body["per_node"][0]["currently_here"] == 2


async def test_analytics_excludes_soft_deleted_nodes(client, db_session):
    r = await client.post("/campaigns/", json=_payload())
    cid = uuid.UUID(r.json()["id"])
    seq = (await db_session.execute(
        select(Sequence).where(Sequence.campaign_id == cid)
    )).scalar_one()

    # Rewrite the graph — old entry node becomes soft-deleted.
    await replace_graph(db_session, seq, [
        {"client_id": "new", "kind": "email", "is_entry": True, "config": {}, "position_x": 0, "position_y": 0},
    ], [])
    await db_session.commit()

    a = await client.get(f"/campaigns/{cid}/sequence/analytics")
    body = a.json()
    # Only the live (new) entry node shows up — not the retired one.
    assert len(body["per_node"]) == 1
