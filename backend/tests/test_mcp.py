"""Agent access: workspace API keys + the MCP endpoint Muse calls."""
from __future__ import annotations

import json
from datetime import datetime, time, timezone

import pytest
from sqlalchemy import select, text

from app.mcp.tools import TOOLS
from app.models import Campaign, CampaignStatus, Lead, Opportunity, OpportunityStage
from app.tenancy.context import tenant_scope
from tests.conftest import DEFAULT_USER_ID, create_test_user


async def _mint(client, name="Muse on my phone") -> str:
    resp = await client.post("/api-keys", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _rpc(anon_client, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return await anon_client.post("/mcp", json=body, headers=_bearer(token))


async def _call(anon_client, token, name, arguments=None):
    resp = await _rpc(anon_client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    return result, json.loads(result["content"][0]["text"]) if not result["isError"] else result["content"][0]["text"]


# --- keys ---------------------------------------------------------------------


async def test_key_shown_once_and_hashed(client, db_session):
    token = await _mint(client)
    assert token.startswith("eb_")
    listing = (await client.get("/api-keys")).json()
    assert len(listing) == 1 and "token" not in listing[0]
    assert listing[0]["prefix"] == token[:11]
    stored = (await db_session.execute(text("SELECT token_hash FROM api_keys"))).scalar()
    assert token not in stored and len(stored) == 64


async def test_key_authenticates_regular_api_as_its_creator(client, anon_client):
    token = await _mint(client)
    me = await anon_client.get("/campaigns/", headers=_bearer(token))
    assert me.status_code == 200


async def test_key_cannot_mint_revoke_or_manage(client, anon_client):
    token = await _mint(client)
    h = _bearer(token)
    assert (await anon_client.post("/api-keys", json={"name": "x"}, headers=h)).status_code == 403
    key_id = (await client.get("/api-keys")).json()[0]["id"]
    assert (await anon_client.post(f"/api-keys/{key_id}/revoke", headers=h)).status_code == 403
    assert (await anon_client.post("/team/invites", json={"email": "a@b.co"}, headers=h)).status_code == 403
    assert (await anon_client.put("/settings/integrations/hunter", json={"api_key": "x"},
                                  headers=h)).status_code == 403
    assert (await anon_client.patch("/auth/me", json={"name": "x"}, headers=h)).status_code == 403
    assert (await anon_client.delete(f"/team/members/{DEFAULT_USER_ID}", headers=h)).status_code == 403


async def test_revoked_key_stops_working(client, anon_client):
    token = await _mint(client)
    key_id = (await client.get("/api-keys")).json()[0]["id"]
    assert (await client.post(f"/api-keys/{key_id}/revoke")).status_code == 200
    assert (await anon_client.get("/campaigns/", headers=_bearer(token))).status_code == 401
    assert (await _rpc(anon_client, token, "tools/list")).status_code == 401


async def test_key_dies_when_creator_leaves(client_factory, _engine, anon_client):
    uid, tid, session = await create_test_user(_engine, tenant_id=None, role="owner")
    member = client_factory(session)
    token = await _mint(member)
    assert (await anon_client.get("/campaigns/", headers=_bearer(token))).status_code == 200
    async with _engine.begin() as conn:
        await conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": uid})
    assert (await anon_client.get("/campaigns/", headers=_bearer(token))).status_code == 401


async def test_last_used_is_stamped(client, anon_client):
    token = await _mint(client)
    await anon_client.get("/campaigns/", headers=_bearer(token))
    assert (await client.get("/api-keys")).json()[0]["last_used_at"] is not None


# --- protocol -----------------------------------------------------------------


async def test_mcp_requires_a_key(anon_client):
    resp = await anon_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401 and "Bearer" in resp.headers["www-authenticate"]
    bad = await anon_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 headers=_bearer("eb_not-a-real-key-at-all"))
    assert bad.status_code == 401


async def test_initialize_list_and_notifications(client, anon_client):
    token = await _mint(client)
    init = (await _rpc(anon_client, token, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "muse", "version": "1"},
    })).json()["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"]["tools"] == {"listChanged": False}
    assert "daily_brief" in init["instructions"]
    note = await anon_client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                  headers=_bearer(token))
    assert note.status_code == 202
    tools = (await _rpc(anon_client, token, "tools/list")).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {t.name for t in TOOLS}
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    unknown = (await _rpc(anon_client, token, "resources/list")).json()
    assert unknown["error"]["code"] == -32601
    assert (await anon_client.get("/mcp", headers=_bearer(token))).status_code == 405


