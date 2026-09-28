"""Research-a-lead → draft → confirm → approved send (the Muse flow)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import (
    ConnectedAccount,
    CrmActivity,
    Lead,
    LinkedInAccount,
    LinkedInAccountStatus,
    Suppression,
    SuppressionReason,
)
from app.routers import outreach_drafts
from app.services.linkedin.base import ActionResult

URL = "https://www.linkedin.com/in/jane-doe-123/"
RESEARCH = {
    "found": True, "quality": "rich", "first_name": "Jane", "last_name": "Doe",
    "headline": "VP Growth at Acme", "company": "Acme", "company_website": "https://acme.com",
    "job_title": "VP Growth", "person_news": ["Spoke at SaaStr"],
}


@pytest.fixture(autouse=True)
def _providers(set_creds):
    set_creds("anthropic", api_key="k")
    set_creds("brevo", api_key="k", sender_email="me@myco.com", sender_name="Me")
    set_creds("unipile", api_key="k")


@pytest.fixture
def compose(monkeypatch):
    calls = []

    async def fake(db, *, linkedin_url, goal, tone="professional", sender_name="", research_mode="fast",
                   output_kind="linkedin_dm", char_limit=600, research=None):
        calls.append({"goal": goal, "output_kind": output_kind, "char_limit": char_limit,
                      "research_reused": research is not None})
        n = len(calls)
        return (research or RESEARCH), {"subject": f"Idea for Acme v{n}", "body": f"Hi Jane — draft {n}"}

    monkeypatch.setattr(outreach_drafts, "research_and_compose", fake)
    return calls


async def _draft(client, **kw):
    resp = await client.post("/outreach-drafts", json={"linkedin_url": URL, "goal": "book a demo", **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_draft_sends_nothing_and_suggests_recipient(client, compose, db_session):
    db_session.add(Lead(email="jane@acme.com", linkedin_url="https://linkedin.com/in/jane-doe-123"))
    await db_session.commit()
    send = AsyncMock()
    with patch("app.services.outreach.brevo.send_email", new=send):
        d = await _draft(client)
    send.assert_not_awaited()
    assert d["status"] == "draft" and d["body"] == "Hi Jane — draft 1"
    assert d["profile"]["company"] == "Acme"
    assert d["to_email"] == "jane@acme.com"  # preselected from the CRM match
    assert d["recipient_suggestions"][0]["source"] == "existing CRM lead"
    assert d["sender_email"] == "me@myco.com"
    assert "Do NOT send" in d["next_step"]


async def test_redraft_reuses_research_and_voids_confirmation(client, compose):
    d = await _draft(client)
    c = await client.post(f"/outreach-drafts/{d['id']}/confirm", json={"to_email": "jane@acme.com"})
    code = c.json()["confirmation"]["confirmation_code"]
    r = (await client.post(f"/outreach-drafts/{d['id']}/redraft", json={"feedback": "shorter"})).json()
    assert r["status"] == "draft" and r["version"] == d["version"] + 1
    assert compose[-1]["research_reused"] is True and "shorter" in compose[-1]["goal"]
    stale = await client.post(f"/outreach-drafts/{d['id']}/send",
                              json={"confirmation_code": code, "user_approved": True})
    assert stale.status_code == 409


async def test_send_requires_confirmation_code_and_approval(client, compose):
    d = await _draft(client)
    early = await client.post(f"/outreach-drafts/{d['id']}/send",
                              json={"confirmation_code": "ABCDEF", "user_approved": True})
    assert early.status_code == 409  # not confirmed yet
    c = (await client.post(f"/outreach-drafts/{d['id']}/confirm", json={"to_email": "jane@acme.com"})).json()
    assert c["status"] == "ready"
    assert c["confirmation"]["to"] == "jane@acme.com" and c["confirmation"]["from"] == "me@myco.com"
    code = c["confirmation"]["confirmation_code"]
    no = await client.post(f"/outreach-drafts/{d['id']}/send",
                           json={"confirmation_code": code, "user_approved": False})
    assert no.status_code == 409
    wrong = await client.post(f"/outreach-drafts/{d['id']}/send",
                              json={"confirmation_code": "000000", "user_approved": True})
    assert wrong.status_code == 403


async def test_approved_email_sends_once_and_logs_crm(client, compose, db_session):
    d = await _draft(client)
    code = (await client.post(f"/outreach-drafts/{d['id']}/confirm",
                              json={"to_email": "Jane@Acme.com"})).json()["confirmation"]["confirmation_code"]
    send = AsyncMock(return_value="<msg-1@brevo>")
    with patch("app.services.outreach.brevo.send_email", new=send):
        sent = await client.post(f"/outreach-drafts/{d['id']}/send",
                                 json={"confirmation_code": code, "user_approved": True})
        again = await client.post(f"/outreach-drafts/{d['id']}/send",
                                  json={"confirmation_code": code, "user_approved": True})
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["status"] == "sent" and body["crm_lead_id"]
    assert again.status_code == 409
    send.assert_awaited_once()
    kw = send.call_args.kwargs
    assert kw["to_email"] == "jane@acme.com" and kw["sender_email"] == "me@myco.com"
    assert kw["subject"] == "Idea for Acme v1"
    lead = await db_session.get(Lead, __import__("uuid").UUID(body["crm_lead_id"]))
    assert lead.company == "Acme" and lead.linkedin_url == URL  # enriched from research


async def test_confirm_refuses_ignored_recipient_and_unknown_sender(client, compose, db_session):
    db_session.add(Suppression(email="stop@acme.com", reason=SuppressionReason.UNSUBSCRIBED))
    await db_session.commit()
    d = await _draft(client)
    blocked = await client.post(f"/outreach-drafts/{d['id']}/confirm", json={"to_email": "stop@acme.com"})
    assert blocked.status_code == 409 and "ignore list" in blocked.json()["detail"]
    spoof = await client.post(f"/outreach-drafts/{d['id']}/confirm",
                              json={"to_email": "jane@acme.com", "sender_email": "ceo@bigbank.com"})
    assert spoof.status_code == 422


async def test_connected_inbox_is_a_valid_sender(client, compose, db_session):
    db_session.add(ConnectedAccount(label="Sales", email_address="sales@myco.com", imap_host="x",
                                    username="sales@myco.com", password_encrypted="x"))
    await db_session.commit()
    d = await _draft(client)
    assert {o["sender_email"] for o in d["sender_options"]} >= {"sales@myco.com", "me@myco.com"}
    ok = await client.post(f"/outreach-drafts/{d['id']}/confirm",
                           json={"to_email": "jane@acme.com", "sender_email": "sales@myco.com"})
    assert ok.status_code == 200


async def test_brevo_failure_marks_failed_and_needs_reconfirm(client, compose):
    d = await _draft(client)
    code = (await client.post(f"/outreach-drafts/{d['id']}/confirm",
                              json={"to_email": "jane@acme.com"})).json()["confirmation"]["confirmation_code"]
    with patch("app.services.outreach.brevo.send_email", new=AsyncMock(side_effect=RuntimeError("boom"))):
        res = (await client.post(f"/outreach-drafts/{d['id']}/send",
                                 json={"confirmation_code": code, "user_approved": True})).json()
    assert res["status"] == "failed" and "boom" in res["send_error"]
    retry = await client.post(f"/outreach-drafts/{d['id']}/send",
                              json={"confirmation_code": code, "user_approved": True})
    assert retry.status_code == 409  # must confirm again


async def _li_account(db_session):
    acc = LinkedInAccount(label="Me", linkedin_email="me@x.com", provider_kind="unipile",
                          unipile_account_id="up-1", status=LinkedInAccountStatus.OK)
    db_session.add(acc)
    await db_session.commit()
    return acc


async def test_linkedin_dm_flow(client, compose, db_session, monkeypatch):
    acc = await _li_account(db_session)
    monkeypatch.setattr(outreach_drafts, "claim_linkedin_slot", AsyncMock(return_value={"ok": True}))
    provider = AsyncMock()
    provider.send_dm.return_value = ActionResult(ok=True, external_id="chat-9")
    d = await _draft(client, channel="linkedin_dm")
    assert compose[0]["output_kind"] == "linkedin_dm" and compose[0]["char_limit"] == 300
    assert d["linkedin_account_id"] == str(acc.id) and d["recipient_suggestions"] == []
    code = (await client.post(f"/outreach-drafts/{d['id']}/confirm", json={})).json()["confirmation"]["confirmation_code"]
    with patch("app.services.linkedin.get_provider", return_value=provider):
        sent = (await client.post(f"/outreach-drafts/{d['id']}/send",
                                  json={"confirmation_code": code, "user_approved": True})).json()
    assert sent["status"] == "sent" and sent["sent_external_id"] == "chat-9"
    provider.send_dm.assert_awaited_once()
    assert provider.send_dm.call_args.args[2] == "Hi Jane — draft 1"
    act = (await db_session.execute(select(CrmActivity))).scalars().one()
    assert act.subject == "LinkedIn message sent"
    lead = await db_session.get(Lead, act.lead_id)
    assert lead.first_name == "Jane" and lead.email is None


async def test_linkedin_dm_failure_suggests_connection_request(client, compose, db_session, monkeypatch):
    await _li_account(db_session)
    monkeypatch.setattr(outreach_drafts, "claim_linkedin_slot", AsyncMock(return_value={"ok": True}))
    provider = AsyncMock()
    provider.send_dm.return_value = ActionResult(ok=False, error="recipient cannot be reached")
    d = await _draft(client, channel="linkedin_dm")
    code = (await client.post(f"/outreach-drafts/{d['id']}/confirm", json={})).json()["confirmation"]["confirmation_code"]
    with patch("app.services.linkedin.get_provider", return_value=provider):
        res = (await client.post(f"/outreach-drafts/{d['id']}/send",
                                 json={"confirmation_code": code, "user_approved": True})).json()
    assert res["status"] == "failed" and "linkedin_connect" in res["send_error"]


async def test_connection_note_capped_at_200(client, compose, db_session):
    await _li_account(db_session)
    d = await _draft(client, channel="linkedin_connect", char_limit=600)
    assert compose[0]["char_limit"] == 200
    await client.patch(f"/outreach-drafts/{d['id']}", json={"body": "x" * 250})
    too_long = await client.post(f"/outreach-drafts/{d['id']}/confirm", json={})
    assert too_long.status_code == 422 and "200" in too_long.json()["detail"]


async def test_missing_brevo_blocks_email_drafts(client, compose, set_creds):
    set_creds("brevo", None)
    resp = await client.post("/outreach-drafts", json={"linkedin_url": URL, "goal": "demo"})
    assert resp.status_code == 409 and resp.json()["provider"] == "brevo"
