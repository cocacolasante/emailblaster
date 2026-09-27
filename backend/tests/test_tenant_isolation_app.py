"""Cross-workspace isolation through the real API (app-level scoping).

Workspace B gets one of everything; the default client (owner of
workspace A) must never see, read, change, or delete any of it — lists
exclude it, detail/PATCH/DELETE return 404, aggregates don't count it,
and bulk operations don't touch it.  (The Postgres RLS safety net under
the non-owner role is covered separately in test_rls_isolation.py.)
"""
from __future__ import annotations

import uuid
from datetime import time as dt_time

import pytest
from sqlalchemy import text

from app.models import (
    Campaign,
    ConnectedAccount,
    CrmActivity,
    CrmActivityType,
    Lead,
    LinkedInAccount,
    LinkedInAccountStatus,
    Notification,
    NotificationKind,
    Opportunity,
    OpportunityStage,
    ReportDefinition,
    SignalWatch,
    SignalWatchStatus,
    SignalWatchType,
    SocialListeningSearch,
    SocialSearchFrequency,
    SocialSearchStatus,
    Suppression,
    SuppressionReason,
)
from app.services import tenant_seed
from app.tenancy.context import tenant_scope
from tests.conftest import create_test_user


@pytest.fixture
async def other(db_session, _engine):
    """Workspace B, fully populated.  Returns a dict of B's record ids."""
    uid, tid, token = await create_test_user(_engine, role="owner", email="b-owner@b.com",
                                             new_tenant_name="Workspace B")
    ids: dict[str, uuid.UUID] = {"tenant": tid, "user": uid}
    with tenant_scope(tid, uid):
        await tenant_seed.seed_tenant_defaults(db_session, tid)
        camp = Campaign(
            name="B campaign", goal="g", tone="t", sender_name="B", sender_email="b@b.com",
            schedule_days=[0, 1, 2, 3, 4], schedule_time_start=dt_time(9), schedule_time_end=dt_time(17),
        )
        db_session.add(camp)
        await db_session.flush()
        lead = Lead(campaign_id=camp.id, email="secret@b.com", first_name="Bea", notes="b notes")
        opp = Opportunity(name="B deal", stage=OpportunityStage.PROPOSAL, amount=99999)
        db_session.add_all([lead, opp])
        await db_session.flush()
        act = CrmActivity(lead_id=lead.id, activity_type=CrmActivityType.TASK, subject="B task")
        db_session.add_all([
            act,
            Suppression(email="shared@x.com", reason=SuppressionReason.MANUAL),
            Notification(kind=NotificationKind.REPLY, title="B notice", dedup_key="b1"),
            ReportDefinition(name="B report", data_source="leads", definition={"columns": ["email"]}),
            SocialListeningSearch(name="B search", topic="t", status=SocialSearchStatus.ACTIVE),
            SignalWatch(watch_type=SignalWatchType.JOB_CHANGE, frequency=SocialSearchFrequency.DAILY,
                        status=SignalWatchStatus.ACTIVE, person_name="B person"),
            ConnectedAccount(label="B inbox", email_address="inbox@b.com", imap_host="imap.b.com",
                             username="inbox@b.com", password_encrypted="x"),
            LinkedInAccount(label="B li", linkedin_email="li@b.com", provider_kind="unipile",
                            unipile_account_id="b-unipile", status=LinkedInAccountStatus.OK),
        ])
        await db_session.commit()
        ids.update(campaign=camp.id, lead=lead.id, opp=opp.id, activity=act.id)
        for model, key in ((ReportDefinition, "report"), (SocialListeningSearch, "search"),
                           (ConnectedAccount, "inbox"), (LinkedInAccount, "linkedin")):
            from sqlalchemy import select
            ids[key] = (await db_session.execute(select(model.id))).scalars().one()
    ids["token"] = token  # type: ignore[assignment]
    return ids


def _ids(resp) -> set[str]:
    body = resp.json()
    items = body if isinstance(body, list) else body.get("items", body.get("searches", []))
    return {str(i.get("id")) for i in items if isinstance(i, dict)}