# --- tools --------------------------------------------------------------------


async def _campaign(db_session, **kw) -> Campaign:
    c = Campaign(name=kw.pop("name", "Q4 push"), goal="g", tone="t", sender_name="S",
                 sender_email="s@x.com", schedule_days=[0, 1, 2, 3, 4],
                 schedule_time_start=time(9), schedule_time_end=time(17), **kw)
    db_session.add(c)
    await db_session.commit()
    return c


async def test_daily_brief_and_reads(client, anon_client, db_session):
    camp = await _campaign(db_session, status=CampaignStatus.RUNNING)
    db_session.add_all([Lead(campaign_id=camp.id, email="jane@acme.com", first_name="Jane", company="Acme"),
                        Opportunity(name="Acme deal", stage=OpportunityStage.PROPOSAL, amount=5000)])
    await db_session.commit()
    token = await _mint(client)
    _, brief = await _call(anon_client, token, "daily_brief")
    assert [c["name"] for c in brief["campaigns"]] == ["Q4 push"]
    assert any(s["stage"] == "proposal" for s in brief["pipeline"])
    _, found = await _call(anon_client, token, "search_leads", {"query": "acme"})
    assert found["total"] == 1 and found["leads"][0]["email"] == "jane@acme.com"
    _, lead = await _call(anon_client, token, "get_lead", {"lead_id": found["leads"][0]["id"]})
    assert lead["company"] == "Acme"
    _, team = await _call(anon_client, token, "list_team_members")
    assert team[0]["is_me"] is True


async def test_tools_only_see_the_keys_workspace(client, anon_client, db_session, _engine):
    uid, tid, _ = await create_test_user(_engine, role="owner")
    with tenant_scope(tid, uid):
        db_session.add(Lead(email="secret@other.com"))
        await db_session.commit()
        other_lead = (await db_session.execute(select(Lead.id))).scalar()
    token = await _mint(client)
    _, found = await _call(anon_client, token, "search_leads", {"query": "secret"})
    assert found["total"] == 0
    result, msg = await _call(anon_client, token, "get_lead", {"lead_id": str(other_lead)})
    assert result["isError"] and "not found" in msg.lower()


async def test_crm_writes_run_as_the_key_owner(client, anon_client, db_session):
    token = await _mint(client)
    _, created = await _call(anon_client, token, "create_lead",
                             {"email": "new@lead.com", "first_name": "Nia", "notes": "met at expo"})
    assert created["owner_id"] == str(DEFAULT_USER_ID)
    _, converted = await _call(anon_client, token, "convert_lead",
                               {"lead_id": created["id"], "amount": 12000, "stage": "qualification"})
    opp_id = converted["opportunity"]["id"]
    _, moved = await _call(anon_client, token, "update_opportunity", {"opportunity_id": opp_id, "stage": "proposal"})
    assert moved["stage"] == "proposal"
    _, task = await _call(anon_client, token, "create_task", {
        "opportunity_id": opp_id, "subject": "Send proposal", "due_at": "2026-10-01T15:00:00Z",
    })
    assert task["owner_id"] == str(DEFAULT_USER_ID)
    _, done = await _call(anon_client, token, "complete_task", {"task_id": task["id"]})
    assert done["completed_at"] is not None
    _, logged = await _call(anon_client, token, "log_activity",
                            {"lead_id": created["id"], "activity_type": "call", "subject": "Intro call"})
    assert logged["activity_type"] == "call"
    # The stage change was audited with the key's member as the actor.
    changed_by = (await db_session.execute(text(
        "SELECT changed_by FROM opportunity_stage_changes ORDER BY created_at DESC LIMIT 1"))).scalar()
    assert changed_by == DEFAULT_USER_ID


