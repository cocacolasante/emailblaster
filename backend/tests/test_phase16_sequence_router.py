"""Integration tests for the /campaigns/{id}/sequence router."""
import pytest


def _campaign_payload(**overrides) -> dict:
    base = {
        "name": "Seq test",
        "goal": "Book a call",
        "tone": "Direct",
        "sender_name": "Anthony",
        "sender_email": "anthony@example.com",
        "research_mode": "fast",
        "sample_count": 3,
        "schedule_days": [0, 1, 2, 3, 4],
        "schedule_time_start": "09:00:00",
        "schedule_time_end": "17:00:00",
        "schedule_timezone": "UTC",
        "min_delay_seconds": 60,
    }
    base.update(overrides)
    return base


async def _create_campaign(client) -> str:
    r = await client.post("/campaigns/", json=_campaign_payload())
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_campaign_creation_auto_creates_default_sequence(client):
    cid = await _create_campaign(client)
    r = await client.get(f"/campaigns/{cid}/sequence")
    assert r.status_code == 200
    body = r.json()
    assert body["is_published"] is True
    assert len(body["nodes"]) == 1
    n = body["nodes"][0]
    assert n["kind"] == "email"
    assert n["is_entry"] is True
    assert n["config"] == {"use_campaign_compose": True}
    assert body["edges"] == []


async def test_put_sequence_replaces_graph(client):
    cid = await _create_campaign(client)

    payload = {
        "nodes": [
            {
                "client_id": "entry",
                "kind": "email",
                "is_entry": True,
                "config": {"use_campaign_compose": True},
                "position_x": 0,
                "position_y": 0,
            },
            {
                "client_id": "wait1",
                "kind": "wait",
                "is_entry": False,
                "config": {"duration_minutes": 4320},
                "position_x": 200,
                "position_y": 0,
            },
            {
                "client_id": "followup",
                "kind": "email",
                "is_entry": False,
                "config": {
                    "subject_template": "Following up, {{first_name}}?",
                    "body_template": "Hi {{first_name}}, just checking in.",
                },
                "position_x": 400,
                "position_y": 0,
            },
        ],
        "edges": [
            {"from_client_id": "entry", "to_client_id": "wait1", "condition": {"op": "always"}},
            {
                "from_client_id": "wait1",
                "to_client_id": "followup",
                "condition": {"op": "not", "child": {"op": "replied"}},
                "priority": 0,
            },
        ],
    }
    r = await client.put(f"/campaigns/{cid}/sequence", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 2
    # PUT clears is_published (publish step revalidates + flips back).
    assert body["is_published"] is False


async def test_put_rejects_two_entry_nodes(client):
    cid = await _create_campaign(client)
    payload = {
        "nodes": [
            {"client_id": "a", "kind": "email", "is_entry": True, "config": {}},
            {"client_id": "b", "kind": "email", "is_entry": True, "config": {}},
        ],
        "edges": [],
    }
    r = await client.put(f"/campaigns/{cid}/sequence", json=payload)
    assert r.status_code == 422


async def test_publish_rejects_unreleased_linkedin_kinds(client):
    """linkedin_inmail / linkedin_comment_post are still gated to M4."""
    cid = await _create_campaign(client)
    payload = {
        "nodes": [
            {"client_id": "entry", "kind": "email", "is_entry": True, "config": {}},
            {
                "client_id": "li",
                "kind": "linkedin_inmail",
                "is_entry": False,
                "config": {},
            },
        ],
        "edges": [
            {"from_client_id": "entry", "to_client_id": "li", "condition": {"op": "always"}},
        ],
    }
    r = await client.put(f"/campaigns/{cid}/sequence", json=payload)
    assert r.status_code == 200

    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    assert pub.status_code == 200
    body = pub.json()
    assert body["ok"] is False
    assert body["is_published"] is False
    assert any("linkedin_inmail" in e for e in body["errors"])


async def test_validate_flags_unreachable_node(client):
    cid = await _create_campaign(client)
    payload = {
        "nodes": [
            {"client_id": "entry", "kind": "email", "is_entry": True, "config": {}},
            {"client_id": "orphan", "kind": "wait", "is_entry": False, "config": {"duration_minutes": 60}},
        ],
        "edges": [],
    }
    r = await client.put(f"/campaigns/{cid}/sequence", json=payload)
    assert r.status_code == 200

    v = await client.post(f"/campaigns/{cid}/sequence/validate")
    body = v.json()
    assert body["ok"] is False
    assert any("unreachable" in e for e in body["errors"])


async def test_publish_succeeds_for_email_wait_email_chain(client):
    cid = await _create_campaign(client)
    payload = {
        "nodes": [
            {"client_id": "entry", "kind": "email", "is_entry": True, "config": {"use_campaign_compose": True}},
            {"client_id": "w", "kind": "wait", "is_entry": False, "config": {"duration_minutes": 4320}},
            {
                "client_id": "f",
                "kind": "email",
                "is_entry": False,
                "config": {"subject_template": "Hi", "body_template": "Body"},
            },
        ],
        "edges": [
            {"from_client_id": "entry", "to_client_id": "w", "condition": {"op": "always"}},
            {"from_client_id": "w", "to_client_id": "f", "condition": {"op": "not", "child": {"op": "replied"}}},
            {"from_client_id": "f", "to_client_id": None, "condition": {"op": "always"}},
        ],
    }
    r = await client.put(f"/campaigns/{cid}/sequence", json=payload)
    assert r.status_code == 200

    pub = await client.post(f"/campaigns/{cid}/sequence/publish")
    body = pub.json()
    assert body["ok"] is True
    assert body["is_published"] is True
    assert body["errors"] == []