async def test_lists_exclude_other_workspace(client, other):
    checks = {
        "/campaigns/": other["campaign"],
        "/leads": other["lead"],
        "/crm/opportunities": other["opp"],
        "/crm/activities": other["activity"],
        "/reports": other["report"],
        "/connected-accounts/": other["inbox"],
        "/linkedin-accounts/": other["linkedin"],
        "/social-radar/searches": other["search"],
    }
    for path, foreign_id in checks.items():
        resp = await client.get(path)
        assert resp.status_code == 200, (path, resp.text)
        assert str(foreign_id) not in _ids(resp), path
        assert str(foreign_id) not in resp.text, path
    watches = await client.get("/signals/watches")
    assert "B person" not in watches.text
    feed = await client.get("/agent/notifications?scope=all")
    assert "B notice" not in feed.text


async def test_details_404_for_other_workspace(client, other):
    for path in (
        f"/campaigns/{other['campaign']}",
        f"/campaigns/{other['campaign']}/leads",
        f"/campaigns/{other['campaign']}/analytics",
        f"/leads/{other['lead']}",
        f"/crm/opportunities/{other['opp']}",
        f"/reports/{other['report']}",
        f"/connected-accounts/{other['inbox']}",
        f"/linkedin-accounts/{other['linkedin']}",
        f"/social-radar/searches/{other['search']}",
    ):
        resp = await client.get(path)
        assert resp.status_code == 404, (path, resp.status_code, resp.text[:200])


async def test_writes_404_for_other_workspace(client, other, _engine):
    assert (await client.patch(f"/crm/leads/{other['lead']}", json={"notes": "pwned"})).status_code == 404
    assert (await client.patch(f"/crm/opportunities/{other['opp']}", json={"name": "pwned"})).status_code == 404
    assert (await client.patch(f"/crm/activities/{other['activity']}", json={"subject": "pwned"})).status_code == 404
    assert (await client.patch(f"/campaigns/{other['campaign']}", json={"name": "pwned"})).status_code == 404
    assert (await client.delete(f"/crm/opportunities/{other['opp']}")).status_code == 404
    assert (await client.delete(f"/campaigns/{other['campaign']}")).status_code == 404
    assert (await client.delete(f"/reports/{other['report']}")).status_code == 404
    bulk = await client.post("/owners/assign", json={
        "record_type": "lead", "ids": [str(other["lead"])], "owner_id": None,
    })
    assert bulk.json() == {"updated": 0}
    async with _engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT l.notes, l.owner_id, o.name FROM leads l, crm_opportunities o "
            "WHERE l.id = :l AND o.id = :o"), {"l": other["lead"], "o": other["opp"]})).one()
    assert row == ("b notes", other["user"], "B deal")


async def test_aggregates_exclude_other_workspace(client, other):
    pipeline = (await client.get("/crm/opportunities/pipeline")).json()
    assert sum(s["count"] for s in pipeline) == 0
    overview = await client.get("/crm/reports/overview")
    assert overview.status_code == 200
    assert "99999" not in overview.text
    run = await client.post("/reports/run", json={
        "data_source": "leads", "definition": {"columns": ["email"]},
    })
    assert run.status_code == 200, run.text
    assert "secret@b.com" not in run.text


async def test_default_pipeline_is_per_workspace(client, other):
    resp = await client.get("/crm/pipelines/default")
    # Workspace A has no seeded pipeline in this test; B's must not leak.
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.json()["id"] not in {str(other["tenant"])}


async def test_other_workspace_sees_only_its_own(client_factory, other):
    b = client_factory(other["token"])
    leads = (await b.get("/leads")).json()["items"]
    assert [l["email"] for l in leads] == ["secret@b.com"]
    campaigns = (await b.get("/campaigns/")).json()
    assert [c["name"] for c in campaigns] == ["B campaign"]


async def test_suppression_does_not_cross_workspaces(db_session, other):
    """B suppressed shared@x.com; A can still email it."""
    from sqlalchemy import select

    q = select(Suppression.id).where(Suppression.email == "shared@x.com")
    assert (await db_session.execute(q)).first() is None
    with tenant_scope(other["tenant"]):
        assert (await db_session.execute(q)).first() is not None