async def test_assign_owner_and_validation(client, anon_client, db_session, _engine):
    teammate, _, _ = await create_test_user(_engine, tenant_id=None, role="member")
    lead = Lead(email="x@y.com")
    db_session.add(lead)
    await db_session.commit()
    token = await _mint(client)
    _, res = await _call(anon_client, token, "assign_owner",
                         {"record_type": "lead", "ids": [str(lead.id)], "owner_id": None})
    assert res == {"updated": 1}
    result, msg = await _call(anon_client, token, "assign_owner",
                              {"record_type": "lead", "ids": [str(lead.id)], "owner_id": str(teammate)})
    assert result["isError"] and "member" in msg


async def test_pause_resume_and_breaker_guard(client, anon_client, db_session):
    camp = await _campaign(db_session, status=CampaignStatus.RUNNING)
    token = await _mint(client)
    _, paused = await _call(anon_client, token, "pause_campaign", {"campaign_id": str(camp.id)})
    assert paused["status"] == "paused"
    _, resumed = await _call(anon_client, token, "resume_campaign", {"campaign_id": str(camp.id)})
    assert resumed["status"] == "running"

    tripped = await _campaign(db_session, name="tripped", status=CampaignStatus.PAUSED,
                              auto_paused_at=datetime.now(timezone.utc),
                              auto_pause_reason="bounce rate 9% > 5%")
    result, msg = await _call(anon_client, token, "resume_campaign", {"campaign_id": str(tripped.id)})
    assert result["isError"] and "circuit breaker" in msg
    await db_session.refresh(tripped)
    assert tripped.status == CampaignStatus.PAUSED


async def test_tool_errors_are_readable(client, anon_client):
    token = await _mint(client)
    result, msg = await _call(anon_client, token, "update_lead", {"lead_id": "00000000-0000-0000-0000-000000000000",
                                                                   "notes": "x"})
    assert result["isError"] and msg.startswith("Email Blaster refused this")
    result, msg = await _call(anon_client, token, "get_lead", {})
    assert result["isError"] and "lead_id" in msg


# --- guardrails ---------------------------------------------------------------

PINNED = {
    "daily_brief", "list_campaigns", "get_campaign", "search_leads", "get_lead", "list_replies",
    "list_opportunities", "get_opportunity", "list_tasks", "list_team_members", "list_notifications",
    "create_lead", "update_lead", "log_activity", "create_task", "complete_task", "convert_lead",
    "create_opportunity", "update_opportunity", "assign_owner", "pause_campaign", "resume_campaign",
    "list_lead_activities", "ignore_lead",
    "research_prospect", "redraft_outreach", "edit_outreach_draft", "confirm_outreach",
    "send_outreach", "discard_outreach",
    "crm_overview", "deals_report", "activities_report", "report_fields", "run_report",
    "list_saved_reports", "run_saved_report", "save_report", "update_report",
}


def test_tool_surface_is_pinned():
    """Widening what an agent can do must be a deliberate edit here."""
    assert {t.name for t in TOOLS} == PINNED


@pytest.mark.parametrize("word", ["approve", "launch", "delete", "remove", "invite", "unignore",
                                  "linkedin", "integration", "api_key", "key", "campaign_send"])
def test_no_outbound_or_admin_tools(word):
    for t in TOOLS:
        assert word not in t.name, f"{t.name} looks like it crosses the agent boundary"


def test_only_human_gated_send_exists():
    """send_outreach is the single tool that reaches a prospect, and it can't
    run without the confirm step's code and an explicit approval flag."""
    senders = [t for t in TOOLS if "send" in t.name]
    assert [t.name for t in senders] == ["send_outreach"]
    schema = senders[0].input_schema
    assert set(schema["required"]) == {"draft_id", "confirmation_code", "user_approved"}
    ann = senders[0].listing()["annotations"]
    assert ann["destructiveHint"] is True and ann["openWorldHint"] is True


def test_research_prospect_never_sends():
    tool = next(t for t in TOOLS if t.name == "research_prospect")
    assert "SENDS NOTHING" in tool.description


def test_read_tools_are_flagged_read_only():
    reads = {t.name for t in TOOLS if not t.mutates}
    assert reads == {n for n in PINNED if n.startswith(("daily_", "list_", "get_", "search_"))} | {
        "crm_overview", "deals_report", "activities_report", "report_fields", "run_report",
        "run_saved_report",
    }


def test_log_activity_cannot_create_tasks_or_send():
    schema = next(t for t in TOOLS if t.name == "log_activity").input_schema
    assert "task" not in schema["properties"]["activity_type"]["enum"]


