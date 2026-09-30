"""Record ownership: defaults, inheritance, assignment, filters, member
removal, and owner-routed notifications."""
from __future__ import annotations

import uuid

from sqlalchemy import select, text

from app.models import (
    Campaign,
    CrmActivity,
    Lead,
    Notification,
    NotificationKind,
    Opportunity,
    OpportunityStage,
)
from app.services import agent_core, notifications
from tests.conftest import DEFAULT_TENANT_ID, DEFAULT_USER_ID, create_test_user


async def _member(engine, role="member", **kw):
    uid, _, token = await create_test_user(engine, tenant_id=DEFAULT_TENANT_ID, role=role, **kw)
    return uid, token


# --- Defaults ---------------------------------------------------------------


async def test_creator_owns_records_created_via_api(client_factory, _engine, db_session):
    uid, token = await _member(_engine)
    c = client_factory(token)
    lead = (await c.post("/crm/leads", json={"email": "a@acme.com"})).json()
    opp = (await c.post("/crm/opportunities", json={"name": "Deal"})).json()
    assert lead["owner_id"] == str(uid)
    assert opp["owner_id"] == str(uid)


async def test_explicit_owner_and_unassigned_on_create(client, _engine):
    uid, _ = await _member(_engine)
    owned = (await client.post("/crm/leads", json={"email": "b@acme.com", "owner_id": str(uid)})).json()
    unassigned = (await client.post("/crm/leads", json={"email": "c@acme.com", "owner_id": None})).json()
    assert owned["owner_id"] == str(uid)
    assert unassigned["owner_id"] is None


async def test_create_with_non_member_owner_rejected(client, _engine):
    stranger, _, _ = await create_test_user(_engine)  # another workspace
    resp = await client.post("/crm/leads", json={"email": "d@acme.com", "owner_id": str(stranger)})
    assert resp.status_code == 422


