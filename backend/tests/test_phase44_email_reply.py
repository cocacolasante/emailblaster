"""Phase 44: the email_reply (reply-in-thread) sequence node — publish
validation.  Worker behaviour is covered in test_phase16_sequencer.py and
the threading headers in test_phase9_brevo.py."""
from __future__ import annotations


async def _make_campaign(client) -> str:
    resp = await client.post("/campaigns/", json={
        "name": "x", "goal": "g", "tone": "Direct",
        "sender_name": "A", "sender_email": "a@example.com",
        "research_mode": "fast",
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _publish(client, cid, reply_node):
    payload = {
        "nodes": [
            {"client_id": "e", "kind": "email", "is_entry": True, "config": {}},
            {"client_id": "w", "kind": "wait", "is_entry": False,
             "config": {"duration_minutes": 2880}},
            reply_node,
        ],
        "edges": [
            {"from_client_id": "e", "to_client_id": "w", "condition": {"op": "always"}},
            {"from_client_id": "w", "to_client_id": "r",
             "condition": {"op": "not", "child": {"op": "replied"}}},
        ],
    }
    await client.put(f"/campaigns/{cid}/sequence", json=payload)
    return await client.post(f"/campaigns/{cid}/sequence/publish")


async def test_publish_accepts_ai_reply(client):
    cid = await _make_campaign(client)
    pub = await _publish(client, cid, {
        "client_id": "r", "kind": "email_reply", "is_entry": False,
        "config": {"ai_compose": True, "ai_prompt": "nudge about the trial"},
    })
    body = pub.json()
    assert body["ok"] is True, body


async def test_publish_accepts_manual_reply(client):
    cid = await _make_campaign(client)
    pub = await _publish(client, cid, {
        "client_id": "r", "kind": "email_reply", "is_entry": False,
        "config": {"body_template": "Just bumping this, {{first_name}}."},
    })
    assert pub.json()["ok"] is True


async def test_publish_rejects_reply_without_body_or_ai(client):
    cid = await _make_campaign(client)
    pub = await _publish(client, cid, {
        "client_id": "r", "kind": "email_reply", "is_entry": False, "config": {},
    })
    body = pub.json()
    assert body["ok"] is False
    assert any("body_template" in e or "ai_compose" in e for e in body["errors"])


async def test_publish_rejects_reply_as_entry(client):
    cid = await _make_campaign(client)
    payload = {
        "nodes": [
            {"client_id": "r", "kind": "email_reply", "is_entry": True,
             "config": {"ai_compose": True}},
        ],
        "edges": [],
    }
    await client.put(f"/campaigns/{cid}/sequence", json=payload)
    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    body = pub.json()
    assert body["ok"] is False
    assert any("entry node" in e for e in body["errors"])