# --- search, activity log, ignore -------------------------------------------


async def test_search_leads_by_email(client, anon_client, db_session):
    camp_a = await _campaign(db_session, name="A")
    camp_b = await _campaign(db_session, name="B")
    db_session.add_all([
        Lead(campaign_id=camp_a.id, email="jane@acme.com", first_name="Jane"),
        Lead(campaign_id=camp_b.id, email="jane@acme.com", first_name="Jane"),
        Lead(email="jane.doe@acme.com", first_name="Other Jane"),
    ])
    await db_session.commit()
    token = await _mint(client)
    _, exact = await _call(anon_client, token, "search_leads", {"email": "JANE@acme.com"})
    assert exact["match"] == "exact" and exact["total"] == 2
    assert {l["email"] for l in exact["leads"]} == {"jane@acme.com"}
    _, partial = await _call(anon_client, token, "search_leads", {"email": "acme.com"})
    assert partial["match"] == "partial" and partial["total"] == 3


async def test_search_opportunities_by_text(client, anon_client, db_session):
    db_session.add_all([
        Opportunity(name="Acme renewal", stage=OpportunityStage.PROPOSAL, email="cfo@acme.com"),
        Opportunity(name="Globex pilot", stage=OpportunityStage.PROSPECTING),
    ])
    await db_session.commit()
    token = await _mint(client)
    _, by_name = await _call(anon_client, token, "list_opportunities", {"query": "acme"})
    assert [o["name"] for o in by_name["opportunities"]] == ["Acme renewal"]
    _, by_email = await _call(anon_client, token, "list_opportunities", {"query": "cfo@acme.com"})
    assert by_email["total"] == 1


async def test_lead_activity_log(client, anon_client, db_session):
    token = await _mint(client)
    _, lead = await _call(anon_client, token, "create_lead", {"email": "log@acme.com"})
    await _call(anon_client, token, "log_activity",
                {"lead_id": lead["id"], "activity_type": "call", "subject": "Discovery call"})
    await _call(anon_client, token, "log_activity",
                {"lead_id": lead["id"], "activity_type": "note", "subject": "Prefers email",
                 "body": "Budget in Q1"})
    _, log = await _call(anon_client, token, "list_lead_activities", {"lead_id": lead["id"]})
    assert log["total"] == 2
    assert {a["subject"] for a in log["activities"]} == {"Discovery call", "Prefers email"}
    _, notes = await _call(anon_client, token, "list_lead_activities",
                           {"lead_id": lead["id"], "activity_type": "note"})
    assert [a["body"] for a in notes["activities"]] == ["Budget in Q1"]


async def test_ignore_lead_suppresses_and_halts(client, anon_client, db_session):
    from app.models import Suppression

    camp = await _campaign(db_session, status=CampaignStatus.RUNNING)
    lead = Lead(campaign_id=camp.id, email="stop@acme.com")
    db_session.add(lead)
    await db_session.commit()
    token = await _mint(client)
    _, res = await _call(anon_client, token, "ignore_lead", {"lead_id": str(lead.id)})
    assert res["email"] == "stop@acme.com" and res["suppressed"] is True
    sup = (await db_session.execute(select(Suppression).where(Suppression.email == "stop@acme.com"))).first()
    assert sup is not None
    _, again = await _call(anon_client, token, "ignore_lead", {"lead_id": str(lead.id)})
    assert again["already_suppressed"] is True


async def test_ignore_lead_without_email_refused(client, anon_client, db_session):
    lead = Lead(email=None, first_name="LinkedIn only", linkedin_url="https://linkedin.com/in/x")
    db_session.add(lead)
    await db_session.commit()
    token = await _mint(client)
    result, msg = await _call(anon_client, token, "ignore_lead", {"lead_id": str(lead.id)})
    assert result["isError"] and "no email" in msg