async def test_converted_opportunity_inherits_lead_owner(client, _engine, db_session):
    uid, _ = await _member(_engine)
    lead = Lead(email="e@acme.com", owner_id=uid)
    db_session.add(lead)
    await db_session.commit()
    resp = await client.post(f"/crm/leads/{lead.id}/convert", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["opportunity"]["owner_id"] == str(uid)


async def test_activity_defaults_to_deal_owner(client, _engine, db_session):
    uid, _ = await _member(_engine)
    opp = Opportunity(name="x", stage=OpportunityStage.PROSPECTING, owner_id=uid)
    db_session.add(opp)
    await db_session.commit()
    resp = await client.post("/crm/activities", json={
        "opportunity_id": str(opp.id), "activity_type": "task", "subject": "Call",
    })
    assert resp.json()["owner_id"] == str(uid)


async def test_leads_added_to_campaign_inherit_campaign_owner(db_session, _engine):
    from app.services.campaign_membership import add_leads_to_campaign

    uid, _ = await _member(_engine)
    from datetime import time as dt_time

    camp = Campaign(
        name="c", goal="g", tone="t", sender_name="s", sender_email="s@x.com", owner_id=uid,
        schedule_days=[], schedule_time_start=dt_time(0, 0), schedule_time_end=dt_time(23, 59),
    )
    src = Lead(email="src@acme.com", owner_id=DEFAULT_USER_ID)
    db_session.add_all([camp, src])
    await db_session.commit()
    await add_leads_to_campaign(db_session, camp, [src.id])
    copied = await db_session.scalar(
        select(Lead).where(Lead.campaign_id == camp.id, Lead.email == "src@acme.com")
    )
    assert copied.owner_id == uid


async def test_background_rows_fall_back_to_workspace_owner(db_session):
    from app.tenancy.context import current_user_id

    token = current_user_id.set(None)  # a background job: no acting user
    try:
        lead = Lead(email="bg@acme.com")
        db_session.add(lead)
        await db_session.commit()
    finally:
        current_user_id.reset(token)
    assert lead.owner_id == DEFAULT_USER_ID


# --- Assignment -------------------------------------------------------------


async def test_single_assign_via_patch_notifies_new_owner(client, _engine, db_session):
    uid, _ = await _member(_engine, name="Sam")
    lead = Lead(email="f@acme.com")
    db_session.add(lead)
    await db_session.commit()
    resp = await client.patch(f"/crm/leads/{lead.id}", json={"owner_id": str(uid)})
    assert resp.status_code == 200 and resp.json()["owner_id"] == str(uid)
    note = await db_session.scalar(select(Notification).where(Notification.kind == NotificationKind.ASSIGNED))
    assert note.user_id == uid and note.lead_id == lead.id
    assert "assigned you a lead" in note.title


async def test_bulk_assign_and_unassign(client, _engine, db_session):
    uid, _ = await _member(_engine)
    leads = [Lead(email=f"g{i}@acme.com") for i in range(3)]
    db_session.add_all(leads)
    await db_session.commit()
    resp = await client.post("/owners/assign", json={
        "record_type": "lead", "ids": [str(l.id) for l in leads], "owner_id": str(uid),
    })
    assert resp.json() == {"updated": 3}
    notes = (await db_session.execute(
        select(Notification).where(Notification.kind == NotificationKind.ASSIGNED)
    )).scalars().all()
    assert len(notes) == 1 and "3 leads" in notes[0].title  # one per batch

    resp = await client.post("/owners/assign", json={
        "record_type": "lead", "ids": [str(leads[0].id)], "owner_id": None,
    })
    assert resp.json() == {"updated": 1}
    await db_session.refresh(leads[0])
    assert leads[0].owner_id is None


async def test_bulk_assign_ignores_other_workspace_ids(client, client_factory, _engine, db_session):
    from app.tenancy.context import tenant_scope

    other_uid, other_tid, other_token = await create_test_user(_engine, role="owner")
    with tenant_scope(other_tid, other_uid):
        theirs = Lead(email="theirs@x.com")
        db_session.add(theirs)
        await db_session.commit()
    resp = await client.post("/owners/assign", json={
        "record_type": "lead", "ids": [str(theirs.id)], "owner_id": str(DEFAULT_USER_ID),
    })
    assert resp.json() == {"updated": 0}
    async with _engine.connect() as conn:
        owner = (await conn.execute(text("SELECT owner_id FROM leads WHERE id = :i"), {"i": theirs.id})).scalar()
    assert owner == other_uid


async def test_assign_to_non_member_rejected(client, _engine, db_session):
    stranger, _, _ = await create_test_user(_engine)
    lead = Lead(email="h@acme.com")
    db_session.add(lead)
    await db_session.commit()
    resp = await client.post("/owners/assign", json={
        "record_type": "lead", "ids": [str(lead.id)], "owner_id": str(stranger),
    })
    assert resp.status_code == 422


async def test_member_can_assign(client_factory, _engine, db_session):
    uid, token = await _member(_engine)
    lead = Lead(email="i@acme.com")
    db_session.add(lead)
    await db_session.commit()
    resp = await client_factory(token).post("/owners/assign", json={
        "record_type": "lead", "ids": [str(lead.id)], "owner_id": str(uid),
    })
    assert resp.json() == {"updated": 1}


# --- Filters ----------------------------------------------------------------


async def test_owner_filters_on_lists(client, _engine, db_session):
    uid, _ = await _member(_engine)
    db_session.add_all([
        Lead(email="mine@x.com"),                     # creator = DEFAULT_USER
        Lead(email="theirs@x.com", owner_id=uid),
        Lead(email="nobody@x.com", owner_id=None),
        Opportunity(name="my deal", stage=OpportunityStage.PROPOSAL),
        Opportunity(name="their deal", stage=OpportunityStage.PROPOSAL, owner_id=uid),
    ])
    await db_session.commit()

    def emails(resp):
        return sorted(i["email"] for i in resp.json()["items"])

    assert emails(await client.get("/leads?owner=me")) == ["mine@x.com"]
    assert emails(await client.get("/leads?owner=unassigned")) == ["nobody@x.com"]
    assert emails(await client.get(f"/leads?owner={uid}")) == ["theirs@x.com"]
    assert len((await client.get("/leads")).json()["items"]) == 3
    opps = (await client.get("/crm/opportunities?owner=me")).json()["items"]
    assert [o["name"] for o in opps] == ["my deal"]
    pipeline = (await client.get("/crm/opportunities/pipeline?owner=me")).json()
    assert sum(s["count"] for s in pipeline) == 1
    assert (await client.get("/leads?owner=bogus")).status_code == 422


# --- Member removal ---------------------------------------------------------


async def test_removing_member_unassigns_their_records(client, _engine, db_session):
    uid, _ = await _member(_engine)
    lead = Lead(email="j@acme.com", owner_id=uid)
    db_session.add(lead)
    await db_session.commit()
    assert (await client.delete(f"/team/members/{uid}")).status_code == 204
    await db_session.refresh(lead)
    assert lead.owner_id is None  # FK ON DELETE SET NULL (owner_id)
    assert lead.tenant_id == DEFAULT_TENANT_ID  # the row stays in the workspace


async def test_removing_member_with_reassign(client, _engine, db_session):
    leaving, _ = await _member(_engine)
    taking_over, _ = await _member(_engine)
    lead = Lead(email="k@acme.com", owner_id=leaving)
    opp = Opportunity(name="deal", stage=OpportunityStage.PROPOSAL, owner_id=leaving)
    db_session.add_all([lead, opp])
    await db_session.commit()
    resp = await client.delete(f"/team/members/{leaving}?reassign_to={taking_over}")
    assert resp.status_code == 204
    await db_session.refresh(lead)
    await db_session.refresh(opp)
    assert lead.owner_id == taking_over and opp.owner_id == taking_over


# --- Notification routing ---------------------------------------------------


async def test_record_notification_routes_to_owner(db_session, _engine, set_creds):
    from unittest.mock import AsyncMock, patch

    set_creds("brevo", api_key="k")
    uid, _ = await _member(_engine, email="rep@acme.com")
    lead = Lead(email="l@acme.com", owner_id=uid)
    db_session.add(lead)
    await db_session.commit()
    agent_settings = await agent_core.get_agent_settings(db_session)
    send = AsyncMock(return_value="m")
    with patch("app.services.notifications.brevo.send_email", new=send):
        out = await notifications.notify(
            db_session, agent_settings, kind=NotificationKind.POSITIVE_REPLY,
            title="Positive reply", dedup_key="pr:1", lead_id=lead.id,
        )
    assert out["notification"].user_id == uid
    assert [c.kwargs["to_email"] for c in send.call_args_list] == ["rep@acme.com"]


async def test_email_preference_respected(db_session, _engine, set_creds):
    from unittest.mock import AsyncMock, patch

    set_creds("brevo", api_key="k")
    uid, _ = await _member(_engine, email="quiet@acme.com")
    async with _engine.begin() as conn:
        await conn.execute(text("UPDATE memberships SET notify_email_enabled = false WHERE user_id = :u"), {"u": uid})
    lead = Lead(email="m@acme.com", owner_id=uid)
    db_session.add(lead)
    await db_session.commit()
    agent_settings = await agent_core.get_agent_settings(db_session)
    send = AsyncMock(return_value="m")
    with patch("app.services.notifications.brevo.send_email", new=send):
        out = await notifications.notify(
            db_session, agent_settings, kind=NotificationKind.REPLY,
            title="Reply", dedup_key="r:1", lead_id=lead.id,
        )
    assert out["emailed"] is False
    send.assert_not_awaited()


async def test_task_reminder_goes_to_assignee(db_session, _engine):
    uid, _ = await _member(_engine)
    task = CrmActivity(
        activity_type="task", subject="Call", owner_id=uid,
        lead_id=(await _lead(db_session)).id,
    )
    db_session.add(task)
    await db_session.commit()
    note = await notifications.create_notification(
        db_session, kind=NotificationKind.TASK_DUE, title="t", dedup_key="td:1",
        activity_id=task.id, lead_id=task.lead_id,
    )
    assert note.user_id == uid


async def _lead(db_session):
    lead = Lead(email=f"{uuid.uuid4().hex[:6]}@acme.com")
    db_session.add(lead)
    await db_session.commit()
    return lead


async def test_notification_feed_scoped_to_me_and_workspace(client, _engine, db_session):
    uid, _ = await _member(_engine)
    db_session.add_all([
        Notification(kind=NotificationKind.REPLY, title="mine", dedup_key="n1", user_id=DEFAULT_USER_ID),
        Notification(kind=NotificationKind.REPLY, title="theirs", dedup_key="n2", user_id=uid),
        Notification(kind=NotificationKind.REPLY, title="everyone", dedup_key="n3", user_id=None),
    ])
    await db_session.commit()
    mine = (await client.get("/agent/notifications")).json()
    assert sorted(i["title"] for i in mine["items"]) == ["everyone", "mine"]
    assert mine["unread"] == 2
    everything = (await client.get("/agent/notifications?scope=all")).json()
    assert len(everything["items"]) == 3
    await client.post("/agent/notifications/read-all")
    theirs = await db_session.scalar(select(Notification).where(Notification.title == "theirs"))
    await db_session.refresh(theirs)
    assert theirs.read_at is None  # read-all only touches my feed


# --- Every response that carries a record must carry its owner ------------------
# (Regression: campaign responses were built from a hand-written field list and
# the lead detail field-by-field, so both silently dropped owner_id and the UI
# showed every record as "Unassigned" even though the DB had an owner.)


async def test_campaign_responses_include_owner(client, _engine):
    uid, _ = await _member(_engine)
    created = await client.post("/campaigns/", json={
        "name": "Owned", "goal": "g", "tone": "t", "sender_name": "S", "sender_email": "s@x.com",
        "schedule_time_start": "09:00:00", "schedule_time_end": "17:00:00",
    })
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["owner_id"] == str(DEFAULT_USER_ID)
    assert (await client.get(f"/campaigns/{cid}")).json()["owner_id"] == str(DEFAULT_USER_ID)
    assert (await client.get("/campaigns/")).json()[0]["owner_id"] == str(DEFAULT_USER_ID)
    patched = await client.patch(f"/campaigns/{cid}", json={"owner_id": str(uid)})
    assert patched.json()["owner_id"] == str(uid)
    assert (await client.get(f"/campaigns/{cid}")).json()["owner_id"] == str(uid)


async def test_lead_detail_includes_owner(client, _engine, db_session):
    uid, _ = await _member(_engine)
    lead = Lead(email="detail@acme.com", owner_id=uid)
    db_session.add(lead)
    await db_session.commit()
    assert (await client.get(f"/leads/{lead.id}")).json()["owner_id"] == str(uid)