async def test_muse_outreach_flow_end_to_end(client, anon_client, set_creds, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.routers import outreach_drafts

    set_creds("anthropic", api_key="k")
    set_creds("brevo", api_key="k", sender_email="me@myco.com")

    async def fake(db, **kw):
        research = kw.get("research") or {"found": True, "first_name": "Jane", "last_name": "Doe",
                                          "company": "Acme"}
        return research, {"subject": "Hello Acme", "body": "Hi Jane" + (" (short)" if "shorter" in kw["goal"] else "")}

    monkeypatch.setattr(outreach_drafts, "research_and_compose", fake)
    token = await _mint(client)
    _, draft = await _call(anon_client, token, "research_prospect",
                           {"linkedin_url": "https://www.linkedin.com/in/jane-doe/", "goal": "demo"})
    assert draft["status"] == "draft" and "Do NOT send" in draft["next_step"]
    _, redrafted = await _call(anon_client, token, "redraft_outreach",
                               {"draft_id": draft["id"], "feedback": "shorter"})
    assert redrafted["body"] == "Hi Jane (short)"
    result, msg = await _call(anon_client, token, "send_outreach",
                              {"draft_id": draft["id"], "confirmation_code": "X" * 6, "user_approved": False})
    assert result["isError"] and "approve" in msg
    _, confirmed = await _call(anon_client, token, "confirm_outreach",
                               {"draft_id": draft["id"], "to_email": "jane@acme.com"})
    code = confirmed["confirmation"]["confirmation_code"]
    assert confirmed["confirmation"]["to"] == "jane@acme.com"
    send = AsyncMock(return_value="<m@brevo>")
    with patch("app.services.outreach.brevo.send_email", new=send):
        _, sent = await _call(anon_client, token, "send_outreach",
                              {"draft_id": draft["id"], "confirmation_code": code, "user_approved": True})
    assert sent["status"] == "sent"
    send.assert_awaited_once()



async def test_reports_via_muse(client, anon_client, db_session):
    db_session.add_all([
        Opportunity(name="A", stage=OpportunityStage.PROPOSAL, amount=1000),
        Opportunity(name="B", stage=OpportunityStage.PROPOSAL, amount=500),
        Opportunity(name="C", stage=OpportunityStage.CLOSED_WON, amount=2000),
    ])
    await db_session.commit()
    token = await _mint(client)
    _, fields = await _call(anon_client, token, "report_fields", {"data_source": "opportunities"})
    keys = {f["key"] for f in fields["objects"][0]["fields"]}
    assert {"stage", "amount", "owner"} <= keys and "definition_format" in fields
    _, by_stage = await _call(anon_client, token, "run_report", {
        "data_source": "opportunities", "group_by": ["stage"],
        "aggregates": [{"fn": "sum", "field": "amount"}, {"fn": "count"}],
        "sort": [{"field": "amount_sum", "dir": "desc"}],
    })
    totals = {r["stage"]: r["amount_sum"] for r in by_stage["rows"]}
    assert totals == {"closed_won": 2000, "proposal": 1500}
    _, mine = await _call(anon_client, token, "run_report", {
        "data_source": "opportunities", "columns": ["name", "owner"],
        "filters": [{"field": "owner", "op": "equals", "value": "me"}],
    })
    assert mine["row_count"] == 3 and mine["rows"][0]["owner"] == "Test Owner"  # names, not ids
    result, msg = await _call(anon_client, token, "run_report",
                              {"data_source": "opportunities", "columns": ["nope"]})
    assert result["isError"] and "nope" in msg
    _, saved = await _call(anon_client, token, "save_report", {
        "name": "Pipeline by stage", "data_source": "opportunities", "group_by": ["stage"],
        "aggregates": [{"fn": "sum", "field": "amount"}],
    })
    _, listed = await _call(anon_client, token, "list_saved_reports")
    assert [r["name"] for r in listed] == ["Pipeline by stage"]
    _, ran = await _call(anon_client, token, "run_saved_report", {"report_id": saved["id"]})
    assert ran["grouped"] is True and len(ran["rows"]) == 2
    _, renamed = await _call(anon_client, token, "update_report", {"report_id": saved["id"], "name": "Stages"})
    assert renamed["name"] == "Stages" and renamed["definition"]["group_by"] == ["stage"]
    _, overview = await _call(anon_client, token, "crm_overview")
    assert isinstance(overview, dict)
    _, won = await _call(anon_client, token, "deals_report", {"outcome": "won"})
    assert isinstance(won, dict)
    _, acts = await _call(anon_client, token, "activities_report")
    assert isinstance(acts, dict)
